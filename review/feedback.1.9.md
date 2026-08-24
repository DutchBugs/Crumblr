# feedback.1.9.md — Real MT5 Data, M1 Completion & Visual Dashboard v0

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.9  
**Date:** 2026-08-24  
**Reviewed artifact:** `status.md` v1.4  
**Previous reviews:** `feedback.1.7.md`, `feedback.1.8.md`  
**Overall verdict:** **GO — CONTINUE FORWARD**  
**M1 verdict:** **FIRST CONTACT PASSED; COMPLETE CONTINUOUS READ + RECONNECT NOW**  
**M2 verdict:** **PASSED ON ITS OWN ACCEPTANCE CRITERIA**  
**M5 / P2 verdict:** **NO-GO**  
**New owner requirement:** **BUILD A SIMPLE READ-ONLY VISUAL DASHBOARD V0**

---

## 1. Executive review

This is the strongest development update so far.

The project has now crossed an important boundary:

```text
documentation/fake MT5
→
real Pepperstone MT5 observation
```

The first real connection succeeded.

Observed from the actual Pepperstone terminal:

```text
environment        DEMO
server             PepperstoneUK-Demo
company            Pepperstone Limited
account mode       RETAIL_HEDGING
currency           EUR
leverage           1:30
broker symbol      EURUSD
symbol suffix      none
trade mode         FULL
filling mode       IOC
open positions     0
account guard      PASS
```

The MT5 enum/filling-mode defect identified before first contact was real, was confirmed against the terminal, and was corrected with shared decode logic.

The full Windows/PostgreSQL suite reportedly passes:

```text
666 passed
3 skipped
0 failures
```

All three skips are documented as platform-specific.

The project is progressing correctly.

The next engineering priority is no longer preparation. It is:

```text
continuous real market read
→ persistence
→ reconnect/recovery
→ M1 completion
```

A simple read-only visual dashboard is now also approved as a parallel product-facing task, provided it does not delay or weaken M1.

---

## 2. Existing review findings

### F-026 — demo-account state

**Status:** CLOSED

The status now records that the demo account exists and credentials remain local-only.

### F-027 — M2 gate semantics

**Status:** CLOSED

The developer compared M2 against `build.md`.

Real-feed evidence is not an M2 acceptance criterion.

Therefore the reviewer accepts:

```text
M2 = PASSED on its own acceptance evidence
```

Real feed belongs to M1.

### F-028 — Pepperstone entity ambiguity

**Status:** RESOLVED FOR THE CURRENT DEMO ENVIRONMENT

The observed terminal reports:

```text
company = Pepperstone Limited
server  = PepperstoneUK-Demo
```

Official Pepperstone legal information identifies **Pepperstone Limited** as its UK entity, while **Pepperstone EU Limited** is the separate Cyprus/CySEC entity.

#### Owner/reviewer decision O-005

For the current development and demo environment:

```text
Broker brand:          Pepperstone
Demo legal entity:     Pepperstone Limited (UK)
Demo server:           PepperstoneUK-Demo
Environment:           DEMO
```

This amends the earlier shorthand “Pepperstone EU demo” for the **current demo environment only**.

It does **not** pre-select the legal entity for a future live account.

Before any live account is opened or funded, legal/entity eligibility must be reviewed again based on the owner's residence and the actual live-account documentation.

D-034 / APP-013 may be closed for M1 demo integration with this scoped decision.

Do not rewrite history; record that O-001 was refined by O-005 after real terminal evidence.

### F-029 — paper-campaign header

**Status:** CLOSED

Broker and server are now populated without exposing account credentials.

### F-030 — full Windows gate with PostgreSQL

**Status:** CLOSED BASED ON DOCUMENTED EVIDENCE

Reported first:

```text
663 passed / 3 skipped
```

and after first-contact fixes:

```text
666 passed / 3 skipped
```

No regression is reported.

### F-031 — first-contact evidence sanitization

**Status:** CLOSED BASED ON DOCUMENTED EVIDENCE

The probe now supports sanitized output and the raw evidence path is git-ignored.

Keep this model:

```text
raw evidence
→ local only

sanitized evidence
→ review/status/test fixture
```

### F-032 — MT5 enum decoding

**Status:** CLOSED BASED ON REAL TERMINAL OBSERVATION

Observed:

```text
filling_mode = 2 → IOC
trade_mode   = 4 → FULL
```

Shared decode logic is now used by both gateway and probe.

---

## 3. New findings

### F-033 — status.md current-state sections still lag behind the first-contact update

**Severity:** MEDIUM / DOCUMENTATION  
**Status:** OPEN

The chronological update log records:

```text
first contact succeeded
Q2 = RETAIL_HEDGING
EURUSD resolved
account guard passed
D-037 closed
```

but earlier/current sections still contain stale items such as:

```text
Q2 unchecked
"run the probe" as next action
MT5 checklist entries still unchecked
```

#### Required update

The top/current sections must reflect the latest state.

At minimum update:

```text
M1 dependency checklist
Component 1 current objective
MT5 checklist
Next actions
current risk descriptions
```

Do not alter historical update-log entries.

---

### F-034 — reconnect must revalidate broker/account truth before data flow resumes

**Severity:** HIGH  
**Status:** OPEN  
**Blocks:** M1 qualification

Continuous read is not complete if reconnect simply reconnects the socket/terminal and continues.

After every meaningful reconnect, the gateway must re-read and verify:

```text
demo/live environment
server
account identity
currency
leverage
account mode
resolved EURUSD symbol
instrument spec/version
trade permissions
terminal health
```

If a safety-relevant value differs or cannot be established:

```text
UNKNOWN
→ HALT / data service unhealthy
→ no automatic return to healthy
```

The system must never assume that reconnecting means reconnecting to the same account or same instrument contract.

#### Required tests

At minimum:

```text
normal disconnect → reconnect → same account → recover

reconnect → wrong server/account
→ fail closed

reconnect → symbol spec changed
→ detect + record new spec version
→ no silent continuation

reconnect → no tick data
→ stale/unhealthy

terminal restart
→ reconnect
→ full account guard re-run
```

---

### F-035 — UI v0 must remain read-only and outside the broker execution boundary

**Severity:** HIGH DESIGN  
**Status:** OPEN UNTIL UI V0 IS BUILT

The owner explicitly wants a simple visual version accessible through VS Code port forwarding.

This is approved.

However, the first visual interface must not create a new execution path.

#### Hard boundary

```text
Dashboard
→ reads PostgreSQL / read-only application status
→ displays state

Dashboard
✗ must not import MetaTrader5
✗ must not hold broker credentials
✗ must not call order_send
✗ must not reset HALT
✗ must not modify risk configuration
✗ must not create TradeIntent
```

For v0 there are **no trading/control buttons**.

The UI is an observation surface only.

---

## 4. APP-016 — terminal AlgoTrading state

The terminal reported:

```text
terminal.trade_allowed = false
account.trade_allowed  = true
account.trade_expert   = true
```

### Reviewer/owner decision

**Do not enable AlgoTrading yet.**

M1 is read-only.

Keeping terminal-level algorithmic trading disabled is an additional safety layer while the project has no approved execution path.

Record APP-016 as:

```text
KNOWN / DEFERRED TO M5 READINESS
```

Before M5, execution readiness must explicitly require:

```text
account trade permission = true
terminal trade permission = true
expected demo account = verified
execution adapter = explicitly enabled
feedback.2.0 = GO
```

Turning the UI toggle on by itself must never be sufficient to enable trading.

---

## 5. Primary engineering priority — complete M1

The answer to the developer's question is:

> **Build continuous read/reconnect first.**

The entity question is now sufficiently resolved for this demo environment and should not consume the main development cycle.

### Required M1 continuous-read behavior

Implement a long-running read-only process/service that:

```text
connects to MT5
validates account
discovers/validates EURUSD
reads ticks continuously
reads/backfills M5 bars
persists raw ticks/bars
updates instrument spec when required
tracks data freshness
detects gaps/out-of-order data
reports gateway health
reconnects after interruption
revalidates everything after reconnect
```

### First operational evidence

Run a controlled read-only soak during an active FX session.

Evidence should include:

```text
number of ticks
number of M5 bars
last tick age
data gaps
duplicate handling
reconnect count
gateway errors
account-guard results
instrument-spec changes
```

Also perform at least one deliberate interruption:

```text
terminal/network interruption
→ reader detects failure
→ unhealthy/stale
→ reconnect
→ full revalidation
→ resumes only if safe
```

---

## 6. Visual Dashboard v0 — approved owner requirement

The owner wants a visual surface now.

This is approved as a **small, read-only M8 preview**, not as a full dashboard milestone.

### Recommended implementation

A lightweight Streamlit application is acceptable for v0 because it is fast to build, Python-native and easy to expose via VS Code port forwarding.

Suggested development binding:

```text
127.0.0.1:8501
```

Expose it through VS Code port forwarding rather than opening a public network listener.

If the developer has a strong reason to use another lightweight web stack, that is acceptable; the architectural boundaries matter more than the framework.

---

## 7. Dashboard v0 — minimum screen

One page is enough.

### Header

Always display prominently:

```text
CRUMBLR — DEMO / READ ONLY
EXECUTION DISABLED
```

### A. System health cards

Show:

```text
MT5 connected       YES / NO
Data feed           HEALTHY / STALE / DOWN
Safety state        RUNNING / HALTED / UNKNOWN
Environment         DEMO
M1 state            IN PROGRESS / READY
Last update UTC
```

### B. Broker/account — sanitized

Show:

```text
Broker              Pepperstone
Entity              Pepperstone Limited (UK) — DEMO
Server              PepperstoneUK-Demo
Currency            EUR
Leverage            1:30
Account mode        RETAIL_HEDGING
Account number      NEVER DISPLAY
```

### C. EUR/USD live panel

Show:

```text
bid
ask
spread
last tick UTC
tick age
current M5 OHLC
```

Optional but useful:

- simple recent bid/ask line chart;
- recent M5 candlestick chart if trivial to implement.

Do not let chart work delay the actual reader.

### D. Decision pipeline

Show the most recent decision window:

```text
Signal / NO_TRADE
TradeIntent
Risk result
Supervisor result
Safety result
reason codes
```

The dashboard should make `NO_TRADE` visible, not look empty when no trade exists.

### E. Recent events

A simple table:

```text
time
event
component
result
reason
correlation id shortened
```

### F. Diagnostics

Show:

```text
reconnects
data gaps
last gateway error
uncalibrated supervisor checks
open positions
current HALT reason
```

---

## 8. Dashboard data-source rule

Preferred flow:

```text
MT5 continuous reader
        ↓
typed application state/events
        ↓
PostgreSQL
        ↓
dashboard
```

Avoid:

```text
dashboard
→ direct MT5 calls
```

The dashboard should show what the platform has recorded, because that is also what can later be audited.

A small read-only health snapshot endpoint is acceptable if PostgreSQL alone cannot express transient connection state, but the dashboard itself must never own the MT5 session.

---

## 9. What can be built in parallel

The developer may structure the next cycle as two workstreams:

```text
WORKSTREAM A — gate-critical
continuous MT5 read + reconnect + persistence

WORKSTREAM B — owner visibility
read-only dashboard v0
```

Rule:

```text
B may not delay A.
```

The dashboard becomes useful as soon as Workstream A starts writing real ticks.

---

## 10. Commit / CI decision

The status says the latest tested work is held pending owner go-ahead.

### Owner/reviewer recommendation

**Commit and push the tested first-contact work now.**

Before commit verify:

```text
.env not tracked
raw first-contact JSON not tracked
sanitized evidence contains no account login
Git identity correct
```

Then let CI run and record its actual result.

There is little value in keeping this major verified checkpoint uncommitted now that the repository is intentionally shared across two machines.

---

## 11. M0 cleanup

M0 should be closed soon rather than remain the nominal current milestone forever.

Still required:

```text
actual CI result
human/reviewer domain-contract approval
```

Do these alongside M1 work.

They should not stop continuous MT5 read development.

---

## 12. Gate decisions

### M0
**Verdict:** ALMOST READY TO CLOSE

Run CI and complete domain-contract review.

### M1
**Verdict:** FIRST CONTACT PASSED — CONTINUE NOW

Complete continuous read, real persistence and reconnect/revalidation.

### M2
**Verdict:** PASSED

Accepted on `build.md`'s own acceptance criteria.

### M3
**Verdict:** CORRECTNESS ONLY

### M4
**Verdict:** REPLAY-TESTED / BROKER VALIDATION STARTING

### M5
**Verdict:** NO-GO

### M6
**Verdict:** FEATURE FREEZE

### M7
**Verdict:** SAFETY WORK ONLY

### Dashboard v0
**Verdict:** GO — READ ONLY

### P2
**Verdict:** NO-GO

---

## 13. Required next action order

```text
1. Process feedback.1.9.md.
2. Record O-005: current demo = Pepperstone Limited (UK); live entity undecided.
3. Fix stale status sections (F-033).
4. Commit/push the tested first-contact changes.
5. Run/record CI.
6. Build continuous MT5 tick + M5 bar reader.
7. Persist real Pepperstone data immediately.
8. Implement reconnect + full post-reconnect revalidation (F-034).
9. Run controlled read-only soak + deliberate interruption test.
10. Build Dashboard v0 against persisted/read-only platform state (F-035).
11. Expose dashboard only through localhost + VS Code port forwarding.
12. Build real reconciliation.
13. Prepare domain contracts for reviewer approval and close M0.
14. Do not enable AlgoTrading yet; defer APP-016 to M5 readiness.
15. feedback.2.0 before any execution adapter/order_send.
```

---

## 14. What not to do next

Do not:

```text
enable real order execution
turn the dashboard into a trading console
add buy/sell buttons
tune ICT from a few hours of real data
start ICT v2
add another market
add another broker
```

The project now needs reliability and observable real-data operation.

---

## 15. Next review

Next regular review:

```text
feedback.1.10.md
```

Recommended trigger:

- continuous read/reconnect evidence is available;
- first real ticks/bars are persisted;
- dashboard v0 exists;
- CI result and/or M0 contract package is available.

The major execution review remains:

```text
feedback.2.0.md
```

before the first `order_send`, including demo.

---

## 16. Final reviewer statement

This project has now proven something materially different from all prior cycles:

> It can connect to a real broker terminal and correctly discover the account and instrument it was designed for.

The next proof is operational rather than architectural:

```text
Can it stay connected?
Can it detect when it is not?
Can it recover safely?
Can it persist exactly what it saw?
Can the owner see all of that clearly?
```

Continuous read/reconnect answers the first four.

Dashboard v0 answers the fifth.

Build both, in that order of priority.
