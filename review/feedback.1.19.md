# feedback.1.19.md — Audit/Idempotence Progress Accepted; Two Execution-Grade Tightenings

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.19  
**Date:** 2026-08-26  
**Reviewed artifact:** `status(20260826-121408).md`  
**Previous review:** `feedback.1.18.md`  
**Overall verdict:** **GO — MATERIAL TECHNICAL PROGRESS; F-051 IS NOW THE MAIN REAL-WORLD CHECKPOINT**  
**M0 verdict:** **OPEN — CI EVIDENCE + ACTUAL DOMAIN-CONTRACT REVIEW**  
**M1 verdict:** **PASSED**  
**M2 verdict:** **PASSED**  
**F-053:** **IMPLEMENTED; BASELINE AUTHORITY MUST BE HARDENED BEFORE M5**  
**F-054:** **REOPENED FOR EXECUTION-GRADE FAILURE SEMANTICS**  
**D-031:** **CLOSED IN IMPLEMENTATION; REAL-SHADOW ROW EVIDENCE PENDING F-051**  
**M5 / first DEMO order:** **NO-GO**  
**Scope note:** This review is based on the supplied status document and its reported evidence. Source code, CI output, `review/domain_contracts.md`, and real-terminal evidence for the newly added F-053/F-054/D-031 paths were not independently inspected.

---

# 1. Executive review

This is the technical progress review 1.18 asked for.

Reported this pass:

```text
F-033 current-state contradictions corrected
F-053 semantic InstrumentSpec reconciliation built
F-054 decision-window state persisted across restart
D-031 full feature-value persistence built
863 tests passed / 3 explained skips / 0 failures
mypy clean
ruff clean
replay determinism reproven
```

The live/shadow architecture is now much closer to evidence-quality operation.

The primary remaining external checkpoint is still:

```text
F-051 real Pepperstone / Windows run
```

However, two details must be tightened before an execution service is allowed to consume live decisions:

1. durable decision idempotence must **fail closed when its record is unreadable**;
2. instrument reconciliation needs an **approved/pinned baseline**, not an automatically self-authorizing first observation.

Neither issue blocks the next real shadow run.

Both block M5 execution readiness.

---

# 2. F-033 — substantially closed, but top summary still needs one final sync

**Status:** PARTLY OPEN / NON-BLOCKING

The four contradictions called out in review 1.18 were corrected.

Accepted:

```text
pending-order support now shown as built
reconciliation now shown as built
LiveDecisionOrchestrator now acknowledged in Risk section
InstrumentSpec producer now acknowledged
real Phase-B reconnect correctly shown as proven
```

There are still small current-summary drifts:

```text
top component table: Platform/Application = REPLAY-TESTED
later current platform section: MT5-INTEGRATED

overall health: 836 passed / 3 skipped
current repository checklist: 863 passed / 3 skipped
```

Fix these while processing this review.

Do not spend a separate development session on them.

---

# 3. F-053 — semantic instrument reconciliation

**Status:** IMPLEMENTED / ACCEPTED FOR SHADOW

The implementation now compares:

```text
earliest durable observed InstrumentSpec
vs
latest durable observed InstrumentSpec
```

through the stable semantic `spec_version`.

Correctly excluded from semantic change identity:

```text
captured_at_utc
dynamic tick_value
```

Correct fail-closed mapping:

```text
missing/unreadable spec → UNKNOWN
changed semantic spec   → MISMATCHED
```

This is a meaningful improvement.

---

# 4. New finding F-055 — instrument baseline must be authorized, not merely first-observed

**Severity:** HIGH BEFORE M5  
**Status:** OPEN

The current F-053 design treats:

```text
the first InstrumentSpec row in the observation database
```

as the expected baseline.

That detects **drift after the first row**.

It does not establish that the first row itself is the approved expected broker contract.

This distinction matters especially because Crumblr already has workflows that:

```text
create/reset/migrate a soak database
```

A fresh database could observe a materially changed broker specification and then call that changed specification its baseline.

Reconciliation would subsequently say:

```text
MATCHED
```

because the system compared the broker to its own new first observation.

That is trust-on-first-use, not full reconciliation authority.

## Required before M5

Use discovery first, then explicit pinning/authorization.

Recommended sequence:

```text
F-051 observes real Pepperstone EURUSD spec
        ↓
compare with already-known M1 evidence
        ↓
explicitly approve/pin semantic spec_version
        ↓
future observed specs reconcile against that pinned baseline
```

The baseline can live in a versioned configuration/authority record or a dedicated immutable baseline store.

It must not silently change because:

```text
database recreated
soak database reset
new observation happens to be first
```

Changing the approved baseline later must be an explicit reviewed event.

### Important

This does **not** mean hard-code EURUSD broker fields before discovery.

The model remains:

```text
discover
→ verify
→ pin
→ reconcile future observations
```

