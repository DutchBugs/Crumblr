# feedback.1.24.md — Phase 4 FORMALLY PASSED

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.24  
**Date:** 2026-08-27  
**Reviewed commit:** `6bdb5b1`  
**Reviewed bundle:** `crumblr_review_1_24_bundle.zip`  
**Previous review:** `feedback.1.23.md`  
**Overall verdict:** **GO — PHASE 4 FORMALLY PASSED**  
**M0:** **OPEN ONLY ON HOSTED CI CONFIRMATION, subject to the human-signoff note in §7**  
**M1:** **PASSED**  
**M2:** **PASSED**  
**F-051 part 1:** **PASSED**  
**F-051 part 2:** **OPEN / may continue independently**  
**First real `order_check`:** **GO under the controlled conditions in §8**  
**M5 / first DEMO `order_send`:** **NO-GO**  
**`feedback.2.0.md`:** **still mandatory before the first broker submission**

---

# 1. Review scope

This review is intentionally narrow.

Reviews 1.22 and 1.23 already source-reviewed the Phase-4 architecture and
implementation. Review 1.23 left exactly four hardening items:

```text
F-058  final-time/session sequencing
F-059  complete approval-chain fingerprint
F-060  real SUBMISSION_STARTED order-frequency authority
F-061  broker-boundary FINAL-Risk guard
```

The supplied bundle contains the current source at commit `6bdb5b1`, the six
requested source/test files, the isolated diff from `5be8624` to `6bdb5b1`,
and a regenerated `review/domain_contracts.md`.

The four fixes were checked against the actual source, not accepted from
`status.md` alone.

No new broad Phase-4 audit was performed or required.

---

# 2. F-058 — CLOSED

**Finding:** FINAL execution context/session recovery could use a different
trading-day boundary from FINAL Risk.

**Verdict:** **CLOSED / ACCEPTED**

The reviewed `ExecutionOrchestrator` now takes:

```python
final_now = self._clock()
```

after the broker-state/spec/tick reads and immediately before the final-stage
risk authorities.

`recover_session()` now receives:

```python
market_day=trading_day(final_now)
```

and the same `final_now` is subsequently used for:

```text
risk-session recovery
FINAL Risk
FINAL-risk execution events
ApprovedOrder.created_at_utc
```

This closes the remaining 17:00 America/New_York rollover race identified in
review 1.23.

The dedicated integration regression test moves the orchestrator clock across
the trading-day boundary and proves the later day is used.

**No further F-058 work required for Phase 4.**

---

# 3. F-059 — CLOSED

**Finding:** immutable execution identity did not bind the complete approval
chain.

**Verdict:** **CLOSED / ACCEPTED**

`_approval_chain_fingerprint()` now binds:

```text
DecisionCapsule.provenance_fingerprint
TradeIntent.decision_hash
complete serialized intent-time RiskDecision
complete serialized SupervisorDecision
```

Using `TradeIntent.decision_hash` rather than dumping `TradeIntent` again is
accepted. The intent contract already owns that deterministic complete-content
identity, including its special handling of the genuine `float` confidence
field.

The post-FINAL binding also now fingerprints:

```text
complete FINAL RiskDecision
complete ApprovedOrder
order_request_id
```

rather than only the FINAL risk decision id.

The new regression test where two otherwise-identical capsules differ only in
`SupervisorDecision.uncalibrated_checks` correctly fails closed with an
`ExecutionRequestConflictError`.

This is the exact failure mode review 1.23 required to be covered.

**No further F-059 work required.**

---

# 4. F-060 — CLOSED

**Finding:** `orders_in_last_hour` was being sourced from claimed execution
requests rather than actual submission attempts.

**Verdict:** **CLOSED / ACCEPTED**

The incorrect:

```text
count_claimed_since(...)
```

has been removed.

The authority is now:

```text
ExecutionEventType.SUBMISSION_STARTED
```

queried through:

```python
ExecutionEventStore.count_events_since(...)
```

FINAL Risk therefore receives the count of logical broker submissions the
platform actually committed to attempting, not the count of capsules that
merely entered preflight.

In Phase 4 the correct value is naturally zero because
`SUBMISSION_STARTED` is never emitted and `order_send` does not exist on the
real path.

That is an honest empty authority, not a hard-coded placeholder.

The new persistence tests cover:

```text
zero events
other event types excluded
cutoff boundary
```

**No further F-060 work required.**

---

# 5. F-061 — CLOSED

**Finding:** `OrderCheckMt5Gateway.order_check()` relied on its caller to have
performed FINAL Risk and would accept an `ApprovedOrder` whose
`final_risk_decision_id` was `None`.

