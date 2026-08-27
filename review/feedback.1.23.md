# feedback.1.23.md — Phase 4 Follow-up Source Review

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.23  
**Date:** 2026-08-27  
**Reviewed artifacts:** `status(20260827-094337).md`, `crumblr_f057_f060_fixes.zip`, and the previously supplied Phase-4 source supplement  
**Previous review:** `feedback.1.22.md`  
**Overall verdict:** **GO — MATERIAL FIXES ACCEPTED; THREE NARROW HARDENING ITEMS REMAIN BEFORE FORMAL PHASE-4 PASS**  
**M0 verdict:** **OPEN — hosted CI confirmation + refreshed/current domain-contract approval**  
**M1:** **PASSED**  
**M2:** **PASSED**  
**F-051 part 1:** **PASSED**  
**F-051 part 2:** **OPEN / may proceed independently**  
**Phase 4 architecture:** **ACCEPTED**  
**Phase 4 implementation:** **NEAR-PASS / NOT YET FORMALLY PASSED**  
**`order_send`:** **NO-GO — unchanged**

---

## 1. Executive verdict

The F-057–F-060 revision is real and materially improves the implementation.

The source now proves:

```text
FINAL Risk has its own durable event
ApprovedOrder links intent-time Risk + FINAL Risk
one broker-state capture feeds reconciliation + FINAL Risk
FINAL Risk uses a fresh final timestamp
approval-chain identity is stronger than intent.decision_hash alone
hard-coded orders_in_last_hour=0 is gone
order_send remains structurally unreachable
```

So review 1.22 did its job: the large problems are being removed before broker submission exists.

However, the current revision does **not yet implement all of the hardening explicitly requested in the follow-up guidance after the F-057–F-060 plan was shared**.

Three narrow items remain:

```text
A. F-059 approval fingerprint still binds only selected decision fields
B. F-060 counts execution claims, not actual submission attempts
C. broker order_check still accepts an ApprovedOrder with no FINAL Risk id
```

There is also one small timing correction required under F-058 before M5:

```text
final_now must determine risk-session market_day as well
```

No redesign is requested.

---

# 2. F-057 — CLOSED

**Status:** CLOSED / SOURCE-ACCEPTED

The original critical finding is fixed.

The current `ApprovedOrder` contract now carries:

```text
intent_risk_decision_id
final_risk_decision_id
supervisor_decision_id
```

`ExecutionOrchestrator` now:

```text
FINAL Risk PASS
→ constructs ApprovedOrder linked to both RiskDecisions
→ appends FINAL_RISK_PASSED
   containing the complete serialized FINAL RiskDecision
   plus an order fingerprint
→ only then calls order_check
```

On a FINAL Risk refusal, the complete FINAL `RiskDecision` is also persisted in `FINAL_RISK_BLOCKED`.

ADR-001 has been corrected to match the sealed-capsule design:

```text
intent-time Risk → sealed DecisionCapsule
FINAL Risk       → append-only execution audit
```

This directly closes the audit-chain break from review 1.22.

### Accepted

No further F-057 work is required inside `ExecutionOrchestrator`.

---

# 3. F-058 — coherent broker observation accepted; final session clock needs one last correction

**Status:** PARTLY CLOSED / ONE NARROW M5 FIX REMAINS

The important half is fixed correctly.

Previously:

```text
account/positions read A → FINAL Risk
broker capture B         → reconciliation
```

Now:

```text
capture_broker_state() once
        ↓
BrokerStateObservation
        ├→ persisted broker snapshots → reconciliation
        └→ raw account_state/position_states → FINAL Risk
```

That is the right design and closes D-047's two-observation problem.

The new `final_now = self._clock()` immediately before FINAL Risk also fixes the original ADR-001 expiry/freshness issue for:

```text
intent expiry
trading-window evaluation inside FINAL Risk
market-data age
FINAL Risk timestamp
ApprovedOrder.created_at_utc
```

