# ADR-002 — Safety-state authority and recovery precedence

**Status:** ACCEPTED · not yet implemented
**Date:** 2026-08-17
**Raised by:** review finding F-012 (`review/feedback.1.1.md`)
**Required before:** M5 — and to be settled *while* M2 is built, not after
**Related:** ADR-001

---

## Context

System safety state is currently held in one place: an atomically-written file,
read at startup, failing closed on anything unclear (review 1.0 F-003).

M2 introduces PostgreSQL for the event journal, incidents, reconciliation
results and account snapshots. That creates a second place where the same
question has an answer — and two stores that can be read independently can
disagree:

```text
file says     RUNNING
journal says  last safety event = HALTED
```

Without a rule, whichever store the startup path happens to consult first
decides whether the system trades. That is not a decision anyone would make on
purpose, and it is the kind that gets made by accident.

The failure mode is asymmetric. Wrongly halting costs opportunity. Wrongly
running resumes trading after a condition that was serious enough to stop it.

## Decision

### 1. The event journal is the record of authority; the file is a latch

PostgreSQL holds the authoritative history. Every trip and every reset is an
append-only event there, and that sequence is what an audit reads.

The file is not a cache of it. It is an independent **safety latch** that
answers one narrower question — "may this process open new orders?" — and it
exists because it survives conditions the database does not: the database being
unreachable, credentials being wrong, the network being down. A system that can
only learn it is halted by querying a database cannot fail closed when the
database is the thing that failed.

### 2. Any disagreement resolves to HALTED

Startup reads both. The state is RUNNING only when **both** say so.

| File | Journal | Result |
|---|---|---|
| RUNNING | RUNNING | RUNNING |
| RUNNING | HALTED | **HALTED** — journal wins |
| HALTED | RUNNING | **HALTED** — latch wins |
| RUNNING | unreachable | **HALTED** — `SAFETY_STATE_UNKNOWN` |
| unreadable | RUNNING | **HALTED** — `SAFETY_STATE_UNKNOWN` |
| unreadable | unreachable | **HALTED** |
| HALTED | HALTED | HALTED |

The rule underneath the table: **never prefer the more permissive store.** Two
sources that disagree about whether it is safe to trade have established only
that the answer is not known.

### 3. Disagreement is an incident, not a transient

A mismatch is not cleared by retrying until the stores agree. It raises an
incident, and clearing it requires the same operator action any other halt
requires — identified operator, written note. Reconciling the two stores is
part of that operator's work, not something the system does on its own and then
carries on.

This follows build.md §7 invariant 7: unknown state means halt, not another
attempt.

### 4. Writes go to both, journal first

A trip writes the journal event, then the latch. If the journal write fails the
trip fails and propagates — same principle as ADR-001's ordering, and the same
one already implemented for the file store: never let a process believe it
halted when nothing recorded it.

If the latch write fails after the journal write succeeded, the system is
halted (the journal says so) and the latch is stale in the *safe* direction on
the next read — the table above resolves file-RUNNING/journal-HALTED to HALTED.

### 5. A reset must clear both

An operator reset writes both stores. A reset that updated only one would
produce exactly the disagreement this ADR exists to resolve, and the next
startup would halt again with no visible cause.

## Consequences

**Accepted costs**

- Two writes per state change, and a read of both at startup. Neither is on a
  hot path; both happen at most a handful of times a day.
- The system can be halted by infrastructure trouble that has nothing to do
  with trading — an unreachable database halts it. That is the intended
  direction of failure, and it should be visible in monitoring so it is not
  mistaken for a risk event.
- Startup gets slower and can fail. Both are acceptable at a gate whose whole
  purpose is refusing to proceed on incomplete information.

**What this does not settle**

- The database schema for safety events. That is M2 work; this ADR fixes the
  precedence rule so the schema is designed against a decision rather than the
  decision being inferred from a schema.
- Whether the latch should also carry a monotonically increasing sequence
  number to detect a *stale but readable* file. Probably yes. Deferred to
  implementation, where the journal's event ordering will make the right shape
  obvious.

## Status of implementation

Not started. The file store implemented for review 1.0 F-003 already behaves
correctly for the single-store case, and its `SafetyStateStore` protocol is the
seam a composite implementation slots into. `status.md` tracks this as APP-004.
