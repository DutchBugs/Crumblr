# feedback.1.2.md — Follow-up Architecture & Safety Review

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.2  
**Review date:** 2026-08-17  
**Reviewed artifact:** `review/FEEDBACK.md`  
**Previous review:** `feedback.1.1.md`  
**Overall verdict:** **GO WITH CONDITIONS**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review evaluates the supplied feedback tracker and its stated evidence. Source code, `status.md`, ADR-001 amendments and ADR-002 were not independently inspected in this review.

---

# 1. Executive review

The response to `feedback.1.1.md` is strong.

The implementation team has:

- closed F-003 with a real cross-process restart test rather than an in-process object round-trip;
- cleaned the stale status contradictions from F-009;
- separated M0 and M1 dependencies under F-010;
- amended ADR-001 at design level for execution-price monetary-risk semantics;
- created ADR-002 for safety-state authority;
- retained M5/P2 as NO-GO;
- independently discovered that M0 logging was never actually implemented.

The last point is particularly positive. Discovering an unreviewed specification gap through systematic gate reconciliation is exactly the behavior the project needs.

The next engineering priority remains:

```text
M0 logging completion
→ M2 PostgreSQL persistence
→ safety-state authority implementation
→ M1 MT5 integration
→ reconciliation
→ M5 execution controls
```

---

# 2. Re-evaluation of findings

## F-001 — Maturity vs gate qualification

**Status:** CLOSED

No change.

The maturity / gate split should remain permanent.

---

## F-002 — Hard-coded safe supervisor state

**Status:** CLOSED BASED ON PREVIOUS DOCUMENTED EVIDENCE

No contrary evidence has been supplied.

Retain explicit UNKNOWN states and fail-closed behavior.

---

## F-003 — Kill-switch state does not survive restart

**Previous status:** IN PROGRESS  
**Review 1.2 status:** **CLOSED BASED ON DOCUMENTED TEST EVIDENCE**

The new evidence directly addresses the prior review concern:

```text
process A writes HALT
→ process A exits
→ process B starts
→ persisted HALT is recovered
```

The tracker explicitly states that a real child process is now spawned rather than constructing two objects in one interpreter.

That is the correct distinction.

### Reviewer note

Retain separate tests for:

```text
missing state
corrupt state
truncated state
unsupported schema
unwritable destination
```

All must continue to fail closed.

---

## F-004 — Strategy feature freeze

**Status:** CLOSED

No change.

The freeze remains appropriate.

---

## F-005 — Inconsistent progress reporting

**Status:** CLOSED

No contrary evidence.

---

## F-006 — Local Git

**Status:** CLOSED

No further action.

---

## F-007 — Execution-time risk revalidation

**Status:** CLOSED AT ARCHITECTURE LEVEL / IMPLEMENTATION PENDING M5

The design decision is accepted.

Do not interpret this as execution readiness.

Implementation remains an M5 prerequisite.

---

## F-008 — Operator cancel / flatten controls

**Status:** CLOSED AT CODE-DESIGN LEVEL / MT5 VALIDATION PENDING

The distinction in the tracker is correct.

Real broker behavior remains unproven.

---

## F-009 — Stale status blockers

**Status:** CLOSED BASED ON TRACKER EVIDENCE

The tracker states that APP-001/APP-002 were closed in place and EV-001 was split rather than history being deleted.

That is the correct remediation pattern.

---

## F-010 — M0 contained M1 dependencies

**Status:** CLOSED BASED ON TRACKER EVIDENCE

The new split between:

```text
build.md M0 deliverables
build.md M0 acceptance
local project policy
M1 dependencies
```

is the correct structure.

This review accepts the closure.

---

## F-011 — Final monetary-risk price basis

**Status:** CLOSED AT DESIGN LEVEL / IMPLEMENTATION PENDING M5

The tracker states ADR-001 now requires monetary risk to be recomputed using the current executable market side while:

```text
approved volume is fixed
volume may never increase
order either remains valid or is blocked
```

That is the intended design.

Because the amended ADR itself was not supplied in this review, closure is accepted based on the tracker rather than independently verified.

---

## F-012 — Safety-state authority

**Status:** CLOSED AT DESIGN LEVEL / IMPLEMENTATION PENDING

The stated ADR-002 rule is sound:

```text
event journal = record of authority
local file    = independent safety latch
disagreement  = HALTED
```

This is the correct conservative resolution.

Again, ADR-002 was not supplied here, so this review accepts the tracker claim provisionally.

---

# 3. New finding

## F-013 — M0 logging deliverable is missing

**Severity:** MEDIUM  
**Status:** OPEN  
**Blocks:** Formal M0 closure

The implementation team correctly discovered that `build.md` requires logging as an M0 deliverable, but no logging implementation exists.

A declared dependency is not an implementation.

Current situation:

```text
structlog dependency exists
observability package exists
runtime logging behavior does not exist
```

### Required implementation

M0 does not need the full production observability stack yet.

Implement a minimal structured logging baseline:

```text
JSON-compatible structured events
UTC timestamps
service/component field
event name
severity
correlation_id where available
no secrets
no raw credentials
deterministic field naming
```

