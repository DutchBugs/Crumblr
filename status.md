# status.md — Autonomous MT5 Trading Platform

**Project:** Autonomous EUR/USD Trading Platform  
**Status document version:** 1.4  
**Last updated:** 2026-08-24  
**Current environment:** DESIGN  
**Live trading permitted:** NO

## For the reviewer

| Document | Purpose |
|---|---|
| [review/DEVIATIONS.md](review/DEVIATIONS.md) | Every departure from `build.md`, with rationale and what to watch. Start here. |
| [review/FEEDBACK.md](review/FEEDBACK.md) | Where findings are filed. Resolved at the start of each session before new work. |
| [HANDOVER.md](HANDOVER.md) | How to pick this project up cold: environment, code map, next steps, traps. |
| [CLAUDE.md](CLAUDE.md) | The working agreements the implementing agent operates under. |
| §10 below | Architectural and risk decision log |
| §13 below | Chronological update log with evidence and problems found |

`build.md` is the specification and is never edited to match the code. Gaps are
recorded in `review/DEVIATIONS.md` rather than closed by rewriting the spec.

---

# 1. Overall status

## Maturity is not qualification

Review finding F-001: `BUILDING` was being used to mean both "code exists" and
"this milestone is close to passing", which are unrelated claims. They are now
tracked separately, and **no amount of implementation maturity passes a gate**.
Unit and replay tests are never sufficient for promotion on their own.

Implementation maturity ladder — each step includes the ones before it:

```text
SPECIFIED         written down in build.md, not built
IMPLEMENTED       code exists
UNIT-TESTED       behaviour asserted in isolation
REPLAY-TESTED     exercised end to end against simulated data
MT5-INTEGRATED    exercised against a real terminal
PAPER-VALIDATED   run unattended on a demo account
SHADOW-VALIDATED  run against a live feed without submitting orders
```

Gate qualification is a separate, human decision recorded in §16.

| Component | Maturity | Gate qualification | Blocker |
|---|---|---|---|
| 1. Platform / Application | REPLAY-TESTED | **NOT PASSED** (M0) | M0's own remainder: human contract review, and CI never run on a runner. Broker and Windows host are M1 dependencies and are tracked there (F-010) |
| 2. Trading Agent | REPLAY-TESTED | **NOT PASSED** (M6) | No real EUR/USD evidence; feature-frozen per F-004 |
| 3. Evaluator / Supervisor | UNIT-TESTED | **NOT PASSED** (M7) | Layer 1 only; post-trade and drift not started |

Progress percentages have been removed. They implied a precision the project
does not have, and they contradicted the detailed checklists below (F-005).

## Status legend

```text
NOT STARTED
DESIGN
BUILDING
TESTING
PAPER
SHADOW
READY FOR REVIEW
BLOCKED
HALTED
LIVE-CANARY
```

## Overall health

```text
Engineering health:   AMBER   (lint/types/tests green locally; CI never executed)
Safety-state health:  AMBER   (fail-closed, durable, and now recovered on the
                                normal path; not yet broker-validated)
Trading health:       GREY    (ict_v1 runs on synthetic data only — no evidence)
Risk health:          AMBER   (engine works in replay; the daily budget now
                                survives a restart; never met a real broker)
Data health:          AMBER   (decisions journalled, ticks and bars stored,
                                schema versioned; no real feed has been seen)
MT5 connectivity:     GREY    (read-only gateway and first-contact probe
                                written; Windows host available 2026-08-24;
                                no demo account, so still never connected)
Paper campaign:       NOT STARTED
Production readiness: 0%
```

---

# 2. Current project gate

**Gate:** M0 — Architecture / repository setup

Review 1.1 finding F-010: this section previously held M0 open partly on broker
and demo-account selection. Those are real dependencies but they belong to M1,
and `build.md` is the specification — `status.md` must not silently redefine
its gates. The criteria below are therefore split into what the specification
itself requires, and what is local project policy on top of it.

### M0 deliverables — build.md §26

- [x] Python project — CPython 3.12.14, `src/crumblr`
- [x] dependency lock — `uv.lock`, CI installs with `--locked`
- [x] lint/type/test pipeline — ruff, mypy `strict`, pytest
- [x] config schema — versioned, fail-closed, no permissive risk defaults
- [x] **logging** — structured JSON to stderr, UTC timestamps, component and
      service fields, correlation-id propagation, secret redaction. Emissions
      cannot raise into the caller and do not affect replay determinism —
      both properties tested (review 1.2 F-013)
- [x] CI — workflow written (see acceptance below for its real status)
- [x] environment separation — paper/shadow demo-only, live double-gated

### M0 acceptance — build.md §26

- [x] clean install from scratch — `uv sync` from an empty environment
- [ ] all tests run locally **and in CI** — 604 pass locally; **CI has never
      executed on a runner**, so this criterion is half met at best. Review 1.6
      §5 offers a way out: record an explicit M0 exception now, and make CI
      execution mandatory before `feedback.2.0.md`. That is an owner decision
- [x] no secrets in repository — `.gitignore`, config-loader rejection, gitleaks job

### Local project policy — not from build.md §26

Recorded separately so the specification's own gates stay legible.

- [ ] domain contracts reviewed by a human — implemented and tested; awaiting review
- [x] durable safety state and fail-closed startup — added after review 1.0 F-003
- [x] status semantics separate maturity from qualification — review 1.0 F-001

### Not M0 — M1 dependencies (F-010)

These block M1 and are tracked there. They are not engineering failures at M0.

- [x] broker selected — Pepperstone, owner decision O-001 (build.md §29 Q1)
- [x] exact MT5 server documented — `PepperstoneUK-Demo`, supplied 2026-08-18
      and recorded in `config/paper.yaml` as a claim the account guard checks
- [ ] **entity confirmed** — O-001 says "Pepperstone EU", the server says UK.
      Different regulator, leverage cap and swap treatment. See D-034
- [ ] MT5 demo account created — the account itself does not exist yet
- [ ] hedging or netting established from `account_info()` (§29 Q2) — not guessed
- [ ] Windows x86-64 host with the MetaTrader 5 terminal provisioned

### Promotion decision

```text
Decision: GO WITH CONDITIONS   (reviewer verdict, feedback.1.1 §4)
Owner:
Date:
Evidence: 604 tests green, mypy strict clean over 88 files, ruff clean,
          replay byte-identical across runs, halt proven to survive a real
          process restart, structured logging shipped and proven not to
          affect determinism, and a run reproducible from the persisted
          journal alone (2026-08-18).
Notes:    Two conditions remain, neither blocked on a broker.
          1. Execute CI on a runner, or keep it honestly marked as unexecuted.
          2. Human review of the domain contracts.
```

---

# 3. Component 1 — Platform / Application

## Current status

```text
Status: BUILDING
Owner:
Current milestone: M0
Implementation maturity: REPLAY-TESTED
Gate qualification: NOT PASSED
Last meaningful update: 2026-08-18 — review feedback.1.3 processed
Next objective: M1 first contact. The adapter is written; what remains is the
                demo account, a Windows x86-64 host and the entity question —
                all three are human tasks
```

## Scope

- MT5 Gateway
- market data
- instrument registry
- event journal
- backtest/replay
- risk engine
- execution engine
- reconciliation
- database
- API/dashboard
- monitoring
- kill switches
- security/secrets

## Milestone tracker

| Milestone | Maturity | Gate | Evidence / why not qualified |
|---|---|---|---|
| M0 Repo / engineering baseline | REPLAY-TESTED | GO WITH CONDITIONS | Logging shipped (F-013). Remaining: human contract review, CI on a runner |
| M1 MT5 read-only gateway | UNIT-TESTED | NOT PASSED | Read-only adapter and a first-contact probe (`scripts/mt5_probe.py`), 59 tests against a fake terminal; execution refused by construction (D-036). **Never run against MetaTrader 5** (D-035). The Windows host arrived 2026-08-24; what remains is the demo account and the entity question (APP-013). Continuous bar/tick read and reconnect behaviour are not implemented — HANDOVER.md §4.5 |
| M2 Data/event journal | REPLAY-TESTED | NOT PASSED | All four deliverables now exist: journal with correlation/causation, raw tick and bar storage, a versioned normalized bar pipeline that detects gaps and out-of-order data, and Alembic migrations with a proven restore. A run reproduces from the `events` table alone and survives a real process restart. **Not passed because none of it has seen a real feed** — every row was written by a seeded generator |
| M3 Replay/backtest | REPLAY-TESTED | NOT PASSED | Deterministic; cost model incomplete (no swap/commission) |
| M4 Risk engine | REPLAY-TESTED | NOT PASSED | Full §8.1 checklist and sizing; never met a real broker |
| M5 Paper execution | SPECIFIED | **NO-GO** | Twelve prerequisites open — see `review/feedback.1.0.md` §6 |
| M6 Trading Agent | REPLAY-TESTED | FEATURE FREEZE | `ict_v1` + `baseline_v1`; next step is evidence, not concepts |
| M7 Evaluator / Supervisor | UNIT-TESTED | SAFETY WORK ONLY | Layer 1. Two of its seven checks now report themselves as **not in force** rather than passing (F-024); post-trade and drift not started |
| M8 Dashboard | SPECIFIED | NOT PASSED | Operator controls exist in code; no interface |
| M9 Paper campaign support | SPECIFIED | NOT PASSED | Blocked behind M5 |
| M10 Shadow support | SPECIFIED | NOT PASSED | Blocked behind M5 |

## Platform checklist

### Repository / build

- [x] `pyproject.toml`
- [x] dependency lockfile — `uv.lock`, CI installs with `--locked`
- [x] environment config — `config/base.yaml` + `config/paper.yaml`, versioned by content hash
- [x] strict typing — mypy `strict`, clean over 88 source files
- [x] linting — ruff check + format, clean
- [x] unit tests — 479
- [x] property tests — 11 Hypothesis-driven; 28 end-to-end replay tests;
      86 integration tests needing a real PostgreSQL (they skip loudly without one)
- [x] schema migrations — Alembic baseline `ce70efeb9fe9`; a migrated database
      is asserted to match the application's metadata, and a `pg_dump` restore
      is asserted to reproduce the run
