# feedback.1.13.md — Dashboard Visual Direction, M0 Closure & Reconciliation Priority

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.13  
**Date:** 2026-08-24  
**Reviewed artifact:** `status.md` v1.4 (`status(7).md`)  
**Previous review:** `feedback.1.12.md`  
**Overall verdict:** **GO — M1 REMAINS PASSED; DASHBOARD FUNCTIONAL BOUNDARY ACCEPTED; VISUAL ITERATION REQUIRED**  
**M0 verdict:** **GO WITH CONDITIONS — CI + DOMAIN CONTRACT REVIEW STILL OPEN**  
**M1 verdict:** **PASSED / MT5-INTEGRATED**  
**M2 verdict:** **PASSED**  
**Dashboard v0 verdict:** **FUNCTIONALLY ACCEPTED, NOT YET OWNER-READY VISUALLY**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review is based on the supplied `status.md` and its stated evidence. The dashboard itself was not visually inspected by the reviewer; only the documented implementation, test evidence and owner feedback on its current appearance were available.

---

## 1. Executive review

Review 1.12 has been processed correctly.

Since that review, the project reportedly closed:

```text
F-039 semantic instrument-spec identity
F-040 stale/implausible broker-clock reference handling
F-041 soak database reset via Alembic only
```

and built Dashboard v0.

The full quality gate now reports:

```text
ruff     clean
mypy     clean
pytest   737 passed
         3 explained skips
         0 failures
```

The dashboard also has a strong safety boundary:

```text
GET /              read-only HTML
GET /api/state     read-only JSON

no POST/PUT/PATCH/DELETE routes
no MetaTrader5 import
no crumblr.mt5_gateway import
no broker credentials
no order controls
```

This functional boundary is accepted.

The owner has now made a new requirement explicit:

> The dashboard must evolve from a functional status page into a modern, clear trading-operations interface resembling the reviewer's earlier example.

That is approved.

The next cycle therefore has three parallel goals:

```text
A. improve Dashboard UX/visual quality
B. close M0 (CI + contract review)
C. begin read-only reconciliation
```

Dashboard polish may proceed, but it must not delay reconciliation or reopen the execution boundary.

---

# 2. Review 1.12 findings

## F-039 — semantic instrument identity

**Status:** CLOSED BASED ON DOCUMENTED EVIDENCE

The project correctly separated live `tick_value` movement from semantic contract identity.

A reconnect should no longer manufacture a false specification-change incident merely because market-dependent values or observation time changed.

---

## F-040 — broker-clock detection fail-closed

**Status:** CLOSED BASED ON DOCUMENTED EVIDENCE

Clock-offset detection now rejects implausible measurements rather than caching them.

The reader maps this to a recoverable disconnected/stale condition rather than silently corrupting timestamp semantics.

---

## F-041 — soak database reset path

**Status:** CLOSED BASED ON DOCUMENTED EVIDENCE

`reset_soak_database.py` now keeps the reset on the Alembic path and refuses an obviously non-soak database.

Good.

---

# 3. Dashboard v0 architectural review

## Functional verdict: ACCEPTED

The chosen architecture is suitable:

```text
FastAPI
+ server-rendered Jinja2
+ separate dashboard package
+ PostgreSQL/read-only health sources
```

Keeping it outside the future authenticated/mutating API package is a sensible separation.

The dashboard reportedly reads:

```text
MarketDataStore
EventJournal
PostgresSafetyStateStore
LiveReader health JSON
```

and never talks directly to MT5.

This is the correct boundary.

### Keep this permanently

The visual redesign must **not** turn Dashboard v0 into a trading terminal.

No visual improvement may introduce:

```text
BUY
SELL
CLOSE POSITION
HALT RESET
CANCEL ORDERS
risk config editors
broker credentials
direct MT5 calls
```

A polished read-only interface is still read-only.

---

# 4. F-042 — Dashboard needs an explicit modern visual design baseline

