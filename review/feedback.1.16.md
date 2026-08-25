# feedback.1.16.md — Broker-State Acceptance, Reconciliation Gate & Fast Track to Live Shadow Agent

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.16  
**Date:** 2026-08-25  
**Reviewed artifact:** `status(10).md`  
**Previous review:** `feedback.1.15.md`  
**Owner objective:** **Get a Trading Agent onto real Pepperstone M5 data soon, followed by one tightly gated autonomous DEMO canary order.**  
**Overall verdict:** **GO — MATERIAL PROGRESS; PROCEED TO RECONCILIATION AND LIVE SHADOW AGENT**  
**M0 verdict:** **STILL OPEN — CI + DOMAIN CONTRACT REVIEW; MUST CLOSE BEFORE feedback.2.0**  
**M1 verdict:** **PASSED / MT5-INTEGRATED**  
**M2 verdict:** **PASSED**  
**F-047 verdict:** **IMPLEMENTED AND PERSISTENCE-TESTED; REAL-MT5 VERIFICATION STILL REQUIRED**  
**F-048 live shadow Agent:** **GO TO BUILD NEXT**  
**M5 / first DEMO order:** **NO-GO YET**  
**Scope note:** This review is based on the supplied status document and stated evidence. Source code, actual database rows, CI output and the new broker-state paths against a real MT5 terminal were not independently inspected.

---

## 1. Executive review

Review 1.15 has changed the project in the intended direction.

The most important new capability is now present:

```text
MT5
→ account state
→ open positions
→ pending orders
→ durable PostgreSQL snapshots
```

The project reports three new append-only tables:

```text
broker_account_snapshots
broker_position_snapshots
broker_pending_order_snapshots
```

with parent/child transaction semantics and an explicit:

```text
COMPLETE
FAILED
UNKNOWN
```

state for position and pending-order collections.

The quality gate is now reported as:

```text
ruff   clean
mypy   clean
pytest 786 passed
       3 explained skips
       0 failures
```

This is the correct direction.

The project is now close enough to the real decisioning phase that the next work must primarily answer:

> Can the platform establish the broker's current truth, reconcile it, and then let a Trading Agent safely make real-market proposals with execution disabled?

That is the immediate target.

---

# 2. F-047 — durable broker state

## Status: IMPLEMENTED / PERSISTENCE-TESTED — NOT YET FULLY MT5-VALIDATED

The implementation described in the status is structurally sound.

Accepted:

```text
balance and equity both persisted
profit/margin context persisted
account reference is non-reversible
positions and pending orders are separate complete sets
failed query ≠ empty set
parent and child rows commit together
append-only observation history
capture on reconnect
periodic capture
migration parity tested
```

This directly resolves the database gap the owner identified.

However, the new capabilities:

```text
pending_orders()
account_extras()
new broker snapshot mapping
```

have not yet run against the real Pepperstone terminal.

Therefore F-047 is **shipped**, but its qualification for M5 remains pending real-terminal evidence.

Do not call that a regression or blocker to continued development.

Verify it during the next Windows/MT5 session.

---

# 3. F-050 — broker-state freshness becomes a safety input

**Severity:** HIGH BEFORE LIVE SHADOW EVIDENCE / CRITICAL BEFORE M5  
**Status:** OPEN

The current implementation deliberately allows:

```text
broker-state capture fails
→ error logged
→ LiveReader market-data status remains healthy
```

That is reasonable for M1, where `LiveReader`'s primary claim is market-data health.

It is **not sufficient once Risk/Supervisor or execution depend on account state**.

A system can have:

```text
fresh EUR/USD ticks
+
stale balance/positions/pending orders
```

and must not treat that as a safe trading state.

## Required separation

Keep two concepts:

```text
MarketDataHealth
BrokerStateHealth
```

Do not overload `ReaderStatus`.

Broker state needs at minimum:

```text
latest successful snapshot time
account snapshot age
position_set_state
pending_order_set_state
last broker-state error
```

## Required rule

For a real live/shadow decision:

```text
broker snapshot missing
or stale
or position set != COMPLETE
or pending-order set != COMPLETE
→ reconciliation = UNKNOWN
```

For execution:

```text
reconciliation != MATCHED
→ no order
```

## Before an order

A periodic 60-second snapshot is not sufficient as the final execution truth.

Immediately before execution:

```text
fresh synchronous broker-state observation
→ reconciliation
→ final Risk revalidation
→ order_check
→ order_send
```

The maximum allowed snapshot age must be explicit and deterministic.

---

# 4. F-051 — prove F-047 against the actual Pepperstone terminal

**Severity:** HIGH BEFORE M5  
**Status:** OPEN

The new status explicitly states that no manual real-terminal verification was possible for this implementation session.