- [ ] Windows integration tests — no MT5 code to integrate yet (M1)
- [ ] CI pipeline — workflow committed, never run (no remote)

### MT5

- [ ] `initialize`
- [ ] account validation
- [ ] environment validation
- [ ] terminal health
- [ ] `symbol_info`
- [ ] `symbol_info_tick`
- [ ] bars/ticks collection
- [ ] `order_check`
- [ ] `order_send`
- [ ] positions
- [ ] orders
- [ ] history
- [ ] reconciliation
- [ ] reconnect behavior

### Risk

Per capability, because "implemented" and "validated" are different claims
(F-005). `impl` / `unit` / `replay` / `MT5` / `paper`:

| Capability | impl | unit | replay | MT5 | paper |
|---|:--:|:--:|:--:|:--:|:--:|
| stale-data check | x | x | x | | |
| spread check | x | x | x | | |
| instrument allow-list | x | x | x | | |
| stop validation | x | x | x | | |
| volume normalisation | x | x | x | | |
| risk-based size calculation | x | x | x | | |
| max open risk | x | x | x | | |
| max daily loss | x | x | x | | |
| max drawdown | x | x | x | | |
| order-frequency protection | x | x | x | | |
| duplicate protection | x | x | x | | |
| automatic HALT | x | x | x | | |
| manual HALT | x | x | | | |
| manual reset workflow | x | x | | | |
| durable HALT across restart | x | x | | | |
| cancel pending orders | x | x | | | |
| flatten positions | x | x | | | |
| one exposure per symbol (O-004) | x | x | x | | |
| intraday entry cut-off (O-003) | x | x | x | | |
| overnight-exposure halt (O-003) | x | x | x | | |
| account currency / leverage guard | x | x | x | | |
| automatic flatten at the deadline | | | | | |
| execution-time revalidation | | | | | |

No capability is MT5-integrated or paper-validated, and none can be until M1.

### Data

Persisted by the running orchestrator, not merely storable (D-030 closed).

- [x] raw ticks — `market_ticks`, written for every window the run observed
- [x] raw bars — `market_bars`, each carrying its origin and, when derived, the
      pipeline version that produced it
- [x] symbol specs — `instrument_specs` table exists; no producer until M1
- [ ] features — the hash and version are journalled, the values are not
- [x] signals — `SignalGenerated`, one per evaluated window including NO_TRADE
- [x] trade intents — `TradeIntentCreated`
- [x] risk decisions — `RiskDecisionMade`
- [x] supervisor decisions — `SupervisorDecisionMade`
- [x] orders/fills — `OrderCheckCompleted`, `OrderSubmitted`, `OrderResultReceived`
- [x] positions — `PositionChanged`, before and after each fill
- [x] decision capsules — in the journal and in `decision_capsules`
- [ ] incidents — the contract exists; no register, so nothing produces one
- [x] model/config versions — carried on every capsule; `config_versions` table exists
- [x] safety state — `safety_state_events`, read with the file latch (ADR-002)
- [x] risk-session state — `risk_session_states`, recovered on startup (F-019)

## Platform quality metrics

Fill during operation.

```text
MT5 uptime:
Data freshness p50:
Data freshness p95:
Gateway errors / day:
Reconnects / day:
Order rejection rate:
Duplicate orders:
Reconciliation mismatches:
Mean order latency:
p95 order latency:
Critical incidents:
```

## Open platform issues

Records are updated in place, never deleted — a closed issue is evidence that
something was found and dealt with (F-009).

| ID | Severity | Issue | Owner | Status | Resolution |
|---|---|---|---|---|---|
| APP-001 | HIGH | No FLATTEN POSITIONS control exists; a halt stops new orders but cannot close an open position (build.md §8.2, D-027) | | **CLOSED 2026-08-17** | Three separately authorised, separately logged controls in `risk/operator_controls.py`; tests assert they are decoupled. **Not MT5-validated** — real broker behaviour is unproven until M1/M5 (review 1.1 F-008) |
| APP-002 | HIGH | Supervisor receives hard-coded `open_incident_count=0` and `reconciliation_matched=True`; those checks can never fire, yet an approval reads as though they passed (D-028) | | **CLOSED 2026-08-17** | `ReconciliationStatus` / `IncidentStatus` with explicit `UNKNOWN`, both defaulting to it. Unknown reconciliation halts, unknown incident vetoes, and the safety gate sits above the policy-enable switch |
| APP-003 | HIGH | Kill-switch state did not survive a restart (review 1.0 F-003) | | **CLOSED 2026-08-17** | `SafetyStateStore` + atomic file store; startup begins disabled. Cross-process restart evidence in `tests/integration/test_halt_survives_restart.py` |
| APP-004 | MEDIUM | Safety-state authority undefined once PostgreSQL arrives (review 1.1 F-012) | | **CLOSED 2026-08-18** | `CompositeSafetyStateStore` is what `application/bootstrap.py` hands the kill switch. Disagreement resolves to HALTED; a fresh database starts UNKNOWN and refuses orders |
| APP-005 | HIGH | Persistence invariants for the event journal (review 1.3 F-015) | | **CLOSED 2026-08-18** | All ten ADR-003 acceptance tests pass against PostgreSQL 17, including replay-from-journal reproducing the in-memory run |
| APP-006 | MEDIUM | Persistence exists but the orchestrator does not use it (D-030) | | **CLOSED 2026-08-18** | Every stage writes through `RunRecorder`; the run is reproducible from the `events` table alone and survives a real process restart. 19 integration tests |
| APP-007 | HIGH | Risk-session state reset in the permissive direction on restart — a crash refilled the daily-loss budget (review 1.5 F-019) | | **CLOSED 2026-08-18** | `risk/session.py` recovers the session and can only ever tighten it; unreadable, future-dated or position-mismatched records halt. 18 unit + 8 restart tests |
| APP-008 | HIGH | The journal records decisions but not the market data they were made from; warm-up windows leave no trace at all (D-031) | | **CLOSED 2026-08-18** | `market_ticks` and `market_bars` written on the ordinary path for every observed window; tick→bar pipeline with gap, out-of-order, duplicate and crossed-quote detection. 46 tests |
| APP-010 | MEDIUM | `metadata.create_all` was the only way to build the schema, on a database that now holds data (D-029) | | **CLOSED 2026-08-18** | Alembic baseline; the runtime migrates rather than creates; a restored `pg_dump` is proven to reproduce the run |
| APP-011 | MEDIUM | Two supervisor checks could not fire but reported as passed (D-015, D-028, EV-002) | | **CLOSED 2026-08-18** | Threshold set to `null` = uncalibrated; every decision carries `uncalibrated_checks` and the run report names them. The calibration itself still needs real data |
| APP-012 | HIGH | Nothing enforced the owner's one-exposure and intraday decisions (O-003, O-004) | | **PARTLY CLOSED 2026-08-18** | One-exposure is a hard constant with the reviewer's four cases tested. Intraday entries are refused and a breach halts — **the automatic flatten is M5** (D-033, ADR-004) |
| APP-014 | MEDIUM | The MT5 adapter has never run against a terminal; the fake it was tested against was written from documentation, not observation (D-035) | | OPEN | Treat first contact as discovery; record disagreements as deviations rather than patching silently |
| APP-015 | MEDIUM | `symbol_info.filling_mode` and `trade_mode` are integer enums stored as strings; the filling mode is a bitmask, so `"3"` is recorded where `FOK\|IOC` was meant (D-037) | | OPEN | `scripts/mt5_probe.py` decodes and prints both readings. Settle it at first contact, then correct the adapter and the fake together |
| APP-013 | MEDIUM | The Pepperstone entity is ambiguous: O-001 says EU, the supplied server says UK | | OPEN | Different regulator, leverage cap and swap treatment. Resolve before M1 is called complete (D-034) |
| APP-009 | MEDIUM | `feedback.1.4.md` is referenced by review 1.5 but is not in the repository; findings F-016 and F-017 are unaccounted for | | **CLOSED 2026-08-18** | Restored unchanged by the owner. F-016 and F-017 were already resolved by the tracker and deviation rewrites; F-020 was new and is now closed |

---

# 4. Component 2 — Trading Agent

## Current status

```text
Status: BUILDING
Owner:
Current milestone: M6
Current strategy: ict_v1 (sweep + shift + displacement gap + OTE, in killzones)
Champion version: none — nothing has been promoted
Challenger versions: baseline_v1 retained as the §9.2 benchmark
Implementation maturity: REPLAY-TESTED
Gate qualification: FEATURE FREEZE (review F-004)
Next objective: Evidence on real data. No new strategy concepts.
```

## Agent contract

The Trading Agent may:

- read normalized market/features;
- read permitted portfolio/risk context;
- choose `BUY`, `SELL` or `NO_TRADE`;
- propose stop/target structure within policy;
- produce confidence and reason codes.

The Trading Agent may not:

- call MT5;
- access broker credentials;
- bypass risk checks;
- choose unrestricted final lot size;
- alter production risk config;
- reset HALT;
- promote itself to live.

## Strategy/version tracker

| Version | Type | Environment | Status | Started | Ended | Notes |
|---|---|---|---|---|---|---|
| baseline_v1 | deterministic | replay | BUILDING | 2026-08-17 | | Benchmark per §9.2. No edge claimed or measured. |
| ict_v1 | deterministic | replay | BUILDING | 2026-08-17 | | ICT entry model, ten enforced conditions. ~3 setups per 12k M5 bars on synthetic data — a number that means nothing on a random walk. |

## Research checklist

- [ ] no-trade benchmark
- [ ] deterministic baseline
- [ ] feature specification
- [ ] leakage tests
- [ ] walk-forward splitter
- [ ] realistic cost model
- [ ] model baseline
- [ ] calibration analysis
- [ ] regime analysis
- [ ] champion/challenger protocol
- [ ] exact model versioning
- [ ] deterministic feature replay

## Trading Agent KPI snapshot

```text
Evaluation window:
Closed trades:
No-trade decisions:
Long intents:
Short intents:

Net P&L:
Expectancy/trade:
Profit factor:
Hit rate:
Avg win:
Avg loss:
Max drawdown:
Time under water:
Sharpe:
Sortino:
Calmar:

Avg spread paid:
Avg slippage:
p95 slippage:
Avg holding time:
Exposure time:

Prediction calibration:
Drift state:
```

