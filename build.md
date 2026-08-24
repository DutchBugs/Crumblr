# build.md — Autonomous MT5 Trading Platform

**Project:** Autonomous EUR/USD Trading Platform  
**Initial market:** EUR/USD spot FX via MetaTrader 5  
**Mode at launch:** Backtest → replay → MT5 demo/paper trading → shadow → only then guarded live  
**Primary language:** Python  
**Document status:** Architecture / build specification v0.1  
**Date:** 2026-08-17

> **Important:** this document is an engineering and risk-control specification, not a promise of profitability or investment advice. The system must be designed so that a bad model, malformed signal, broken connection, stale price, hallucinating LLM, or software bug cannot directly create unlimited trading risk.

---

## 1. Executive decision

Do **not** build “one AI agent that can trade”.

Build three separated systems:

1. **Trading Platform / Application**
   - market data
   - MT5 connection
   - order lifecycle
   - risk engine
   - storage
   - replay/backtesting
   - dashboard
   - observability
   - kill switches

2. **Trading Agent**
   - observes normalized market state
   - produces a typed `TradeIntent`
   - never talks to MT5 directly
   - never owns broker credentials
   - does not determine unrestricted position size

3. **Evaluator / Supervisor Agent**
   - independently evaluates behavior and system health
   - can approve/block intents according to policy
   - can trip a kill switch
   - cannot invent new trades
   - cannot reset a kill switch
   - continuously compares expected vs realized behavior

The core architectural principle is:

> **Agent proposes. Risk engine constrains. Supervisor vetoes. Execution service executes. Reconciliation verifies.**

This separation is the most important safety decision in the project.

---

## 2. Critical design choices

### 2.1 Do not put an LLM directly on the order button

A language model may be useful later for:

- incident analysis;
- log summarization;
- extracting structured information from economic/news sources;
- explaining why a strategy is outside its normal operating regime;
- generating research hypotheses;
- comparing champion/challenger models.

It should **not** initially be trusted to:

- set arbitrary lot sizes;
- bypass stop-loss policy;
- directly call `order_send`;
- reset a halted system;
- change production risk limits;
- deploy a newly generated strategy by itself.

All actions that can create financial exposure should pass deterministic validation.

### 2.2 MT5 must be isolated behind a gateway

The official MetaTrader5 Python package communicates with the locally available MetaTrader 5 terminal. As of 2026-08-17, the current PyPI distribution is `MetaTrader5 5.0.6090` and the published wheels are Windows x86-64.

Therefore:

- run the **MT5 Gateway** on Windows x86-64;
- keep all direct `MetaTrader5` package calls in that process;
- expose an internal typed API to the rest of the application;
- serialize MT5 mutations through one execution worker;
- do not scatter MT5 calls throughout strategy code.

This allows the rest of the platform to later run independently on Linux/cloud infrastructure.

### 2.3 Paper trading must be more than “a backtest”

Use four validation environments:

1. **Historical backtest**
2. **Deterministic market replay**
3. **MT5 demo account**
4. **Live-market shadow mode without real orders**

A demo account is valuable, but it does not prove that production spread, slippage, liquidity, latency, reconnect behavior, or broker-specific conditions will behave identically.

---

# 3. Target architecture

```text
                    ┌───────────────────────┐
                    │ Operator Dashboard    │
                    │ config / metrics /    │
                    │ halt / audit          │
                    └──────────┬────────────┘
                               │
                     ┌─────────▼─────────┐
                     │ Control API       │
                     │ FastAPI           │
                     └─────────┬─────────┘
                               │
                 ┌─────────────▼──────────────┐
                 │ Event Journal / PostgreSQL │
                 └───────┬──────────┬─────────┘
                         │          │
               ┌─────────▼───┐   ┌──▼────────────────┐
               │ Trading     │   │ Evaluator /       │
               │ Agent       │   │ Supervisor Agent  │
               └──────┬──────┘   └───────┬───────────┘
                      │ TradeIntent       │ approve/veto/halt
                      └──────────┬────────┘
                                 ▼
                       ┌──────────────────┐
                       │ Deterministic    │
                       │ Risk Gateway     │
                       └────────┬─────────┘
                                │ ApprovedOrder
                                ▼
                       ┌──────────────────┐
                       │ Execution Engine │
                       └────────┬─────────┘
                                │ internal RPC
                                ▼
                       ┌──────────────────┐
                       │ MT5 Gateway      │
                       │ Windows + MT5    │
                       └────────┬─────────┘
                                │
                                ▼
                      MetaTrader 5 / Broker
```

### Required transaction flow

