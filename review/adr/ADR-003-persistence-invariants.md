# ADR-003 — Persistence invariants for the event journal

**Status:** ACCEPTED · not yet implemented
**Date:** 2026-08-18
**Raised by:** review finding F-015 (`review/feedback.1.3.md`)
**Required before:** M2 can be called implementation-complete
**Related:** ADR-002 (safety-state authority), build.md §11, §18, §23

---

## Context

M2 introduces PostgreSQL. The reviewer's framing is the right one:

> M2 is not complete merely because rows are stored. It is complete when
> persisted state safely supports replay, audit, restart, recovery,
> reconciliation and future execution.

These invariants are written before the schema exists, because a storage layer
that has already been built tends to have its invariants inferred from whatever
it happens to do.

Much of what is needed is already in the domain layer and only has to survive
the trip to the database and back:

| Requirement | Already exists |
|---|---|
| stable event identity | `Event.event_id` |
| causal ordering | `occurred_at_utc`, `correlation_id`, `causation_id` |
| schema evolution | `Event.schema_version` |
| capsule identity | `DecisionCapsule.capsule_id` |
| tamper evidence | `provenance_fingerprint`, `decision_hash`, `feature_values_hash` |
| exact money | `ExactDecimal` — floats are rejected at the model boundary |
| UTC | `UtcDatetime` — naive datetimes are rejected |

The work of M2 is therefore mostly about *not losing* properties the domain
already guarantees.

---

## Decision

### 1. The journal is append-only

`INSERT` only. No `UPDATE`, no `DELETE` on event tables — enforced by the
database, not by convention: the application role is granted `INSERT` and
`SELECT` and nothing else, so a mistaken `UPDATE` fails as a permission error
rather than succeeding quietly.

A correction is a new event that references the original through
`causation_id`. History is added to, never edited.

Retention and archival are separate concerns and will need a differently
privileged role; that is out of scope here and must not be solved by loosening
the application's grants.

### 2. Identity is assigned by the producer, not the database

`event_id` is a UUID generated where the event is created. A `BIGSERIAL` column
may exist for physical ordering, but it is not identity and must never be used
as one — a sequence is unique to one database, and a replay against a rebuilt
database would silently renumber everything.

### 3. Writes are idempotent on `event_id`

```sql
INSERT INTO events (...) VALUES (...) ON CONFLICT (event_id) DO NOTHING
```

A retry after an ambiguous outcome must converge, not duplicate. The writer
returns whether the row was newly inserted or already present, because "this
already existed" is information the caller sometimes needs — a duplicate order
event means something different from a duplicate heartbeat.

### 4. Database order is not event order

Three clocks exist and must not be conflated:

```text
occurred_at_utc   when it happened in the market's terms
recorded_at_utc   when the row was written
sequence          physical insertion order
```

Replay and audit order by `occurred_at_utc`, with `sequence` as a deterministic
tie-break. Ordering by insertion time would reorder events after a reconnect
backfill, which is precisely when order matters most.

Out-of-order arrival is expected and is not an error; an event whose
`occurred_at_utc` precedes one already stored is inserted normally.

### 5. Atomic state transitions

Where one logical transition spans several rows, they commit in one
transaction. Specifically: sealing a decision capsule and appending its
`DecisionCapsuleSealed` event; and writing a safety-state event together with
its incident record.

A safety-critical transition must never be observable half-done. Where a
transaction cannot span the boundary — a database write plus the local safety
latch (ADR-002) — the ordering rule applies instead: journal first, latch
second, and disagreement resolves to HALTED.

### 6. Crash consistency has one rule

**Write to the journal before acting, acknowledge after.**

The recovery path must converge for each of these:

```text
died before commit            → the event never happened; no action was taken
died after commit, before ack → the event happened; the actor re-reads and continues
retry after an ambiguous outcome → idempotent insert makes it a no-op
database reconnect            → resume from the last acknowledged sequence
```