**Severity:** MEDIUM / OWNER EXPERIENCE  
**Status:** OPEN  
**Blocks:** calling Dashboard v0 “owner-ready”; does not block M0/M1

The dashboard currently satisfies the functional data checklist, but no visual QA or design acceptance criterion exists.

The owner explicitly wants a more modern environment resembling the reviewer's earlier dashboard concept.

This is now a real requirement, not optional polish.

## Required visual direction

Think:

```text
modern trading operations console
not
developer debug page
```

### General appearance

Use a **dark, restrained, professional** interface.

Characteristics:

```text
dark charcoal/navy background
slightly lighter cards
subtle borders
generous spacing
clear typographic hierarchy
minimal decorative effects
small status badges
high information density without looking cramped
```

Avoid:

```text
raw HTML-table appearance
large walls of text
default browser styling
bright rainbow colours
oversized headings
developer/debug vocabulary as the main interface
```

The page should feel closer to:

```text
a modern broker/market-data dashboard
+
an infrastructure health console
```

than to an internal test report.

---

# 5. Dashboard target layout

One responsive desktop page is enough.

## Top navigation / identity bar

Left:

```text
CRUMBLR
EUR/USD Autonomous Trading Platform
```

Right, permanently visible:

```text
DEMO
READ ONLY
EXECUTION DISABLED
```

`EXECUTION DISABLED` must never be visually subtle.

---

## Row 1 — system status cards

Four compact cards:

```text
MT5
CONNECTED / DISCONNECTED

DATA FEED
HEALTHY / STALE / DOWN

SAFETY
RUNNING / HALTED / UNKNOWN

MILESTONE
M1 PASSED
```

Each card should have:

```text
large state
small supporting value
last-change/update time where useful
```

Example:

```text
MT5
● CONNECTED
Last reconnect 16:10 UTC
```

Do not rely on colour alone; include text/icon/state.

---

## Row 2 — EUR/USD hero area

This should be the visual centre of the page.

### Left / main

```text
EUR/USD
1.17xxx / 1.17xxx
BID        ASK
Spread: x.x pip
Last tick: 1.6s ago
```

Prices should be large enough to scan immediately.

### Main chart

Display recent **closed M5 bars**.

Preferred:

```text
candlestick chart
```

Acceptable first iteration:

```text
clean close-price line chart
```

But the chart should occupy meaningful space rather than being a tiny diagnostic sparkline.

Suggested visible history:

```text
30–60 recent M5 bars
```

Use persisted PostgreSQL data, not direct MT5 calls.

---

# 6. Row 3 — operating health

Cards/panels:

### Connection

```text
Server
PepperstoneUK-Demo

Reconnects
2

Broker clock
UTC +180m observed / normalized

Last gateway error
—
```

### Data integrity

```text
Tick quality
GOOD

Latest tick age
1.6s

M5 gaps
0

Bar anomalies
0
```

### Account context

```text
Broker
Pepperstone

Entity
Pepperstone Limited (UK) — DEMO

Mode
RETAIL_HEDGING

Currency
EUR

Leverage
1:30
```

Never display the broker login/account number.

If a value is not persisted, display:

```text
NOT AVAILABLE
```

not a guessed value.

---

# 7. Row 4 — decision pipeline

This is important because the owner should eventually be able to understand:

> What is the machine thinking?

Represent the latest window as a horizontal pipeline:

```text
TRADING AGENT
    ↓
RISK ENGINE
    ↓
SUPERVISOR
    ↓
EXECUTION
```

For now execution ends visibly in:

```text
DISABLED
```

Each stage gets one compact card.

Example:

```text
TRADING AGENT
NO_TRADE
reason: no valid setup

RISK ENGINE
NO ACTION

SUPERVISOR
NO ACTION
uncalibrated: signal_frequency, confidence_band

EXECUTION
DISABLED
```

When there is no TradeIntent, the screen must not look broken or empty.

`NO_TRADE` is a valid decision and should look intentional.

---

