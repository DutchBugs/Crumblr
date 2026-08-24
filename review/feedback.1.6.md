# feedback.1.6.md — Persistence Integration & M1 Readiness Review

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.6  
**Date:** 2026-08-18  
**Reviewed artifact:** `status.md` v1.1  
**Previous owner handover:** `feedback.1.5.md`  
**Missing historical review:** `feedback.1.4.md` must still be added to the repository  
**Overall verdict:** **GO WITH CONDITIONS**  
**M2 verdict:** **SUBSTANTIAL PROGRESS — NOT YET COMPLETE**  
**M1 verdict:** **PREPARE NOW**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review evaluates `status.md` and its stated test evidence. Source code was not independently inspected.

---

## 1. Executive review

This development cycle contains real progress.

The persistence layer is no longer sitting beside the application. It is now reportedly used by the normal orchestration path:

```text
normal application flow
→ typed events
→ EventJournal
→ PostgreSQL

decision window
→ sealed Decision Capsule
→ PostgreSQL

startup
→ CompositeSafetyStateStore
→ fail-closed recovery

risk session
→ persisted/recovered
→ restart may only tighten risk
```

The reported evidence is strong for this stage:

```text
491 tests
64 PostgreSQL integration tests
real child-process restart tests
journal reconstruction matches the live run
replay remains deterministic
```

This closes the two biggest technical concerns from review 1.4:

```text
F-018 persistence not wired
F-019 daily-loss/equity session lost on restart
```

The project is now much closer to being ready for its first real MT5 data integration.

The next work should remain infrastructure-focused:

```text
raw market-data persistence
database migrations
M0 housekeeping
Pepperstone demo environment
Windows MT5 host
read-only gateway
```

Do not add strategy features.

---

## 2. Review 1.4 history must be restored

`status.md` reports that `feedback.1.4.md` is not present in the developer repository.

That review is part of the audit trail and must be added unchanged.

Do not recreate or renumber it.

### Current reviewer interpretation of review 1.4 findings

```text
F-016  FEEDBACK tracker stale after M2
       → status unknown in this review; latest FEEDBACK.md not supplied

F-017  DEVIATIONS stale present-tense descriptions
       → status unknown in this review; latest DEVIATIONS.md not supplied

F-018  persistence not used by orchestrator
       → CLOSED based on status.md evidence

F-019  risk-session/daily-loss state lost on restart
       → CLOSED based on status.md evidence

F-020  Alembic/schema migration strategy
       → OPEN and now higher priority
```

---

## 3. Accepted closures

### F-018 — Persistence on the normal path

**Status:** CLOSED BASED ON DOCUMENTED INTEGRATION EVIDENCE

The ordinary orchestration path now reportedly writes every stage through `RunRecorder`, persists Decision Capsules, uses the composite safety-state store at startup, and reconstructs the run from the `events` table.

This is the correct architecture.

### F-019 — Risk-session state survives restart

**Status:** CLOSED BASED ON DOCUMENTED RESTART EVIDENCE

The reported implementation now persists and recovers risk-session state and applies the key rule:

```text
recovery may only tighten risk
```

Unsafe or contradictory recovery state halts.

That is the correct failure direction.

---

## 4. New findings

### F-021 — `status.md` contains stale current-state sections

**Severity:** MEDIUM  
**Status:** OPEN

The lower update log correctly says:

```text
D-030 closed
persistence on normal path
Pepperstone selected
M5 timeframe selected
risk session durable
```

but several current-state sections still describe older reality.

Examples:

- Platform “Next objective” still says to wire the journal into the orchestrator.
- M1 dependency list still says broker is not selected.
- “Next 10 actions” still lists broker Q1 as unanswered.
- the top Platform blocker associates “no broker/no Windows host” with the still-open M0 qualification even though those are M1 dependencies.
- status document version remains `1.1` despite substantial state changes.

**Required change**

Use this rule:

```text
top/current sections = current truth
update log            = historical truth
```

---

### F-022 — Raw market data is not persisted

**Severity:** HIGH BEFORE REAL FEED / M5  
**Status:** OPEN  
**Blocks:** full M2 qualification and auditable real-data operation

The event journal records:

```text
what the system decided
```

but not yet:

```text
what the market actually showed it
```

That is acceptable with a seeded deterministic generator.

It is not acceptable once Pepperstone supplies real ticks/bars.

### Required minimum v1 market-data persistence

Persist enough to reconstruct the decision input:

```text
broker/source
canonical symbol
broker symbol
event timestamp UTC
received timestamp UTC
bid
ask
last where supplied
volume/flags where supplied
bar timeframe
OHLC
tick/bar provenance
```

For normalized bars, preserve the transformation/version identity used to create them.

Important distinction:

```text
event journal = what the system did
market store  = what the system saw
```

Before M5, if the real feed cannot be recorded sufficiently for audit/replay, autonomous execution remains disabled.

---

### F-023 — Database migrations are now required

**Severity:** MEDIUM / HIGH DATA GOVERNANCE  
**Status:** OPEN  
**Blocks:** M5/P2 and valuable long-running evidence

The project is now writing ordinary runs to PostgreSQL.

Before paper evidence becomes valuable:

```text
Alembic baseline revision
versioned migrations
upgrade test
backup procedure
restore test
```

At least one restore test should prove that a backup can create a database from which the application can reconstruct its audit state.

---

### F-024 — Supervisor frequency check is now actionable because M5 is fixed

