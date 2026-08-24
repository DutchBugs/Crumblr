# feedback.1.4.md — M2 Persistence Review

**Project:** Autonomous EUR/USD Trading Platform  
**Review version:** 1.4  
**Review date:** 2026-08-18  
**Reviewed artifacts:** `status.md`, `review/DEVIATIONS.md`, `review/FEEDBACK.md`  
**Previous review:** `feedback.1.3.md`  
**Overall verdict:** **GO WITH CONDITIONS**  
**M2 verdict:** **IMPLEMENTATION ACCEPTED, INTEGRATION NOT YET COMPLETE**  
**M5 / P2 verdict:** **NO-GO**  
**Scope note:** This review evaluates the supplied documentation and stated test evidence. Source code and PostgreSQL test output were not independently inspected.

---

## 1. Executive review

There is meaningful engineering progress.

M2 is no longer just a design. The persistence layer has reportedly been implemented against PostgreSQL 17 with:

- append-only event storage;
- producer-assigned event identity;
- idempotent writes;
- Decimal and UTC fidelity;
- Decision Capsule integrity checks;
- safety-state authority handling;
- replay-from-journal acceptance testing.

This is the right milestone and the right direction.

However, the persistence layer is currently **built beside the running system rather than inside it**.

The orchestrator still uses in-memory capsule accumulation and does not yet use the PostgreSQL-backed safety-state path.

Therefore:

```text
Persistence foundation = built
Persistence integration = not finished
M2 gate               = not passed
```

The immediate priority is wiring and proving the integrated path.

---

## 2. Existing findings

### F-001 through F-015

No earlier finding is reopened on substance.

Important implementation-state updates now implied by `status.md`:

```text
F-012 Safety-state authority:
Finding:        CLOSED
Implementation: SHIPPED IN PERSISTENCE LAYER
Integration:    PENDING ORCHESTRATOR WIRING

F-015 Persistence invariants:
Finding:        CLOSED
Implementation: SHIPPED + TESTED AGAINST POSTGRESQL
Integration:    PENDING ORCHESTRATOR WIRING
```

The implementation tracker should be updated to reflect this new state.

---

## 3. New findings

### F-016 — FEEDBACK.md is stale after M2 implementation

**Severity:** MEDIUM / DOCUMENTATION  
**Status:** OPEN

`FEEDBACK.md` still says F-012/F-015 are pending M2 and that PostgreSQL persistence is still the next open item, while `status.md` reports the persistence layer is now implemented.

**Required change**

Update implementation states without erasing history.

Recommended:

```text
F-012 | CLOSED | SHIPPED — integration pending D-030
F-015 | CLOSED | SHIPPED — 10/10 PostgreSQL acceptance tests
```

And replace “PostgreSQL persistence still open” with:

```text
Persistence layer built
Orchestrator wiring pending
```

---

### F-017 — DEVIATIONS.md contains stale present-tense descriptions

**Severity:** MEDIUM / DOCUMENTATION SAFETY  
**Status:** OPEN

Several deviation entries retain historical present-tense descriptions that no longer match current state.

Priority cleanup:

```text
D-011
D-012
D-027
D-028
D-030
```

Use:

```text
Original gap:
Current state:
Remaining gap:
Gate affected:
```

Historical information should stay, but current risk must be immediately legible.

---

### F-018 — M2 persistence is not yet used by the running orchestrator

**Severity:** HIGH  
**Status:** OPEN  
**Blocks:** M2 qualification and later M5

This is already identified as `D-030` / `APP-006`.

Current reported situation:

```text
EventJournal / CapsuleStore = built
CompositeSafetyStateStore   = built
PostgreSQL tests            = green

ReplayOrchestrator          = still in-memory
startup safety path         = not yet wired to composite store
```

**Required implementation**

Wire:

1. `CapsuleStore` into decision sealing;
2. event journal writes into the real orchestration path;
3. `CompositeSafetyStateStore` into startup/recovery;
4. incident/safety-state reads from persistence;
5. replay/recovery from the persisted journal.

**Required integration tests**

```text
run → persist → process stop → restart → recover same safety state
run → persist → DB-backed replay → same decisions
persisted HALT → restart → new orders remain disabled
database unavailable at startup → fail closed
journal/latch disagreement → HALTED
duplicate event through orchestrator → one logical event
```

M2 only qualifies when the application itself uses the persistence layer.

---

### F-019 — Equity/daily-loss state is still in memory

**Severity:** HIGH BEFORE M5  
**Status:** OPEN  
**Required before:** M5 / autonomous paper execution

The kill switch is durable, but the equity ledger / daily-loss baseline is still reported as in-memory.

Future unsafe scenario:

```text
daily loss approaches limit
→ restart
→ daily-loss baseline resets
→ system believes less loss occurred
```

This is not an immediate execution risk because no broker is reachable yet.

Before M5, persist or reconstruct at least:

```text
trading session/day id
session-start equity
realized P&L
high-water mark / drawdown reference
daily-loss consumed
open-risk state
```

A restart may never reset risk budgets in the permissive direction.

---

### F-020 — Database schema migration strategy required before valuable paper data

**Severity:** MEDIUM  
**Status:** OPEN  
**Required before:** M5/P2

Current schema creation uses `metadata.create_all`; Alembic is not yet configured.

That is acceptable while the local database is disposable.

Before paper data becomes evidence:

- create an Alembic baseline;
- version schema changes;
- test upgrades;
- define PostgreSQL backup/restore.

Do not wait until after the first valuable paper campaign.

---

## 4. M0 assessment

M0 is technically close to closure.

Two non-broker conditions remain visible:

```text
CI has never run on an actual runner
human domain-contract review is pending
```

Recommendation:

Either run CI on an appropriate runner, or explicitly approve a local-project exception that CI execution is deferred until remote collaboration.

The domain contracts should receive deliberate human approval before formal M0 closure.

---

## 5. Gate decisions

### M0
**Verdict:** READY FOR OWNER DECISION / CLOSURE WITH TWO CONDITIONS

### M1 — MT5 read-only gateway
**Verdict:** GO WHEN HUMAN DEPENDENCIES ARE CHOSEN

Needs:
- broker;
- demo account;
- hedging/netting account model;
- exact MT5 server;
- Windows x86-64 host.

### M2 — Persistence
**Verdict:** FOUNDATION ACCEPTED — INTEGRATION MUST CONTINUE NOW

Do not branch into new features before F-018 / D-030 is closed.

### M3 — Replay/backtest
**Verdict:** CORRECTNESS ONLY

### M4 — Risk Engine
**Verdict:** CONTINUE / NO PROMOTION

### M5 — Paper execution
**Verdict:** NO-GO

Still requires:
- M1 real MT5 integration;
- M2 wired persistence;
- real reconciliation;
- durable/reconstructable equity-session state;
- ADR-001 execution-time risk implementation;
- schema migration/backup discipline;
- MT5 validation of cancel/flatten;
- demo/live account guard.

### M6 — Trading Agent
**Verdict:** FEATURE FREEZE MAINTAINED

### M7 — Evaluator / Supervisor
**Verdict:** SAFETY WORK ONLY

### P2 — Autonomous demo campaign
**Verdict:** NO-GO

---

## 6. Immediate owner decisions required

### Decision A — Broker and MT5 server

Choose one first demo broker.

Recommendation: one primary broker for M1, optionally keep a second broker only as a later benchmark. Do not build multi-broker support now.

### Decision B — Hedging or netting account model

Choose one mode for v1.

Do not support both simultaneously in the first implementation.

### Decision C — Strategy horizon / bar interval

The current prototype is heavily exercised on M5.

Recommendation: keep **M5 as initial decision timeframe** unless there is a strong reason to change.

This choice affects:
- data granularity;
- supervisor rate thresholds;
- ICT session logic;
- tick-data requirements;
- expected trade frequency.

### Decisions that can wait until before P1/P2/M5

- overnight positions allowed?
- paper risk per trade?
- max drawdown?
- who may reset production HALT?

---

## 7. Required next action order

```text
1. Wire PostgreSQL persistence into orchestrator (F-018 / D-030)
2. Wire CompositeSafetyStateStore into startup/recovery
3. Add persisted restart/replay integration tests
4. Persist or reconstruct equity-session risk state (F-019)
5. Clean stale FEEDBACK/DEVIATIONS state (F-016/F-017)
6. Owner chooses broker + demo account
7. Owner confirms hedging/netting account model
8. Owner confirms initial M5 strategy horizon
9. Provision Windows x86-64 MT5 host
10. Build read-only MT5 Gateway
11. Build broker reconciliation
12. Establish Alembic baseline before M5/P2
13. feedback.2.0 before any order_send
```

---

## 8. Next review

The next regular review should be:

```text
feedback.1.5.md
```

Trigger it when persistence is wired into the orchestrator and recovery tests are available.

`feedback.2.0.md` remains mandatory before the first real or demo `order_send`.

---

## 9. Final reviewer statement

The project is progressing.

The important achievement in this cycle is not simply that a database exists.

It is that there is now a tested persistence foundation with explicit invariants.

The key unfinished step is:

> The running system must actually use that persistence foundation.

Do that before adding new trading features.

After that, the project is ready for the transition from simulated truth to real MT5 broker truth.