**Verdict:** **CLOSED / ACCEPTED**

The gateway now checks that condition at the very beginning of
`order_check()` and raises:

```text
MissingFinalRiskDecisionError
```

before constructing an MT5 request or touching the terminal.

This is the correct defense-in-depth boundary:

```text
replay ApprovedOrder without FINAL Risk
→ may continue to exist as a replay contract

real MT5 order_check
→ FINAL Risk linkage mandatory
→ missing linkage = fail closed
```

The dedicated unit test additionally proves the fake terminal's `order_check`
is never called in this case.

**No further F-061 work required.**

---

# 6. Phase 4 final verdict

## Architecture

**PASSED**

The accepted separation remains intact:

```text
LiveReader
    = observe / persist

LiveDecisionOrchestrator
    = decide

ExecutionOrchestrator
    = execution preflight
```

## Implementation

**PASSED**

The source now provides the complete approved non-sending chain:

```text
sealed intent-time-approved DecisionCapsule
        ↓
durable order_request_id
        ↓
immutable request claim
        ↓
execution eligibility
        ↓
ExecutionPreflightGate
        ↓
one coherent fresh broker-state observation
        ↓
persist observation
        ↓
reconciliation MATCHED
        ↓
current InstrumentSpec + fresh executable tick
        ↓
final_now / current risk session
        ↓
FINAL Risk
        ↓
exact approved volume HOLDS or BLOCKS
        ↓
FINAL_RISK_PASSED audit evidence
        ↓
ApprovedOrder linked to intent Risk + FINAL Risk + Supervisor
        ↓
real-capable MT5 order_check boundary
        ↓
ORDER_CHECKED / ORDER_CHECK_REJECTED audit event
        ↓
STOP
```

The real execution adapter still unconditionally refuses:

```text
order_send
cancel_pending_orders
close_all_positions
```

There is no Phase-4 submission flag capable of enabling those methods.

**Phase 4 is therefore formally PASSED.**

The previously-noted execution-event content-conflict hardening
(`same event identity + different payload → conflict`) remains a sensible
Phase-6 idempotence improvement, as explicitly deferred in review 1.23. It is
not reopened here and is not a Phase-4 blocker.

---

# 7. Domain contract review — APPROVED BY REVIEWER

The regenerated `review/domain_contracts.md` is materially improved and now
describes commit `6bdb5b1` rather than the pre-Phase-4 `f67f341` state.

The current package correctly reflects:

```text
frozen / extra-forbid contracts
Decimal and UTC semantics
TradeIntent agent boundary
intent-time Risk ownership
FINAL Risk ownership
Supervisor veto-only boundary
ApprovedOrder's two risk links
sealed DecisionCapsule semantics
real order_check vs simulated order_send separation
execution-request/event persistence
real MT5 adapter privilege boundary
```

It also correctly distinguishes the two `ApprovedOrder` producers:

```text
ReplayOrchestrator
→ simulated path
→ final_risk_decision_id may be None

ExecutionOrchestrator
→ real non-sending preflight
→ final_risk_decision_id required before order_check
```

The document is internally honest that the real `OrderCheckMt5Gateway` has
not yet been exercised against the actual terminal and that no real
`order_send` path exists.

**Reviewer contract verdict: APPROVED for the current Phase-4 codebase.**

### M0 wording note

`status.md` phrases the local M0 condition as "domain contracts reviewed by a
human." I am the independent project reviewer in this workflow, but I should
not represent myself as a human reviewer.

Therefore:

```text
technical/reviewer contract review = PASSED
```

If the project's M0 governance requirement literally requires a human
signature rather than reviewer approval, the owner should add a one-line
human countersign such as:

```text
Owner reviewed and accepts the current domain-contract package at commit 6bdb5b1.
```

No additional engineering or contract redesign is required for that
countersign.

Subject to that governance interpretation, the only substantive M0 technical
gate still open is the hosted CI rerun.

---

# 8. Real-terminal `order_check` — GO NOW, NON-SENDING ONLY

Reviews 1.22/1.23 deliberately postponed real-terminal `order_check` evidence
until FINAL Risk and final-context hardening were complete.

They now are.

One controlled real-terminal `order_check` evidence run is therefore
**AUTHORIZED**.

Required conditions for that run:

```text
Pepperstone DEMO only
expected account/server guard passes
current approved InstrumentSpec pin
fresh broker state
positions COMPLETE
pending orders COMPLETE
reconciliation MATCHED
market data current
safety RUNNING
FINAL Risk PASS
ApprovedOrder carries final_risk_decision_id
order_send remains structurally impossible
```

