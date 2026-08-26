# feedback.1.17.md — Reconciliation + Live-Shadow Agent Acceptance and M5 Execution Ramp

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.17  
**Date:** 2026-08-26  
**Reviewed artifact:** `status(20260826-093329).md`  
**Previous review:** `feedback.1.16.md`  
**Overall verdict:** **GO — RECONCILIATION AND LIVE-SHADOW AGENT ARE NOW BUILT; PROVE THEM AGAINST REAL MT5 NEXT**  
**M0 verdict:** **OPEN ONLY ON CI + ACTUAL HUMAN/REVIEWER CONTRACT APPROVAL**  
**M1 verdict:** **PASSED**  
**M2 verdict:** **PASSED**  
**F-048 verdict:** **IMPLEMENTED / INTEGRATION-TESTED — REAL PEPPERSTONE SHADOW EVIDENCE STILL REQUIRED**  
**M5 / first DEMO order:** **NO-GO, BUT EXECUTION ENGINEERING MAY START AFTER THE REAL SHADOW CHECKPOINT**  
**Scope note:** This review is based on the supplied status document and its stated evidence. Source code, GitHub Actions output, `review/domain_contracts.md`, and the newly added F-047/F-048 paths against the real MT5 terminal were not independently inspected.

---

## 1. Executive review

The project has materially advanced along the critical path agreed in reviews 1.15 and 1.16.

Newly reported:

```text
F-052 coherent account observation       CLOSED
F-050 BrokerStateHealth                  CLOSED
read-only reconciliation v0              BUILT
domain-contract review package           ASSEMBLED
LiveDecisionOrchestrator                 BUILT
InstrumentSpec durable producer          BUILT
full test suite                          836 passed / 3 explained skips
```

Most importantly, the architectural gap between:

```text
real MT5 market data
```

and:

```text
Trading Agent → Risk → Supervisor
```

has now been closed in code.

The new flow is:

```text
LiveReader
→ PostgreSQL
→ LiveDecisionOrchestrator
→ Trading Agent
→ intent-time Risk
→ reconciliation
→ Supervisor
→ persistence
→ STOP
```

There is still no `ApprovedOrder`, `order_check`, or `order_send` path.

That is exactly the desired state immediately before real shadow validation.

---

# 2. F-050 — BrokerStateHealth

**Status:** CLOSED BASED ON DOCUMENTED EVIDENCE

Broker account-state freshness is now distinct from market-data health.

Accepted rule:

```text
fresh price data
≠
fresh broker/account truth
```

`BrokerStateHealth` tracks:

```text
last snapshot time
position-set completeness
pending-order-set completeness
last broker-state error
```

and exposes an explicit usability predicate.

This is the correct input to reconciliation.

---

# 3. F-052 — coherent account snapshot

**Status:** CLOSED BASED ON DOCUMENTED EVIDENCE

One `BrokerAccountSnapshot` is now derived from one raw `account_info()` observation.

The previous two-read race is removed.

Correct.

---

# 4. Reconciliation v0

**Status:** IMPLEMENTED / DATABASE-INTEGRATED — REAL BROKER MATCH EVIDENCE PENDING

The reconciliation design is directionally correct:

```text
expected platform state
vs
durable observed broker snapshot
```

and explicitly distinguishes:

```text
MATCHED
MISMATCHED
UNKNOWN
```

The current pre-execution expected state is correctly:

```text
0 expected positions
0 expected pending orders
```

until a durable execution history exists.

The database-only smoke test correctly returned `UNKNOWN` because no new broker-state observation existed in the soak database. That is a positive fail-closed result, not a failure.

### Required real check

During the next real MT5 session prove:

```text
correct demo account
fresh COMPLETE broker snapshot
0 observed positions
0 observed pending orders
expected flat state
→ MATCHED
```

Then inject or simulate:

```text
stale/incomplete snapshot
→ UNKNOWN

unexpected position/order
→ MISMATCHED
→ HALT
```

---

# 5. F-048 — live/shadow Trading Agent pipeline

**Status:** IMPLEMENTED / FIRST-WIRING ACCEPTED

The architectural boundary is accepted.

The implementation reportedly uses the same:

```text
Trading Agent
Risk Engine
Supervisor
```

that replay already uses.

Only the data source changes.

The live decision pipeline reads persisted:

```text
real bars
latest tick
instrument spec
broker account/position state
```

and feeds real reconciliation status into the Supervisor.

It then stops structurally before execution.

This satisfies the **implementation** requirement for F-048.

It does not yet qualify as real shadow evidence because the current session did not have a new real-terminal run with these additions.

---

# 6. F-051 — now the immediate real-world checkpoint

**Status:** OPEN  
**Priority:** HIGHEST NEXT