```text
OBSERVE
  ↓
GENERATE SIGNAL
  ↓
PROPOSE TRADE INTENT
  ↓
PRE-TRADE RISK CHECK
  ↓
SUPERVISOR CHECK
  ↓
ORDER BUILD
  ↓
MT5 order_check
  ↓
MT5 order_send
  ↓
BROKER RESULT
  ↓
POSITION / ORDER RECONCILIATION
  ↓
POST-TRADE EVALUATION
  ↓
AUDIT + METRICS
```

No component may skip stages.

---

# 4. Recommended tech stack

## 4.1 Runtime

| Area | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Stable, current ecosystem, supported by current MT5 wheel |
| Dependency management | `uv` + `pyproject.toml` + lockfile | Fast, reproducible builds |
| Validation/models | Pydantic v2 | Typed boundaries and strict validation |
| API | FastAPI | Typed internal/control API |
| ORM | SQLAlchemy 2 | Mature persistence layer |
| Migrations | Alembic | Versioned schema changes |
| Primary DB | PostgreSQL | Orders, events, model/config state, audit |
| Time-series extension | TimescaleDB optional | Useful when tick/bar volumes grow |
| Dataframes | Polars + NumPy | Fast feature/data processing |
| Pandas | Adapter-only where MT5/examples require it | Compatibility |
| Testing | pytest + Hypothesis | Unit + property-based risk tests |
| Logging | structlog or stdlib JSON logging | Machine-readable audit |
| Metrics | Prometheus + Grafana | Operational and trading telemetry |
| Tracing | OpenTelemetry | End-to-end latency and dependency tracing |
| Packaging | Docker for non-MT5 services | Repeatability |
| MT5 runtime | Dedicated Windows x86-64 host/service | Official package distribution |
| Secrets | Windows Credential Manager / Vault / cloud secret manager | Credentials outside repo |
| CI | GitHub Actions or equivalent; include Windows runner | Test gateway separately |

### Optional ML stack

Do not add all of this on day one.

- scikit-learn — baselines and preprocessing
- LightGBM or XGBoost — tabular signal models
- PyTorch — only when a neural model has a justified research case
- MLflow — experiment/model registry when model count grows

### Avoid in v1

- Kubernetes
- a large microservice estate
- reinforcement learning
- autonomous prompt-generated strategies
- dozens of indicators
- multi-broker routing
- multi-market trading
- LLM-based high-frequency decisions

Start with a **modular monolith plus an isolated MT5 Gateway**. Split services only after actual scaling or isolation requirements appear.

---

# 5. Repository layout

```text
trading-platform/
├─ pyproject.toml
├─ uv.lock
├─ .env.example
├─ README.md
├─ build.md
├─ status.md
├─ config/
│  ├─ base.yaml
│  ├─ paper.yaml
│  ├─ shadow.yaml
│  └─ live.yaml
├─ src/
│  ├─ domain/
│  │  ├─ models.py
│  │  ├─ enums.py
│  │  ├─ money.py
│  │  └─ events.py
│  ├─ market_data/
│  │  ├─ normalization.py
│  │  ├─ bars.py
│  │  └─ replay.py
│  ├─ mt5_gateway/
│  │  ├─ client.py
│  │  ├─ adapter.py
│  │  ├─ execution.py
│  │  ├─ reconciliation.py
│  │  └─ health.py
│  ├─ risk/
│  │  ├─ sizing.py
│  │  ├─ policies.py
│  │  ├─ exposure.py
│  │  └─ kill_switch.py
│  ├─ trading_agent/
│  │  ├─ features.py
│  │  ├─ strategy.py
│  │  ├─ model.py
│  │  └─ agent.py
│  ├─ evaluator/
│  │  ├─ pretrade.py
│  │  ├─ posttrade.py
│  │  ├─ drift.py
│  │  ├─ incidents.py
│  │  └─ supervisor.py
│  ├─ backtest/
│  │  ├─ engine.py
│  │  ├─ fills.py
│  │  ├─ costs.py
│  │  └─ reports.py
│  ├─ application/
│  │  ├─ orchestration.py
│  │  ├─ scheduler.py
│  │  └─ state_machine.py
│  ├─ api/
│  └─ observability/
├─ migrations/
├─ tests/
│  ├─ unit/
│  ├─ property/
│  ├─ replay/
│  ├─ integration/
│  └─ chaos/
├─ notebooks/
│  └─ research_only/
└─ scripts/
   ├─ backfill.py
   ├─ run_replay.py
   └─ reconcile.py
```

`notebooks/` is research only. Production strategy logic must live in tested Python modules.

---

# 6. Domain contracts

Strongly typed contracts are mandatory.

## 6.1 MarketSnapshot

Minimum fields:

```python
class MarketSnapshot:
    symbol: str
    event_time_utc: datetime
    received_time_utc: datetime

    bid: Decimal
    ask: Decimal
    spread_points: int

    timeframe: str
    bars: list[Bar]

    session_state: str
    symbol_spec_version: str
    data_quality: str
```

Store both event time and receive time.