# 8. Row 5 — activity/event timeline

A compact table at the bottom:

```text
TIME       COMPONENT     EVENT                  RESULT
21:10:00   LiveReader    tick received          GOOD
21:10:00   MarketData    M5 bar persisted       GOOD
21:10:05   Supervisor    decision               NO ACTION
...
```

Use the existing event journal.

Show only recent events by default.

Do not dump full JSON payloads into the primary UI.

A future detail drawer is acceptable, but not needed now.

---

# 9. Visual-state semantics

Use consistent state language.

## Healthy

```text
CONNECTED
HEALTHY
RUNNING
GOOD
MATCHED
```

## Warning

```text
STALE
UNCALIBRATED
DEGRADED
```

## Unsafe/blocked

```text
DISCONNECTED
HALTED
UNKNOWN
MISMATCHED
EXECUTION DISABLED
```

Do not show a green card with a warning buried in small text.

The most conservative state should dominate visually.

---

# 10. Responsive / implementation expectations

This is still v0.x.

Do not build a JavaScript application framework merely to make it attractive.

FastAPI + Jinja2 remains acceptable.

Use:

```text
HTML
CSS
small amount of vanilla JavaScript if needed for refresh/chart rendering
```

A lightweight chart library is acceptable if it remains purely client-side presentation and does not introduce trading/control behavior.

### Auto-refresh

Preferred:

```text
5-second refresh/poll for state
```

without full-page flicker.

If JavaScript polling is added:

```text
GET /api/state only
```

No mutation endpoint.

---

# 11. F-043 — Dashboard stale-data presentation must be explicit

**Severity:** HIGH UX/SAFETY  
**Status:** OPEN

A polished dashboard creates a new risk:

> Old data can look convincingly live.

Therefore visual freshness is safety-relevant.

### Required

The dashboard must prominently display:

```text
last tick age
last M5 bar time
dashboard snapshot time
LiveReader health
```

If the data is stale:

```text
STALE
```

must visibly replace the healthy/live presentation.

Do not keep displaying a normal-looking price panel with only a tiny old timestamp.

### Acceptance

Tests should establish presentation/state behavior for:

```text
fresh data
stale data
reader disconnected
reader health snapshot missing
database unavailable
```

At least the dashboard state model must distinguish these explicitly.

---

# 12. F-044 — UI must not invent “live platform decisions” from replay-only events

**Severity:** HIGH SEMANTIC INTEGRITY  
**Status:** OPEN

The current dashboard reads latest Signal/Risk/Supervisor events from the journal.

At this stage, the real MT5 `LiveReader` and the replay decision orchestrator remain distinct.

Therefore the dashboard must not accidentally combine:

```text
real Pepperstone price
+
old/synthetic replay decision
```

into a screen that visually implies:

> “This is the current decision for this live price.”

### Required

Every displayed decision must clearly show its context:

```text
environment/source
occurred_at_utc
strategy/version
correlation/window id
```

If there is no decision associated with the current real-data stream, label it honestly, for example:

```text
LATEST REPLAY DECISION
```

or:

```text
NO LIVE DECISION PIPELINE ACTIVE
```

Do not label historical replay decisions simply as “Latest Decision” beside a real live price unless they genuinely belong to the same run/context.

This becomes increasingly important once the dashboard looks professional, because a polished UI makes semantic ambiguity more convincing.

---

# 13. Documentation/current-state cleanup

F-033 is improved, but stale statements still remain.

Examples include current sections saying:

```text
Component 1 last meaningful update = review 1.10
Next objective = run the real soak
```

even though M1 is passed and Dashboard v0 has shipped.

Other present-tense entries still claim some capabilities have never met the real broker despite Phase A/B.

### Required

Perform one focused current-state cleanup.

Do not keep repeatedly fixing individual stale lines after every review.

Prefer a small set of authoritative current-state sections generated/updated from one checklist.

This remains documentation quality, not a gate rollback.

---

# 14. M0 — stop deferring CI

