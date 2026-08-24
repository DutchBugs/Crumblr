# feedback.1.3.md — Follow-up Architecture & Safety Review

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.3  
**Review date:** 2026-08-18  
**Reviewed artifact:** `review/FEEDBACK.md`  
**Previous review:** `feedback.1.2.md`  
**Overall verdict:** **GO WITH CONDITIONS**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review evaluates the supplied feedback tracker and its stated evidence. Source code, `status.md`, ADR-001, ADR-002 and logging implementation were not independently inspected in this review.

---

## 1. Executive review

The project continues to move in the right direction.

The two-field review model is now properly adopted:

```text
Finding status
Implementation status
```

This is materially better than using one `CLOSED` label for both design resolution and shipped/validated behavior.

The tracker now makes these distinctions explicit:

```text
F-007  CLOSED / DECIDED — PENDING M5
F-011  CLOSED / DECIDED — PENDING M5
F-012  CLOSED / DECIDED — PENDING M2
F-008  CLOSED / SHIPPED — not MT5-validated
F-013  CLOSED / SHIPPED
```

That is the correct review vocabulary.

The next engineering phase should focus on **M2 PostgreSQL persistence**. Before that implementation expands, the persistence invariants should be explicit so the database becomes the audit/recovery backbone the architecture requires, not merely a storage layer.

---

## 2. Re-evaluation of existing findings

### F-001 through F-012

**Status:** No reopenings based on the supplied tracker.

Important caveats:

- F-007 and F-011 are design-closed only and still require M5 implementation.
- F-012 is design-closed only and still requires M2 implementation.
- F-008 is shipped but remains unproven against real MT5/broker state.

The tracker communicates this distinction correctly.

### F-013 — M0 structured logging baseline

**Previous status:** OPEN  
**Review 1.3 status:** **CLOSED BASED ON DOCUMENTED TEST EVIDENCE**

The tracker reports:

```text
observability/logging.py
25 unit tests
all seven required properties covered
```

That is sufficient to accept F-013 as closed for this documentation review.

Because the implementation itself was not supplied, this is not independent code verification.

Structured logging must remain:

```text
observability only
not business state
not execution authority
not a dependency for safety decisions
```

A logging failure must never make a blocked order executable.

---

## 3. New findings

### F-014 — FEEDBACK.md contains a stale note contradicting F-013 closure

**Severity:** LOW / DOCUMENTATION  
**Status:** OPEN

The tracker correctly states that F-013 is now `CLOSED / SHIPPED`.

However, the reviewer notes still state that logging "does not exist". That is now stale and contradicts the current finding register.

**Required change**

Preserve the historical discovery, but rewrite it in past tense.

Recommended form:

```text
One gap neither review originally caught was that build.md §26 required logging
while only the dependency/package skeleton existed. This was formalised as F-013
and is now implemented and tested.
```

**Acceptance**

- Current notes no longer state that logging is still missing.
- The historical discovery remains auditable.

---

### F-015 — PostgreSQL persistence invariants must be explicit before M2 grows

**Severity:** HIGH DESIGN / DATA INTEGRITY  
**Status:** OPEN  
**Required before:** M2 can be considered implementation-complete

M2 is now the next engineering priority.

Before a large persistence layer is built, define the invariants that make it safe for audit, replay and crash recovery.

At minimum:

#### 1. Append-only authority

Business/system events must not be silently overwritten.

Corrections should be represented by new events rather than mutating historical truth.

```text
bad:
UPDATE event SET payload = corrected_payload

preferred:
OriginalEvent
→ Correction/CompensatingEvent
```

#### 2. Stable event identity

Each event requires an immutable globally unique `event_id`.

Retries must not create duplicate logical events.

#### 3. Idempotent writes

A repeated write with the same event identity must either return the existing event or fail deterministically as a duplicate.

It must never append a second logical copy.

#### 4. Causal ordering

Persist and validate:

```text
occurred_at_utc
correlation_id
causation_id
schema_version
```

Database insertion order must not be treated as market-event time.

#### 5. Transaction boundaries

Where one logical state transition requires multiple records, define whether they must commit atomically.

A safety-critical transition must not appear complete when only part of it committed.

#### 6. Crash consistency

Test:

```text
process dies before commit
process dies after DB commit but before acknowledgement
retry after ambiguous outcome
database reconnect
```

The recovery path must converge without duplicating state.

#### 7. Schema evolution

Every persisted event/model carries a schema version.

Old records must remain readable or have an explicit migration/upcasting strategy.

#### 8. Decision Capsule immutability

Once sealed, a Decision Capsule must not be silently edited.