Important APP-016 qualification remains unchanged:

> Do **not** enable terminal AlgoTrading merely to make `order_check` pass.

If `order_check` succeeds while AlgoTrading remains off, record that as useful
non-sending real-terminal evidence.

If MT5 refuses `order_check` because AlgoTrading is disabled, record the
result honestly and stop. Do not toggle AlgoTrading yet solely for this test.

No ticket, fill, or exposure is expected or permitted.

---

# 9. Test / quality evidence

The bundle reports, for commit `6bdb5b1`:

```text
pytest:       939 passed, 3 skipped
ruff check:   clean
mypy:         clean, 135 source files
replay:       byte-identical stdout across two 600-bar runs
```

The source/diff and targeted regression tests were reviewed here.

The full repository suite was not independently re-executed from this small
review bundle because it intentionally contains only the requested review
files rather than a runnable repository checkout. The reported full-suite
result therefore remains developer/local evidence, separate from the still
open hosted-CI gate.

---

# 10. M0 / CI

Hosted CI remains the remaining technical M0 gate.

The known F-056 dependency defect is fixed, but:

```text
local green ≠ hosted green
```

To close it, supply the actual hosted result for the current/main commit:

```text
commit SHA
Linux job green
Windows job green
PostgreSQL integration coverage as configured
secret/gitleaks job green
explained skips only
overall workflow green
```

Do not waive this now that CI has already demonstrated its value by finding a
real dependency defect.

---

# 11. F-051 part 2

Still independent and still open.

Continue natural real M5 accumulation and run the already-approved live-shadow
proof when the genuine warm-up requirement is met.

Do not manufacture a BUY/SELL.

Valid outcomes remain:

```text
NO_TRADE
Risk BLOCK
Supervisor VETO/HALT
BUY/SELL if naturally produced
```

The purpose is real-path evidence, not forcing an order-shaped result.

A `baseline_v1` real run may close the F-051/F-048 plumbing proof, but does
not upgrade `ict_v1`'s own strategy evidence. `ict_v1` itself must eventually
consume genuine real-market windows for that.

---

# 12. What remains before feedback.2.0 / first DEMO submission

Phase 4 being passed does **not** mean Crumblr may submit a broker order.

The remaining critical path is now much shorter and clearer:

```text
A. Owner-approved risk policy
   - risk per trade
   - max daily loss
   - max drawdown
   - last-entry cutoff
   - mandatory flatten deadline
   - HALT-reset authority

B. Submission-era execution safety
   - automatic flatten submission
   - real SubmissionGate / F-049
   - durable execution activation authority
   - SUBMISSION_STARTED emission at the correct pre-side-effect point
   - ambiguous order_send outcome recovery
   - no blind resubmission
   - post-fill reconciliation from durable platform history
   - broker-side SL verification
   - execution-event content-conflict hardening

C. Evidence / gates
   - F-051 part 2
   - hosted CI green
   - real non-sending order_check evidence
   - owner/human domain-contract countersign if M0 wording is literal
```

Then, and only then:

```text
feedback.2.0 review
        ↓
GO
        ↓
one deliberately constrained autonomous MT5 DEMO canary order
```

---

# 13. Minor status synchronization — NON-BLOCKING

Do not reopen F-033 and do not spend a dedicated cycle on documentation.

When `status.md` is next touched anyway, sync the current-state summaries that
still predate the Phase-4 completion, especially the Risk capability row for
execution-time revalidation and the top-level update wording/test counts.

This is bookkeeping only and does not alter any gate in this review.

---

# 14. Next review

The next normal immutable reviewer artifact is:

```text
feedback.1.25.md
```

Trigger it on a meaningful bundle, preferably one or more of:

```text
real-terminal order_check evidence
F-051 part 2 completion
hosted CI green result
owner risk-policy decisions
substantial Phase-5 / Phase-6 submission-safety implementation
```

Do not create another review merely for documentation cleanup.

`feedback.2.0.md` remains reserved for the explicit pre-submission gate before
the first actual demo `order_send`.

---

# 15. Final reviewer statement

**Phase 4 is formally PASSED.**

The result is not "Crumblr can trade now."

The result is:

> Crumblr now has a source-reviewed, deterministic, durable and fail-closed
> pre-execution chain that can reach a real broker-side `order_check` while
> remaining structurally unable to submit an order.

That is the correct foundation for the final move toward controlled autonomous
DEMO execution.

The architectural invariant remains:

> **Trader proposes. Risk Engine constrains. Supervisor vetoes. Execution Service executes. Reconciliation verifies.**