All internal timestamps are UTC.

## 6.2 TradeIntent

The Trading Agent may only produce this object.

```python
class TradeIntent:
    intent_id: UUID
    strategy_id: str
    strategy_version: str
    model_version: str | None

    symbol: str
    side: Literal["BUY", "SELL", "FLAT"]
    created_at_utc: datetime
    expires_at_utc: datetime

    entry_type: Literal["MARKET", "LIMIT", "STOP"]
    reference_price: Decimal

    stop_loss_price: Decimal | None
    take_profit_price: Decimal | None

    confidence: float
    reason_codes: list[str]

    requested_risk_fraction: Decimal
    feature_snapshot_id: UUID
    decision_hash: str
```

The Trading Agent does **not** send `lot_size`.

The final order quantity is determined by the risk engine using:

- current account equity;
- stop distance;
- broker symbol specification;
- tick value;
- volume minimum/maximum/step;
- portfolio exposure;
- configured risk budget.

## 6.3 RiskDecision

```text
PASS
BLOCK
HALT
```

Include machine-readable reason codes, e.g.:

```text
STALE_MARKET_DATA
SPREAD_TOO_WIDE
DAILY_LOSS_LIMIT
OPEN_RISK_LIMIT
INVALID_STOP
SYMBOL_NOT_ALLOWED
MARKET_DISABLED
RECONCILIATION_MISMATCH
SUPERVISOR_VETO
```

## 6.4 ExecutionResult

Persist:

- request ID
- intent ID
- MT5 order/deal/position IDs
- request payload
- normalized order payload
- `order_check` result
- `order_send` result
- retcode
- broker timestamps
- measured latency
- requested price
- executed price
- slippage
- error details

Never log passwords or secret values.

---

# 7. MT5 Gateway specification

The gateway is the only module allowed to import and call `MetaTrader5`.

## Required MT5 capabilities

### Connection

- `initialize`
- `login`
- `shutdown`
- `version`
- `terminal_info`
- `account_info`
- `last_error`

### Market information

- `symbols_get`
- `symbol_select`
- `symbol_info`
- `symbol_info_tick`
- `copy_rates_*`
- `copy_ticks_*` where needed

### Trading

- `order_calc_margin`
- `order_calc_profit`
- `order_check`
- `order_send`
- `orders_get`
- `positions_get`
- `history_orders_get`
- `history_deals_get`

## Gateway invariants

1. Credentials live only in the gateway runtime.
2. Every mutation has an idempotency key.
3. Every order is preceded by local risk validation.
4. Every order is preceded by `order_check` where applicable.
5. Every `order_send` result is persisted.
6. Broker state is reconciled after every mutation.
7. Unknown state means **halt**, not “try another trade”.
8. A reconnect never silently replays an order.
9. Gateway startup begins read-only until reconciliation completes.
10. Gateway shutdown does not imply position liquidation unless explicitly configured.

## Broker-specific symbol mapping

Never hard-code `"EURUSD"` as the permanent broker symbol.

Some brokers use suffixes/prefixes.

Create an `InstrumentRegistry`:

```text
canonical_symbol = EUR/USD
broker_symbol    = EURUSD | EURUSD.a | EURUSDm | ...
currency_base
currency_profit
contract_size
digits
point
tick_size
tick_value
volume_min
volume_max
volume_step
stops_level
freeze_level
filling_modes
trade_mode
```

Refresh specifications at startup and detect changes.

---

# 8. Risk engine — non-negotiable

The risk engine must be deterministic and independent from the strategy.

## 8.1 Pre-trade checks

A trade must be blocked when any required check fails:

- account not connected;
- wrong account;
- not a demo account during paper phase;
- expert trading disabled;
- symbol not allow-listed;
- market data stale;
- bid/ask invalid;
- spread outside configured limit;
- order would exceed allowed risk;
- position size below/above broker limits;
- volume step invalid;
- stop-loss invalid;
- stop distance violates broker rules;
- max open positions exceeded;
- max open risk exceeded;
- daily loss limit reached;
- session/maintenance blackout active;
- trade intent expired;
- duplicate/idempotency violation;
- MT5/repository position mismatch;
- evaluator veto;
- system is HALTED.

## 8.2 Kill switches

Independent kill switches:

### Automatic HALT

Trigger on:

- repeated MT5 connection failures;
- stale market data;
- account mismatch;
- unexpected live account while paper mode is configured;
- reconciliation mismatch;
- repeated order rejections;
- excessive slippage;
- abnormal spread;
- max daily loss breach;
- max drawdown breach;
- evaluator detects critical drift;
- order frequency spike;
- duplicate orders;
- corrupted feature/model/config version.

### Manual HALT

The dashboard must have:

- `HALT NEW ORDERS`
- optional `CANCEL PENDING`
- separately controlled `FLATTEN POSITIONS`