---

# 5. F-054 — durable decision idempotence

**Status:** REOPENED FOR EXECUTION-GRADE FAILURE SEMANTICS

The persistence work itself is good.

Accepted:

```text
decision state survives restart
PostgreSQL-backed store exists
key includes canonical symbol + strategy + config
last handled window is restored
seen decision hashes are restored
state is written while processing the window
real PostgreSQL restart simulation exists
```

This moves F-054 materially forward.

But the latest status explicitly says:

```text
an unreadable decision-window record
→ collapses to "nothing recorded"
```

and that this should be revisited once execution exists.

That means the review 1.17 invariant is not yet fully satisfied for execution.

If this state is corrupted or unreadable after a trade-capable service exists:

```text
existing decision state becomes unreadable
        ↓
system treats it as empty
        ↓
same M5 window may be considered new
        ↓
second executable proposal becomes possible
```

That is exactly the failure class F-054 exists to prevent.

## Required before execution service attachment

Distinguish:

```text
genuinely no prior record
```

from:

```text
record exists but is unreadable/corrupt
store unavailable
schema/version invalid
state inconsistent
```

Required fail-safe:

```text
unreadable / unavailable / inconsistent
→ execution unavailable
→ HALT or explicit UNKNOWN/fail-closed state
```

A genuinely absent state may initialize only under a defined bootstrap condition.

### Defense in depth

Later `order_request_id` idempotence remains required too.

Decision-window durability and order-request idempotence protect different boundaries and should both exist.

---

# 6. D-031 — feature-value persistence

**Status:** CLOSED IN IMPLEMENTATION

This is a strong improvement.

The platform now reports a durable:

```text
feature_snapshots
```

store containing the full strategy-specific `FeatureEvidence` payload, including windows that result in:

```text
NO_TRADE
Risk BLOCK
HALT
```

and it is wired into both replay and live decision paths.

That gives the future Trader/Trainer system a much better audit basis than a hash alone.

## F-051 acceptance addition

During the real live-shadow checkpoint, inspect at least one real decision window and prove:

```text
real M5 window
→ feature snapshot row exists
→ SignalGenerated references the same logical evidence
→ Risk/Supervisor chain can be traced from it
```

The reviewer does not require a separate feature-store project.

---

# 7. Quality evidence

The reported local gate is healthy:

```text
ruff                   clean
ruff format            clean
mypy                   clean, 125 source files
pytest                  863 passed
pytest skips            3 explained
failures                0
replay determinism      identical
```

The new tests reportedly cover:

```text
InstrumentSpec reconciliation
InstrumentSpecStore earliest()
decision-window persistence
PostgreSQL restart behavior
feature persistence
```

Accepted as implementation evidence.

It is not a substitute for F-051 or CI.

---

# 8. F-051 — now the decisive live-shadow checkpoint

**Status:** OPEN  
**Priority:** HIGHEST

The next real Windows/MT5 run should now exercise the complete current path rather than individual fragments.

Required evidence:

```text
1. Current code connects to PepperstoneUK-Demo.
2. Account guard passes.
3. Current InstrumentSpec is persisted.
4. Broker account snapshot is persisted.
5. Balance/equity/profit/margin values are sensible.
6. Position query = COMPLETE.
7. Pending-order query = COMPLETE.
8. Account is flat: positions=0, pending=0.
9. Approved/pinned spec baseline is established for F-055.
10. Reconciliation = MATCHED including semantic InstrumentSpec.
11. At least one newly closed real M5 bar is persisted.
12. LiveDecisionOrchestrator evaluates it.
13. Full FeatureEvidence is persisted.
14. Trader emits NO_TRADE or intent naturally.
15. Intent-time Risk runs on real context if an intent exists.
16. Supervisor receives the real reconciliation result.
17. Decision chain is durably journalled.
18. Execution remains structurally unavailable.
```

A natural `NO_TRADE` remains valid.

Do not force a trade.

---

# 9. Optional but valuable F-051 restart check

Because F-054 changed this pass, add one controlled restart during/after shadow decisioning if practical:

```text
handle closed M5 window
→ persist decision-window state
→ restart decision process
→ rerun
→ same window is not evaluated as a second independent decision
```

This is not a substitute for fixing F-054's corrupt-store semantics.

It proves the normal restart path against the real persistence environment.

---

# 10. M0 — still two evidence actions

M0 still needs:

```text
actual CI result
actual human/reviewer domain-contract approval
```

The status says the domain-contract package exists, but it still was not included in this review package.

Supply:

```text
review/domain_contracts.md
```

unchanged next time.

CI evidence remains:

```text
commit SHA
Linux result
Windows result
PostgreSQL tests
gitleaks
unexpected skips
overall result
```

No additional M0 architecture work is requested.

---

# 11. Dashboard

