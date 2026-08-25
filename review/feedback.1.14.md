# feedback.1.14.md — Dashboard Visual Acceptance & Operational Semantics

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.14  
**Date:** 2026-08-25  
**Reviewed artifacts:** `status(8).md` + owner-provided Dashboard v0.1 screenshot  
**Previous review:** `feedback.1.13.md`  
**Overall verdict:** **GO — DASHBOARD VISUAL DIRECTION ACCEPTED; MOVE PRIMARY ENGINEERING FOCUS TO CI / M0 / RECONCILIATION**  
**M0 verdict:** **GO WITH CONDITIONS — CI + DOMAIN CONTRACT REVIEW STILL OPEN**  
**M1 verdict:** **PASSED / MT5-INTEGRATED**  
**M2 verdict:** **PASSED**  
**Dashboard v0.1 verdict:** **VISUALLY ACCEPTED WITH SEMANTIC CLEANUP**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** Source code and CI output were not independently inspected. The dashboard was visually inspected from the screenshot supplied by the owner; runtime interactions were not independently exercised by the reviewer.

---

## 1. Executive review

Review 1.13 has produced the intended result.

Dashboard v0.1 now visibly resembles a modern trading-operations interface rather than a developer status dump.

The screenshot demonstrates:

```text
dark ops-console visual language
persistent READ ONLY / EXECUTION DISABLED state
four headline health cards
EUR/USD bid/ask hero
M5 candlestick chart
connection / integrity / account panels
decision-pipeline cards
clear red / amber / green state semantics
```

The implementation report additionally states that:

```text
F-042 visual baseline       closed
F-043 stale presentation    closed
F-044 replay/live context   closed
read-only boundary          unchanged
pytest                      748 passed / 3 explained skips
mypy / ruff                 clean
```

The reviewer accepts the **visual direction**.

Do not spend another full engineering cycle making the dashboard prettier.

From here, dashboard work should be incremental and driven by operational truth.

The primary engineering path returns to:

```text
CI
→ M0 domain-contract closure
→ read-only reconciliation
```

---

# 2. Dashboard visual review

## Verdict: ACCEPTED

The owner's screenshot is materially closer to the intended cockpit.

### What works well

#### A. Strong hierarchy

The top row answers immediately:

```text
MT5
DATA FEED
SAFETY
MILESTONE
```

This is the correct first question set for an autonomous system.

#### B. Execution state is prominent

`READ ONLY` and `EXECUTION DISABLED` are visible at the top.

Keep this permanently prominent until the project has passed the separate execution gates.

#### C. EUR/USD is visually central

The latest bid/ask and M5 chart are given the largest market-data area.

Correct.

#### D. Historical gaps are not visually hidden

The chart visibly contains the break between recorded sessions rather than inventing candles to fill it.

That matches the platform's data-integrity rules.

#### E. Unknown states look unsafe

`UNKNOWN` is red rather than neutral/green.

Correct.

---

# 3. F-042 — visual baseline

**Status:** CLOSED

The redesigned interface satisfies review 1.13's requested baseline.

No frontend framework rewrite is required.

FastAPI + Jinja2 + restrained vanilla JS remains sufficient.

---

# 4. F-043 — stale-data presentation

**Status:** CLOSED IN IMPLEMENTATION, WITH ONE NEW VISUAL REFINEMENT BELOW**

The documented test matrix now distinguishes:

```text
fresh
stale
disconnected
missing health snapshot
database unavailable
```

That is accepted.

The screenshot is also useful evidence: with old persisted data, the dashboard does **not** pretend that MT5/data/safety are healthy.

---

# 5. F-044 — replay/live decision context

**Status:** CLOSED IN DATA MODEL / IMPLEMENTATION**

The implementation now explicitly labels journal decisions as replay-derived and carries their source/context.

Keep that.

### Visual refinement

The screenshot section title still reads roughly:

```text
DECISION PIPELINE — LATEST WINDOW
```

For an owner looking at a live-market chart, that phrase is still easier to misread than necessary.

The visible heading itself should prefer:

```text
DECISION PIPELINE — LATEST REPLAY WINDOW
```

or:

```text
REPLAY DECISION PIPELINE
```

when no live decision pipeline exists.

Even if the detailed banner lower in the panel already explains the source, the headline should carry the same semantic truth.

This is a UI-copy refinement, not a reopened safety finding.

---