## Regime breakdown

| Regime | Trades | Expectancy | P&L | Drawdown | Notes |
|---|---:|---:|---:|---:|---|
| Trend | | | | | |
| Range | | | | | |
| High volatility | | | | | |
| Low volatility | | | | | |
| Unknown | | | | | |

## Promotion evidence

```text
Candidate:
From gate:
To gate:
Reason:
Out-of-sample evidence:
Cost assumptions:
Regime coverage:
Known weaknesses:
Decision:
Reviewer:
Date:
```

## Open Trading Agent issues

| ID | Severity | Issue | Owner | Status | Resolution |
|---|---|---|---|---|---|
| TA-001 | | | | | |

---

# 5. Component 3 — Evaluator / Supervisor Agent

## Current status

```text
Status: BUILDING
Owner:
Current milestone: M7
Policy version: supervisor-policy-v1
Statistical monitor version: none
LLM analyst enabled: NO
Implementation maturity: UNIT-TESTED
Gate qualification: NOT PASSED — safety work only (review F-007 gate decision)
Next objective: Real incident and reconciliation inputs, then post-trade scorecard
```

## Supervisor permissions

```text
Can approve intent:       YES
Can veto intent:          YES
Can trip HALT:            YES
Can submit order:         NO
Can create a BUY/SELL:    NO
Can change lot size:      NO
Can change risk policy:   NO
Can reset HALT:           NO
Can promote strategy:     NO
```

## Evaluator layers

| Layer | Status | Purpose |
|---|---|---|
| Deterministic policy | BUILDING | hard safety |
| Statistical monitor | NOT STARTED | drift/anomaly detection |
| LLM analyst | DISABLED | explanation/research only |

## Pre-trade checks

- [ ] strategy allowed
- [ ] model version allowed
- [ ] known regime
- [ ] confidence sane
- [ ] signal-frequency sane
- [ ] spread sane
- [ ] market data current
- [ ] no duplicate intent
- [ ] recent execution behavior sane
- [ ] no active incident
- [ ] no active HALT

## Post-trade checks

- [ ] slippage
- [ ] spread cost
- [ ] latency
- [ ] MAE/MFE
- [ ] expected vs realized
- [ ] stop/target behavior
- [ ] exit reason
- [ ] calibration
- [ ] drawdown contribution
- [ ] anomaly flags

## Drift dashboard

```text
Feature drift:
Prediction drift:
Performance drift:
Spread drift:
Latency drift:
Trade-frequency drift:
Reject-rate drift:
Live-vs-backtest divergence:
```

## Evaluator KPI snapshot

```text
Window:
Intents evaluated:
Approvals:
Vetoes:
Halts:
False-positive veto reviews:
Critical anomalies:
Warnings:
Incidents opened:
Incidents closed:
```

## Open Evaluator issues

| ID | Severity | Issue | Owner | Status | Resolution |
|---|---|---|---|---|---|
| EV-001 | HIGH | Four of seven pre-trade checks were inert — confidence band, signal frequency, active incident, reconciliation (D-028) | | **SPLIT 2026-08-17** | Incident and reconciliation resolved: both now carry explicit `UNKNOWN` and fail closed. The two calibration items are carried forward as EV-002 |
| EV-002 | MEDIUM | Confidence band (0.0-1.0) and signal-frequency threshold (20/hour on an M5 cadence) are configured to values nothing can fall outside, so neither can fire (D-015) | | OPEN | Recalibrate once the bar interval is settled — build.md §29 Q3 |

---

# 6. Paper-trading campaign

## Campaign status

```text
Campaign: NOT STARTED
Environment: MT5 DEMO
Broker:
Server:
Canonical symbol: EUR/USD
Broker symbol:
Start date:
End date:
Strategy version:
Evaluator policy version:
Risk config version:
```

## Daily paper log

| Date | Trades | P&L | Max DD | Vetoes | Errors | Reconnects | Incidents | Verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| | | | | | | | | |

## Weekly review

```text
Week:
Reliability:
Trading behavior:
Execution quality:
Drift:
Largest incident:
Largest unexpected behavior:
Changes made:
Changes intentionally NOT made:
Promotion status:
```

### Anti-overfitting rule

Do not change strategy parameters after every bad day.

Every strategy/config change must create a new version and reset or explicitly segment evaluation evidence.

---

# 7. Shadow-mode campaign

```text
Status: NOT STARTED
Start:
Live feed/account context:
Orders actually submitted: 0
```

Track:

| Metric | Demo | Shadow | Difference | Acceptable? |
|---|---:|---:|---:|---|
| Median spread | | | | |
| p95 spread | | | | |
| Signal count | | | | |
| Hypothetical fill slippage | | | | |
| Data gaps | | | | |
| Reconnects | | | | |

---

# 8. Kill-switch log

| Time UTC | Trigger | Source | State before | State after | Operator reset | Incident |
|---|---|---|---|---|---|---|
| | | | | | | |

Current state:

```text
NEW ORDERS: DISABLED
PENDING ORDER CANCEL: MANUAL
POSITION FLATTEN: MANUAL
HALT reason: Project not yet in paper mode
```

---

# 9. Incident register

Severity:

```text
SEV-0 = potential uncontrolled financial exposure
SEV-1 = execution/risk safety compromised
SEV-2 = degraded operation or material data problem
SEV-3 = non-critical defect
```

| Incident | Severity | Opened | Closed | Component | Root cause | Corrective action | Regression test |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

A SEV-0 or unresolved SEV-1 blocks promotion.

---

# 10. Decision log

Use this for architectural or risk decisions.

| Date | Decision | Why | Alternatives rejected | Owner |
|---|---|---|---|---|
| 2026-08-17 | Separate Trading Agent from MT5 execution | Prevent strategy/model from bypassing deterministic controls | Direct agent→MT5 | |
| 2026-08-17 | Evaluator gets veto/halt but no execution rights | Independent control plane | Second trading agent | |
| 2026-08-17 | Paper → shadow → live-canary | Reduce deployment risk | Backtest directly to live | |
| 2026-08-17 | MT5 Gateway on Windows x86-64 | Current official Python wheel distribution | Unsupported direct Linux dependency | |
| 2026-08-17 | Develop on macOS arm64; defer the gateway to a Windows x86-64 host | Everything except the gateway is host-independent; `MetaTrader5` is marked `sys_platform == 'win32'` so `uv sync` works on both | Wine, x86 emulation, developing only on Windows | |
| 2026-08-17 | Package as `src/crumblr/…` rather than build.md §5's `src/domain/…` | Avoids claiming top-level import names like `domain` and `risk` | Literal §5 layout | |
| 2026-08-17 | All monetary values are `Decimal`; `float` rejected at the model boundary | `Decimal(1.1)` carries a silent binary error, and in a stop-loss price that is both invisible and expensive | Floats with rounding discipline | |
| 2026-08-17 | `stop_loss_price` mandatory on BUY/SELL intents | Position size is derived from stop distance, so an unstopped intent is not merely risky — it is unsizeable | Optional stop with a later risk-engine check | |
| 2026-08-17 | Identity by content hash: `decision_hash`, `spec_version`, `config_version`, `provenance_fingerprint` | Makes §11 auditability and §13.3 replay determinism checkable rather than aspirational | Sequential version numbers | |
| 2026-08-17 | No `config/live.yaml` in the repository; live needs both an in-file acknowledgement and `CRUMBLR_ALLOW_LIVE=1` | A live config that merely exists is one editing mistake away from being selected | Ship live.yaml disabled by a flag | |
| 2026-08-17 | Freeze `ict_v1` feature development until real EUR/USD data exists | Review F-004: strategy sophistication was running ahead of any evidence. Bug, determinism, contract and test work continue; new concepts, extra confirmations, parameter tuning and ML overlays do not | Continue building ICT v2 | Reviewer |
| 2026-08-17 | Local Git allowed; remote deferred until collaboration | Review F-006. Note: the recorded agreement was to hold *commits* until a working prototype, and `status.md` §2 lists "Repository created" as an M0 exit criterion — so `git init` executed the plan rather than departing from it. No commit has been made | Removing the local repository | Reviewer + owner |
| 2026-08-17 | Safety-critical state is represented as MATCHED/MISMATCHED/UNKNOWN, never as a boolean | Review F-002. A boolean forces "not checked" and "checked and fine" into one value, and the safe-looking one wins by default | Boolean with a safe default | Reviewer |
| 2026-08-17 | System safety state is persisted; startup begins with new orders disabled | Review F-003. A halt that does not survive a restart is not a halt | In-memory only until M2 | Reviewer |
| 2026-08-17 | Execution-time risk revalidation before `order_send` (ADR-001) | Review F-007. State can change between approval and submission | Single check at intent time | Reviewer |
| 2026-08-18 | The event journal is append-only and identity is producer-assigned (ADR-003) | Review F-015. A journal that can be edited is not an audit trail, and a database sequence is not identity across a rebuild | Mutable rows with a serial primary key | Reviewer |
| 2026-08-18 | M2 is complete when a replay driven from the persisted journal reproduces the in-memory run byte for byte | Review F-015. Storing rows is not the same as being able to prove what happened | Row counts as the completion signal | Reviewer |
| 2026-08-18 | Skip review 1.4; build M2 first | Two consecutive review rounds produced documentation only. `feedback.1.3.md` §8 itself triggers 1.4 on M2 availability, and §9 states simulated development has exhausted its value. There is nothing new to review until the persistence layer exists | Another documentation-only review cycle | Project owner |
| 2026-08-18 | Journal event ids are derived from the event type, its window and its payload, never from `uuid4` | ADR-003 invariant 3 makes an append idempotent on `event_id`, which only converges if the same logical event yields the same id. A random id turns a rerun after an ambiguous outcome into duplicated history | Random ids with a de-duplication pass on read | |
| 2026-08-18 | A decision window's events and its sealed capsule commit in one transaction; a halt is flushed immediately | ADR-003 invariant 5 wants a multi-row transition to be atomic. A halt is the exception because a safety event that waits for the next commit is one a crash can lose | One transaction per event; or buffering halts with the rest | |
| 2026-08-18 | Risk-session recovery may only ever tighten; anything it cannot establish halts | Review F-019. A restart that refills a spent daily-loss budget moves a safety limit in the one direction it may never move on its own | Trusting the record as-is; recomputing from live equity alone | Reviewer |
| 2026-08-18 | A position-count disagreement between the record and the account halts, in both directions | Review 1.5 §4 step 8 makes this the rule at M5. Applying it now to the only position book that exists means the reconciliation path is exercised before MT5 rather than written for the first time against it | Deferring all reconciliation to M5 | Reviewer |
| 2026-08-18 | The owner's v1 decisions: Pepperstone EU demo on MT5, an M5 decision cadence, no overnight positions, one EUR/USD exposure at a time | Review 1.5 O-001…O-004. Broker choice unblocks M1; the cadence fixes what the supervisor's frequency threshold must be calibrated against | Multi-broker routing in v1; deferring the timeframe decision | Project owner |
| 2026-08-18 | The FX session boundary is 17:00 America/New_York and is not configurable (ADR-004) | O-003 forbids overnight positions, and "overnight" is defined by the rollover that charges swap. A configurable boundary would be a second definition of the trading day, able to drift from the one the daily-loss baseline already uses | Midnight UTC; a configurable clock time | Reviewer + owner |
| 2026-08-18 | One EUR/USD exposure is a constant in code, not a configuration field | O-004 is a business rule the owner approved, not a budget to tune. A YAML key would let someone raise it without the decision that should accompany doing so | `max_exposures_per_symbol` in `config/` | Project owner |
| 2026-08-18 | The intraday flatten is detected and halted on, not performed | Refusing to open is safe and ships now; promising to close is a promise the system cannot keep before M5. A policy claiming otherwise would read as though positions were managed out when nothing was managing them | Building an automatic flatten ahead of the M5 gate | Reviewer |
| 2026-08-18 | The supervisor's frequency check is marked uncalibrated rather than given a number | Review 1.6 F-024. O-002 fixed the cadence at M5, which permits 12 windows an hour, so the old threshold of 20 was an absent control wearing a number. The only honest calibration needs real EUR/USD observations | A structural rate limit derived from synthetic trade frequency | Reviewer |
| 2026-08-18 | A bar records its origin and, when derived, the pipeline version that made it | A bar the broker sent and one this platform built are not interchangeable evidence, and nobody will remember which was which. Without the version, changing the aggregation rules silently rewrites history | Storing bars as bare OHLC rows | |
| 2026-08-18 | A conflicting bar for an interval already stored raises rather than being ignored | build.md §26 requires raw data to be immutable. Keeping whichever arrived first satisfies the wording and loses the contradiction, which is the finding | `ON CONFLICT DO NOTHING` for bars as well as ticks | |
| 2026-08-18 | Alembic is the deployment path; `create_all` is for tests only | Ordinary runs now write data. A schema built one way and migrated another is two schemas that happen to agree today | Keeping `create_all` everywhere until a deployment exists | Reviewer |
| 2026-08-18 | The account guard checks currency and leverage as well as server and login | Both change what a risk budget means without changing anything the strategy or the risk engine can otherwise see. Review 1.5 requires broker metadata to be discovered and verified, never assumed | Trusting the configured values | Project owner |
| 2026-08-17 | Risk values in `config/paper.yaml` are provisional placeholders | They encode risk policy, which build.md §29 reserves for a human; the platform still needs loadable values to be testable | Leave them unset and block all progress | |