### Remaining clock-boundary issue

`recover_session()` still executes **before** `final_now` is taken and receives:

```python
market_day=trading_day(now)
```

where `now` is the earlier `run_once()` timestamp.

A broker interaction can therefore theoretically straddle the 17:00 America/New_York risk-session boundary:

```text
early now        = old trading day
broker reads
reconciliation
cross 17:00 NY
final_now        = new trading day
FINAL Risk       = new time
risk ledger      = recovered for old day
```

Before M5, make the final timing sequence:

```text
single broker capture
→ persist
→ reconcile
→ spec
→ fresh tick
→ final_now = clock()
→ recover_session(... market_day=trading_day(final_now))
→ construct final portfolio
→ FINAL Risk(now=final_now)
```

Then all final-stage time-sensitive authorities share the same clock boundary.

This is a small sequencing correction, not a redesign.

---

# 4. F-059 — improved, but approval-chain fingerprint is still incomplete

**Status:** PARTLY CLOSED

The original implementation used only:

```text
intent.decision_hash
```

The new `_approval_chain_fingerprint()` is materially better and the supplied conflict test proves that a different approved volume for the same intent now fails closed.

Accepted progress.

However the helper still manually selects only part of the actual decision contracts.

For `RiskDecision`, omitted content includes for example:

```text
decided_at_utc
account_equity
stop_distance_points
risk_amount
```

For `SupervisorDecision`, omitted content includes:

```text
decided_at_utc
statistical_monitor_version
observed_regime
notes
uncalibrated_checks
```

The last item is especially relevant: `uncalibrated_checks` explicitly changes what a Supervisor approval means.

The immutable execution request should not have to be updated every time a new material field is added to either contract.

## Required fix

Use canonical complete decision content:

```text
fingerprint({
    provenance_fingerprint,
    trade_intent: trade_intent.model_dump(mode="json"),
    intent_risk_decision: risk_decision.model_dump(mode="json"),
    supervisor_decision: supervisor_decision.model_dump(mode="json"),
})
```

or an equivalent complete canonical representation.

Likewise the post-FINAL binding should bind:

```text
complete FINAL RiskDecision
+ complete ApprovedOrder
```

not rely on the final risk decision id as the only representation of FINAL Risk content.

### Required regression test

Same logical intent and same fields currently included in the manual fingerprint, but change an omitted semantically meaningful field, for example:

```text
SupervisorDecision.uncalibrated_checks
```

Expected:

```text
ExecutionRequestConflictError
```

not harmless retry.

---

# 5. F-060 — REOPENED: a claimed execution request is not an order

**Severity:** HIGH BEFORE M5  
**Status:** REOPENED

The hard-coded zero is gone, which is progress.

But this replacement is not the correct authority:

```python
count_claimed_since(...)
```

A request is claimed **before** eligibility and before every later gate.

Therefore all of these currently count as an "order":

```text
INELIGIBLE
GATE_CLOSED
RECONCILIATION_BLOCKED
FINAL_RISK_BLOCKED
ORDER_CHECK_REJECTED
```

and the request currently being evaluated also counts itself.

That makes the meaning of:

```text
orders_in_last_hour
```

incorrect.

With `evaluate()` blocking when:

```text
orders_in_last_hour >= max_orders_per_hour
```

counting the current claim also moves the effective limit by one in the restrictive direction.

This is fail-safe, but it is not the control the field claims to represent.

## Required authority

For the future submission path, use a durable execution event that means:

> the platform has committed to attempting one broker submission.

The correct candidate already exists in the enum:

```text
SUBMISSION_STARTED
```

So the eventual authority should be:

```text
count SUBMISSION_STARTED events in [final_now - 1 hour, final_now]
```

Why `SUBMISSION_STARTED` rather than `SUBMITTED`:

```text
persist SUBMISSION_STARTED
→ broker call
→ response lost / process crashes
```

An ambiguous submission must conservatively consume the frequency budget because it may already have reached the broker.

