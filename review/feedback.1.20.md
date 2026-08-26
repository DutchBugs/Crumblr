# feedback.1.20.md — F-054/F-055 Accepted; Go Run F-051

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.20  
**Date:** 2026-08-26  
**Reviewed artifact:** `status(20260826-124654).md`  
**Previous review:** `feedback.1.19.md`  
**Overall verdict:** **GO — F-054 HARDENING ACCEPTED; F-055 MECHANISM ACCEPTED; RUN F-051 NOW**  
**M0 verdict:** **OPEN — CI RESULT + ACTUAL DOMAIN-CONTRACT REVIEW**  
**M1 verdict:** **PASSED**  
**M2 verdict:** **PASSED**  
**F-053:** **IMPLEMENTED / READY FOR REAL VALIDATION**  
**F-054:** **CLOSED IN IMPLEMENTATION — FAIL-CLOSED RECOVERY ACCEPTED**  
**F-055:** **MECHANISM IMPLEMENTED; ACTUAL PEPPERSTONE BASELINE PIN PENDING F-051**  
**D-031:** **CLOSED IN IMPLEMENTATION; REAL-SHADOW EVIDENCE PENDING F-051**  
**M5 / first DEMO order:** **NO-GO**  
**Scope note:** Review is based on the supplied status document and its reported test evidence. Source code, GitHub Actions output, `review/domain_contracts.md`, and real-terminal evidence for the newest paths were not independently inspected.

---

# 1. Executive review

Review 1.19 identified two narrow execution-grade weaknesses.

Both have now been addressed in code:

```text
F-054
corrupt/unreadable decision state
→ no longer treated as empty
→ fail closed / kill switch

F-055
first observed broker spec
→ no longer becomes trusted automatically
→ explicit expected_spec_version required
→ unpinned = UNKNOWN
```

Reported local quality gate:

```text
877 passed
3 explained skips
0 failures
ruff clean
format clean
mypy clean
replay determinism reproven
```

No new architectural blocker is introduced by this review.

The next engineering event should be **F-051 against the real Pepperstone terminal**.

---

# 2. F-054 — accepted

**Status:** CLOSED IN IMPLEMENTATION

The revised design now distinguishes three meaningful conditions:

```text
valid prior decision state
genuinely no prior state
unreadable/untrustworthy state
```

The last case no longer collapses into a fresh start.

Reported behavior:

```text
connection failure
schema-version mismatch
malformed row
→ DecisionWindowRecord(unreadable=...)
→ DECISION_STATE_UNKNOWN
→ kill switch trip
```

This satisfies review 1.19's execution-grade recovery requirement.

The choice to perform recovery lazily on the first `decide_once()` rather than in the constructor is also reasonable.

### F-051 evidence addition

If practical, prove the normal restart path once with the real persistence environment:

```text
process real M5 window
→ restart LiveDecisionOrchestrator
→ same window is not independently reprocessed
```

No deliberate database corruption is required during the real-terminal session; the failure semantics already belong in automated tests.

---

# 3. F-055 — implementation accepted, real pin still pending

**Status:** IMPLEMENTED / OPERATIONAL COMPLETION PENDING F-051

The authority model is now correct:

```text
discover
→ verify
→ explicitly pin
→ reconcile
```

The new configuration field:

```text
MarketConfig.expected_spec_version
```

defaults to:

```text
None
```

and `None` reconciles as:

```text
UNKNOWN
```

not `MATCHED`.

That is the correct fail-closed behavior.

The previous trust-on-first-use design has therefore been removed from the safety path.

`InstrumentSpecStore.earliest()` may remain as a discovery/diagnostic helper because reconciliation no longer trusts it.

### Important distinction

F-055's **mechanism** is complete.

The actual Pepperstone EUR/USD baseline is **not yet pinned**, because that must happen only after the current F-051 observation has been inspected.

Therefore do not pre-fill `expected_spec_version` merely to make the next run pass.

F-051 should deliberately begin with:

```text
expected_spec_version = None
→ reconciliation UNKNOWN
```

Then:

```text
observe actual current Pepperstone EURUSD semantic spec
→ compare against prior known M1 evidence
→ human/reviewer accepts it
→ put approved spec_version into config
→ config change is versioned/reviewable
→ rerun
→ reconciliation MATCHED
```

That two-step behavior is desirable evidence.

---

# 4. Strong point: fail-closed behavior is now visible end to end

The new F-055 integration tests reportedly prove:

```text
unpinned baseline
→ produced intent
→ reconciliation UNKNOWN
→ Supervisor HALT
```