Do not combine these three actions into one ambiguous button.

### Reset rule

Agents may trip a kill switch.

Agents may **not** reset a kill switch in production.

Reset requires explicit operator action and an incident note.

---

# 9. Trading Agent specification

## 9.1 Definition

The Trading Agent is an autonomous decision component that maps:

```text
market state
+ account/risk context
+ strategy configuration
→ TradeIntent | NoTrade
```

It must not own execution.

## 9.2 Start with a benchmark strategy, not “AI”

Before training advanced models, implement at least:

1. no-trade benchmark;
2. deterministic baseline strategy;
3. simple statistical/ML challenger.

This creates a sanity baseline.

If a complex model cannot outperform a simple baseline **after realistic costs and out-of-sample validation**, it should not be promoted.

## 9.3 Suggested research progression

### Stage A — deterministic baseline

Example families:

- trend-following;
- breakout;
- mean-reversion;
- volatility-regime filter.

The purpose is infrastructure validation, not claiming an edge.

### Stage B — supervised ML

Potential target:

```text
Expected risk-adjusted return over horizon H
```

or classify:

```text
LONG / SHORT / NO-TRADE
```

Features may include:

- returns across multiple horizons;
- realized volatility;
- bid/ask spread;
- ATR-like range measures;
- time-of-day/session;
- trend/mean-reversion statistics;
- distance from rolling extrema;
- recent trade/activity proxies;
- regime features.

Feature computation must be:

- causal;
- reproducible;
- versioned;
- identical in backtest and live;
- free from future leakage.

### Stage C — regime router

Rather than one model that trades every market condition:

```text
Regime Detector
      ↓
Strategy Router
 ┌────┼─────┐
trend range high-vol
```

Each regime can select a specialized strategy or choose `NO_TRADE`.

### Stage D — champion/challenger

One strategy is allowed to execute.

Several challengers run in shadow mode.

The Evaluator compares them continuously.

## 9.4 Explicitly postpone reinforcement learning

RL is only considered after:

- an event-driven simulator is validated;
- fill/cost behavior is credible;
- environment leakage is tested;
- baseline strategies are understood;
- reproducibility is proven.

Otherwise RL will usually optimize imperfections in the simulator.

---

# 10. Evaluator / Supervisor Agent specification

This agent is **not a second trader**.

It is a control and learning system.

## 10.1 Responsibilities

### Pre-trade

Evaluate:

- is the system healthy?
- is this strategy enabled?
- is current market state inside known operating envelope?
- is confidence calibrated?
- is spread/volatility abnormal?
- is the signal a duplicate?
- is trade frequency abnormal?
- is recent behavior inconsistent with policy?
- does the intent contain all required evidence?

Outputs:

```text
APPROVE
VETO
HALT
```

Prefer veto-only behavior. The evaluator should not rewrite a BUY into a SELL.

### Post-trade

Evaluate:

- realized vs expected return;
- slippage;
- execution latency;
- MAE/MFE;
- spread paid;
- stop/target behavior;
- reason for exit;
- model calibration;
- risk-adjusted expectancy;
- drawdown contribution.

### Continuous system evaluation

Track:

- feature drift;
- prediction drift;
- performance drift;
- spread/liquidity drift;
- order reject rate;
- connection/reconnect rate;
- missed decision windows;
- data gaps;
- live/backtest divergence;
- trade frequency;
- exposure concentration.

## 10.2 Evaluator architecture

Use three layers.

### Layer 1 — deterministic policy engine

Hard rules.

Can veto or halt.

### Layer 2 — statistical monitor

Examples:

- rolling z-scores;
- PSI / distribution drift;
- KS-style distribution checks;
- EWMA changes;
- calibration curves;
- rolling expectancy;
- sequential change detection.

Can veto/halt only through predefined policies.

### Layer 3 — LLM analysis assistant

Optional.

Allowed to:

- summarize incidents;
- identify recurring patterns;
- propose experiments;
- explain drift reports;
- produce a daily review;
- draft hypotheses.

Not allowed to:

- directly send orders;
- silently alter risk config;
- promote a model;
- reset production halt.

This division keeps LLM reasoning useful without making it the final safety boundary.

---

# 11. Decision Capsule — auditability feature

For every decision window, persist an immutable **Decision Capsule**.

```text
capsule_id
timestamp
canonical_symbol
broker_symbol
market_snapshot_id
feature_set_version
feature_values_hash
strategy_version
model_version
model_output
trade_intent
risk_config_version
risk_decision
supervisor_decision
execution_result
position_state_before
position_state_after
```

Benefits:

- exact replay;
- incident analysis;
- model comparison;
- reproducibility;
- post-trade evaluation;
- future regulatory/audit needs.

This is strongly recommended.

---

# 12. Market data design

## 12.1 Store raw and derived data separately