### Phase-4 implementation now

It is fine to add the event-count query already.

Because Phase 4 emits no `SUBMISSION_STARTED` events, its truthful value today is:

```text
0
```

That is not a placeholder; it is a real query against the durable submission authority with zero rows because submission is structurally unavailable.

Do not use `execution_requests` claim count as order-frequency authority.

---

# 6. F-061 — broker preflight must reject an order without FINAL Risk linkage

**Severity:** HIGH BEFORE REAL `order_check` / M5  
**Status:** NEW / OPEN

`ApprovedOrder.final_risk_decision_id` is intentionally optional because the legacy replay/paper simulation path has no FINAL execution-time Risk step yet.

That compatibility decision is understandable.

But `OrderCheckMt5Gateway.order_check()` currently accepts any `ApprovedOrder`, including one with:

```text
final_risk_decision_id = None
```

That means a replay-shaped order object could cross the real broker preflight boundary without proof that FINAL Risk ran.

The current `ExecutionOrchestrator` always supplies the field, so this is defense-in-depth rather than a reachable current failure.

Still, the broker-facing boundary should enforce its own prerequisite.

## Required

At the start of `OrderCheckMt5Gateway.order_check()`:

```text
if final_risk_decision_id is None:
    fail closed
```

Use a dedicated exception/reason if useful; do not silently infer approval.

Add one unit test:

```text
ApprovedOrder(final_risk_decision_id=None)
→ order_check refuses
→ MT5 module.order_check was never called
```

This lets the replay contract remain backwards compatible while making the real-MT5 preflight contract strict.

---

# 7. Execution-event conflict hardening — recommended, not a Phase-4 blocker

The current `ExecutionEventStore.append()` still uses:

```text
event_id = (order_request_id, event_type)
ON CONFLICT DO NOTHING
```

That correctly deduplicates identical retries.

But if the same event identity is retried with different payload/reason/detail, the conflict is currently silently ignored.

For simple Phase-4 transitions this is not enough to withhold this review on its own.

Before submission recovery exists, adopt the same rule already used by `ExecutionRequestStore`:

```text
same event id + same content
→ idempotent retry

same event id + different content
→ conflict / fail closed
```

This becomes important for:

```text
SUBMISSION_STARTED
BROKER_ACK
FILLED
RECONCILED
```

where conflicting payloads cannot be treated as harmless duplicates.

Track it with Phase-6 ambiguous-result/idempotence work rather than opening another standalone blocker now.

---

# 8. Source-supplement review

The supplementary source requested in review 1.22 was sufficient to verify the important adjacent contracts:

```text
BrokerStateObservation / BrokerStateStore
reconciliation fail-closed semantics
risk-session recovery
kill-switch authority
Decimal primitives
UTC normalization
Supervisor ownership boundary
journal/capsule persistence
migration assertions
```

No hidden architectural contradiction was found in those files that requires redesigning Phase 4.

The core invariant still holds:

> Agent proposes. Risk engine constrains. Supervisor vetoes. Execution service executes. Reconciliation verifies.

---

# 9. Domain contracts / M0

The actual domain source has now been inspected substantially more deeply than in earlier reviews.

Verified properties include:

```text
Contract is frozen
extra fields forbidden
Decimal/float rejection for exact numeric fields
UTC-aware timestamp normalization
TradeIntent/Trader cannot name broker execution authority
RiskDecision owns approved volume
SupervisorDecision cannot resize or submit
ApprovedOrder links approval identities
DecisionCapsule is immutable/sealed by contract
```

However the supplied human-facing `review/domain_contracts.md` still describes the pre-Phase-4 world and now needs regeneration against current source.

Therefore:

```text
M0 contract review = technically near-complete
formal document approval = still OPEN until refreshed package is supplied
```

Do this as part of the next engineering/status update, not as a standalone documentation session.

---

# 10. CI / M0

The current source bundle reports:

```text
936 passed
3 skipped
mypy clean over 135 files
ruff clean
```