An ambiguous outcome resolves to *reconcile*, never to *retry the action*. That
is the same rule build.md §7 invariant 7 applies to broker state.

### 7. Every persisted record carries its schema version

Stored as an integer column, not inferred from column presence. A reader that
meets a version it does not understand raises rather than guessing — the same
choice already made in `FileSafetyStateStore`, and for the same reason: a
record from an unknown schema may not mean what it appears to mean.

Upcasting, when it becomes necessary, happens on read into the current domain
model. The stored bytes are never rewritten.

### 8. A sealed capsule is immutable

Once written, a `DecisionCapsule` row is never modified. `provenance_fingerprint`
is stored alongside it, and the loader recomputes and compares it — a mismatch
is a tamper signal and raises rather than returning a degraded object.

Post-trade evaluation attaches to a capsule by reference, in its own table. It
does not edit the capsule, because the capsule's value is being a record of what
was known *at the time*.

### 9. Safety-state disagreement resolves to HALTED

ADR-002's precedence table is implemented as part of M2, not after it. The
recovery path reads both the journal and the local latch, and any disagreement —
including either being unreadable — produces `UNKNOWN`, which halts.

The permissive source never wins.

### 10. Round-trips preserve exactness

| Domain type | Column type | Never |
|---|---|---|
| `ExactDecimal`, `Price`, `Volume` | `NUMERIC` | `float8` / `REAL` |
| `UtcDatetime` | `TIMESTAMPTZ` | `TIMESTAMP` without zone |
| `UUID` | `UUID` | `TEXT` |
| fingerprints, hashes | `TEXT` | anything lossy |

The domain rejects binary floats at its boundary; storing money as `float8`
would reintroduce exactly the error that boundary exists to prevent, one layer
down where nothing is watching.

---

## Acceptance tests

M2 is not complete until these pass. The last one is the one that matters most.

1. inserting the same `event_id` twice stores one row and reports the duplicate
2. a retry after a simulated ambiguous commit converges to one row
3. a process killed mid-write leaves the journal readable and consistent
4. events arriving out of order are stored and read back in `occurred_at_utc`
   order, with a deterministic tie-break
5. `Decimal("0.00001")` survives a round trip bit-exact, and no monetary column
   is a float type
6. a timezone-aware UTC timestamp survives a round trip, and a naive one is
   refused
7. an unknown `schema_version` raises on read rather than being coerced
8. modifying a sealed capsule is refused by the database, and a tampered
   `provenance_fingerprint` is detected on read
9. a journal saying HALTED and a latch saying RUNNING resolves to halted
10. **a replay driven from the persisted journal reproduces the same decision
    sequence, byte for byte, as the in-memory replay**

Number 10 is the point of the whole milestone. If the journal cannot reproduce
a run, it is storage rather than an audit trail.

---

## Consequences

**Accepted costs**

- Append-only means the journal only grows. At EUR/USD M5 with one strategy this
  is a few hundred thousand rows a year — irrelevant now, and a partitioning
  decision later rather than a reason to allow mutation.
- Idempotent inserts need a unique index on `event_id`, and every write pays for
  it. Cheap relative to what it prevents.
- Two clocks and a sequence per event is more columns than a naive design. The
  alternative is discovering during an incident that "when it happened" was never
  recorded separately from "when it was written".

**Deliberately deferred**

- Partitioning, retention, archival.
- TimescaleDB. build.md §4.1 lists it as optional and tick volume does not yet
  justify it.
- Read models and projections. The journal is the source; views come later.

## Status of implementation

Shipped. M2 passed; §3's "writer returns whether newly inserted or already
present" invariant now covers both `execution_requests` (`ExecutionRequestStore
._claim()`, Phase 4) and `execution_events` (`ExecutionEventStore.append()`,
core critical path item 4, 2026-09-01) — see `persistence/execution.py` and
`review/FEEDBACK.md`. This status line was stale for a long time; noted here
rather than silently corrected, per this project's own documentation
discipline.