---

# 11. Current risks / assumptions

| Risk / assumption | Impact | Mitigation | Status |
|---|---|---|---|
| Broker execution differs from demo | High | shadow mode + conservative promotion | OPEN |
| Backtest leakage | High | causal feature tests + walk-forward | OPEN |
| Duplicate order after crash/reconnect | Critical | idempotency + reconciliation | OPEN |
| Stale tick interpreted as current | Critical | data-age policy + HALT | OPEN |
| Strategy overfits one regime | High | regime breakdown + challengers | OPEN |
| LLM behaves nondeterministically | High | no direct execution privileges | MITIGATED BY DESIGN |
| Broker symbol/spec differs | High | dynamic instrument registry | OPEN |
| Risk config accidentally omitted | Critical | no permissive defaults | MITIGATED BY DESIGN |
| Operator cannot close a position once open | High | FLATTEN POSITIONS built 2026-08-17; unproven against a real broker | MITIGATED — pending MT5 validation |
| Restart clears a halt | Critical | durable safety state + fail-closed startup; cross-process restart evidence recorded | CLOSED 2026-08-17 |
| Safety state disagrees between file and event journal | High | precedence rule in ADR-002: any disagreement resolves to HALTED | CLOSED 2026-08-18 — `CompositeSafetyStateStore` is on the normal path |
| Restart refills a spent daily-loss budget | Critical | risk-session state persisted and recovered; recovery can only tighten | CLOSED 2026-08-18 |
| The journal records what was decided but not what it saw | High | raw tick and bar storage, written on the ordinary path | CLOSED 2026-08-18 for ticks and bars; feature *values* are still only hashed (D-031) |
| A schema change loses data nobody can regenerate | Medium | Alembic baseline, migrated deployment path, proven restore | CLOSED 2026-08-18 — no backup *schedule* exists yet, only a proven restore |
| A position is carried through the 17:00 rollover | High | entries refused inside the window; a surviving exposure halts | MITIGATED — detection only. The flatten itself is M5 (D-033) |
| A second EUR/USD exposure is opened | High | hard constant in the risk engine, above the account model | CLOSED 2026-08-18 |
| The connected account is not the one that was configured | High | server, login, currency and leverage all checked; mismatch halts | MITIGATED — never verified against a terminal |

---

# 12. Next 10 actions

Done in this pass:

- [x] Create repository and Python 3.12 environment.
- [x] Add `pyproject.toml`, lockfile, pytest and type checker.
- [x] Implement Pydantic domain/event contracts.

- [x] Persist the decision flow, ticks and bars to PostgreSQL (M2).
- [x] Version the schema with Alembic and prove a restore (F-020, F-023).
- [x] Make the risk session survive a restart (F-019).
- [x] Encode the one-exposure rule and the intraday window (O-003, O-004).
- [x] Implement the deterministic risk policy engine and kill switch (M4).

Blocked on a human decision — see build.md §29 and `review/feedback.1.6.md` §6:

- [x] ~~§29 Q1 — broker~~ — Pepperstone (O-001).
- [ ] **Resolve the Pepperstone entity**: EU per O-001, UK per the server name
      supplied. D-034.
- [ ] Create the Pepperstone MT5 demo account; login and password to the secret
      store, never to Git, logs, status or review documents.
- [ ] §29 Q2 — hedging or netting, read from `account_info()` on that account.
      Do not guess; v1 supports exactly one mode.
- [ ] Confirm or replace the provisional risk budget in `config/paper.yaml`
      (§29 Q7-Q8) — D-013.
- [ ] Confirm the intraday cut-off and flatten offsets — ADR-004 §3.
- [ ] Review and approve the domain contracts (M0).
- [ ] Decide the CI exception, or run CI on a runner (M0) — review 1.6 §5.

Next engineering steps once unblocked:

- [ ] Provision a Windows x86-64 host with the MetaTrader 5 terminal.
- [ ] Implement the Windows MT5 read-only connection (M1). No `order_send`.
- [ ] Discover the real EUR/USD broker symbol and its full specification.
- [ ] Persist real Pepperstone ticks and bars immediately — the store and the
      tick→bar pipeline exist and have never seen a real quote.
- [ ] Implement the reconciliation loop (M5 prerequisite).
- [ ] Store feature values, not only their hash — D-031.

---

# 13. Update log

## Update 2026-08-17

```text
Component: 1 — Platform / Application
Milestone: M0 — repository and engineering baseline
Status before: NOT STARTED
Status after:  BUILDING (M0 substantially complete)
```

**Completed**

- Python 3.12.14 project scaffolded with `uv`, ruff, mypy (strict) and pytest.
- Pydantic v2 domain contracts: `MarketSnapshot`, `Bar`, `InstrumentSpec`,
  `TradeIntent`, `RiskDecision`, `SupervisorDecision`, `ApprovedOrder`,
  `ExecutionResult`, `AccountState`, `PositionState`, `Incident`,
  `DecisionCapsule`. All frozen, all `extra="forbid"`.
- Event journal vocabulary: all 13 event types from build.md §23 plus
  `DecisionCapsuleSealed`, in a generic envelope carrying `correlation_id`,
  `causation_id` and `schema_version`.
- Versioned, fail-closed configuration with no risk defaults.
- `.gitignore`, `.env.example`, CI workflow (Linux + Windows + gitleaks).

**Evidence**

- tests: 185 passed (174 unit, 11 property). `uv run pytest`
- coverage: 99% line, 98% branch over `src/crumblr`
- types: `uv run mypy` — clean, strict, 23 files
- lint: `uv run ruff check .` and `ruff format --check .` — clean
- artifact/commit: staged in git, not yet committed

**Problems found**

- Pydantic `computed_field` values are emitted by `model_dump` but rejected by
  `extra="forbid"` on reload, which silently broke journal round-trips for
  every model carrying a derived hash. Fixed on the `Contract` base class:
  computed fields are dropped on input and recomputed. Caught by the event
  round-trip tests, not by inspection.
- Ruff's `TCH` rules propose moving pydantic field types into `TYPE_CHECKING`
  blocks, which turns into a runtime `NameError` the moment such a type is used
  as a model field. Rule deselected, with the reason recorded in `pyproject.toml`.

**Risk impact**

- Three risks in §11 are now partially mitigated by construction rather than by
  intent: "risk config accidentally omitted" (required fields), "stale tick
  interpreted as current" (mandatory UTC + age helper), and "broker symbol/spec
  differs" (dynamic registry with change detection by content hash).
- No execution risk exists yet: nothing in the codebase can reach a broker.

**Decision**

- Proceed to M1 only after the broker and demo account are chosen. Building a
  gateway against an unknown broker's symbol conventions would be guesswork.

**Next**

- Resolve build.md §29 Q1, Q2, Q7 and Q8.
- Provision the Windows x86-64 MT5 host.

