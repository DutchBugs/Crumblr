# feedback.1.15.md — Accelerated Path to Autonomous Demo Trading & Broker-State Persistence

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.15  
**Date:** 2026-08-25  
**Reviewed artifact:** `status(9).md`  
**Previous review:** `feedback.1.14.md`  
**Owner direction:** **Move toward an attachable Trading Agent and actual autonomous DEMO trading without unnecessary delay.**  
**Overall verdict:** **GO — SHIFT FROM UI/FOUNDATION WORK TO THE M5 CRITICAL PATH**  
**M0 verdict:** **MUST CLOSE NEXT: CI + DOMAIN CONTRACT REVIEW**  
**M1 verdict:** **PASSED / MT5-INTEGRATED**  
**M2 verdict:** **PASSED**  
**Dashboard verdict:** **VISUAL SCOPE FROZEN; ONLY OPERATIONAL DATA PANELS MAY BE ADDED**  
**M5 / first DEMO order verdict:** **NO-GO YET, BUT THE PATH IS NOW SHORT AND EXPLICIT**  
**Live-money trading:** **OUT OF SCOPE FOR THIS PROMOTION**  
**Scope note:** This review is based on the supplied status document and stated evidence. Source code, CI output and database contents were not independently inspected.

---

## 1. Executive review

The latest update mostly completes review 1.14's dashboard-semantic cleanup rather than advancing CI or reconciliation.

Completed in this pass:

```text
F-045 PAPER label → DEMO DATA
F-046 historical/offline market-data treatment
F-044 visible replay heading refinement
F-033 current-state cleanup
754 tests passed / 3 explained skips
```

Those are accepted.

However, the project owner now wants a different emphasis:

> Get to the point where a Trading Agent can operate on real market data and, after the safety gates are satisfied, autonomously trade the MT5 demo account.

The reviewer agrees.

The project has enough UI and foundational work.

From this review onward, the shortest safe path is:

```text
close M0
→ persist broker account/position truth
→ reconciliation
→ live shadow decision pipeline with Trading Agent
→ execution-time risk + execution adapter + flatten safety
→ feedback.2.0
→ first autonomous DEMO order
```

No further strategy sophistication is needed for this.

---

# 2. Review 1.14 status

## F-045 — environment badge

**Status:** CLOSED

`PAPER` is no longer used to describe a read-only demo-data session.

Correct.

---

## F-046 — historical/offline treatment

**Status:** CLOSED

Historical bid/ask/chart data remains visible but explicitly loses any "live" claim when the reader is not healthy.

Correct.

---

## F-044 copy refinement

**Status:** CLOSED

The visible heading now states:

```text
Decision pipeline — latest replay window
```

which is semantically honest while no live decision pipeline exists.

---

## F-033 current-state documentation

**Status:** SUBSTANTIALLY CLOSED

The current sections are now materially more accurate.

Do not spend another engineering cycle on documentation cleanup unless a contradiction affects a gate or operator decision.

---

# 3. New owner objective O-006 — first autonomous trading target is DEMO, not live money

The phrase "daadwerkelijk getrade kan worden" is interpreted for the next promotion as:

```text
MT5 DEMO account
autonomous decisioning
real order submission
real demo fills
zero live-money exposure
```

This is the correct next operational goal.

It does **not** authorize:

```text
live account
real-money orders
higher autonomy level
strategy promotion based on a few demo trades
```

The required major review remains:

```text
feedback.2.0.md
```

before the first `order_send`, even on demo.

---

# 4. The owner is correct: durable broker balance and open-trade state are missing

The current database stores substantial audit state, including:

```text
market ticks
market bars
journal events
risk-session state
safety state
simulated/replay PositionChanged events
```

but the current status does **not** show a durable producer for the real broker's current account state and open-position book.

That is now a blocking gap for M5.

---

# 5. F-047 — persist broker account state, open positions and pending orders

**Severity:** HIGH BEFORE M5 / FIRST ORDER  
**Status:** OPEN  
**Blocks:** reconciliation, robust live risk state, first autonomous demo order