# 6. New finding F-045 — environment badge must describe the actual operating state

**Severity:** MEDIUM / SEMANTIC UX  
**Status:** OPEN

The screenshot's top-right environment badge says:

```text
PAPER
```

but the current project state says:

```text
Paper campaign: NOT STARTED
Live trading permitted: NO
current system is read-only MT5/data observation
```

Calling the current screen `PAPER` can imply that autonomous demo trading is already underway.

It is not.

### Required change

Until M5/P2 actually starts, prefer:

```text
DEMO
READ ONLY
EXECUTION DISABLED
```

or:

```text
DEMO DATA
READ ONLY
EXECUTION DISABLED
```

Reserve `PAPER` for an actual paper-execution campaign.

### Acceptance

Environment badges must distinguish:

```text
DEMO READ-ONLY
PAPER EXECUTION
SHADOW
LIVE-CANARY
LIVE
```

rather than treating demo connectivity and paper trading as synonyms.

---

# 7. New finding F-046 — historical market data needs a stronger visual "not live" treatment

**Severity:** MEDIUM/HIGH UX-SAFETY  
**Status:** OPEN

The screenshot shows:

```text
MT5          UNKNOWN
DATA FEED    UNKNOWN
SAFETY       UNKNOWN
last tick    ~1008 minutes old
```

while the old bid/ask and candle chart remain fully rendered.

The page does state that the chart source is persisted PostgreSQL rather than live MT5, which is good.

However, as the dashboard becomes visually convincing, historical prices should become even harder to mistake for current prices.

### Required behavior

When:

```text
data feed != HEALTHY
or
reader session is absent
```

the EUR/USD hero/chart should visibly enter a historical/offline presentation.

For example:

```text
HISTORICAL DATA
Last live tick: 16h 48m ago
```

and/or a subdued chart overlay:

```text
NO ACTIVE LIVE DATA SESSION
```

Do not hide the chart; historical evidence is useful.

Just make its status impossible to overlook.

### State separation

Keep distinct:

```text
MT5 UNKNOWN
= dashboard cannot establish current terminal connection state

DATA STALE / HISTORICAL
= stored market observation is old

SAFETY UNKNOWN
= safety authority cannot currently be established from available state
```

One UNKNOWN should not be used as a generic label for all three concepts.

---

# 8. Dashboard account/connection context — next useful data improvement

The screenshot correctly shows `NOT AVAILABLE` instead of inventing:

```text
Entity
Margin mode
Broker clock offset
```

That honesty is good.

But these values are already known/observable by the reader.

Rather than hard-coding them into the dashboard, the next useful evolution is:

```text
LiveReader/account observation
→ persisted/read-only operational account snapshot
→ Dashboard
→ Reconciliation
```

This is not primarily a dashboard feature.

It naturally belongs with the upcoming reconciliation work.

A persisted current account/instrument snapshot can later power:

```text
Account mode
Entity/company observation
Server
Currency
Leverage
Broker clock offset
Instrument semantic version
Position count
Reconciliation state
```

without letting the dashboard talk directly to MT5.

---

# 9. Screenshot-specific polish suggestions

These are **non-blocking**.

## A. Time formatting

Prefer:

```text
24 Aug 18:49:04 UTC
```

in the compact price card.

Keep the full ISO timestamp available in a tooltip/detail view.

Long ISO strings wrap awkwardly in the narrow card.

## B. Age formatting

Prefer:

```text
16h 48m ago
```

over:

```text
1008m ago
```

The status report says the formatter was already improved after manual smoke testing; make sure the screenshot/runtime build uses that version.

## C. Data-gap annotation

The chart currently shows the gap honestly as whitespace.

Later, a subtle label such as:

```text
DATA GAP · 70m
```

would make the reason obvious.

No need for a complex charting library.

## D. Price colours

Bid/ask can remain visually distinct, but do not encode them as though bid is intrinsically "bad" and ask intrinsically "good".

This is cosmetic only.

---

# 10. Dashboard scope now freezes

The dashboard has reached the point where more visual work has sharply diminishing value.

Allowed incremental work:

```text
semantic labels
freshness/offline treatment
reconciliation panel
account snapshot data
small layout/accessibility fixes
```

Do **not** spend the next cycle on:

```text
animations
complex chart tools
themes
user customisation
mobile-first redesign
frontend framework migration
trading controls
```

The visual goal has been met sufficiently.