---

## Update 2026-08-17 (second pass — runnable prototype)

```text
Component: all three
Milestone: M3/M4/M6/M7 partial
Status before: M0 only, nothing executable
Status after:  end-to-end replay running against a simulated broker
```

**Completed**

- Deterministic synthetic market generator with injectable faults (§20, §25.5).
- Causal feature pipeline (EMA/ATR/regime) and `baseline_v1`, a deliberately
  simple trend strategy that returns NO_TRADE in roughly 80% of windows.
- Full §8.1 pre-trade risk checklist, risk-based position sizing from equity,
  stop distance and symbol spec, plus the kill switch and equity ledger.
- Supervisor layer-1 deterministic veto policy.
- `BrokerPort` protocol with a simulated implementation; the real MT5 adapter
  drops into the same interface at M1.
- `ReplayOrchestrator` running the complete §3 flow and sealing a decision
  capsule per window.
- `scripts/run_replay.py` with `--chaos`, `--max-daily-loss` and
  `--wrong-server` to demonstrate each guardrail.

**Evidence**

- tests: 257 passed (225 unit, 11 property, 21 replay)
- coverage: 95% line over `src/crumblr`
- types: `uv run mypy` — clean, strict, 47 files
- replay: 900 bars → 93 intents, 80 approved, 80 filled, 0 fills after a halt

**Problems found**

- The replay was not reproducible: the generator seeded its clock from
  `utc_now()`, so provenance fingerprints differed between identical runs.
  Wall-clock time was leaking into replay input, which §13.3 forbids. Fixed
  with a fixed `REPLAY_EPOCH`. Caught by a determinism test, not by review.
- `baseline_v1` proposed ATR stops tighter than the configured minimum stop
  distance in calm regimes, so 78% of intents were blocked as `INVALID_STOP`.
  The agent now receives the policy floor and proposes stops within it, per
  the §9.1 agent contract.
- The supervisor's signal-frequency threshold was set to 12 per hour while an
  M5 cadence permits at most 12 windows per hour, so it vetoed 52% of ordinary
  traffic. A control that refuses half of normal operation trains its operator
  to ignore it. Threshold recalibrated above the structural maximum and the
  calibration requirement documented.
- Max drawdown was reported as the drawdown at the end of the run rather than
  the deepest reached. Since drawdown is a promotion criterion (§15), the
  ledger now tracks the running maximum.

**Risk impact**

- Still zero execution risk: no component can reach a broker.
- Two §11 risks now have working mitigations rather than intentions:
  "duplicate order after crash/reconnect" (idempotency on `order_request_id`,
  proven in test) and "stale tick interpreted as current" (blocked with a
  recorded reason code under fault injection).

**Decision**

- Do not tune `baseline_v1` for better synthetic P&L. It exists to exercise
  infrastructure, and optimising it against a random walk would be the
  overfitting §6 of status.md warns about.

**Next**

- Broker and demo account selection remain the blocking decisions.
- Persist the event journal and decision capsules to PostgreSQL (M2).

---

## Update 2026-08-17 (third pass — review loop)

```text
Component: process
Milestone: n/a
Status before: status.md updated per session; no deviation register, no feedback loop
Status after:  reviewer-facing documentation in place
```

**Completed**

- `review/DEVIATIONS.md` — nineteen recorded departures from `build.md`, each
  with spec reference, rationale, status (deliberate / provisional / pending)
  and what to watch. Keyed `D-001`…`D-019` so findings can cite them.
- `review/FEEDBACK.md` — the inbox the reviewing agent writes into, with a
  severity scale and the response each severity requires.
- `CLAUDE.md` — working agreements, including the session-start protocol:
  open findings are resolved before new work begins.
- Pointer table at the top of this document so a reviewer lands in the right
  place.

**Evidence**

- No code changed in this pass. Quality gate re-run unchanged: 257 tests, 95%
  coverage, mypy strict clean, ruff clean, replay byte-identical across runs.

**Problems found**

- The previous two passes recorded *what* was built and *what went wrong*, but
  not *where the implementation departs from the specification*. That is the
  gap a reviewer most needs closed, and it was the one thing missing. Nineteen
  deviations existed; none were written down as such.

**Risk impact**

- Two deviations deserve attention above the rest and were previously only
  implicit: `D-011` (a restart clears the kill switch, because it is in-memory)
  and `D-009`/`D-010` (synthetic data and an incomplete cost model mean no
  performance figure from this system is currently meaningful).

**Decision**

- `build.md` is never edited to match the implementation. Gaps are recorded,
  not closed by rewriting the specification.

**Next**

- Unchanged: broker and demo account selection remain the blocking decisions.

---

## Update 2026-08-17 (fourth pass — ICT entry model)

```text
Component: 2 — Trading Agent
Milestone: M6
Status before: baseline_v1 (moving-average separation) as the configured strategy
Status after:  ict_v1 configured; baseline_v1 retained as the benchmark
```

**Completed**

- ICT primitives, each with one exact definition: market structure (swings,
  break of structure, market structure shift, dealing range, premium/discount),
  imbalance (fair value gaps, displacement, order blocks), liquidity (pools,
  sweeps, targets) and sessions (killzones, trading week).
- `ict_v1`: ten separately enforceable conditions. A trade requires a liquidity
  sweep, a structure shift, a displacement gap, price retraced back into that
  gap, a discount/premium location and an OTE retracement, inside a killzone,
  on an open market.
- Killzones computed in New York local time via `zoneinfo`, so daylight saving
  is handled rather than hard-coded.
- Strategy registry and a `Strategy` protocol; `strategy_id` in configuration
  selects which strategy runs, and an unknown id fails loudly.
- Session volatility profile and a real trading week in the replay generator.

**Evidence**

- tests: 344 passed (305 unit, 11 property, 28 replay). 80 cover the ICT model, built on hand-constructed
  bar sequences where the correct answer is known by construction.
- types: `uv run mypy` — clean, strict, 56 files
- replay: `ict_v1` completes a 12,000-bar replay deterministically

**Problems found**

- Four real defects, each caught by measurement rather than review. In order:
  the supervisor vetoed every valid ICT setup, because a setup entered on a
  structure shift reported an UNKNOWN regime; the entry trigger was missing, so
  location was judged at the displacement extreme where every long is in
  premium; sweeps were detected in 87% of windows because spent levels stayed
  sweepable; and premium/discount was measured against an unrelated swing range
  instead of the impulse leg.
- Separately: `max_daily_loss` was measuring loss since the start of the run,
  not since the start of a day, because nothing rolled the session baseline.
  See `review/DEVIATIONS.md` D-026.
- A quadratic scan in sweep detection made long replays take 17 seconds and the
  test suite time out. Precomputing first-touch per pool brought a 4,000-bar
  replay to 1.6 seconds.

**Risk impact**

- No change to execution risk: still nothing that can reach a broker.
- `ict_v1` produces roughly 3 intents per 12,000 M5 bars on synthetic data.
  That number is not evidence of anything, and has deliberately not been tuned.

**Decision**

- Do not loosen ICT conditions to raise the trade count on synthetic data. The
  model is tested against constructed setups instead, and the pipeline tests
  that need trade volume run `baseline_v1`.

**Next**

- Unchanged: broker and demo account selection remain the blocking decisions.
- §29 Q3 (strategy horizon) now matters more: it sets the bar interval, which
  determines whether the killzone windows and the M5 cadence are the right ones.

---

## Update 2026-08-17 (fifth pass — review feedback.1.0 processed)

```text
Component: all three
Trigger:   review/feedback.1.0.md — GO WITH CONDITIONS, M5/P2 NO-GO
```

**Findings closed**

- **F-002 (CRITICAL)** — `ReconciliationStatus` and `IncidentStatus` replace the
  two booleans. Both default to `UNKNOWN`. Unknown reconciliation halts; unknown
  incident state vetoes. The safety gate now sits *above* the `enabled` switch,
  so disabling supervisor policy cannot launder unknown state into an approval.
- **F-003 (HIGH)** — `SafetyStateStore` port with an atomically-written file
  implementation. `KillSwitch.on_startup` begins disabled and only releases on an
  explicitly RUNNING record. Missing, corrupt, wrong-schema and unparseable
  records all resolve to UNKNOWN, which counts as halted. The record is written
  before the in-memory state changes, so a failed write cannot leave a process
  believing it halted when nothing was recorded.
- **F-008 (HIGH)** — three separately authorised, separately logged operator
  controls. Tests assert the decoupling directly: halting does not close
  positions, flattening does not halt.
- **F-001 / F-005** — maturity ladder separated from gate qualification;
  progress percentages removed; the risk checklist is now per-capability across
  impl/unit/replay/MT5/paper.
- **F-004** — `ict_v1` feature freeze recorded in §10 and `CLAUDE.md` §4.
- **F-007** — `review/adr/ADR-001-execution-time-risk-revalidation.md`.

**Answered rather than actioned**

- **F-006** — decision adopted (local Git allowed, remote deferred), but the
  premise is corrected: the agreement was to hold *commits*, and "Repository
  created" is an M0 exit criterion in §2. `git init` executed the plan. No
  commit has been made.

**Evidence**

- tests: 384 passed (up from 344). 40 new tests covering fail-closed safety
  state and the operator controls.
- types: `uv run mypy` — clean, strict, 60 files
- lint: ruff clean

**Problems found while doing it**

- The supervisor's `enabled=False` path would have bypassed the new UNKNOWN
  checks entirely, turning "switch off policy judgement" into "switch off the
  safety gate". Restructured so safety state is evaluated first and is not
  subject to the policy toggle.
- Persisting after mutating in-memory state would let a failed disk write leave
  the process believing it had halted with nothing recorded. Order inverted.

**Risk impact**

- Two of the reviewer's three CRITICAL/HIGH execution-safety blockers for M5 are
  closed in code. M5 remains NO-GO: the rest need MT5, PostgreSQL and broker
  reconciliation.

**Next**

- PostgreSQL event persistence (M2). The reviewer's GO NOW item and the only
  major piece not blocked on a human decision or hardware.

---

## Update 2026-08-17 (sixth pass — review feedback.1.1 processed)