### Raw

- ticks
- bid/ask
- broker timestamps
- bars received from MT5
- symbol specifications

### Derived

- normalized bars
- features
- regime labels
- signals
- decisions

Do not overwrite raw input.

## 12.2 Time

Use UTC internally.

Persist:

```text
event_time_utc
received_time_utc
source_time_raw
```

Never rely on local machine time or broker chart timezone alone.

## 12.3 Data quality flags

Every dataset/snapshot gets:

```text
GOOD
STALE
GAPPED
OUT_OF_ORDER
SUSPECT
```

A strategy should not trade on `SUSPECT` data.

---

# 13. Backtesting and replay

## 13.1 Do not backtest on close prices only

For an executable FX strategy, model at least:

- bid/ask;
- spread;
- commissions if applicable;
- swaps/financing when positions cross relevant boundaries;
- latency assumptions;
- slippage;
- minimum volume;
- volume steps;
- stop/freeze rules;
- order rejection;
- partial or delayed behavior if broker/product makes it relevant.

For short-horizon strategies, use tick data where possible.

## 13.2 Required anti-bias tests

- no look-ahead;
- no future-filled indicators;
- train/validation/test split ordered in time;
- walk-forward validation;
- transaction costs included before evaluation;
- hyperparameters not selected on final test set;
- feature generation identical between backtest and live.

## 13.3 Replay engine

The replay engine feeds historical events through the **same**:

- feature pipeline;
- Trading Agent;
- risk engine;
- Evaluator;
- order state machine.

Only the final execution adapter is simulated.

Goal:

> A historical replay should exercise production code, not a separate research implementation.

---

# 14. Paper-trading program

## Gate P0 — engineering simulation

Requirements:

- unit tests passing;
- risk property tests passing;
- deterministic replay;
- no order duplication;
- reconciliation tested;
- failure scenarios tested.

## Gate P1 — historical walk-forward

Requirements:

- realistic transaction costs;
- strict out-of-sample segments;
- benchmark comparison;
- drawdown analysis;
- regime breakdown;
- no unexplained performance discontinuities.

Do not promote based only on Sharpe or win rate.

## Gate P2 — MT5 demo / paper trading

The official MT5 platform supports demonstration accounts with virtual money for strategy testing.

Run continuously and capture:

- all signals;
- all vetoes;
- every `order_check`;
- every order response;
- fills;
- spread;
- slippage;
- reconnects;
- broker errors.

Suggested promotion evidence:

- meaningful sample of trades;
- multiple market regimes;
- multiple weeks of uninterrupted operation;
- no unresolved critical incidents;
- backtest/replay/live-paper behavior within documented tolerance.

The exact minimum sample must depend on strategy frequency. Do not promote a low-frequency strategy after an arbitrary short calendar period.

## Gate P3 — live-feed shadow mode

Before real execution:

- connect to production/live market feed if allowed;
- create real-time intents;
- run risk and evaluator;
- do **not** send orders;
- simulate what would have happened;
- compare spread/latency/session behavior with demo.

## Gate P4 — guarded live canary

Not part of the initial launch.

Requires manual approval.

Principles:

- smallest operational exposure compatible with the broker;
- one symbol;
- one strategy;
- tight risk budget;
- no automated self-promotion;
- automatic HALT on anomalies;
- daily operator review.

Only after canary stability can scope increase.

---

# 15. Promotion scorecard

A strategy/model version may move to the next gate only when all categories pass.

| Category | Example requirements |
|---|---|
| Correctness | replay deterministic, no state mismatch |
| Data | no unresolved leakage or timestamp issue |
| Risk | all hard limits tested |
| Execution | order lifecycle/reconciliation reliable |
| Performance | positive expected value after costs in agreed test protocol |
| Robustness | acceptable results across regimes, not one isolated period |
| Drift | current distributions within policy |
| Operations | monitoring/alerts/kill switches tested |
| Audit | all decisions traceable to code/config/model version |
| Security | secrets isolated, least privilege |
| Review | explicit promotion decision recorded |

One red category blocks promotion.

---

# 16. Metrics

## Trading metrics

- net P&L;
- gross P&L;
- expectancy/trade;
- profit factor;
- hit rate;
- average win/loss;
- max drawdown;
- time under water;
- Sharpe/Sortino where statistically meaningful;
- Calmar;
- turnover;
- average holding time;
- exposure time;
- tail loss;
- MAE/MFE;
- slippage;
- spread cost;
- rejected-order rate.

## Model metrics

Depending on target:

- calibration;
- Brier score;
- precision/recall by trade threshold;
- expected return by prediction decile;
- out-of-sample stability;
- drift metrics.

Classification accuracy alone is not a trading KPI.

## System metrics

