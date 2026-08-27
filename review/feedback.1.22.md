# feedback.1.22.md — Phase 4 Source Review: Architecture Strong, Audit/Final-Context Fixes Required

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.22  
**Date:** 2026-08-27  
**Reviewed artifacts:** `status(20260827-081233).md` + `crumblr_phase4_review.zip`  
**Previous review:** `feedback.1.21.md`  
**Overall verdict:** **GO WITH FIXES — PHASE 4 IS SUBSTANTIALLY BUILT, BUT NOT YET FORMALLY PASSED**  
**M0 verdict:** **OPEN — hosted CI confirmation + current domain-contract approval**  
**M1 verdict:** **PASSED**  
**M2 verdict:** **PASSED**  
**F-051 part 1:** **PASSED**  
**F-051 part 2:** **OPEN / real-bar evidence may complete independently**  
**Phase 4 non-sending implementation:** **SOURCE-REVIEWED / CONDITIONALLY ACCEPTED**  
**M5 / first DEMO order:** **NO-GO**  
**`order_send`:** **PROHIBITED — unchanged**

---

# 1. Executive review

This is the first Phase-4 review based on the actual implementation rather than `status.md` alone.

The bundle confirms that the main architecture requested in
`PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` was taken seriously.

Verified directly in source:

```text
separate OrderCheckMt5Gateway
order_send/cancel/flatten structurally disabled
separate ExecutionOrchestrator
execution eligibility
preflight gate separated from future SubmissionGate
FINAL Risk uses same-volume-or-BLOCK
ApprovedOrder constructed only after FINAL Risk PASS
immutable execution_requests
append-only execution_events
atomic first-insert claim
old decisions blocked by activation watermark
real order_check adapter path
no order_send call site from ExecutionOrchestrator
```

The overall direction is correct.

The source review also found several execution-grade gaps that the fake-terminal
end-to-end test does not expose.

The most important is an audit-chain break around FINAL Risk.

Phase 4 should therefore receive **GO WITH FIXES**, not a clean PASS yet.

---

# 2. What is accepted

## 2.1 Separate execution tier — ACCEPTED

`application/execution.py` is a distinct process/orchestrator boundary.

The reviewed structure remains:

```text
LiveReader
    = observe/persist

LiveDecisionOrchestrator
    = decide

ExecutionOrchestrator
    = execution preflight
```

This is the correct separation.

No execution logic has been folded into the Trader or live-decision process.

---

## 2.2 `OrderCheckMt5Gateway` — ACCEPTED FOR PHASE 4

The new adapter is intentionally smaller than a real execution adapter.

It may:

```text
read broker state
call order_check
```

It unconditionally refuses:

```text
order_send
cancel_pending_orders
close_all_positions
```

There is no configuration flag inside those methods that can turn submission
on.

This is a strong structural control.

The explicit source grep also confirms that the Phase-4 code contains no
reachable `order_send` call from `ExecutionOrchestrator`.

---

## 2.3 FINAL Risk same-volume-or-BLOCK — ACCEPTED

`risk/policies.py::revalidate_fixed_volume_at_execution_time()` follows the
reviewed rule:

```text
approved intent-time volume
        ↓
same volume still safe?
   YES         NO
    ↓           ↓
same volume    BLOCK
```

It does not resize down and never increases volume.

It also reprices monetary risk using:

```text
BUY  → current ask
SELL → current bid
```

rather than the stale intent-time reference price.

The dedicated tests cover the important BUY/SELL, adverse move, favourable
move, spread, equity-drop, expiry and kill-switch cases.

The algorithm itself is accepted.

---

## 2.4 Eligibility / two-gate split — ACCEPTED IN PRINCIPLE

The code now distinguishes:

```text
ExecutionEligibility
ExecutionPreflightGate
SubmissionGate
```

The `SubmissionGate` remains an always-closed design stub.

Good.

The developer's additional claim about `activation_watermark` was independently
verified:

```text
config/base.yaml     → no activation_watermark
config/paper.yaml    → no activation_watermark
config.py            → no activation_watermark
```

`ExecutionOrchestrator.__init__` defaults it to `None`.

Therefore every current production/default construction is fail-closed unless
a caller explicitly injects a watermark.

No accidental config flip exists today.

---

## 2.5 Immutable request + append-only events — DIRECTION ACCEPTED

The first successful:

```text
INSERT ... ON CONFLICT DO NOTHING RETURNING
```

is the claim.

That fits Crumblr's append-only database permissions better than an UPDATE-based
lease.