The next MT5 run should prove at least:

```text
account snapshot inserted
balance correct
equity correct
profit correct
margin values correct
account fingerprint stable
RETAIL_HEDGING preserved

positions_get succeeds
0 positions persists as COMPLETE + zero child rows

orders_get succeeds
0 pending orders persists as COMPLETE + zero child rows

capture repeats after the configured interval
reconnect creates a new complete observation
no raw account login appears anywhere
```

Also verify the actual MT5 mappings introduced for pending-order enums.

Do not wait until the first order to discover that the fake-terminal representation was wrong.

---

# 5. F-052 — one broker account snapshot must represent one coherent account observation

**Severity:** HIGH DATA INTEGRITY BEFORE M5  
**Status:** OPEN

The status says the new snapshot composition currently obtains:

```text
gateway.account()
+
gateway.account_extras()
```

and `account_extras()` performs a **second `account_info()` call**.

That means one stored `BrokerAccountSnapshot` can theoretically combine values observed at two different moments.

For example around a fill:

```text
call 1:
balance/equity = state A

trade/fill changes account

call 2:
profit/margin fields = state B
```

The database would then hold one row that never existed at the broker.

### Preferred fix

Perform one MT5 `account_info()` read for one broker observation and derive:

```text
AccountState
+
snapshot extras
```

from that same raw observation.

Alternative designs are acceptable only if the fields have separate observation timestamps and are never presented as one coherent broker snapshot.

For M5, the preferred single-read model is substantially simpler.

---

# 6. Broker account fields — confirm the complete M5 set

Review 1.15 requested more than `balance` and `equity`.

Before F-047 is considered execution-ready, verify that the durable account observation contains or can reliably derive:

```text
balance
equity
profit
margin
free margin / margin_free
margin level
currency
leverage
margin/account mode
account trade permission
terminal trade permission
server/environment/account fingerprint
```

If Pepperstone/MT5 does not supply one of these in a particular state, represent that explicitly.

Do not invent zero.

All monetary values stay Decimal/NUMERIC.

This is not a request for dashboard polish; these values are inputs to risk and operational truth.

---

# 7. Reconciliation is now the immediate engineering priority

F-047 has created the observed side.

Now build:

```text
Expected Platform State
         ↕
Reconciliation
         ↕
Observed Broker Snapshot
```

Minimum reconciliation v0:

```text
account identity
server/environment
currency/leverage/account mode
EUR/USD symbol/spec
open positions
pending orders
```

Output exactly:

```text
MATCHED
MISMATCHED
UNKNOWN
```

## Fail-closed rules

```text
missing observation      → UNKNOWN
stale observation        → UNKNOWN
FAILED position set      → UNKNOWN
FAILED pending-order set → UNKNOWN
unexpected position      → MISMATCHED
missing expected position→ MISMATCHED
unexpected pending order → MISMATCHED
wrong account/server     → MISMATCHED
```

A mismatch should trip HALT.

UNKNOWN must never be upgraded into MATCHED by the dashboard, Agent, or Supervisor.

---

# 8. Reconciliation expected-state semantics must be explicit

Before orders exist, expected state is simple:

```text
expected positions = none
expected pending orders = none
```

Therefore the first real reconciliation run against the currently flat demo account can already prove:

```text
observed COMPLETE positions = 0
observed COMPLETE pending orders = 0
correct account
correct instrument
→ MATCHED
```

Once execution exists, expected state must come from the platform's durable execution/order history, not from whatever the latest MT5 snapshot says.

Otherwise reconciliation becomes:

> compare MT5 to MT5.

That detects nothing.

---

# 9. F-048 — live/shadow Agent pipeline is still GO

The project should now move quickly toward:

```text
real closed Pepperstone M5 bar
        ↓
feature pipeline
        ↓
Trading Agent
        ↓
BUY / SELL / NO_TRADE
        ↓
intent-time deterministic Risk Engine
        ↓
Supervisor
        ↓
persist everything
        ↓
EXECUTION DISABLED
```

The reviewer does **not** require the execution adapter before this.

Build it as a separate `LiveDecisionOrchestrator`/equivalent rather than turning `LiveReader` into a trading process.

Keep:

```text
LiveReader = observe/persist broker + market
Decision Orchestrator = decide
Execution service = later execute
```

---

# 10. Close D-031 before calling live shadow evidence audit-quality

The feature hash alone is no longer enough once real market decisions begin.

Persist the actual feature snapshot or an equivalent reconstructible feature record containing:

```text
feature values
feature schema/version
input window identity
source market-bar identities
occurred_at_utc
```

Then a real NO_TRADE/BUY/SELL can be explained from persisted data without re-running code that may later change.

A first wiring test may occur before this.