**Severity:** MEDIUM  
**Status:** OPEN

The owner has fixed the v1 decision timeframe to M5.

The status still reports a supervisor frequency threshold of `20/hour`, while a single-symbol M5 decision loop has at most 12 decision windows per hour.

Therefore the check still cannot fire.

The old reason “timeframe not yet settled” is no longer valid.

**Required decision**

Do not calibrate from synthetic P&L or synthetic trade frequency.

Choose one honest state:

```text
A. define a deterministic structural rate limit with documented rationale
or
B. mark the supervisor anomaly check explicitly DISABLED / UNCALIBRATED
   until real EUR/USD observations exist
```

Reviewer preference: use B if there is no defensible real-data calibration yet.

---

### F-025 — Owner exposure/intraday decisions must become executable policy before M5

**Severity:** HIGH BEFORE M5  
**Status:** OPEN

The decision log records:

```text
M5 decision timeframe
no overnight positions
max one EUR/USD exposure
```

These are now owner-approved policies.

They need explicit implementation/evidence before execution.

#### One-exposure rule

The deterministic system must block a second EUR/USD exposure.

Test at least:

```text
no position + BUY → potentially allowed
long exists + BUY → block
long exists + SELL that creates overlapping/opposite exposure → block or explicit close workflow
short exists + SELL → block
```

#### Intraday rule

Before M5 define:

```text
last allowed new-entry time
mandatory flatten deadline
trading-day/session boundary
behavior when flatten fails
behavior when market/broker unavailable near deadline
```

Failure to prove flatness at the boundary must not silently become “overnight allowed”.

---

## 5. M0 — simple owner decision needed

M0 has two visible loose ends:

```text
1. domain contracts await human review
2. CI has never run on a runner
```

Broker and Windows must not hold M0 open.

### Reviewer recommendation on CI

Because the owner chose local development before collaboration:

```text
document an M0 exception:
"CI workflow authored; runner execution deferred"
```

Then make actual CI execution mandatory no later than:

```text
before feedback.2.0 / first order_send
```

### Domain contracts

Do not approve blindly.

Provide the current domain contract definitions or generated contract/spec summary for explicit reviewer/human approval.

---

## 6. M1 — human actions now required

Broker ambiguity is gone:

```text
Primary broker = Pepperstone EU
Platform       = MT5
Environment    = demo
```

What remains is practical provisioning.

### Owner action 1 — Create/select Pepperstone MT5 demo account

After creation, provide the developer only the non-secret facts:

```text
exact MT5 server
account mode: hedging or netting
actual EUR/USD broker symbol if visible
```

Credentials belong in the secret store.

### Owner action 2 — Choose/provision Windows x86-64 host

The project needs one Windows machine/VM/VPS that can run:

```text
MetaTrader 5 terminal
official MetaTrader5 Python integration
MT5 Gateway service
```

One dedicated host is enough for v1.

---

## 7. Gate decisions

### M0
**Verdict:** READY FOR OWNER CLOSURE DECISION AFTER CONTRACT REVIEW / CI EXCEPTION

### M1
**Verdict:** PREPARE NOW

First M1 remains read-only.

### M2
**Verdict:** MAJOR CORE COMPLETE, BUT MILESTONE NOT PASSED

Remaining important deliverables:

```text
raw tick/bar persistence
normalized bar pipeline
migration discipline
```

### M3
**Verdict:** CORRECTNESS ONLY

### M4
**Verdict:** REPLAY-TESTED / NO PROMOTION

### M5
**Verdict:** NO-GO

### M6
**Verdict:** FEATURE FREEZE

### M7
**Verdict:** SAFETY WORK ONLY

### P2
**Verdict:** NO-GO

---

## 8. Required next action order

```text
1. Add missing feedback.1.4.md to the repository unchanged
2. Clean stale current-state sections in status.md (F-021)
3. Build raw tick/bar persistence + normalized bar pipeline (F-022)
4. Add Alembic baseline/migration path (F-023)
5. Mark/rework supervisor frequency check now M5 is fixed (F-024)
6. Encode/test max-one-EURUSD exposure policy (F-025)
7. Define intraday cutoff/flatten semantics before M5
8. Owner creates Pepperstone MT5 demo account
9. Inspect account mode: hedging or netting
10. Provision Windows x86-64 MT5 host
11. Build read-only MT5 Gateway
12. Persist real Pepperstone market data immediately
13. Build reconciliation
14. Run feedback.2.0 before any order_send
```

---

## 9. What must NOT happen next

Do not spend the next cycle on:

```text
ICT v2
more indicators
new markets
multi-broker routing
ML
strategy optimisation
real orders
```

The next value comes from proving:

```text
real market input
→ durable data
→ safe recovery
→ correct MT5 state
```

---

## 10. Next review

The next normal review is:

```text
feedback.1.7.md
```

Trigger it when one or more are ready:

- raw tick/bar persistence;
- Alembic;
- corrected supervisor frequency-state;
- Pepperstone demo / Windows MT5 provisioning;
- read-only gateway implementation.

The mandatory execution review remains:

```text
feedback.2.0.md
```

before the first call to `order_send`.

---

## 11. Final reviewer statement

The project has made a meaningful transition.

Previously:

```text
the database was tested but unused
```

Now:

```text
the normal system path is durable and restart-aware
```

That is real progress.

The next transition is:

```text
from reproducible synthetic inputs
to captured, auditable Pepperstone market inputs
```

Finish that data boundary, then move into read-only MT5 integration.