A later claim with the same `order_request_id` but a different supplied
fingerprint raises instead of silently disappearing.

That is materially better than the original one-row
`ON CONFLICT DO NOTHING` proposal.

---

# 3. F-057 — FINAL Risk is not durably linked into the execution audit chain

**Severity:** CRITICAL BEFORE M5  
**Status:** OPEN

This is the main source-review finding.

ADR-001 explicitly requires:

```text
Both risk decisions are persisted.
```

The current implementation computes a distinct execution-time `RiskDecision`:

```python
final_risk = revalidate_fixed_volume_at_execution_time(...)
```

but on PASS:

```text
the FINAL RiskDecision is not persisted
there is no FINAL_RISK_PASSED execution event
ApprovedOrder.risk_decision_id points to prior_decision.decision_id
```

The actual construction currently uses:

```python
risk_decision_id=prior_decision.decision_id
```

not:

```python
final_risk.decision_id
```

The later `ORDER_CHECKED` event persists only the `OrderCheckCompleted`
response.

Therefore the durable record cannot currently answer:

> Which exact FINAL RiskDecision authorized this ApprovedOrder?

That is unacceptable once submission exists and incomplete even for an
evidence-quality preflight chain.

## Required

Do **not** mutate the already-sealed DecisionCapsule.

Instead extend the execution audit stream.

Recommended:

```text
FINAL_RISK_PASSED
payload:
    complete serialized final RiskDecision

FINAL_RISK_BLOCKED
payload:
    complete serialized final RiskDecision
```

Then make the execution-order contract explicitly link both risk stages.

Preferred contract:

```text
intent_risk_decision_id
final_risk_decision_id
supervisor_decision_id
```

If backwards compatibility makes that awkward, at minimum:

```text
ApprovedOrder.risk_decision_id
→ MUST identify FINAL Risk

and execution request/event data must separately retain
the original intent-time RiskDecision identity.
```

The exact `ApprovedOrder` supplied to `order_check` must also be reconstructible
from durable data.

### ADR update

ADR-001 currently says the DecisionCapsule records both decisions.

That is no longer compatible with the intentionally sealed-capsule architecture.

Correct the ADR to:

```text
intent-time Risk → sealed DecisionCapsule
FINAL Risk       → append-only execution audit
```

Both remain durable and linked without mutating a sealed record.

---

# 4. F-058 — FINAL execution context is not yet one coherent, current observation

**Severity:** HIGH BEFORE M5  
**Status:** OPEN

The reviewed Phase-4 plan required:

```text
fresh broker/market observation
→ persist that observation
→ reconcile that observation
→ FINAL Risk from that same execution context
```

The implementation currently does this:

```text
MT5 account read A
MT5 positions read A

then

capture_broker_state()
    → another account read B
    → another position read B
    → pending orders / terminal state
→ persist B
→ reconciliation uses B

then

FINAL Risk uses account/positions from A
```

This is already documented as D-047.

The source confirms it.

That means reconciliation and FINAL Risk can theoretically judge two different
broker states.

For a non-sending fake-terminal proof this is tolerable.

It is not acceptable for the final order-submission boundary.

## Also part of F-058: clock freshness

`run_once()` captures:

```python
now = self._clock()
```

once before iterating capsules.

That same `now` is then reused through:

```text
eligibility
live MT5 reads
reconciliation
session recovery
fresh tick request
FINAL Risk
ApprovedOrder.created_at_utc
```

ADR-001 explicitly requires intent expiry to be checked:

> against the clock at the final check.

A sufficiently slow broker call can therefore cross the intent expiry or
session boundary while FINAL Risk still evaluates using the earlier
`run_once()` timestamp.

In Phase 4 this can only cause an incorrect `order_check`.

At M5 the same behavior could authorize a now-expired order.

## Required

One execution attempt should establish a coherent object such as:

```text
ExecutionObservation
    observed_at_utc
    account
    positions
    pending orders
    terminal state
    InstrumentSpec
    latest tick
```

Persist that observation.

Reconcile that exact observation.

Build the FINAL Risk portfolio from that exact observation.

Then obtain a new:

```text
final_now = clock()
```

immediately before FINAL Risk and use that same final time for:

```text
intent expiry
trading-window check
market-data age
FINAL Risk decision timestamp
ApprovedOrder creation
```

The first DEMO `order_send` remains blocked until this is closed.

---

# 5. F-059 — execution-request fingerprint does not bind the approval chain

**Severity:** HIGH BEFORE M5  
**Status:** OPEN