```text
Trigger: review/feedback.1.1.md — GO WITH CONDITIONS, M5/P2 NO-GO
Result:  every finding F-001 … F-012 now CLOSED; four at design level only
```

**F-003 was correctly reopened, and the reviewer was right**

The original evidence built two `KillSwitch` objects inside one interpreter.
That demonstrates the store round-trips; it demonstrates nothing about a
restart, because nothing restarted. `tests/integration/test_halt_survives_restart.py`
now spawns a real child process that writes the halt and exits, and asserts the
acceptance sequence literally. Also covered: truncated, empty, array-shaped,
wrong-schema, broken-timestamp and naive-timestamp records, plus an unwritable
destination — all fail closed.

**New findings closed**

- **F-009** — APP-001 and APP-002 closed in place with resolution text, APP-003
  added for the durable halt, APP-004 opened for safety-state authority, EV-001
  split so the two resolved checks and the two calibration items are not carried
  as one status. No record deleted.
- **F-010** — §2 rebuilt. `build.md` §26 deliverables and acceptance are listed
  as the specification states them; local project policy is a separate list; and
  broker, demo account, server and Windows host are marked as M1 dependencies
  rather than M0 engineering failures.
- **F-011** — ADR-001 amended. The final gate recomputes monetary risk from the
  *current executable side*, because a fixed volume does not mean fixed exposure:
  a BUY approved with 20 pips to its stop risks 60% more if the ask moves 12 pips
  before submission. Sizing is not recomputed; the volume holds or the order is
  blocked, never increased. Eight required tests specified.
- **F-012** — ADR-002 written. The event journal is the record of authority, the
  file store is an independent latch that survives the database being
  unreachable, and any disagreement resolves to HALTED. The rule underneath:
  never prefer the more permissive store.

**Problem found that neither review raised**

`build.md` §26 lists **logging** as an M0 deliverable. `structlog` is declared as
a dependency, `observability/` is an empty package, and nothing imports either.
It had been treated as delivered because the dependency existed. Now recorded in
§2 as not done. Found by checking the gate criteria against the specification
rather than against memory — which is exactly what F-010 asked for.

**Evidence**

- tests: 397 passed (up from 384), including 13 integration tests that spawn
  real child processes
- types: `uv run mypy` — clean, strict, 61 files
- lint: ruff clean · replay byte-identical across runs

**Risk impact**

- No change to execution risk. Nothing can reach a broker.
- The M5 blocker list is unchanged in substance: it needs MT5, PostgreSQL and
  broker reconciliation, none of which exist.

**Next**

- PostgreSQL event persistence (M2) — the reviewer's highest engineering
  priority and the only large piece not blocked on a human decision or hardware.
  ADR-002 must be implemented as part of it, not after.

---

## Update 2026-08-18 (seventh pass — review feedback.1.2 processed)

```text
Trigger: review/feedback.1.2.md — GO WITH CONDITIONS, M5/P2 NO-GO
Result:  F-013 closed and shipped; tracker semantics improved per §4
```

**F-013 — the missing M0 logging deliverable, now built**

`src/crumblr/observability/logging.py`. Structured JSON records carrying a UTC
timestamp, service and component fields, event name, severity and any bound
correlation id. Configured against a stream, so it needs no external
infrastructure — Prometheus, Loki and OpenTelemetry remain production concerns
and are explicitly not M0.

Two design choices worth stating, because both are load-bearing:

- **Logs go to stderr; the replay report goes to stdout.** The determinism gate
  hashes stdout. Interleaving log lines into it would break that gate, and break
  it non-deterministically, which is the worst possible failure for a check
  whose entire job is detecting non-determinism.
- **Emissions cannot raise into the caller.** Every log call swallows its own
  exceptions. A full disk must not throw an exception out of a halt. There is a
  test that trips the kill switch through a deliberately broken stream and
  asserts the halt still takes.

Emitting today: configuration load, safety-state recovery (both directions),
kill-switch trip and reset, replay start and finish, risk block and halt,
supervisor veto and halt.

**Tracker semantics (review 1.2 §4)**

The register now carries two fields instead of one. `Finding` answers whether
the reviewer's concern is resolved; `Implementation` answers what actually
exists — `SHIPPED`, `DECIDED` (an ADR with no code), or `PENDING M2`/`M5`.
Three findings are `DECIDED` with nothing built (F-007, F-011, F-012) and one is
shipped but unproven against a real broker (F-008). Under a single column all
four read as "CLOSED", which is exactly the misreading §4 warned about.

**Evidence**

- tests: 422 passed (up from 397); 25 new, covering all seven properties the
  review specified for logging
- types: `uv run mypy` — clean, strict, 63 files
- lint: ruff clean
- determinism: `run_replay.py --bars 2000 | md5` identical across runs **with
  logging enabled**, verified explicitly rather than assumed

**Problem found while doing it**

The first determinism test compared two `make_intent()` calls and failed — not
because logging changed anything, but because `feature_snapshot_id` is random
per intent and is part of the decision hash. The test was wrong, not the code.
Fixed by holding the id fixed. Worth recording because a test that fails for
the wrong reason is one step from a test that passes for the wrong reason.

**Risk impact**

- None. Logging is observability and cannot alter a trading result — a property
  now asserted rather than asserted about.

**Next**

- M2 PostgreSQL persistence, with ADR-002's authority semantics built into the
  recovery path rather than added afterwards.

---

## Update 2026-08-18 (eighth pass — review feedback.1.3 processed)

```text
Trigger: review/feedback.1.3.md — GO WITH CONDITIONS, M5/P2 NO-GO
Result:  F-014 and F-015 closed; M2 invariants decided before any database code
```

**F-014 — a stale note in my own tracker**

The finding register said logging was `CLOSED / SHIPPED` while a note further
down still said, in the present tense, that it "does not exist". Rewritten in
past tense, keeping the discovery itself because *how* it was found is the part
worth remembering: comparing gate criteria against the specification rather than
against memory.

Small, but it is the same class of defect as F-009 — a document contradicting
itself is worse than a document that is merely incomplete, because a reader has
no way to tell which half is current.

**F-015 — persistence invariants, decided before the layer exists**

`review/adr/ADR-003-persistence-invariants.md`. Ten invariants, ten acceptance
tests. Written now rather than after the schema, because a storage layer that
already exists tends to have its invariants inferred from whatever it happens
to do.

The load-bearing decisions:

- **Append-only, enforced by the database.** The application role gets `INSERT`
  and `SELECT` and nothing else, so a mistaken `UPDATE` fails as a permission
  error rather than succeeding quietly. Corrections are new events linked by
  `causation_id`.
- **Identity is producer-assigned.** `event_id` is a UUID made where the event
  is created. A `BIGSERIAL` may exist for physical ordering but is never
  identity — a sequence is unique to one database, and a replay against a
  rebuilt one would silently renumber everything.
- **Three clocks, never conflated.** `occurred_at_utc` (market time),
  `recorded_at_utc` (write time), `sequence` (insertion order). Replay orders by
  the first. Ordering by insertion time would reorder events after a reconnect
  backfill — exactly when order matters most.
- **Money is `NUMERIC`, never `float8`.** The domain rejects binary floats at its
  boundary; storing them as floats would reintroduce that error one layer down,
  where nothing is watching.

A useful thing fell out of writing it: most of what M2 needs already exists in
the domain layer. `event_id`, `correlation_id`, `causation_id`, `schema_version`,
`capsule_id` and the fingerprints are all there. M2's job is largely to carry
those across the boundary without losing them, not to invent them.

**The acceptance test that defines the milestone**

> A replay driven from the persisted journal reproduces the same decision
> sequence, byte for byte, as the in-memory replay.

Recorded as a decision in §10. Without it, the journal is storage rather than an
audit trail, and row counts would become the completion signal — which is what
the reviewer warned against.

**Evidence**

- No code changed. 422 tests still green, mypy strict clean, ruff clean.
- Two ADRs and one tracker correction.

**Risk impact**

- None directly. Indirectly: M2's definition of done is now a property, not a
  volume of rows.

**Next**

- Implement M2 against ADR-002 and ADR-003 together. Docker is available for a
  local PostgreSQL; no external dependency blocks this.

---

## Update 2026-08-18 (ninth pass — M2 persistence implemented)

```text
Component: 1 — Platform / Application
Milestone: M2 — PostgreSQL event journal
Maturity before: SPECIFIED (invariants decided, no code)
Maturity after:  UNIT-TESTED against real PostgreSQL 17
Gate:            still NOT PASSED — see below
```

**Process decision first**

Review 1.4 was skipped by the project owner so this could be built. Recorded in
§10 and in `review/FEEDBACK.md`. `feedback.1.3.md` §8 itself triggers 1.4 on M2
availability, so this is following the reviewer's own sequencing rather than
departing from it. `feedback.2.0.md` before the first `order_send` is unchanged.

**Completed**

- `persistence/schema.py` — five tables. Append-only enforced by grants rather
  than convention; `NUMERIC` for money; `TIMESTAMPTZ` throughout; three clocks
  per event; producer-assigned `event_id` as the primary key.
- `persistence/journal.py` — `EventJournal` (idempotent on `event_id` via
  `ON CONFLICT DO NOTHING`, ordered by market time) and `CapsuleStore` (sealed
  once, fingerprint recomputed and compared on read).
- `persistence/safety_state.py` — `PostgresSafetyStateStore` and
  `CompositeSafetyStateStore`, implementing ADR-002's precedence table.
- `HANDOVER.md` — what a developer picking this up cold needs: environment,
  code map, ordered next steps, the traps, and an honest ranking of what the
  evidence supports.
- CI now runs a PostgreSQL service and **fails if the database was unreachable**,
  because a silently skipped persistence suite would look green.

**Evidence — all ten ADR-003 acceptance criteria**

| # | Criterion | Where |
|---|---|---|
| 1 | duplicate insert stores one row | `test_persistence_invariants.py::TestIdempotentWrites` |
| 2 | retry after ambiguous commit converges | same |
| 3 | crash before commit leaves nothing | `TestCrashConsistency` |
| 4 | read order is market time, not insertion | `TestOrdering` |
| 5 | Decimal survives bit-exact; no float columns | `TestExactnessSurvivesTheRoundTrip` |
| 6 | UTC survives; no naive timestamp columns | same |
| 7 | payload disagreeing with its column is refused | `TestSchemaVersioning` |
| 8 | tampered capsule detected on read | `TestSealedCapsulesAreImmutable` |
| 9 | journal/latch disagreement resolves to halted | `TestSafetyStateAuthority` |
| 10 | **replay from the journal reproduces the run** | `test_replay_from_journal.py` |