Accepted as local evidence.

Hosted CI remains a separate M0 condition.

Need only the actual post-F-056 hosted result:

```text
commit SHA
Linux result
Windows result
PostgreSQL integration actually ran
gitleaks
skip count
overall result
```

No new CI engineering is requested unless that run fails.

---

# 11. F-051 part 2

Still independent of this hardening.

Proceed whenever sufficient real bars exist.

Desired evidence remains:

```text
real closed M5
→ real features persisted
→ Trader decision
→ intent-time Risk if intent exists
→ real reconciliation
→ Supervisor
→ durable decision chain
→ execution disabled
```

`NO_TRADE` remains a successful plumbing result.

Do not force a trade.

---

# 12. Phase-4 decision

## Architecture

**PASS / ACCEPTED**

## F-057

**CLOSED**

## F-058

**MAIN FINDING CLOSED; final session-boundary sequencing tweak remains before M5**

## F-059

**PARTLY CLOSED — use complete serialized decision contracts**

## F-060

**REOPENED — claims are not order/submission frequency**

## F-061

**OPEN — `order_check` must require FINAL Risk linkage**

## Formal Phase-4 implementation PASS

**NOT YET**

This is intentionally a narrow hold, not another architecture cycle.

---

# 13. Exact next engineering bundle

Do not start a new large phase first.

Apply these small changes:

```text
1. F-059
   use complete serialized TradeIntent/RiskDecision/SupervisorDecision
   in approval-chain fingerprint

2. F-060
   replace claim-count authority with SUBMISSION_STARTED event count

3. F-061
   OrderCheckMt5Gateway rejects final_risk_decision_id=None

4. F-058 final timing
   take final_now before recover_session
   recover trading_day(final_now)

5. recommended
   execution-event duplicate with different content conflicts
   (may be implemented now or carried explicitly into Phase 6)
```

Tests required:

```text
fingerprint conflict on previously omitted Supervisor/Risk content
submission-event frequency count excludes claims/blocks
order_check refuses missing FINAL Risk id without touching MT5
session boundary uses final_now
existing order_send-never-called assertion remains green
```

Then run:

```text
ruff
format check
mypy
pytest solo
```

---

# 14. What may proceed in parallel

GO:

```text
F-051 part 2 real shadow evidence
hosted CI evidence retrieval
regenerate current domain_contracts.md
owner risk-policy decisions
non-sending design for auto-flatten / post-execution reconciliation
```

Do not build agent-platform abstractions yet if the current core developer is still touching these execution boundaries; waiting until this narrow bundle lands remains sensible.

---

# 15. Still prohibited

No change:

```text
terminal AlgoTrading stays OFF
SubmissionGate stays closed
no permissive execution flag
no order_send
no autonomous DEMO canary
```

The first broker submission still requires:

```text
feedback.2.0 GO
```

---

# 16. Next review

Next reviewer file:

```text
feedback.1.24.md
```

Trigger it on the small source diff above, ideally bundled with any of:

```text
F-051 part 2 result
hosted CI green result
refreshed domain_contracts.md
owner risk policy decisions
```

If the narrow fixes are implemented as requested, review 1.24 should not need another broad Phase-4 source audit.

It should be able to:

```text
PASS Phase 4 formally
close the current F-059/F-060/F-061 hardening
possibly close M0
and reduce the remaining route to feedback.2.0 to Phase-5/6 execution controls
```

---

# 17. Final reviewer statement

This update is good progress.

The source confirms that review 1.22 caused real execution-safety improvements rather than documentation changes.

We are no longer arguing about the shape of Phase 4.

We are now tightening the last semantic distinctions that matter before a broker can ever receive an order:

```text
an approval identity must mean the whole approval
an order count must mean actual submission attempts
FINAL Risk must be mandatory at the broker boundary
one final clock must define the final risk session
```

Close those narrowly.

Do not redesign the platform.

Then Phase 4 can pass.