and:

```text
correctly pinned baseline
→ that specific reconciliation reason clears
```

This is better than simply testing the reconciliation function in isolation.

It proves that an unsafe/unknown state reaches the actual supervisory decision path.

Accepted.

---

# 5. F-033 — do not reopen again for one stale sentence

The two specific current-summary drifts from review 1.19 were fixed:

```text
Platform/Application maturity
test count
```

The document now reports the platform as MT5-integrated for M1 and the current suite as 877 passed / 3 skipped.

There is still a minor current-section wording lag in the platform block describing the “last meaningful update” as review 1.18 work even though review 1.19 has now been processed.

Fix this opportunistically the next time `status.md` is touched.

Do **not** spend another session on F-033 and do not reopen it solely for this sentence.

---

# 6. F-051 — GO NOW

**Status:** OPEN  
**Priority:** IMMEDIATE

This is now the dominant technical checkpoint.

Do not build more platform safety abstractions before running it.

Recommended real-terminal sequence:

```text
A. Discovery / fail-closed proof
1. Run current migrations on the dedicated soak database.
2. Confirm expected_spec_version is intentionally unpinned.
3. Start current LiveReader against PepperstoneUK-Demo.
4. Account/server/environment guard passes.
5. Persist current InstrumentSpec.
6. Persist coherent broker account snapshot.
7. Persist positions snapshot = COMPLETE.
8. Persist pending-order snapshot = COMPLETE.
9. Confirm the account is flat.
10. Run reconciliation.
11. Expected result at this point: UNKNOWN because spec baseline is unpinned.

B. Human pin
12. Inspect current semantic EURUSD spec.
13. Compare against the already observed M1 Pepperstone contract evidence.
14. Approve the spec_version.
15. Put the approved spec_version in the versioned paper config.

C. Real shadow proof
16. Restart/reload with the pinned config.
17. Capture fresh broker state.
18. Reconciliation → MATCHED.
19. Wait for at least one newly closed real M5 bar.
20. LiveDecisionOrchestrator evaluates that window.
21. FeatureEvidence row exists for it.
22. Trader returns natural BUY / SELL / NO_TRADE.
23. If an intent exists, intent-time Risk sees real broker context.
24. Supervisor sees real reconciliation=MATCHED.
25. Signal/Risk/Supervisor/capsule chain is durably traceable.
26. Execution remains structurally unavailable.
```

Do not force an intent.

A real `NO_TRADE` is valid success evidence.

---

# 7. What F-051 should close

If the complete run above succeeds, the reviewer expects to be able to close/qualify:

```text
F-051
real validation of F-047 broker snapshots
real validation of F-052 coherent account observation
real validation of F-053 spec reconciliation
operational completion of F-055 baseline pin
real validation of F-048 live-shadow wiring
real evidence for D-031 feature persistence
normal real-environment validation of F-054 decision persistence
```

At that point the project should stop talking about whether the Trader can be attached to real data.

It will have been proven.

---

# 8. M0 remains administrative/evidence work

M0 still has only:

```text
CI result
domain-contract human/reviewer approval
```

No new M0 code is requested.

Retrieve actual GitHub Actions evidence:

```text
commit SHA
Linux
Windows
PostgreSQL-backed tests
gitleaks
unexpected skips
overall result
```

And supply:

```text
review/domain_contracts.md
```

unchanged for actual review.

Do not wait on either task to run F-051 if the Windows host is available.

---

# 9. Execution engineering after F-051

If F-051 passes, immediately make Phase 4 the main engineering stream.

Required architecture remains:

```text
Trader
→ intent-time Risk
→ Supervisor
→ FINAL execution-time Risk
→ Execution Service
→ MT5
→ Reconciliation
```

Phase 4 should build:

```text
separate execution-capable MT5 adapter
ApprovedOrder construction outside Trader/Supervisor
order_check
durable order_request_id
ExecutionResult persistence
FINAL execution-time Risk
fresh executable BUY=ask / SELL=bid
fresh spread/equity/exposure
fixed approved volume: hold or block, never increase
automatic intraday flatten
terminal/account execution guard
post-execution reconciliation
```

The existing read-only gateway remains read-only.

---

# 10. One execution invariant to preserve while Phase 4 is built

F-054 now protects decision-window duplication.

The later execution layer must separately protect broker submission.

Required:

```text
one logical approved order
→ one durable order_request_id
```

Across:

```text
retry
timeout
process crash
ambiguous broker response
reconnect
```