- tests: 454 passed (up from 422); 32 new, 25 of them against real PostgreSQL
- types: mypy strict clean, 71 files · lint: ruff clean
- replay determinism unchanged and still byte-identical

**Problems found**

- The `sequence` column had no default. SQLAlchemy only auto-creates a sequence
  for the primary key, and the primary key here is `event_id` — deliberately, so
  identity comes from the producer. Fixed with an explicit `Identity()`. The
  failure was loud and immediate, which is what a `NOT NULL` is for.
- `Event[TradeIntent]` is not an `Event[Contract]`: generics are invariant. The
  journal serialises the payload and never inspects its type, so the write path
  now takes `Event[Any]` — accurate rather than a loosening.
- My first CI step named "fail if the database was unavailable" did not fail if
  the database was unavailable; it re-ran the tests, which would skip. Replaced
  with an explicit reachability assertion. A check that cannot detect the thing
  it is named after is worse than no check.

**Risk impact**

- No change to execution risk. Nothing can reach a broker.
- The audit trail is now durable and verifiable, but **the running system does
  not use it yet** — see D-030 and APP-006. Until that wiring lands, the
  guarantees are real and unused. *(Closed by the 2026-08-18 update below.)*

**Next**

- Wire `CapsuleStore` into `_seal` and `CompositeSafetyStateStore` into startup.
  Small change, tested foundation.
- Then M1, which needs a broker and a Windows host — both human decisions.

---

## Update 2026-08-18 — persistence on the normal path

```text
Component: 1 — Platform / Application
Milestone: M2 — event journal and data pipeline
Status before: journal built and proven, unused by the running system
Status after:  journal, capsules, safety state and risk-session state all
               written and recovered on the orchestrator's ordinary path
Review:        feedback.1.5.md steps 1-3 (findings F-018, F-019)
```

**Completed**

- `application/recording.py` — `RunRecorder` with a forgetful default and a
  PostgreSQL implementation. Every stage of the build.md §3 flow is written as
  a typed event; a window's events and its sealed capsule commit together; a
  halt flushes immediately.
- `application/bootstrap.py` — assembles the journal, the ADR-002 composite
  safety store, the risk-session store and a kill switch recovered through
  `KillSwitch.on_startup`. A database with no RUNNING record starts UNKNOWN and
  refuses new orders.
- `application/reconstruction.py` — rebuilds a run from the `events` table
  alone and states plainly what the journal does not carry.
- `risk/session.py` + `persistence/risk_session.py` — risk-session state
  persisted and recovered, seeded so recovery can only ever tighten (F-019).
- `build_event` gained producer-supplied `event_id` and `occurred_at_utc`, so
  journal identity is content-addressed and ordering is by market time.
- `scripts/run_replay.py --persist`, with an explicit `--operator` /
  `--incident-note` pair to clear a recorded halt. Nothing automatic clears one.

**Evidence**

- tests: 491 passed (388 unit, 11 property, 28 replay, 64 integration against
  a real PostgreSQL 17). Previously 454.
- new: `tests/integration/test_orchestrator_persistence.py` (11),
  `tests/integration/test_run_survives_restart.py` (8, spawning real child
  processes), `tests/unit/test_risk_session.py` (18).
- the reviewer's acceptance criterion holds: the decision fingerprint of an
  in-memory run equals the one reconstructed from the journal, and the
  reconstructed tally matches the live one counter for counter.
- types: `uv run mypy` — clean, strict, 79 files. lint: clean.
- determinism: `scripts/run_replay.py --bars 600 | md5` byte-identical across
  runs, and unchanged from before this work.

**Problems found**

- `build_event` used `uuid4` and `utc_now`. Both are wrong for a journalled
  replay: a random id makes a rerun duplicate history rather than converge,
  and a wall-clock timestamp is write time, which ADR-003 invariant 4 says is
  never what replay orders by. Fixed by letting the producer supply both.
- Recovering the session before the loop crashed: `SimulatedBroker.positions()`
  needs a clock, and there is none until the first tick. Moved into the first
  iteration. A small bug, but the shape of it is worth noting — recovery needs
  market time, and market time does not exist until data arrives.
- Replaying the same series twice into one database halts with
  `SAFETY_STATE_UNKNOWN`, because the recorded trading day is then ahead of the
  data being fed in. That is correct — time went backwards — but it was
  unreadable until the halt *detail* was surfaced in the report. It is now.
- A capsule cannot explain a NO_TRADE: it has no field for the strategy's
  reason codes when there is no intent. The reasons live in `SignalGenerated`,
  which is the first thing in this project that the capsule store alone cannot
  answer and the journal can. There is a test asserting exactly that.

**Risk impact**

- No change to execution risk. Nothing can reach a broker.
- Two safety properties moved from "designed" to "exercised on the normal
  path": ADR-002 authority, and a risk budget that survives a restart.
- One new honest gap: the journal records decisions, not the market data they
  were made from (D-031, APP-008). Nothing depends on it today because the
  generator is seeded and deterministic. Everything will depend on it the day
  a real feed arrives.

**Next**

- Raw tick/bar storage and the normalized bar pipeline — the rest of M2's own
  deliverable list (D-031).
- Alembic, now that ordinary runs write data (D-029, escalated).
- Then M1: the Pepperstone EU demo account (O-001) and a Windows x86-64 host,
  both of which need a human.


## Update 2026-08-18 — the data boundary, migrations, and the owner's policies

```text
Component: 1 — Platform / Application
Milestone: M2 — event journal and data pipeline (deliverables now complete)
Status before: decisions journalled; nothing recorded what the system saw
Status after:  ticks and bars stored, schema versioned, restore proven,
               owner decisions O-003 and O-004 enforced in the risk engine
Reviews:       feedback.1.4.md (restored), feedback.1.6.md — F-020 … F-025
```

**Completed**

- **Raw market data (F-022).** `MarketTick` and `MarketBar` contracts,
  `market_ticks` / `market_bars` tables, and `persistence/market_data.py`. The
  orchestrator records every window it observes — including the warm-up ones
  that seal no capsule and emitted no event, which is the hole D-031 named.
  A bar carries its origin and, when derived, the pipeline version that made
  it; a conflicting bar for a stored interval raises rather than being ignored.
- **Normalized bar pipeline (F-022, build.md §26 M2).**
  `market_data/pipeline.py` aggregates ticks into bars with a transformation
  identity that includes the price basis, and detects gaps, out-of-order
  arrivals, duplicates and crossed quotes. It never invents a bar to cover a
  gap and never discards a late tick to make a series look tidy.
- **Alembic (F-020, F-023).** Baseline `ce70efeb9fe9`; the durable runtime
  migrates rather than calling `create_all`; a `pg_dump` → drop → restore is
  proven to produce a database from which the run still reconstructs.
- **One EUR/USD exposure (O-004, F-025).** A constant in the risk engine, above
  the account model, with the reviewer's four cases plus the symmetric fifth.
- **Intraday policy (O-003, F-025).** `risk/trading_window.py` and `ADR-004`.
  Entries refused outside the trading window; exposure surviving the flatten
  deadline halts. The flatten itself is M5 and is written down as such.
- **Supervisor honesty (F-024).** The frequency threshold is `null` —
  uncalibrated, not passing. Every decision carries `uncalibrated_checks` and
  the run report prints which controls were not in force.
- **Pepperstone facts (O-001).** Server, currency and leverage recorded in
  `config/paper.yaml` as claims the account guard checks. No credentials.

**Evidence**

- tests: 604 passed (479 unit, 11 property, 28 replay, 86 integration against a
  real PostgreSQL 17). Previously 491.
- types: `uv run mypy` clean, strict, 88 files. lint: clean.
- determinism: `run_replay.py --bars 600 | md5` byte-identical across runs.
  The hash itself moved, because `config/paper.yaml` changed — the intraday
  policy and the Pepperstone server are both inputs to `config_version`.
- the intraday policy is live on the real path, not only in unit tests: a
  1,500-bar `baseline_v1` replay records 44 `SESSION_BLACKOUT` refusals.

**Problems found**

- **Running a migration in-process silently reconfigured the platform's
  logging.** Alembic's `env.py` calls `logging.config.fileConfig`, which
  reconfigures the root logger and disables existing ones. Twenty logging tests
  failed the moment the migration tests ran before them. The tests caught a
  real defect, not test pollution: an in-process migration would have replaced
  structured JSON logging with Alembic's format, routing audit-relevant records
  somewhere nobody reads. `fileConfig` now runs only on the CLI path.
- **The session-phase check had a hole at the boundary.** At 17:00 New York the
  day rolls and the phase becomes OPEN for the *new* day, so a position that
  survived the old day's flatten deadline stopped looking like a breach one
  second after becoming one. Closed by comparing trading days directly —
  `has_crossed_rollover` — as well as asking about the phase.
- **The supervisor's frequency check needed a second look.** Marking it
  uncalibrated exposed that the confidence band is inert for the same reason:
  its range is the one the contract already enforces. Both are now reported as
  absent rather than passed, which is what D-028 was originally about.
- **A configured `supervisor.policy_version` was never read.** The decision is
  stamped with a constant in code, so the two could drift and a capsule could
  name a policy the configuration never described. There is now a test
  asserting they match.

**Risk impact**

- No change to execution risk. Nothing can reach a broker, and `order_send`
  remains unreachable.
- The audit trail can now answer "what did it see?" as well as "what did it
  decide?" — for ticks and bars. Not for feature values, which are still only
  hashed (D-031).
- Two owner policies moved from prose into refusals the deterministic engine
  makes. One of them, the flatten, is only half there and is recorded as D-033
  rather than implied to be complete.

**Next**

- M1: the Pepperstone demo account and a Windows x86-64 host, both human tasks.
  Resolve the EU/UK entity question first (D-034).