- MT5 connected;
- terminal version;
- account ID hash;
- environment (`paper`, `shadow`, `live`);
- last market event age;
- last successful reconcile;
- order latency;
- signal latency;
- process heartbeat;
- DB health;
- queue depth;
- errors/min;
- reconnects;
- duplicate-intent count.

---

# 17. Configuration model

Configuration must be versioned and immutable per decision.

Example:

```yaml
environment: paper

markets:
  - canonical_symbol: EUR/USD
    enabled: true

risk:
  max_risk_per_trade: REQUIRED
  max_open_risk: REQUIRED
  max_daily_loss: REQUIRED
  max_drawdown: REQUIRED
  max_orders_per_hour: REQUIRED

execution:
  max_spread_points: REQUIRED
  max_market_data_age_ms: REQUIRED
  order_timeout_ms: REQUIRED
  max_slippage_points: REQUIRED

trading_agent:
  strategy_id: baseline_v1
  model_version: null

supervisor:
  enabled: true
  veto_on_unknown_regime: true
  halt_on_reconciliation_mismatch: true
```

Do **not** ship production defaults for financial risk.

Force values to be explicitly configured and validated.

---

# 18. Database model

Minimum tables:

```text
accounts
instrument_specs
market_ticks
market_bars
feature_snapshots
strategy_versions
model_versions
config_versions
trade_intents
risk_decisions
supervisor_decisions
orders
order_events
fills
positions
position_snapshots
decision_capsules
evaluation_results
drift_metrics
heartbeats
incidents
kill_switch_events
deployment_versions
```

Important keys:

- `intent_id`
- `order_request_id`
- MT5 ticket/deal/position IDs
- strategy version
- model version
- config version

---

# 19. Execution state machine

Suggested order state:

```text
CREATED
  ↓
RISK_APPROVED
  ↓
SUPERVISOR_APPROVED
  ↓
ORDER_CHECKED
  ↓
SUBMITTED
  ↓
ACKNOWLEDGED
  ↓
PARTIAL/FILLED/REJECTED
  ↓
RECONCILED
  ↓
CLOSED
```

Every transition:

- is persisted;
- has a timestamp;
- has a reason;
- is idempotent;
- emits an event.

Illegal state transitions must fail closed.

---

# 20. Failure scenarios to test

Mandatory chaos/integration tests:

1. MT5 terminal closes during an open position.
2. Python process crashes after `order_send` but before DB acknowledgement.
3. DB becomes unavailable.
4. Same intent is delivered twice.
5. Tick stream becomes stale.
6. Bid/ask becomes nonsensical.
7. Spread suddenly expands.
8. Broker rejects stop distance.
9. Broker symbol spec changes.
10. MT5 account switches unexpectedly.
11. Demo config connects to a live account.
12. Network reconnect occurs during order submission.
13. Position exists at broker but not locally.
14. Local position exists but broker does not show it.
15. Clock moves or machine resumes after sleep.
16. Strategy emits 100x normal trade frequency.
17. Model file hash differs from registry.
18. Config is changed while a decision is in flight.
19. Evaluator process is unavailable.
20. Operator triggers HALT while an order is pending.

Expected default in ambiguous situations:

> **No new exposure. Reconcile first.**

---

# 21. Security

## Secrets

Never store in:

- Git;
- source code;
- notebooks;
- logs;
- prompts;
- model context;
- `status.md`.

Store:

- MT5 account number;
- password;
- server;
- external API keys

in a secret store.

## Privilege separation

Recommended identities:

```text
trading_agent    -> read market/features, write intents
evaluator        -> read everything, write decisions/incidents
execution_engine -> read approved intents, request execution
mt5_gateway      -> broker credentials + MT5 access
dashboard        -> authenticated operator functions
```

The Trading Agent has no broker secret.

## Production controls

- MFA on infrastructure accounts;
- encrypted disks;
- firewall/allow-list;
- automatic OS security updates with controlled maintenance;
- code review before production;
- signed/reproducible deployment artifact;
- immutable model/config version IDs;
- append-only audit retention.

---

# 22. Observability dashboard

Required panels:

## Platform

- MT5 connection
- account/environment
- last tick age
- last reconcile
- DB health
- active kill switch
- current positions
- pending orders

## Trading Agent

- current regime
- last signal
- confidence
- signal frequency
- active strategy/model version
- feature drift
- no-trade percentage

## Evaluator

- last evaluation
- approve/veto rate
- active warnings
- drift state
- abnormal execution metrics
- incidents

## Risk

- current equity
- open risk
- daily realized/unrealized P&L
- configured limits
- distance to halt thresholds

Red/amber/green is useful, but always show the numeric evidence behind a color.

---

# 23. Agent communication

Prefer explicit typed events rather than free-form agent chat.

Core events:

```text
MarketSnapshotReady
SignalGenerated
TradeIntentCreated
RiskDecisionMade
SupervisorDecisionMade
OrderCheckCompleted
OrderSubmitted
OrderResultReceived
PositionChanged
ReconciliationCompleted
EvaluationCompleted
IncidentRaised
SystemHalted
```

Every event has:

```text
event_id
event_type
occurred_at_utc
correlation_id
causation_id
schema_version
payload
```

This gives the system deterministic provenance.

---

# 24. Multi-market expansion design

EUR/USD is only the first capability.

Do not let EUR/USD assumptions leak into core code.

Abstract:

```python
Instrument
MarketDataAdapter
ExecutionAdapter
CostModel
SessionCalendar
RiskModel
Strategy
```

Before adding another market, implement a **Market Capability Matrix**:

| Capability | EUR/USD | Future market |
|---|---:|---:|
| Tick data | yes | validate |
| Bid/ask | yes | validate |
| Fractional volume | broker-specific | validate |
| Trading hours | FX | validate |
| Stop rules | broker-specific | validate |
| Financing | swap | validate |
| Contract size | broker-specific | validate |
| Margin | broker-specific | validate |

A strategy may only trade an instrument whose capability profile it supports.

---

# 25. Innovative extensions worth adding later

## 25.1 Shadow Twin

For each real-time decision, run:

- production strategy;
- previous champion;
- challenger model;
- no-trade baseline.

Only the champion executes.

Store counterfactual outcomes for the others.

This accelerates research without adding trading risk.

## 25.2 Decision fingerprinting

Hash together:

```text
input data
feature version
model artifact
config
code commit
```

Persist the fingerprint with the Decision Capsule.

You can then prove exactly what created a decision.

## 25.3 Independent risk budget service

Treat risk as a scarce resource allocated by a separate component.

Future multiple strategies request risk budget; none owns it.

This makes later multi-market expansion much safer.

## 25.4 Automatic incident narratives

An LLM can read sanitized event data after an incident and generate:

- timeline;
- most likely cause;
- affected trades;
- violated invariants;
- proposed tests.

Human reviews the report.

## 25.5 Chaos trading lab

Periodically replay data with injected faults:

- latency;
- missing ticks;
- doubled events;
- rejected orders;
- widened spreads;
- gateway reconnects.

A strategy cannot be promoted until it survives predefined fault suites.

---

# 26. Build plan

## Milestone 0 — repository and engineering baseline

Deliver:

- Python project
- dependency lock
- lint/type/test pipeline
- config schema
- logging
- CI
- environment separation

Acceptance:

- clean install from scratch;
- all tests run locally and in CI;
- no secrets in repository.

---

## Milestone 1 — MT5 read-only gateway

Build:

- connection management;
- account inspection;
- symbol registry;
- tick/bar collection;
- health endpoints;
- reconnect handling.

Acceptance:

- connects only to configured demo account;
- reads EUR/USD ticks/bars;
- persists symbol specification;
- restart/reconnect tested;
- wrong account causes HALT.

---

## Milestone 2 — event journal and data pipeline

Build:

- PostgreSQL schema;
- raw market storage;
- normalized bar pipeline;
- event IDs/correlation;
- UTC handling;
- data-quality flags.

Acceptance:

- events can be replayed in original order;
- gaps/out-of-order data detected;
- raw data immutable.

---

## Milestone 3 — deterministic replay/backtest engine

Build:

- event-driven simulator;
- cost/fill model;
- bid/ask handling;
- exact production feature pipeline;
- reports.

Acceptance:

- same input → same result;
- no look-ahead tests;
- transaction costs tested;
- baseline no-trade and simple strategy run.

---

## Milestone 4 — risk engine

Build:

- account-aware position sizing;
- broker volume normalization;
- stop validation;
- exposure limits;
- daily loss/drawdown gates;
- spread/staleness checks;
- kill switch;
- property tests.

Acceptance:

- fuzz/property tests cannot create order outside configured constraints;
- missing/invalid state fails closed.

---

## Milestone 5 — paper execution

Build:

- `order_check`;
- `order_send`;
- order state machine;
- result persistence;
- reconciliation;
- idempotency;
- close/cancel flows.

Acceptance:

- demo account only;
- duplicate request does not duplicate exposure;
- crash-after-submit scenario reconciles safely.

---

## Milestone 6 — Trading Agent v1

Build:

- feature pipeline;
- deterministic baseline;
- `TradeIntent` generation;
- reason codes;
- versioned strategy.

Acceptance:

- no direct MT5 dependency;
- deterministic replay;
- every intent reproducible from Decision Capsule.

---

## Milestone 7 — Evaluator/Supervisor v1

Build:

- policy engine;
- pre-trade veto;
- post-trade scorecard;
- drift monitor;
- incident generation.

Acceptance:

- evaluator can veto;
- evaluator can HALT;
- evaluator cannot execute a trade;
- evaluator cannot reset HALT.