A trading system cannot safely say:

```text
"I know my balance"
"I know my exposure"
"I know I am flat"
```

only because MT5 returned those values to an in-memory object once.

The observed broker state must be durable and auditable.

## Required broker account snapshot

Persist at least:

```text
observed_at_utc
recorded_at_utc
environment
server
account_ref / non-reversible account fingerprint
currency
leverage
margin mode

balance
equity
profit
margin
free_margin
margin_level

account trade permission
terminal trade permission where observed
```

### Important

`balance` and `equity` are different.

Use both.

For risk decisions, current equity is normally the more relevant instantaneous capital state, while balance remains essential audit/account context.

All monetary values remain:

```text
Decimal / NUMERIC
```

Never binary float.

Do not persist or display the full MT5 login.

---

## Required broker position snapshot

A complete broker observation must persist every open position with at least:

```text
snapshot_id
observed_at_utc
broker position/ticket id
canonical symbol
broker symbol
side
volume
open time
open price
current price
stop loss
take profit
unrealized P&L
swap
magic/comment where useful and sanitized
```

For the current `RETAIL_HEDGING` account, each MT5 position must remain individually identifiable even though owner policy still allows only one EUR/USD exposure.

---

## Required pending-order snapshot

Also persist pending orders.

A position book can be flat while a pending broker order still creates future exposure.

At minimum:

```text
broker order id
symbol
order type
volume
price
SL/TP
expiration
state
observed_at_utc
```

---

## Complete-set semantics

This is critical.

A snapshot saying:

```text
0 positions
```

must mean:

> MT5 was successfully queried and returned a complete empty set.

It must not mean:

> the positions call failed.

Use explicit state such as:

```text
COMPLETE
UNKNOWN
FAILED
```

or an equivalent typed representation.

The existing `positions_get(None)` fail-vs-empty distinction must survive into persistence.

---

## Suggested storage shape

Prefer append-only observation history:

```text
broker_account_snapshots
broker_position_snapshots
broker_pending_order_snapshots
```

or:

```text
broker_state_snapshots
  + child position/order rows
```

Do not maintain only one mutable "current account" row.

The current state may be a query/view over the latest complete snapshot.

Historical observations are useful for:

```text
audit
reconciliation
incident analysis
risk reconstruction
dashboard
```

---

## When to capture broker state

At minimum:

```text
connect
reconnect
each live decision window
immediately before order submission
immediately after order result
after reconciliation mismatch
after fill/position change
```

A periodic observation cycle may also run between decisions.

---

# 6. Broker state belongs in reconciliation, not only the dashboard

The owner asked about showing balance and open trades.

Yes, add them to the dashboard **after they have a proper platform-owned data source**.

Correct flow:

```text
MT5
↓
broker-state observer
↓
durable PostgreSQL snapshot
↓
reconciliation + risk
↓
dashboard
```

Not:

```text
dashboard
↓
direct account_info()/positions_get()
```

Suggested dashboard additions once F-047 exists:

```text
ACCOUNT
Balance        €...
Equity         €...
Free margin    €...
Open P/L       €...

POSITIONS
EUR/USD BUY    0.xx lot
Open           ...
Current        ...
SL             ...
TP             ...
P/L            ...
```

When flat:

```text
OPEN POSITIONS
0 — broker snapshot COMPLETE
```

That wording is better than merely displaying `0`.

---

# 7. F-048 — attach the Trading Agent to a real live-data decision pipeline now

**Severity:** HIGH PATH-TO-M5  
**Status:** OPEN  
**Goal:** agent integration without prematurely enabling execution

Today:

```text
LiveReader
→ real MT5 ticks/bars
→ PostgreSQL

ReplayOrchestrator
→ Trading Agent
→ Risk
→ Supervisor
```

The two paths remain separate.

That separation was correct for M1.

It is now the main thing preventing the agent from operating on real market observations.

## Required next architecture

Build a live/shadow decision orchestrator:

```text
real closed M5 bar
        ↓
feature pipeline
        ↓
Trading Agent
        ↓
TradeIntent / NO_TRADE
        ↓
intent-time deterministic Risk Engine
        ↓
Supervisor
        ↓
STOP HERE FOR SHADOW MODE
```

Persist the complete decision chain.

No order submission yet.

This lets the owner see within the dashboard:

```text
REAL MARKET
→ REAL AGENT DECISION
→ REAL RISK RESULT
→ REAL SUPERVISOR RESULT
→ EXECUTION DISABLED
```

That is the fastest useful step toward autonomous demo trading.

---

# 8. Agent boundary remains non-negotiable

Whether the eventual Trading Agent is:

```text
ict_v1
another deterministic strategy
an AI/LLM-assisted proposal component
```

the contract stays:

> Agent proposes. Risk engine constrains. Supervisor vetoes. Execution service executes. Reconciliation verifies.

The agent may never receive:

```text
MT5 credentials
order_send access
HALT-reset authority
risk-policy mutation
final unrestricted lot-size authority
promotion authority
```

An "attachable agent" means:

```text
implements the TradingAgent/Strategy proposal contract
```

not:

```text
gets a broker connection
```

---

# 9. D-031 becomes higher priority before live agent decisions

The database currently journals feature identity/version but not the full feature values.

That was tolerable while the real feed and decision engine were separate.

Once the agent begins making decisions from real data, it becomes audit-critical.

Before the live shadow decision pipeline is considered evidence-quality, persist enough of the actual feature snapshot to reconstruct:

```text
what inputs the agent saw
what values caused the proposal
which feature implementation/version produced them
```

A hash alone proves sameness; it does not explain the decision.

Do not block the first wiring prototype on a perfect research feature store, but close this before autonomous demo execution.

---

# 10. Reconciliation is now a direct prerequisite, not a later improvement

Build reconciliation alongside F-047.

Compare:

```text
configured account identity
vs observed broker account

locally expected positions
vs observed broker positions

locally expected pending orders
vs observed broker pending orders

expected instrument semantic spec
vs observed broker spec
```

Output:

```text
MATCHED
MISMATCHED
UNKNOWN
```

Rules:

```text
UNKNOWN    → fail closed
MISMATCHED → HALT
MATCHED    → may continue
```

Reconciliation must run:

```text
startup
reconnect
before first order after reconnect
after each execution result
periodically while exposure exists
```

---

# 11. M0 must stop consuming roadmap attention

Current M0 blockers remain only:

```text
hosted CI
human/reviewer domain-contract review
```

The status still reports CI as never run.

This must be closed in the next development cycle.

### Required

Run CI and provide the contract package.

Do not build more unrelated functionality before doing this.

M0 has been open long enough.

---

# 12. The fastest safe route to the first autonomous DEMO trade

This is now the required critical path.

## Phase 1 — close foundation administratively

```text
CI passes
domain contracts reviewed
M0 closes
```

## Phase 2 — broker truth

```text
F-047 account snapshots
F-047 position snapshots
pending-order snapshots
read-only reconciliation
```

## Phase 3 — attach the agent

```text
real M5 close
→ features
→ Trading Agent
→ intent-time risk
→ Supervisor
→ persist
→ dashboard
→ EXECUTION DISABLED
```

Run this in shadow/dry-run mode first.

This can happen **before** `feedback.2.0`.

## Phase 4 — execution safety

Complete already-known M5 requirements:

```text
separate execution-capable MT5 adapter
order_check
execution-time final Risk Engine revalidation
current executable bid/ask
current spread
current equity/account state
approved fixed volume may hold or block — never increase
idempotent order_request_id
execution result persistence
real reconciliation after result
automatic intraday flatten semantics
terminal/account execution guard
```

## Phase 5 — owner policy

Owner must explicitly approve:

```text
risk per trade
max daily loss
max drawdown
last-entry cutoff
mandatory flatten deadline
production/demo HALT-reset authority
```

## Phase 6 — execution review