The immutable execution request design correctly detects:

```text
same order_request_id
+ different fingerprint
→ conflict
```

But the orchestrator currently supplies:

```python
fingerprint=intent.decision_hash
```

and `order_request_id` itself is also derived only from that decision hash.

This proves that two different **TradeIntents** cannot share one request id
without detection.

It does **not** prove that two different approved execution requests cannot.

The same logical TradeIntent can theoretically be accompanied by different:

```text
intent-time approved volume
RiskDecision
SupervisorDecision
Supervisor policy version
approval evidence
```

while still having the same `intent.decision_hash`.

The current duplicate path compares only the intent fingerprint, then treats
the existing row as a harmless retry.

## Required

Keep the logical `order_request_id` stable if desired.

But the immutable request fingerprint must bind the whole pre-execution
approval chain.

For example, a deterministic fingerprint over:

```text
intent.decision_hash
intent-time RiskDecision content
SupervisorDecision content
capsule/provenance identity
strategy/risk/supervisor versions
```

Do not rely on random UUIDs if that would destroy deterministic retry
convergence.

After FINAL Risk passes, persist a second order/preparation fingerprint that
binds the actual:

```text
ApprovedOrder
FINAL RiskDecision
```

The invariant is:

> One `order_request_id` may never silently refer to two materially different
> approved orders.

Add a test where two capsules have the same intent decision hash but a
different approved volume or supervisor approval content.

Expected:

```text
fail closed / conflict
```

not:

```text
already claimed, harmless retry
```

---

# 6. F-060 — FINAL Risk still has one known live-control placeholder

**Severity:** HIGH BEFORE M5  
**Status:** OPEN

`ExecutionOrchestrator` currently builds:

```python
PortfolioState(
    ...
    orders_in_last_hour=0,
    seen_decision_hashes=frozenset(),
    ...
)
```

`seen_decision_hashes` is partly superseded at this boundary by the durable
execution-request claim, so using an empty set for the decision currently
being revalidated is understandable.

`orders_in_last_hour=0` is different.

It means:

```text
max_orders_per_hour
```

cannot fire at FINAL Risk from real execution history.

This was previously acceptable as D-046 while no execution path existed.

That exception expires before M5.

## Required before first order

Derive the current order/submission count from durable platform execution
history.

Do not derive it from MT5 alone.

The final Risk Engine must see the actual current value, not a hard-coded zero.

---

# 7. Activation watermark — safe today, authority not finished

**Status:** SAFE DEFAULT / M5 AUTHORITY STILL REQUIRED

The developer's note is correct:

```text
activation_watermark is constructor-only
default = None
not present in YAML/config model
```

That is a strong safe default today.

However `execution_eligibility.py` describes this value as:

```text
human-set
durably persisted
```

and it currently is neither persisted nor versioned by Crumblr.

Therefore:

```text
current Phase-4 default = ACCEPTED as fail-closed
future execution enabling = NOT YET ACCEPTED
```

Before a real SubmissionGate can open, execution activation needs a durable,
reviewable source of authority.

It may be:

```text
versioned config
append-only operator-approval record
dedicated execution-activation authority store
```

but it must survive restart and be auditable.

Do not turn this into a casual CLI timestamp.

No separate finding number is required yet because it belongs directly to the
still-unbuilt F-049 SubmissionGate/feedback.2.0 gate.

---

# 8. Domain-contract review — not closable yet

**M0 contract condition:** OPEN

The supplied `review/domain_contracts.md` was indeed delivered unchanged as
requested.

That was useful because it reveals its age.

It describes the code as of:

```text
commit f67f341
2026-08-25
```

and still states:

```text
ApprovedOrder is not constructed anywhere
ExecutionResult is not constructed anywhere
ReadOnlyMt5Gateway is the only real MT5 adapter
```

Those statements were true when the package was created.

They are no longer current after Phase 4.

This is not a criticism of the developer for supplying it unchanged — that is
exactly what the reviewer requested.

It does mean the file cannot now serve as current M0 approval evidence.

More importantly, the current `ApprovedOrder` contract itself does not yet
carry the FINAL Risk linkage required by F-057.

Therefore approve the contract package **after** F-057 is corrected and the
package is regenerated/updated against current source.

---

# 9. Additional source bundle required to complete review 1.22 follow-up

The supplied bundle was good.

To close the remaining verification gap without asking for the whole
repository, provide one small supplementary ZIP containing:

```text
src/crumblr/application/broker_state.py
src/crumblr/application/reconciliation.py

src/crumblr/persistence/broker_state.py
src/crumblr/persistence/instrument_specs.py
src/crumblr/persistence/risk_session.py
src/crumblr/persistence/journal.py

src/crumblr/mt5_gateway/readonly.py

src/crumblr/risk/session.py
src/crumblr/risk/kill_switch.py

src/crumblr/domain/money.py
src/crumblr/domain/timeutils.py
src/crumblr/domain/events.py

src/crumblr/evaluator/pretrade.py
src/crumblr/application/recording.py

tests/integration/test_migrations.py
```

Why these are needed:

```text
broker_state + reconciliation
→ verify F-058's exact observation semantics

risk session + kill switch
→ verify FINAL Risk recovery/authority

money + timeutils
→ finish actual domain-contract Decimal/UTC review

journal + recording
→ verify sealed capsule / append-only execution boundary

evaluator/pretrade
→ verify Supervisor ownership/order in the current contracts

domain/events
→ verify OrderCheckCompleted persistence shape

migration test
→ verify the migration-equivalence claim directly
```

No other repository files are currently requested.

---

# 10. CI

M0 still needs the hosted CI rerun result.

The Phase-4 local source evidence is strong:

```text
931 passed
3 skipped
ruff clean
mypy clean
```

But hosted CI is a separate gate.

If the new GitHub Actions run is already green, supply:

```text
commit SHA
Linux result
Windows result
PostgreSQL-backed tests
gitleaks
skip count
overall result
```

A screenshot or copied Actions summary is sufficient if no API/CLI access is
available.

---

# 11. F-051 part 2

This review does not block the already-running real-data work.

F-051 part 2 may finish in parallel.

Once sufficient real bars exist:

```text
real M5
→ real Trader decision
→ intent Risk
→ reconciliation
→ Supervisor
→ durable feature/decision evidence
```

is still useful and independent of the Phase-4 fixes above.

Do not force a BUY/SELL.

`NO_TRADE` remains valid evidence.

---

# 12. What the developer may do now

GO:

```text
fix F-057
fix F-058
fix F-059
fix F-060
provide the small source supplement
finish F-051 part 2
retrieve hosted CI evidence
```

Also safe to continue design work on:

```text
automatic flatten submission
submission ambiguous-result recovery
post-execution reconciliation
real SubmissionGate
```

but keep all submission code structurally disabled.

---

# 13. What must NOT happen yet

Still prohibited:

```text
enable terminal AlgoTrading for execution testing
build a permissive submission flag
call order_send
treat provisional risk values as approved policy
start the autonomous DEMO canary
```

`feedback.2.0 GO` remains mandatory before the first real demo submission.

---

# 14. Gate decision after source review

## Phase 4 architecture
**ACCEPTED**

The three-tier architecture and non-sending boundary are correct.

## Phase 4 implementation
**GO WITH FIXES / NOT YET FORMALLY PASSED**

Four source-level items remain:

```text
F-057 final Risk audit/linkage
F-058 coherent/current final execution context
F-059 approval-chain execution fingerprint
F-060 real order-frequency value at FINAL Risk
```

## Real `order_check`
**WAIT FOR F-057/F-058 FIXES FOR EVIDENCE-QUALITY VALIDATION**

It is non-mutating, but there is little value in collecting real evidence
against a preflight chain whose final approval evidence is not yet fully
durable.

## `order_send`
**NO-GO**

No change.

---

# 15. Next review

Do not create another documentation-only review.

The next normal reviewer artifact will be:

```text
feedback.1.23.md
```

Best package:

```text
F-057 through F-060 fixes
+ supplementary source ZIP
+ F-051 part 2 if completed
+ hosted CI result if available
```

If the fixes are clean, review 1.23 should be able to:

```text
formally PASS Phase 4
finish the current domain-contract approval
possibly close M0 if CI is green
and define the final checklist into feedback.2.0
```

---

# 16. Final reviewer statement

Phase 4 is not being rejected.

The important architecture is good, and the implementation is much stronger
than the original proposal before review.

The source review found exactly the sort of issues this review boundary is for:
not “does order_check work?”, but:

```text
Can we later prove exactly why an order was allowed?
Did FINAL Risk judge the exact state reconciliation judged?
Is the request identity bound to the actual approval chain?
Are all live risk controls real rather than placeholders?
```

Those are narrow, fixable issues.

Close them now, while `order_send` is still impossible.

That is substantially cheaper than discovering them after the first broker
submission.