---

# 11. F-033 — current-state documentation still contains contradictions

**Status:** OPEN

The top/current status has improved materially.

However, the same document still contains current-risk/checklist statements that predate Phase B, for example claims equivalent to:

```text
real reconnect not yet exercised
only first-contact probe ran against real terminal
nothing is MT5-integrated in the risk/platform capability narrative
```

while the same current document records M1 as PASSED / MT5-INTEGRATED.

### Required

Do one focused cleanup of current-state sections.

Examples:

```text
APP-014 should reflect continuous read/reconnect completed
connected-account risk should say real Phase B reconnect was proven
MT5 test/evidence wording should distinguish automated tests vs manual real-terminal evidence
Risk section should not say "none can be MT5-integrated until M1" now that M1 passed
```

Historical logs remain unchanged.

This is documentation debt, not a rollback of M1.

---

# 12. CI is now the highest-priority unfinished M0 action

The project has delayed CI long enough.

Current local evidence is strong:

```text
748 passed
3 explained skips
mypy clean
ruff clean
Windows + PostgreSQL
```

Now run the hosted CI workflow.

Required recorded evidence:

```text
commit SHA
Linux job result
Windows job result
PostgreSQL-backed test result
gitleaks result
unexpected skip count
overall workflow result
```

If CI fails, fix the actual cross-environment defect.

Do not weaken CI to make it green.

---

# 13. Domain contracts — reviewer package now due

The final human M0 review is still outstanding.

Provide the actual current contract definitions or a generated contract package covering at least:

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

The review must specifically check:

```text
immutability
extra-field rejection
Decimal/time semantics
ownership boundaries
execution permissions
risk/supervisor separation
fields that may/may not be agent-controlled
```

Do not mark this approved merely because tests exist.

---

# 14. Reconciliation is now the primary safety engineering task

Once CI is running / being fixed, begin read-only reconciliation.

The base invariant remains:

```text
Agent proposes.
Risk engine constrains.
Supervisor vetoes.
Execution service executes.
Reconciliation verifies.
```

Reconciliation v0 should compare:

```text
expected configured account
observed MT5 account
expected EURUSD instrument
observed instrument
locally known positions
observed MT5 positions
```

Output exactly:

```text
MATCHED
MISMATCHED
UNKNOWN
```

Safety rule:

```text
MISMATCHED → HALT
UNKNOWN    → HALT / fail closed
```

The dashboard may display this result once built.

The dashboard must never calculate reconciliation itself.

---

# 15. Gate decisions

## M0
**GO WITH CONDITIONS**

Still open:

```text
CI
domain contract review
```

## M1
**PASSED / MT5-INTEGRATED**

## M2
**PASSED**

## Dashboard v0.1
**VISUAL DIRECTION ACCEPTED**

Semantic refinements F-045/F-046 remain.

## M5
**NO-GO**

## P2
**NO-GO**

No execution permission follows from the dashboard work.

---

# 16. Required next action order

```text
1. Process feedback.1.14.md.
2. Change PAPER badge to DEMO/DEMO DATA until paper execution actually starts (F-045).
3. Add strong historical/offline treatment to the market hero/chart (F-046).
4. Make replay context explicit in the visible decision-pipeline heading.
5. Stop major dashboard visual work after those refinements.
6. Run hosted CI and record the complete result.
7. Provide domain contracts for reviewer approval.
8. Close remaining F-033 current-state documentation contradictions.
9. Build read-only reconciliation.
10. Persist/read an operational account/instrument snapshot as needed by reconciliation.
11. Display reconciliation on dashboard only after the reconciliation service owns the truth.
12. Decide remaining owner risk/intraday/HALT-reset policies before M5.
13. Keep AlgoTrading OFF.
14. No execution adapter / no order_send.
15. feedback.1.15.md after CI + contract review and/or initial reconciliation evidence.
16. feedback.2.0.md before any first order.
```

---

# 17. Final reviewer statement

The dashboard has crossed the visual threshold the owner asked for.

It now looks like a credible operations cockpit.

The next challenge is not making it prettier.

It is making every attractive pixel semantically exact:

```text
demo is not paper
historical is not live
replay is not live decisioning
unknown is not healthy
```

After those small refinements, move engineering effort back to the safety path:

```text
CI
→ contracts
→ reconciliation
```

The interface is now good enough to support the platform.

The platform should not pause to support the interface.