```text
feedback.2.0
→ GO
```

Only then:

```text
enable terminal AlgoTrading for DEMO
enable execution adapter explicitly
submit the first tightly controlled demo order
```

---

# 13. First demo order should be a canary, not “paper campaign started”

The first order is a technical execution canary.

It should be deliberately constrained.

The purpose is to prove:

```text
proposal
risk
supervisor
final risk
order_check
order_send
broker acknowledgement
fill/position observation
reconciliation
SL presence
position closure
audit trail
```

It is **not** evidence that the strategy is profitable.

Only after the execution canary behaves correctly should the unattended paper campaign begin.

---

# 14. New finding F-049 — execution enablement must be multi-gated

**Severity:** CRITICAL BEFORE M5  
**Status:** OPEN / previously partly designed, now formalized for first demo order

The first demo order must require all of these simultaneously:

```text
environment = DEMO
expected account/server = verified
reconciliation = MATCHED
market data = HEALTHY
safety state = RUNNING
risk policy = owner-approved
execution adapter = explicitly enabled
terminal AlgoTrading = enabled
feedback.2.0 = GO
```

If any one is false or unknown:

```text
order submission unavailable
```

Turning on the MT5 AlgoTrading button must never be enough.

---

# 15. Gate decisions

## M0
**CLOSE NEXT**

CI + domain contracts only.

## M1
**PASSED**

## M2
**PASSED**

## Dashboard
**SCOPE FROZEN**

Only add broker-state/reconciliation/real-decision information.

## Live shadow decision pipeline
**GO NOW**

Agent may operate on real data with execution disabled.

## M5 / first autonomous DEMO order
**NO-GO YET — ACTIVE CRITICAL PATH**

## Live money
**NO-GO / NOT THIS PROMOTION**

---

# 16. Required next action order

```text
1. Process feedback.1.15.md.
2. Run hosted CI and record result.
3. Supply domain contracts for reviewer approval; close M0.
4. Implement F-047 durable broker account snapshots.
5. Implement F-047 complete broker position + pending-order snapshots.
6. Build read-only reconciliation from those snapshots.
7. Add balance/equity/open positions/reconciliation to Dashboard via PostgreSQL.
8. Build F-048 real live-data shadow decision orchestrator.
9. Feed real closed M5 bars → features → Agent → Risk → Supervisor.
10. Persist full live decision evidence; close D-031 feature-value audit gap.
11. Run the live decision pipeline with EXECUTION DISABLED.
12. Implement separate execution MT5 adapter + order_check.
13. Implement ADR-001 final execution-time risk revalidation.
14. Implement and test automatic flatten/deadline behavior.
15. Obtain owner risk/intraday/HALT-reset decisions.
16. Prepare feedback.2.0 execution-readiness package.
17. Only after feedback.2.0 GO: enable demo AlgoTrading + execution gate.
18. Execute one controlled autonomous DEMO canary trade.
19. Reconcile the canary end-to-end before starting unattended paper.
```

---

# 17. Next review cadence

Do not request another review merely after one small implementation change.

Next regular review:

```text
feedback.1.16.md
```

should be triggered when there is a meaningful package such as:

```text
CI + M0 closure
and/or
broker-state persistence + reconciliation
and/or
live shadow Agent decisions on real Pepperstone M5 data
```

The mandatory major review remains:

```text
feedback.2.0.md
```

immediately before first `order_send`.

---

# 18. Final reviewer statement

The platform has enough foundation to stop behaving like a prototype that is always preparing for the next stage.

The next target is concrete:

> A real Trading Agent should soon be consuming real Pepperstone M5 data and producing real proposals through Risk and Supervisor — with execution visibly disabled.

At the same time, the platform must begin durably recording the broker truth it will need once money-like state exists:

```text
balance
equity
margin
open positions
pending orders
```

Then reconciliation can become real.

After that, execution is no longer a vague future milestone.

It becomes a short, explicit checklist ending in `feedback.2.0` and one controlled DEMO canary order.