- Read-only MT5 gateway, then persist real ticks and bars immediately — the
  store and the pipeline exist and have never seen a real quote.
- Feature values in storage (D-031), and the automatic flatten (D-033, M5).

---

## Update 2026-08-23 (M1 read-only gateway prepared)

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Maturity before: SPECIFIED (BrokerPort only, no adapter)
Maturity after:  UNIT-TESTED against a fake terminal
Gate:            still NOT PASSED — nothing has met MetaTrader 5
```

Review 1.6 puts M1 at **PREPARE NOW**, and everything it still needs from a
human — the demo account, a Windows host, the entity question — leaves the code
itself unblocked. This converts waiting time into readiness.

**Completed**

- `mt5_gateway/client.py` — connection management. The `MetaTrader5` import is
  deferred to call time so the rest of the platform still runs on macOS, and
  every call goes through a checked wrapper that turns MT5's convention of
  returning `None`/`False` with the reason in `last_error()` into an exception
  naming what failed.
- `mt5_gateway/readonly.py` — the read half of `BrokerPort`: account, instrument
  specification, positions, terminal health.
- 41 tests against a fake terminal that mimics MT5's actual failure convention.

**Three decisions worth stating**

1. **Execution is refused by construction, not by configuration** (D-036). The
   mutating methods exist only to raise. A flag would make "M1 cannot trade" a
   promise about usage; this makes it a property of the type. M5 must add a
   separate adapter rather than relax these, so the read-only one stays
   available for shadow mode.
2. **The broker symbol is discovered, never assumed** (O-001). An exact match
   wins, otherwise the shortest candidate — `EURUSD.a` is the instrument,
   `EURUSD.a.cfd` is something else. A hard-coded `"EURUSD"` is a bug that only
   appears against a real account.
3. **MT5 floats become Decimal through `repr`, not directly.** `Decimal(1e-05)`
   preserves the binary error; `Decimal(repr(1e-05))` is the shortest decimal
   that round-trips, which is what the terminal meant. The domain rejects floats
   at its boundary and this is the conversion that lets it.

**A failure mode the tests pin down**

`positions_get` returning `None` is ambiguous: an empty book and a failed call
look identical. The adapter distinguishes them by error code — `RES_S_OK` means
genuinely flat, anything else raises. Reading a failed call as "no positions" is
how a reconciliation check passes while the terminal is unreachable.

**Evidence**

- tests: 645 passed (up from 604), 41 new
- types: mypy strict clean, 91 files · lint: ruff clean
- replay determinism unchanged

**What this does not prove**

Nothing here has run against MetaTrader 5. The fake was written from the
documented API, not from observation, so where the real terminal disagrees the
fake is wrong and the adapter probably is too. Spread, fills, reconnect
behaviour, the real symbol name, the account mode and the actual field names are
all broker facts. Recorded as D-035 and APP-014; first contact is discovery, and
review 1.5 step 7 asks for disagreements to become deviations rather than quiet
patches.

**Next**

- Three human tasks: create the Pepperstone demo account, provision a Windows
  x86-64 host, and resolve the EU/UK entity question (APP-013).
- Then M1 first contact, and `feedback.2.0.md` before anything can submit.

## Update 2026-08-24

```text
Component: 1 — Platform / Application
Milestone: M1 — MT5 read-only gateway
Status before: adapter written, no host, no runbook
Status after:  Windows host available; first-contact probe and runbook ready;
               still never connected to a terminal
```

**What changed, and why now**

The owner reported that a Windows x86-64 machine is available. That removes the
hardware blocker recorded on 2026-08-23 and makes MT5 first contact the next
real step, so the work this session was aimed squarely at making that step
executable by someone who was not here for the previous ones.

**Completed**

- `scripts/mt5_probe.py` — connects to a terminal, reads account, symbol,
  instrument and position state, and prints them for a human to compare against
  `config/paper.yaml` and `build.md`. Read-only by construction: it holds a
  `ReadOnlyMt5Gateway`, whose execution methods raise (D-036).
- `HANDOVER.md` rewritten around the two-host reality: a Windows setup section,
  a six-step first-contact runbook (§4), reordered next steps now that hardware
  is no longer blocking, and the MT5-specific traps.
- `.gitattributes` pinning the checkout to LF. With macOS and Windows both in
  play, a CRLF checkout would change the determinism hash and the format check
  without changing a line of code.
- D-037 and APP-015 opened — see below.

**A defect found while writing the probe**

`ReadOnlyMt5Gateway.instrument` stores `filling_modes=(str(info.filling_mode),)`
and `trade_mode=str(info.trade_mode)`. MetaTrader 5 reports both as integers,
and `filling_mode` is a **bitmask**: a symbol allowing FOK and IOC reports `3`.
Stringifying that yields `"3"`, which reads like a filling mode and is not one.

The fake in `tests/unit/test_mt5_readonly_gateway.py` supplies `"IOC"` and
`"FULL"` as strings, so the tests agree with the adapter and both are wrong
together. That is a worked example of the weakness D-035 already names: a fake
written from documentation confirms the reading its author had.

It is deliberately **not** patched. The decode is documented but unobserved,
and writing an unverified mapping into the instrument spec — which sizing and
order building read — would give an assumption the appearance of a fact. The
probe prints the raw value beside the decoded one so the first connection
settles it. Recorded as D-037 / APP-015.

**Evidence**

- tests: 663 passed (up from 645), 18 new in `tests/unit/test_mt5_probe.py`
- without a database, 73 of those skip — verified by pointing
  `CRUMBLR_DATABASE_URL` at an unreachable port, not assumed
- types: mypy strict clean, 93 source files · lint and format: ruff clean
- replay determinism unchanged

**What this does not prove**

Still nothing about MetaTrader 5. The probe has been run only against a fake,
and the fake is the thing under suspicion. Its value is that it turns first
contact into a single command whose output is a record, rather than a session
of interactive poking whose findings live in someone's memory.

**Risk impact**

None to the running system — no execution path was touched. The probe cannot
place an order, and a test fails if it ever reaches for one.

**Next**

- Blocked on one human task now, not three: **create the Pepperstone demo
  account** and log into it once in the terminal. The entity question (APP-013)
  is answered by the probe's own output.
- Then run `uv run python scripts/mt5_probe.py --json first-contact.json` on the
  Windows host, record the result here, and open a deviation for each
  disagreement rather than editing the code to match.
- Continuous bar/tick read and observed reconnect behaviour complete M1
  (HANDOVER.md §4.5). `feedback.2.0.md` remains mandatory before any
  `order_send`, demo included.

## Update 2026-08-24 (second entry) — the repository has a remote

```text
Component: 1 — Platform / Application
Milestone: M0 — repository and engineering baseline
Status before: no commits, no remote; every quality claim from one machine
Status after:  initial commit pushed to a private remote; CI able to run
```

**What changed**

The owner lifted the commit hold, which had stood since the first session
(F-006), because the code now has to reach a second machine — the Windows MT5
host. Initial commit `fd6a890`, 123 files, pushed to the private repository
`DutchBugs/Crumblr`.

**Separation of the personal and work accounts**

The owner runs an unrelated project under a different GitHub account, and asked
explicitly that the two not mix. Three crossover routes were found and closed:

1. **Commit attribution.** The global git identity was the owner's work email
   address, so every commit would have been attributed to the work account.
   Overridden **repo-locally**; the global config was left untouched.
2. **The SSH key.** The default key on the macOS host (`~/.ssh/id_ed25519`)
   turned out to be a *deploy key belonging to the unrelated work repository* —
   verified, not assumed. The remote therefore uses HTTPS, and the handover
   says not to switch it to SSH.
3. **The stored credential.** `credential.https://github.com.username` is
   pinned repo-locally, so the macOS keychain keeps one entry per account
   instead of one shared github.com credential.

The token itself is fine-grained and scoped to this single repository. It never
entered a chat transcript or a file: the first push was run by the owner in
their own terminal.

**A rejection worth recording**

The first push was refused: a fine-grained token cannot create
`.github/workflows/ci.yml` without the Workflows permission. The push was
atomic, so nothing partial landed. Resolved by adding **Workflows: read and
write** rather than by dropping the workflow file — dropping it would have
preserved D-019 indefinitely, and the workflow is the point.

**Evidence**

- `git ls-remote origin` → `refs/heads/main` at `fd6a890`, identical to local
  `HEAD`; branch tracking established
- commit author verified as the personal address, not the work address
- no `.env` tracked; a scan of all 123 tracked files found no credential-shaped
  strings beyond the local development PostgreSQL and test fixtures

**Risk impact**

None to the running system. The relevant risk was disclosure, and the two
things that could have leaked — MT5 credentials and the GitHub token — are both
outside the repository by construction, one of them enforced by the config
loader itself.

**Next**

- Record the first CI result here, pass or fail. Until then the quality figures
  still rest on one developer machine (D-019 amended, not closed).
- Clone to the Windows host and run the gate there. Expect **590 passed,
  73 skipped** without a PostgreSQL — read the skip count rather than the
  colour.
- Then the demo account and MT5 first contact (HANDOVER.md §4).
- **`feedback.1.7.md` is due.** Review 1.6 §10 named five triggers; all five
  are now met. Nothing since 2026-08-22 — the M1 adapter, the probe, D-035…
  D-037, the repository change — has been independently reviewed.
  `review/FEEDBACK.md` now carries an *Unreviewed work* section saying what to
  look at and where the evidence is.

---

# 14. Update template

Copy this block whenever meaningful progress occurs.

```text
## Update YYYY-MM-DD HH:MM UTC

Component:
Milestone:
Status before:
Status after:

Completed:
- 

Evidence:
- tests:
- logs:
- metrics:
- artifact/commit:

Problems found:
- 

Risk impact:
- 

Decision:
- 

Next:
- 
```

---

# 15. Release / deployment record

| Version | Date | Environment | Code commit | Strategy | Model | Risk config | Evaluator policy | Result |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |

---

# 16. Promotion history

| Date | Component | From | To | Decision | Evidence | Reviewer |
|---|---|---|---|---|---|---|
| | | | | | | |

No automatic process may add a promotion from `SHADOW` to `LIVE-CANARY` or from `LIVE-CANARY` to broader live scope without a recorded human approval.