M0 is now the oldest unfinished gate.

Remaining:

```text
CI runner result
domain-contract human/reviewer review
```

The codebase now has:

```text
remote repository
Windows host
real MT5 evidence
737 passing local tests
```

There is no longer a useful reason to keep CI as “never run”.

### Priority

Run the existing CI workflow now and record:

```text
Linux job
Windows job
PostgreSQL-backed tests
gitleaks
overall result
```

A CI failure is useful evidence; do not work around it.

---

# 15. Domain contract review

Provide the current domain contract definitions or a generated contract summary in the next review package.

At minimum include:

```text
MarketSnapshot
Bar
InstrumentSpec
TradeIntent
RiskDecision
SupervisorDecision
ApprovedOrder
ExecutionResult
AccountState
PositionState
Incident
DecisionCapsule
```

The reviewer will evaluate ownership, mutability, forbidden fields and execution boundaries.

This should close the final human-review part of M0.

---

# 16. Reconciliation remains the next safety-critical engineering task

Visual improvement is approved, but the platform's next important safety capability is still read-only reconciliation.

Required model:

```text
platform expected state
        vs
real MT5 observed state
```

Result:

```text
MATCHED
MISMATCHED
UNKNOWN
```

Unknown or mismatch is never promoted to MATCHED by presentation/UI logic.

Dashboard should eventually display this state prominently once it exists.

Do not build order execution merely to test reconciliation.

---

# 17. Gate decisions

## M0
**GO WITH CONDITIONS — RUN CI + CONTRACT REVIEW**

## M1
**PASSED / MT5-INTEGRATED**

No change.

## M2
**PASSED**

No change.

## M3
**CORRECTNESS / EVIDENCE WORK**

## M4
**REPLAY-TESTED**

## M5
**NO-GO**

## M6
**FEATURE FREEZE**

## M7
**SAFETY WORK ONLY**

## Dashboard v0
**FUNCTIONALLY ACCEPTED**

## Dashboard owner-facing visual quality
**ITERATE NOW**

## P2
**NO-GO**

---

# 18. Required next action order

```text
1. Process feedback.1.13.md.
2. Keep the existing dashboard read-only boundary unchanged.
3. Implement the modern dashboard visual baseline in §§4–10.
4. Fix F-043 stale-data presentation.
5. Fix F-044 decision-context ambiguity.
6. Run actual CI and record the result.
7. Provide domain contracts for human/reviewer approval; close M0.
8. Build read-only reconciliation against real MT5.
9. Add reconciliation state to the dashboard once it exists.
10. Clean the remaining stale current-state lines in status.md.
11. Decide owner risk/intraday/HALT-reset policies before M5.
12. Keep AlgoTrading OFF.
13. No execution adapter/order_send.
14. feedback.1.14.md after dashboard visual iteration + CI/contracts/reconciliation evidence.
15. feedback.2.0.md before any first order.
```

---

# 19. Owner visual acceptance checklist

Dashboard v0.x is owner-ready when the owner can open one page and answer within five seconds:

```text
Is MT5 connected?
Is the data fresh?
Is the system safe?
What is EUR/USD doing?
When was the last tick?
Has the connection recently failed?
What did the agent/risk/supervisor last decide?
Is execution enabled?  → visibly NO
```

And within fifteen seconds:

```text
Which broker/server is this?
Are there data gaps/errors?
What was the last reconnect/error?
What are the latest closed M5 bars?
Are displayed decisions actually from this live context or from replay?
```

If those answers require reading raw text blocks or JSON, the visual iteration is not done.

---

# 20. Final reviewer statement

The developer has built the correct read-only foundation for the dashboard.

Now improve the presentation without weakening that foundation.

The target is not a flashy trading terminal.

It is a calm, modern operations cockpit where the owner can instantly see:

```text
market
health
safety
decision flow
```

and where stale, unknown or disabled states are impossible to mistake for healthy live operation.

Modernise the interface.

Do not turn it into a control surface.