One short Windows/MT5 session can now validate several pieces together.

Required sequence:

```text
1. start real LiveReader with current code
2. persist current InstrumentSpec
3. persist broker account snapshot
4. prove real balance/equity/margin values
5. positions query → COMPLETE
6. pending-orders query → COMPLETE
7. run reconciliation → MATCHED while account is flat
8. allow at least one new closed real M5 bar
9. run LiveDecisionOrchestrator
10. persist Signal/Risk/Supervisor result
11. confirm decision context is REAL/SHADOW, not replay
12. confirm execution remains unreachable
```

If the strategy produces `NO_TRADE`, that is perfectly valid evidence.

Do not force a BUY/SELL setup merely to make the run interesting.

---

# 7. New finding F-053 — reconciliation must now include the semantic instrument specification

**Severity:** HIGH BEFORE M5  
**Status:** OPEN

Reconciliation v0 was originally built without EUR/USD contract-spec comparison because `instrument_specs` had no producer.

That limitation was understandable at the time.

F-048 has now added the missing producer:

```text
LiveReader
→ InstrumentSpecStore
```

Therefore the old reason for omitting instrument reconciliation no longer exists.

### Required

Reconciliation should compare the expected/current semantic contract identity for EUR/USD, including the safety-relevant stable fields such as:

```text
broker symbol
digits
point
tick size
contract size
volume min/max/step
stops level
freeze level
trade mode
filling capability
```

Do not use volatile market-derived `tick_value` as a semantic-change trigger; F-039 already settled that.

### Result

```text
same semantic spec
→ MATCHED

material spec difference
→ MISMATCHED

missing/unreadable spec
→ UNKNOWN
```

This must be complete before `feedback.2.0`.

---

# 8. New finding F-054 — live decision-window idempotence must survive restart before execution exists

**Severity:** CRITICAL BEFORE FIRST ORDER  
**Status:** OPEN

The live orchestrator currently keeps:

```text
seen_decision_hashes
```

only in process memory.

The status correctly notes that today a restart can at worst produce a duplicate audit decision because there is no order path.

That changes the instant an execution service is attached.

The same closed M5 window must never become two executable proposals merely because the process restarted.

### Required before execution

Use durable decision-window/idempotence identity.

A suitable invariant is:

```text
same strategy
+ same config
+ same canonical symbol
+ same closed M5 decision window
+ same feature/input identity
→ same logical decision identity
```

Across:

```text
normal operation
restart
reconnect
ambiguous crash recovery
```

the platform may:

```text
return the existing decision
or
idempotently reconstruct it
```

but must not create a second independently executable order request.

This should connect directly to the later durable `order_request_id`.

---

# 9. D-031 — feature values now block evidence-quality shadow operation

The reviewer previously allowed the first F-048 wiring test without persisting full feature values.

That exception has served its purpose.

Before a real shadow run is treated as evidence for promotion, persist enough to answer:

> Exactly what did the Trading Agent see?

Required:

```text
feature values
feature schema/version
input bar/window identities
strategy version
occurred_at_utc
```

A hash remains useful but is not sufficient by itself.

### Reviewer position

The first real F-051 wiring run may happen before this lands.

A sustained/evidence-quality live-shadow campaign may not.

Close D-031 immediately after or alongside F-051.

---

# 10. Domain-contract package — ready for actual reviewer inspection

The developer has assembled:

```text
review/domain_contracts.md
```

covering all requested contracts.

Good.

But this reviewer has **not been given that actual file**.

Therefore M0 contract review is not yet approved.

### Required

Supply `review/domain_contracts.md` unchanged in the next review package.

The reviewer will check the actual definitions/claims, not approve them from the status summary.

Do not hold engineering idle while this happens.

---

# 11. CI — human action now

The project reports that pushes to `main` should already have triggered GitHub Actions, but the development session could not inspect the Actions result.

This is no longer an engineering blocker.

It is an evidence retrieval task.

### Required

Check GitHub Actions and record:

```text
commit SHA
Linux job
Windows job
PostgreSQL tests
gitleaks
unexpected skips
overall status
```

If green:

```text
CI condition closes
```

If red:

```text
fix the real failure
```

Do not waive CI now.

---

# 12. M0 closure

M0 should now close as soon as these two evidence actions finish:

```text
CI actual result
domain contracts actual human/reviewer approval
```

No new architectural M0 work is requested.

---

# 13. Execution engineering may now be prepared, but not enabled

Once F-051 has produced a real:

```text
broker snapshot
→ reconciliation MATCHED
→ real M5 Agent/Risk/Supervisor decision
```

the developer should begin Phase 4.

Required components remain:

```text
separate execution-capable MT5 adapter
order_check
ApprovedOrder contract
ExecutionResult contract
durable order_request_id
ADR-001 FINAL execution-time Risk revalidation
current executable ask for BUY / bid for SELL
current spread
fresh broker equity/state
reconciliation=MATCHED
fixed approved volume: hold or block, never increase
execution result persistence
post-result reconciliation
automatic flatten implementation
```

Still no `order_send` until `feedback.2.0`.

---

# 14. Owner decisions now move onto the critical path

The project is close enough to execution engineering that these should be decided now, in parallel:

```text
risk per trade
max daily loss
max drawdown
last-entry cutoff
mandatory flatten deadline
HALT reset authority
```

Do not let provisional YAML values become policy by accident.

These values are required for `feedback.2.0`.

---

# 15. Dashboard — useful operational additions only

After F-051 validates the real persisted sources, update the existing dashboard with:

```text
Balance
Equity
Open P/L
Free margin
Open positions
Pending orders
Broker-state age
Reconciliation status
Live/shadow decision pipeline
```

The dashboard remains read-only.

Do not spend another cycle on visual redesign.

---

# 16. Current path to first autonomous DEMO order

```text
M1 real MT5 read/reconnect                 ✅
M2 market/event persistence                ✅
broker-state persistence                    ✅ code
F-050 freshness semantics                  ✅
F-052 coherent account snapshot            ✅
reconciliation v0                          ✅ code
LiveDecisionOrchestrator                    ✅ code
InstrumentSpec persistence                  ✅ code

F-051 real MT5 proof                       ⏳
D-031 feature values                       ⏳
F-053 instrument reconciliation            ⏳
F-054 durable decision idempotence          ⏳
CI                                          ⏳ human evidence
domain-contract approval                    ⏳ reviewer/human

execution adapter                           ❌
order_check                                 ❌
FINAL Risk                                  ❌
auto flatten                                ❌
owner risk policy                           ❌
F-049 execution multi-gate                  ❌
feedback.2.0                                ❌
first DEMO canary                           🚫
```

The architectural uncertainty is now small.

The remaining work is increasingly concrete execution-safety engineering.

---

# 17. Gate decisions

## M0
**CLOSE NEXT — NO NEW M0 ENGINEERING**

## M1
**PASSED**

## M2
**PASSED**

## Reconciliation v0
**IMPLEMENTED — REAL MATCH VALIDATION PENDING**

## F-048 live-shadow Agent
**IMPLEMENTED — GO FOR REAL MT5 WIRING TEST**

## Sustained shadow evidence
**NOT YET — D-031 MUST CLOSE**

## Phase 4 execution engineering
**GO AFTER F-051 REAL CHECKPOINT**

## First DEMO `order_send`
**NO-GO**

Still requires `feedback.2.0 GO`.

---

# 18. Required next action order

```text
1. Process feedback.1.17.md.
2. Run F-051 against the real Pepperstone terminal.
3. Prove real balance/equity/positions/pending-order snapshots.
4. Prove flat broker reconciliation → MATCHED.
5. Run one real closed M5 through LiveDecisionOrchestrator.
6. Persist and inspect the real Signal/Risk/Supervisor chain.
7. Close D-031 feature-value persistence.
8. Add F-053 instrument-spec reconciliation.
9. Add F-054 durable live-decision idempotence.
10. Check and record GitHub Actions CI result.
11. Supply review/domain_contracts.md unchanged for actual review.
12. Close M0.
13. Add broker state/reconciliation/live decision to the existing dashboard.
14. Start Phase-4 execution adapter + order_check + FINAL Risk + flatten work.
15. Obtain owner risk/intraday/HALT-reset policy decisions.
16. Prepare feedback.2.0 execution-readiness package.
17. No order_send before feedback.2.0 GO.
```

---

# 19. Next review

The next normal review is:

```text
feedback.1.18.md
```

Best trigger:

```text
F-051 real Pepperstone validation
+ real shadow Agent decision
+ CI result
+ domain_contracts.md supplied
```

If all of that arrives together, review 1.18 should be able to:

```text
close M0
qualify the real live-shadow wiring
and pivot almost entirely to M5 execution readiness
```

The mandatory major review remains:

```text
feedback.2.0.md
```

immediately before the first `order_send`.

---

# 20. Final reviewer statement

This update crosses another meaningful boundary.

The project no longer merely has:

```text
a Trading Agent
and
a real MT5 data reader
```

as separate capabilities.

It now has code that joins them safely:

```text
real market observation
→ Trading Agent
→ Risk
→ reconciliation
→ Supervisor
→ audit trail
```

while execution remains absent.

That is exactly the state the project needed to reach before beginning the execution phase.

Now prove it once against the real terminal, finish the audit/idempotence gaps, close M0, and move on to execution engineering.