Minimum components that should emit structured logs:

```text
application startup/shutdown
configuration load
safety-state recovery
kill-switch transition
replay start/finish
risk block/halt
supervisor veto/halt
unexpected exception
```

### Important distinction

Do not confuse:

```text
runtime logs
```

with:

```text
business/event journal
```

The event journal is authoritative trading/system state.

Logs are observability.

A missing log must never change the trading result.

### Required tests

At minimum:

1. logging initializes without external infrastructure;
2. emitted records are structured and parseable;
3. UTC timestamp exists;
4. correlation ID is propagated when supplied;
5. secrets are redacted or rejected;
6. logging failure cannot bypass safety logic;
7. logging does not mutate decision hashes or replay determinism.

### Acceptance

M0 logging may be marked complete when the baseline is implemented and tested locally.

Prometheus/Grafana/Loki/OpenTelemetry production deployment is not required for M0.

---

# 4. Tracker semantics recommendation

This is not a blocking finding, but the current tracker vocabulary could be made more precise.

The register says status is one of:

```text
OPEN
IN PROGRESS
CLOSED
ANSWERED
```

while several findings are recorded as:

```text
CLOSED at design level
```

That distinction is important.

Recommended improvement:

Either add:

```text
DECIDED
```

or track two fields:

```text
Finding status
Implementation status
```

Example:

```text
F-011
Finding: CLOSED
Implementation: PENDING M5
```

This avoids future confusion where “CLOSED” is mistaken for “shipped and validated”.

Not a blocker if the current wording remains explicit.

---

# 5. Gate decisions — review 1.2

## M0 — Engineering baseline

**Verdict:** GO WITH CONDITIONS / NOT YET CLOSED

Remaining reviewer-visible blockers:

- structured logging baseline (F-013);
- human domain-contract approval, if still pending;
- CI must remain honestly marked as locally green but not run remotely.

Once these are addressed, M0 can be formally reviewed for closure.

---

## M1 — MT5 read-only gateway

**Verdict:** GO WHEN DEPENDENCIES AVAILABLE

Still depends on:

- broker selection;
- demo account;
- MT5 server;
- Windows x86-64 host.

---

## M2 — PostgreSQL persistence

**Verdict:** GO NOW

Still the highest-value engineering milestone after the small M0 logging gap is closed.

Recommended first persistence objects:

```text
event journal
decision capsules
config versions
incidents
safety-state events
account snapshots
instrument specs
reconciliation results
```

---

## M3 — Replay/backtest

**Verdict:** CONTINUE FOR CORRECTNESS ONLY

The implementer's own warning remains correct:

```text
intrabar ordering = assumption
swap = missing
commission = missing
```

Therefore no strategy-performance number is decision-grade evidence.

---

## M4 — Risk Engine

**Verdict:** CONTINUE / NO PROMOTION

No change.

---

## M5 — Paper execution

**Verdict:** NO-GO

Still requires:

```text
real MT5 Gateway
PostgreSQL persistence
broker reconciliation
execution-time revalidation implementation
authoritative safety-state implementation
MT5 validation of operator controls
demo/live account guard
real order lifecycle tests
```

---

## M6 — Trading Agent

**Verdict:** FEATURE FREEZE MAINTAINED

No change.

---

## M7 — Evaluator / Supervisor

**Verdict:** SAFETY WORK ONLY

No LLM expansion.

---

## P2 — Autonomous demo campaign

**Verdict:** NO-GO

No change.

---

# 6. Required next action order

```text
1. Implement M0 structured logging baseline (F-013)
2. Update tracker/status with evidence
3. Implement M2 PostgreSQL persistence
4. Implement ADR-002 authority semantics in persistence/recovery
5. Select broker + demo account
6. Provision Windows MT5 host
7. Build read-only MT5 Gateway
8. Build reconciliation against real broker state
9. Implement ADR-001 final risk revalidation with M5
10. Validate cancel/flatten against MT5 demo
11. Perform feedback.2.0 review before first order_send
```

---

# 7. Positive reviewer note

The most important positive signal in this review is not the number of closed findings.

It is this:

> The implementation team independently discovered that logging was required by the specification but had not actually been delivered.

That shows the review process is becoming self-correcting rather than purely reactive.

Continue using this method:

```text
specification
→ implemented evidence
→ test evidence
→ gate comparison
→ deviation/finding
```

rather than relying on memory or “it feels finished”.

---

# 8. Next review version

The next normal follow-up is:

```text
feedback.1.3.md
```

Use it when:

- F-013 logging is implemented; and/or
- PostgreSQL M2 implementation is ready for review.

However, the review immediately before the first actual MT5 `order_send` must be:

```text
feedback.2.0.md
```

That major review will require actual code/integration evidence and should not rely on tracker claims alone.

---

# 9. Final reviewer statement

Review 1.2 accepts the majority of the implementer's closures.

The project remains disciplined:

```text
M5 = NO-GO
P2 = NO-GO
strategy = frozen
persistence = next
```

The newly discovered logging gap should be fixed, but it is not a fundamental architecture problem.

The project should now complete the small remaining M0 observability baseline and then move decisively into M2 persistence.