A meaningful live-shadow evidence run may not.

---

# 11. CI and M0 — no longer allowed to drift

The repository is committed/pushed and the status says nothing external blocks CI.

Yet CI is still marked as never executed.

Run it now.

It can run in parallel with reconciliation engineering.

It must not remain open until the execution adapter is ready.

Required evidence:

```text
commit SHA
Linux result
Windows result
PostgreSQL result
gitleaks
unexpected skips
overall workflow result
```

Also provide the actual domain-contract package for reviewer approval.

M0 must be closed before `feedback.2.0`.

---

# 12. Dashboard — only operational additions now

The owner's DB request can now naturally become dashboard data, but only through the new persisted store.

Once real-MT5 validation succeeds, add:

```text
ACCOUNT
Balance
Equity
Open P/L
Margin
Free margin

BROKER BOOK
Open positions
Pending orders
Snapshot age
Completeness

RECONCILIATION
MATCHED / MISMATCHED / UNKNOWN
```

No visual redesign.

No direct MT5 import.

---

# 13. Progress toward the first autonomous DEMO canary

Current critical-path state:

```text
M1 real read/reconnect             ✅
M2 durable market/event data       ✅
modern read-only dashboard         ✅
broker account snapshots           ✅ implemented
broker position snapshots          ✅ implemented
broker pending-order snapshots     ✅ implemented

real F-047 MT5 verification        ⏳
CI                                 ❌
domain-contract approval           ❌
reconciliation                     ❌
live Agent decision pipeline       ❌
feature-value persistence          ❌
execution adapter/order_check      ❌
final execution-time Risk          ❌
automatic flatten                  ❌
owner risk policy                  ❌
feedback.2.0                       ❌
first DEMO canary order            🚫
```

This is a materially shorter list than at review 1.15.

---

# 14. Owner decisions can now be prepared in parallel

Do not stop engineering while waiting for these, but they must be resolved before `feedback.2.0`:

```text
risk per trade
max daily loss
max drawdown
last-entry cutoff
mandatory flatten deadline
HALT-reset authority
```

The reviewer recommends bringing these to the owner once reconciliation and live shadow wiring are in progress, rather than waiting until the execution adapter is finished.

---

# 15. Gate decisions

## M0
**GO WITH CONDITIONS — CLOSE BEFORE feedback.2.0**

## M1
**PASSED**

## M2
**PASSED**

## F-047
**IMPLEMENTED / TESTED — REAL MT5 VALIDATION PENDING**

## Reconciliation
**GO NOW**

## F-048 Live Shadow Agent
**GO NOW, AFTER/ALONGSIDE RECONCILIATION**

Execution remains physically unavailable.

## M5
**NO-GO**

## First DEMO order
**NO-GO until feedback.2.0**

---

# 16. Required next action order

```text
1. Process feedback.1.16.md.
2. Fix F-052: one coherent account_info observation per broker snapshot.
3. Define F-050 BrokerStateHealth/freshness semantics.
4. Run F-047 once against the real Pepperstone terminal.
5. Verify COMPLETE empty position + pending-order sets and real account values.
6. Build read-only reconciliation from broker-state snapshots.
7. Prove flat real demo account → MATCHED.
8. Prove stale/failed snapshot → UNKNOWN; injected mismatch → MISMATCHED + HALT.
9. Run hosted CI in parallel and record result.
10. Supply actual domain contracts for reviewer approval; close M0.
11. Build F-048 live shadow decision orchestrator.
12. Persist feature values / close D-031.
13. Run real Pepperstone M5 → Agent → Risk → Supervisor with EXECUTION DISABLED.
14. Add persisted balance/equity/positions/reconciliation to Dashboard.
15. Obtain owner risk/intraday/HALT-reset policy decisions.
16. Begin execution adapter/order_check/final-risk/flatten work.
17. Prepare feedback.2.0.
18. No order_send before feedback.2.0 GO.
```

---

# 17. Next review

Next regular review:

```text
feedback.1.17.md
```

Preferred trigger is a meaningful bundle:

```text
real F-047 validation
+ reconciliation
+ CI/M0 closure
and/or
first live-shadow Agent decisions on real M5
```

Do not wait for every item if a real integration defect appears; those are useful review triggers too.

---

# 18. Final reviewer statement

The project is now moving toward trading rather than merely preparing to move toward trading.

The broker-state persistence work is a meaningful milestone because the platform can finally start answering three different questions independently:

```text
What does the market look like?
What does the broker account look like?
What does my own platform think should exist?
```

Reconciliation joins the last two.

The live shadow Agent joins the first one to the decision system.

Once both exist, the remaining path to the first DEMO order becomes execution engineering rather than architectural uncertainty.

Keep moving.