Later evaluation data should be linked or explicitly versioned.

#### 9. Safety-state disagreement

ADR-002 semantics must be implemented as part of M2:

```text
journal state vs local safety latch disagree
→ UNKNOWN / HALTED
→ reconciliation required
```

Never choose the permissive source.

#### 10. UTC and Decimal fidelity

Round-tripping through PostgreSQL must preserve:

```text
timezone-aware UTC timestamps
Decimal monetary values
UUID/event identity
hash/fingerprint fields
```

No float coercion for monetary values.

**Required tests**

At minimum:

- duplicate event insert;
- retry after ambiguous commit;
- crash/restart recovery;
- event ordering with out-of-order timestamps;
- Decimal round-trip;
- UTC round-trip;
- schema-version round-trip;
- sealed Decision Capsule mutation rejection;
- conflicting safety-state recovery;
- replay from persisted journal reproduces the same decision/event sequence.

**Acceptance**

M2 is not complete merely because rows are stored.

M2 is complete when persisted state safely supports:

```text
replay
audit
restart
recovery
reconciliation
future execution
```

---

## 4. M0 status

**Reviewer view:** M0 is now close to formal closure.

Based on the supplied tracker, the logging gap is addressed.

However, previous non-logging M0 conditions still need to be checked in the current `status.md`, especially:

```text
human domain-contract approval
CI status represented honestly
```

This review does not independently close M0 because the latest `status.md` was not supplied.

---

## 5. Gate decisions — review 1.3

### M0 — Engineering baseline

**Verdict:** **READY FOR CLOSURE REVIEW, NOT AUTOMATICALLY CLOSED**

F-013 no longer blocks.

Supply current `status.md` at the next closure check.

### M1 — MT5 read-only gateway

**Verdict:** **GO WHEN DEPENDENCIES AVAILABLE**

Still requires:

```text
broker
demo account
MT5 server
Windows x86-64 host
```

### M2 — PostgreSQL persistence

**Verdict:** **GO NOW — PRIMARY ENGINEERING MILESTONE**

Implement F-015 invariants as part of the design, not after the database layer is already built.

### M3 — Replay / backtest

**Verdict:** **CONTINUE FOR CORRECTNESS ONLY**

No trading-performance promotion.

### M4 — Risk Engine

**Verdict:** **CONTINUE / NO PROMOTION**

No change.

### M5 — Paper execution

**Verdict:** **NO-GO**

Still blocked on:

```text
real MT5 integration
PostgreSQL persistence
reconciliation
ADR-001 implementation
ADR-002 implementation
MT5 validation of operator controls
real order lifecycle evidence
```

### M6 — Trading Agent

**Verdict:** **FEATURE FREEZE MAINTAINED**

No change.

### M7 — Evaluator / Supervisor

**Verdict:** **SAFETY WORK ONLY**

No change.

### P2 — Autonomous demo campaign

**Verdict:** **NO-GO**

No change.

---

## 6. Required next action order

```text
1. Fix stale FEEDBACK.md note (F-014)
2. Define M2 persistence invariants (F-015)
3. Implement PostgreSQL event journal
4. Implement Decision Capsule persistence
5. Implement ADR-002 safety-state authority/recovery semantics
6. Add crash/idempotency/replay persistence tests
7. Reconcile M0 current status and close formally if criteria are met
8. Select broker + demo account
9. Provision Windows MT5 host
10. Build read-only MT5 Gateway
11. Build real reconciliation
12. Prepare feedback.2.0 before first order_send
```

---

## 7. Reviewer note on evidence quality

A positive pattern continues:

```text
claim
→ evidence path
→ test
→ implementation-state label
```

The tracker no longer uses `CLOSED` as shorthand for “production-ready”.

For future integration work, evidence quality should increase in stages:

```text
unit evidence
→ process/integration evidence
→ persistent-state evidence
→ MT5 terminal evidence
→ broker/demo evidence
```

A later stage should not be inferred from an earlier one.

---

## 8. Next review version

The next regular review is:

```text
feedback.1.4.md
```

Recommended trigger:

- PostgreSQL M2 design/implementation is available;
- F-014/F-015 are addressed;
- current `status.md` is supplied for M0 closure.

The major review immediately before the first actual `order_send` remains:

```text
feedback.2.0.md
```

---

## 9. Final reviewer statement

The project has now mostly exhausted the value of further simulated feature development.

The important engineering question is changing from:

```text
"Can the pipeline make decisions?"
```

to:

```text
"Can the system preserve, recover and prove exactly what happened?"
```

M2 persistence is therefore not housekeeping.

It is part of the trading safety architecture.
