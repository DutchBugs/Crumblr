# feedback.1.1.md — Follow-up Architecture & Safety Review

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.1  
**Review date:** 2026-08-17  
**Reviewed artifacts:** `review/FEEDBACK.md`, `status.md`, `review/adr/ADR-001-execution-time-risk-revalidation.md`  
**Previous review:** `feedback.1.0.md`  
**Overall verdict:** **GO WITH CONDITIONS**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review evaluates the supplied documentation and stated test evidence. Source-code implementation was not independently inspected in this review.

---

# 1. Executive review

The response to `feedback.1.0.md` is strong. The implementation team has adopted the core reviewer direction:

- implementation maturity is separated from gate qualification;
- permissive supervisor placeholders have been replaced conceptually by explicit safety states;
- durable safety-state handling has been introduced;
- `ict_v1` is feature-frozen;
- operator controls have been separated;
- execution-time risk revalidation has been accepted in ADR-001;
- M5/P2 remains explicitly blocked.

This is the correct direction.

The next review priority is no longer strategy design. It is **state truthfulness and persistence**:

```text
PostgreSQL event persistence
→ authoritative safety state
→ MT5 integration
→ broker reconciliation
→ M5 execution controls
```

There are, however, a few inconsistencies in the documentation that need correction before this review is considered fully closed.

---

# 2. Re-evaluation of findings F-001 through F-008

## F-001 — Milestone status conflates maturity with qualification

**Previous severity:** HIGH  
**Review 1.1 status:** **CLOSED**

The new maturity ladder clearly separates:

```text
SPECIFIED
IMPLEMENTED
UNIT-TESTED
REPLAY-TESTED
MT5-INTEGRATED
PAPER-VALIDATED
SHADOW-VALIDATED
```

from human gate qualification.

This directly addresses the finding.

**Reviewer note:** retain this structure. Do not reintroduce percentage-complete reporting.

---

## F-002 — Supervisor receives hard-coded safe state

**Previous severity:** CRITICAL  
**Review 1.1 status:** **CLOSED BASED ON DOCUMENTED EVIDENCE**

The documented design now uses explicit safety states rather than safe booleans:

```text
MATCHED / MISMATCHED / UNKNOWN
CLEAR / ACTIVE / UNKNOWN
```

The supplied status also states that UNKNOWN reconciliation halts and UNKNOWN incident state vetoes, and that these safety checks sit above the optional policy-enable switch.

That is the correct fail-closed behavior.

**Important:** `status.md` still contains stale open issues describing the old behavior. See new finding F-009.

---

## F-003 — Kill-switch state does not survive restart

**Previous severity:** HIGH  
**Review 1.1 status:** **IN PROGRESS — DESIGN/CODE CLAIM ACCEPTED, RESTART EVIDENCE STILL NEEDED**

The implementation is documented as:

- `SafetyStateStore` port;
- durable atomically-written file implementation;
- startup begins disabled;
- missing/corrupt/wrong-schema state becomes UNKNOWN;
- persistence is attempted before in-memory state changes.

This is a strong design.

However, the supplied evidence says that tests cover fail-closed safety state, but does not explicitly demonstrate the original acceptance condition:

```text
HALT
→ process stops
→ a new process starts
→ HALT remains active
```

nor the equivalent machine-restart scenario.

The finding therefore remains **IN PROGRESS** until explicit restart/recovery evidence is recorded.

### Required evidence

At minimum add an integration test or documented test sequence proving:

1. process A writes HALT;
2. process A exits;
3. process B starts from persisted state;
4. process B starts with new orders disabled;
5. only explicit operator reset can return to RUNNING.

Also test:

```text
missing state file
corrupt state file
truncated state file
unsupported schema version
unwritable state destination
```

All must fail closed.

---

## F-004 — Strategy development ahead of evidence

**Previous severity:** MEDIUM / HIGH  
**Review 1.1 status:** **CLOSED**

Feature freeze is explicitly recorded.

Current classification remains appropriate:

```text
baseline_v1 = infrastructure benchmark
ict_v1      = research challenger
champion    = none
```

Do not reopen strategy feature work before real EUR/USD evidence exists.

---

## F-005 — Inconsistent maturity/progress reporting

**Previous severity:** MEDIUM  
**Review 1.1 status:** **CLOSED, WITH DOCUMENT CLEANUP REQUIRED UNDER F-009**

Progress percentages are removed and risk capabilities now show separate maturity columns.

This is a material improvement.

Remaining stale issue entries are handled separately in F-009.

---

## F-006 — Local Git process deviation

**Previous severity:** LOW / PROCESS  
**Review 1.1 status:** **ANSWERED / CLOSED**

The implementation team correctly challenged part of the previous review premise.

The adopted decision is sound:

```text
Local Git:   ALLOWED
Remote Git:  DEFERRED until collaboration
```

No further action required.

**Reviewer correction:** the previous review overstated the conflict around `git init`. The controlling concern is avoiding an unintended remote/collaboration workflow, not the existence of a local repository.