---

## Milestone 8 — operator dashboard

Build:

- platform health;
- orders/positions;
- agent decisions;
- veto explanations;
- risk metrics;
- manual HALT;
- audit search.

Acceptance:

- operator can understand current system state in under one screen;
- dangerous actions require confirmation and are logged.

---

## Milestone 9 — paper-trading campaign

Run:

- continuous demo trading;
- incident drills;
- restart drills;
- broker-error tracking;
- performance review;
- drift review.

Deliver weekly:

- engineering reliability report;
- strategy report;
- evaluator report;
- unresolved incidents;
- promotion decision.

---

## Milestone 10 — shadow production evaluation

Run real-time production feed without order submission.

Compare:

- demo vs live spread;
- latency;
- session behavior;
- signal frequency;
- hypothetical fills;
- expected slippage.

No live execution yet.

---

## Milestone 11 — live-canary readiness review

Requires explicit decision outside the automatic agent loop.

Checklist:

- all critical incidents closed;
- paper evidence sufficient;
- shadow evidence sufficient;
- secrets/ops hardened;
- backup/recovery tested;
- monitoring/alerts active;
- kill switch tested;
- strategy/model/config frozen;
- rollback procedure documented.

---

# 27. Suggested first 10 implementation tickets

1. Scaffold Python 3.12 project with `uv`, pytest and strict type checking.
2. Define Pydantic domain contracts and event schema.
3. Implement Windows MT5 read-only gateway.
4. Implement account/environment guard: paper mode must reject live account.
5. Build dynamic EUR/USD instrument registry from MT5 symbol metadata.
6. Persist ticks/bars, gateway health and symbol specs in PostgreSQL.
7. Build reconciliation loop for account/orders/positions.
8. Build event replay harness.
9. Implement deterministic risk policy engine and kill switch.
10. Implement one deliberately simple baseline strategy producing `TradeIntent`.

Only after these are stable should model sophistication increase.

---

# 28. Definition of Done for the initial platform

The initial system is done when:

- EUR/USD market data is collected reliably from MT5;
- only a demo account can be used in paper mode;
- Trading Agent produces typed intents;
- hard risk checks cannot be bypassed by the Trading Agent;
- Evaluator can veto/halt independently;
- MT5 orders are idempotent and reconciled;
- all decisions are auditable;
- system restarts safely;
- market replay is deterministic;
- monitoring exposes stale data and account mismatch;
- paper trading can run unattended;
- a human can immediately halt new orders;
- no component is allowed to self-promote to live trading.

---

# 29. Questions to resolve before coding production behavior

These do not block the infrastructure build, but they must be resolved before strategy evaluation:

1. Which broker and exact MT5 server will be used?
2. What account type: hedging or netting?
3. What is the expected strategy horizon: seconds, minutes, hours, days?
4. Are overnight positions allowed?
5. Are positions allowed around high-impact macro events?
6. What are the permitted trading sessions?
7. What risk budget is acceptable in paper, shadow and eventual live mode?
8. What is the maximum acceptable drawdown before mandatory review?
9. Is the goal absolute return, risk-adjusted return, capital preservation, or another objective?
10. What data beyond MT5 price data is permitted?
11. What constitutes enough paper evidence for promotion?
12. Who has authority to reset a production kill switch?

---

# 30. Critical recommendations

1. **Build the execution/risk platform before an advanced AI strategy.**
2. **Keep MT5 credentials out of both agents.**
3. **Use a demo account first and enforce that in code.**
4. **Treat “NO_TRADE” as a first-class and often desirable action.**
5. **Use one production champion and many shadow challengers.**
6. **Make every decision replayable.**
7. **Never use backtest P&L alone as promotion evidence.**
8. **Model bid/ask, spread and execution costs.**
9. **Fail closed when state is unknown.**
10. **Require human promotion between paper, shadow and live modes.**
11. **Do not let an LLM write, deploy and activate its own trading strategy.**
12. **Do not add more markets until the EUR/USD lifecycle is operationally boring.**

“Operationally boring” is a success criterion: reconnects, restarts, rejected orders, stale data and reconciliation should all have predictable outcomes before the strategy becomes more ambitious.

---

# 31. External technical references validated for this design

Accessed 2026-08-17.

- MetaQuotes — Python Integration / MetaTrader5 API:  
  https://www.mql5.com/en/docs/python_metatrader5
- MetaQuotes — `initialize`:  
  https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py
- MetaQuotes — `order_send`:  
  https://www.mql5.com/en/docs/python_metatrader5/mt5ordersend_py
- MetaTrader 5 Help — Demo and real accounts:  
  https://www.metatrader5.com/en/terminal/help/startworking/acc_open
- PyPI — official `MetaTrader5` package metadata and Windows wheel distribution:  
  https://pypi.org/project/MetaTrader5/