After F-051 passes, the existing read-only dashboard may finally receive the operational panels already planned:

```text
Balance
Equity
Open P/L
Free margin
Positions
Pending orders
Broker-state age/completeness
Reconciliation
Live/shadow Trader → Risk → Supervisor
```

Still:

```text
no direct MT5 access
no execution controls
no visual redesign cycle
```

---

# 12. Execution engineering

The project is nearly at the point where Phase 4 becomes the dominant task.

The reviewer allows implementation preparation in parallel, but no execution service may become order-capable until the F-054 fail-closed fix is complete.

Phase 4 remains:

```text
separate execution-capable MT5 adapter
ApprovedOrder
ExecutionResult
order_check
durable order_request_id
FINAL execution-time Risk
fresh synchronous broker-state capture
reconciliation MATCHED
current executable ask/bid
current spread
current equity/exposure
automatic intraday flatten
execution-result persistence
post-execution reconciliation
```

No `order_send` before `feedback.2.0`.

---

# 13. Owner policy is now becoming a real blocker

Before `feedback.2.0`, the owner must approve:

```text
risk per trade
max daily loss
max drawdown
last-entry cutoff
mandatory flatten deadline
HALT-reset authority
```

Do not let provisional values become policy through continued development.

The developer can continue F-051 and execution plumbing while these are prepared.

---

# 14. Current path to first autonomous DEMO canary

```text
M1 real read/reconnect                         ✅
M2 persistence                                ✅
broker account/position/order snapshots       ✅ code
BrokerStateHealth                             ✅
reconciliation                               ✅ code
live shadow decision orchestrator             ✅ code
feature-value persistence                     ✅ code
semantic spec comparison                      ✅ code
durable normal restart decision state         ✅ code

F-054 fail-closed corrupt recovery             ⏳
F-055 pinned spec authority                    ⏳
F-051 real current-code validation             ⏳
CI                                             ⏳
domain-contract approval                       ⏳

execution adapter                              ❌
order_check                                    ❌
FINAL Risk                                     ❌
auto flatten                                   ❌
owner risk policy                              ❌
feedback.2.0                                   ❌
first DEMO canary                              🚫
```

This is now a short and concrete list.

---

# 15. Gate decisions

## M0
**OPEN — EVIDENCE ONLY**

## M1
**PASSED**

## M2
**PASSED**

## F-053
**IMPLEMENTED / SHADOW-READY**

M5 requires F-055 baseline authority.

## F-054
**IMPLEMENTED BUT REOPENED FOR FAIL-CLOSED RECOVERY**

## D-031
**CLOSED IN IMPLEMENTATION**

Real evidence follows F-051.

## Live-shadow real run
**GO NOW**

## Execution engineering
**GO TO PREPARE / BUILD NON-SENDING COMPONENTS**

## First `order_send`
**NO-GO**

Requires `feedback.2.0 GO`.

---

# 16. Required next action order

```text
1. Process feedback.1.19.md.
2. Fix F-054 unreadable/unavailable-state handling to fail closed.
3. Add F-055 approved/pinned InstrumentSpec baseline authority.
4. Sync the two remaining F-033 top-summary lines while touching status.md.
5. Run F-051 against the real Pepperstone terminal.
6. Prove flat reconciliation=MATCHED including the pinned spec.
7. Run one real closed M5 through Trader → Risk → Supervisor.
8. Verify real FeatureEvidence persistence.
9. Verify normal restart deduplication if practical.
10. Retrieve and record GitHub Actions CI.
11. Supply review/domain_contracts.md unchanged for actual review.
12. Close M0.
13. Add operational broker/reconciliation/live-decision panels to dashboard.
14. Obtain owner risk/intraday/HALT-reset decisions.
15. Build Phase-4 execution adapter/order_check/FINAL Risk/flatten.
16. Prepare feedback.2.0.
17. No order_send before feedback.2.0 GO.
```

---

# 17. Next review

Next normal review:

```text
feedback.1.20.md
```

Preferred trigger:

```text
F-054 hardened
+ F-055 pinned baseline
+ F-051 real Pepperstone validation
+ real shadow decision
+ CI and/or domain-contract package
```

If the real F-051 run exposes an integration defect, review immediately rather than waiting for the whole bundle.

---

# 18. Final reviewer statement

This update is a meaningful improvement.

Crumblr can now retain much more of the evidence needed by the future Trader, Supervisor and Trainer:

```text
market
broker state
instrument state
feature values
decision state
risk
supervisor result
```

The remaining idempotence issue is narrow but important:

> persistence is not execution-safe if corruption is interpreted as absence.

And reconciliation is strongest when its expected baseline has authority:

> discover first, then pin; do not silently let “first row in a fresh database” redefine what normal means.

Fix those two points, run the current stack once against Pepperstone, and the project can move decisively into execution engineering.