---

## F-007 — No execution-time risk revalidation

**Previous severity:** HIGH  
**Review 1.1 status:** **CLOSED AT DESIGN LEVEL — IMPLEMENTATION REMAINS M5 PREREQUISITE**

ADR-001 is accepted and captures the intended architecture:

```text
Intent risk check
→ Supervisor
→ Final execution-time risk check
→ order_check
→ order_send
```

The ADR correctly states:

- final risk may only be equal or more restrictive;
- supervisor cannot override a deterministic block;
- expiry is rechecked;
- both risk decisions are persisted;
- approved volume is never increased.

This closes the architecture finding.

Two implementation clarifications are required before M5; see F-011.

---

## F-008 — Missing FLATTEN / cancel-pending control path

**Previous severity:** HIGH  
**Review 1.1 status:** **CLOSED BASED ON DOCUMENTED EVIDENCE**

The status reports separate, separately-authorized controls and tests proving they are decoupled:

```text
HALT NEW ORDERS
CANCEL PENDING
FLATTEN POSITIONS
```

That is the desired design.

This does **not** mean the controls are MT5-validated. They remain unqualified until M1/M5 integration proves their real broker behavior.

`status.md` still contains stale entries claiming the controls do not exist. See F-009.

---

# 3. New findings

## F-009 — `status.md` contains stale blockers that contradict the review tracker

**Severity:** MEDIUM  
**Status:** OPEN

The top-level review tracker says F-002 and F-008 are closed, and the update log says the new safety states and operator controls are implemented.

However, `status.md` still lists:

- `APP-001`: no FLATTEN POSITIONS control exists;
- `APP-002`: supervisor still receives hard-coded safe values;
- `EV-001`: active-incident and reconciliation checks are inert;
- current risks: operator cannot close a position once open.

These entries describe superseded behavior.

That makes `status.md` internally contradictory and weakens its value as the project's current truth source.

### Required change

Update each stale record rather than deleting history.

Example:

```text
APP-001 | CLOSED | Operator controls implemented; MT5 validation still pending
APP-002 | CLOSED | Explicit UNKNOWN safety state implemented
EV-001  | SPLIT  | incident/reconciliation fixed; remaining calibration items tracked separately
```

For historical context, retain the original resolution text or link to the update log.

### Acceptance

- current-state tables reflect the current implementation claim;
- historical issues remain auditable;
- no issue is simultaneously OPEN and CLOSED in different sections without explanation.

---

## F-010 — M0 exit criteria contain M1 dependencies

**Severity:** MEDIUM  
**Status:** OPEN

`status.md` currently holds M0 open partly because:

```text
MT5 demo account not selected
broker/server not documented
```

Those are legitimate project dependencies, but they belong to M1 MT5 integration rather than the engineering-baseline acceptance in `build.md`.

This matters because `build.md` is declared the specification and `status.md` should not silently redefine its gates.

### Required change

Move or label broker/demo selection as:

```text
M1 dependency
```

rather than an M0 exit criterion, unless a deviation/decision explicitly changes the build specification.

M0 may still remain open for other valid reasons, especially CI not having executed on a real runner and pending human contract approval.

### Acceptance

- gate criteria in `status.md` map directly to `build.md`;
- additional criteria are explicitly marked as local/project policy rather than original spec.

---

## F-011 — ADR-001 must define the price basis of final monetary-risk validation

**Severity:** HIGH DESIGN CLARIFICATION  
**Status:** OPEN  
**Required before:** M5 implementation

ADR-001 correctly requires fresh market/account/position state and fixes the approved volume.

One point should be made explicit before implementation:

> The final risk check must recompute the **actual current monetary exposure** using the current executable side of the market, not merely re-run validation against the stale reference/approved entry price.

For a market BUY, for example, risk-to-stop should be evaluated from the current executable ask or a conservative execution-price envelope. For a market SELL, use the corresponding bid side.

The check should consider:

```text
current bid/ask
current spread
approved stop
fixed approved volume
current equity
current portfolio exposure
configured max slippage/deviation
intent expiry
current symbol specification
```

The final gate must block if price movement has increased monetary risk beyond policy even though volume is unchanged.

### Required ADR addition

Clarify:

```text
Sizing is not recomputed.
Monetary risk is recomputed using current execution-state assumptions.
Volume may stay unchanged or the order is blocked; volume may never increase.
```

### Acceptance

Tests include:

1. approved BUY where ask moves away from stop and makes fixed volume too risky → BLOCK;
2. approved SELL equivalent → BLOCK;
3. favorable move that lowers risk → original volume retained, never increased;
4. widened spread causes block;
5. symbol-spec change causes block/reconciliation requirement.

---

## F-012 — Safety-state source of truth must be defined before PostgreSQL/M5 convergence

**Severity:** MEDIUM / HIGH  
**Status:** OPEN  
**Required before:** M5