the platform must never decide “I am unsure, so submit another order”.

Required recovery order:

```text
persist request identity
→ check known execution state
→ reconcile broker
→ only then decide whether submission is still required
```

This is not a new finding yet because the execution path does not exist.

It is a hard Phase-4 design requirement.

---

# 11. Owner policy now needs to be decided in parallel

Before `feedback.2.0`, the owner must approve the actual paper risk policy.

Still open:

```text
risk per trade
max daily loss
max drawdown
last-entry cutoff
mandatory flatten deadline
HALT-reset authority
```

The developer should not pause F-051 or non-sending execution plumbing while these are decided.

But no canary order can be authorized without them.

---

# 12. Dashboard after F-051

Once the broker-state sources have been validated for real, add only the already-approved operational information:

```text
Balance
Equity
Open P/L
Free margin
Open positions
Pending orders
Broker-state age/completeness
Reconciliation
latest live/shadow Trader decision
Risk result
Supervisor result
```

No new design cycle.

No MT5 import.

No execution controls.

---

# 13. Current path to first autonomous DEMO order

```text
M1 read/reconnect                              ✅
M2 persistence                                ✅
broker-state persistence                      ✅ code
reconciliation                                ✅ code
live Trader/Risk/Supervisor path              ✅ code
feature evidence                              ✅ code
decision-window persistence                   ✅ code
F-054 fail-closed recovery                    ✅ code
F-055 approved-baseline mechanism             ✅ code

F-051 real current-stack proof                 ⏳
actual Pepperstone spec pin                   ⏳
CI                                             ⏳
domain-contract approval                      ⏳
owner risk policy                             ⏳

execution adapter                              ❌
order_check                                    ❌
FINAL execution Risk                          ❌
automatic flatten                             ❌
submission idempotence/recovery               ❌
feedback.2.0                                   ❌
first DEMO canary                              🚫
```

---

# 14. Gate decisions

## M0
**OPEN — evidence/approval only**

## M1
**PASSED**

## M2
**PASSED**

## F-054
**CLOSED IN IMPLEMENTATION**

## F-055
**MECHANISM ACCEPTED — REAL BASELINE PIN PENDING F-051**

## Real live-shadow stack
**GO FOR F-051 NOW**

## Phase-4 non-sending execution engineering
**GO IN PARALLEL / IMMEDIATELY AFTER F-051**

## `order_send`
**NO-GO**

Still requires:

```text
feedback.2.0 GO
```

---

# 15. Required next action order

```text
1. Process feedback.1.20.md.
2. Run F-051 on Windows/MT5.
3. Prove unpinned spec → reconciliation UNKNOWN.
4. Inspect and approve the actual current Pepperstone EURUSD spec.
5. Pin expected_spec_version through versioned config.
6. Prove fresh flat reconciliation → MATCHED.
7. Run a real closed M5 through Trader → Risk → Supervisor.
8. Verify real FeatureEvidence and complete audit trace.
9. Optionally prove normal decision-process restart dedup.
10. Retrieve GitHub Actions result.
11. Supply review/domain_contracts.md unchanged.
12. Close M0 when both evidence items pass.
13. Record owner risk/intraday/HALT-reset decisions.
14. Add validated operational data to dashboard.
15. Start/continue Phase-4 execution engineering.
16. Prepare feedback.2.0.
17. No order_send before feedback.2.0 GO.
```

---

# 16. Next review

The next normal reviewer file is:

```text
feedback.1.21.md
```

Best trigger:

```text
F-051 real run
```

Do not wait for CI/contracts if F-051 exposes a real integration defect.

If F-051 passes cleanly, include:

```text
sanitized real-run evidence
observed semantic spec + approved spec_version
reconciliation UNKNOWN-before-pin evidence
reconciliation MATCHED-after-pin evidence
broker account/position/pending evidence
real M5 decision evidence
feature snapshot evidence
decision/Risk/Supervisor trace
confirmation execution remained unavailable
```

---

# 17. Final reviewer statement

The platform has reached the point where additional simulated safety work has diminishing value.

The important new properties are now present in code:

```text
corrupt decision state fails closed
unapproved instrument state fails closed
feature evidence is durable
real-data decision path exists
broker truth is durable
reconciliation exists
```

Now use them against the real terminal.

The next question is no longer:

> “Have we designed enough protection?”

It is:

> **“Does the current protected stack actually behave correctly against Pepperstone from broker observation through Trader, Risk and Supervisor?”**

Run F-051 and answer that with evidence.