The current durable safety state uses a local file store, while M2 is about to introduce PostgreSQL for events, incidents and other system state.

Before execution exists, define which store is authoritative for:

```text
HALT state
operator reset
incidents
reconciliation state
startup recovery
```

Otherwise startup may face conflicting truths, e.g.:

```text
file says RUNNING
database says last event = HALTED
```

### Required design

Define a recovery precedence rule.

Recommended principle:

```text
Any disagreement in safety-critical state
→ UNKNOWN / HALTED
→ reconcile before enabling orders
```

Do not silently prefer the more permissive store.

The local durable file can remain a valid safety latch, but its relationship to the append-only event journal must be explicit.

---

# 4. Gate decisions — review 1.1

## M0 — Engineering baseline

**Verdict:** **GO WITH CONDITIONS**

Remaining reviewer items:

- human domain-contract approval;
- CI status must remain honestly marked as not executed remotely;
- fix M0/M1 gate-criteria drift under F-010.

Broker/demo selection should be tracked as an M1 dependency, not an implicit M0 engineering failure.

---

## M1 — MT5 read-only gateway

**Verdict:** **GO WHEN HUMAN/HOST DEPENDENCIES ARE AVAILABLE**

Still requires:

- broker selection;
- demo account;
- server;
- Windows x86-64 host.

No reason to expand trading-strategy scope while waiting.

---

## M2 — PostgreSQL / event persistence

**Verdict:** **GO NOW — HIGHEST ENGINEERING PRIORITY**

Build next:

```text
event journal
decision capsules
config versions
instrument specs
incidents
reconciliation records
account snapshots
safety-state events
```

Also resolve F-012 while deciding persistence authority.

---

## M3 — Replay/backtest

**Verdict:** **CONTINUE FOR CORRECTNESS ONLY**

Do not interpret performance until the fill/cost model has:

```text
commission
swap/financing
credible intrabar/fill assumptions
real spread data
```

The implementation's own warning on this point is correct.

---

## M4 — Risk engine

**Verdict:** **CONTINUE / NO PROMOTION**

Replay-tested is appropriate.

Add F-011 requirements to the M5 implementation plan.

---

## M5 — Paper execution

**Verdict:** **NO-GO**

Still blocked on:

- M1 real MT5 integration;
- M2 persistence;
- broker reconciliation;
- restart evidence for durable HALT;
- final execution-time risk implementation;
- execution-price risk semantics from F-011;
- MT5 validation of cancel/flatten controls;
- authoritative safety-state/recovery semantics.

---

## M6 — Trading Agent

**Verdict:** **FEATURE FREEZE MAINTAINED**

No additional trading concepts.

---

## M7 — Evaluator / Supervisor

**Verdict:** **SAFETY WORK ONLY**

Allowed next work:

- connect real incident state from persistence;
- connect reconciliation state;
- post-trade scorecard;
- later statistical monitoring.

No LLM execution authority.

---

## P2 — Autonomous demo campaign

**Verdict:** **NO-GO**

No change.

---

# 5. Required next action order

```text
1. Clean stale status records (F-009)
2. Align M0/M1 gate criteria (F-010)
3. Implement PostgreSQL event persistence (M2)
4. Define safety-state source-of-truth/recovery semantics (F-012)
5. Add explicit restart/recovery test evidence for F-003
6. Amend ADR-001 with current execution-price monetary-risk semantics (F-011)
7. Select broker + demo account
8. Provision Windows MT5 host
9. Build read-only MT5 Gateway
10. Implement reconciliation
11. Implement final risk revalidation with M5
12. Validate operator controls against real MT5 demo state
13. Re-review before first order_send
```

---

# 6. Reviewer response to implementer notes

The correction on F-006 is accepted. The previous reviewer statement was too categorical about `git init`.

The implementer's warning that the fill model is still the softest part of the evidence chain is also accepted. Until commission, swap and intrabar fill assumptions are credible, strategy P&L remains engineering output rather than trading evidence.

The deterministic replay requirement remains valuable:

```text
same inputs + same code/config
→ byte-identical decision/event output
```

This should remain a regression gate.

---

# 7. Conditions for feedback.1.2.md

Create the next review as:

```text
feedback.1.2.md
```

when at least one of these is true:

- PostgreSQL persistence is implemented;
- F-009 through F-012 are addressed;
- broker/demo account and Windows MT5 host are ready;
- read-only M1 integration is available for review.

The first review before any real/demo `order_send` should receive a major bump:

```text
feedback.2.0.md
```

That review must treat actual MT5/broker evidence as mandatory rather than documentation/test claims.

---

# 8. Final reviewer statement

The response to review 1.0 materially improved the system.

The project is now making the right transition:

```text
from:
"we have safe-looking algorithms"

to:
"we have explicit, durable, auditable safety state"
```

The next transition must be:

```text
from simulated truth
to broker truth
```

Do not spend the next engineering cycle making the Trading Agent more sophisticated. Spend it making system state authoritative, persistent and recoverable.
