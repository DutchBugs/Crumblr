# Crumblr

Autonomous EUR/USD trading platform on MetaTrader 5.

**Current gate: M0 open only on hosted-CI confirmation + human contract
review · M1 PASSED (MT5-INTEGRATED) · M2 PASSED · Phase 4 (execution
engineering) formally PASSED · M5/`order_send` NO-GO until `feedback.2.0`
gives an explicit GO.** Real EUR/USD market data reaches the Trading
Agent, Risk Engine and Supervisor end to end and has been proven against
the real Pepperstone demo terminal (F-051, both parts CLOSED). The full
non-sending execution chain exists and is real — `order_check`, the
submission multi-gate, real per-ticket close/flatten, order-send
idempotence, ambiguous-outcome recovery, post-fill reconciliation — but
every activation flag defaults closed and `order_send` itself remains
structurally unreachable from any shipped configuration. Since
2026-08-27 the project also runs a second, external-agent-driven track
(an Agent Gateway, an authenticated proposal boundary, a Static Agent
fork) alongside the original internal-strategy path, converging on the
same Core Risk/execution chain. No live trading is permitted. See
[status.md](status.md) for the live picture (start at its compact
"Current state" header), [HANDOVER.md](HANDOVER.md) for how to pick this
up cold, and [build.md](build.md) for the architecture and risk
specification.

## Two development tracks, one safety core

This repository is developed by two coordinated tracks, each with its
own instructions and its own local finding register:

| Track | Owns | Instructions | Findings |
|---|---|---|---|
| **Core / Execution** (Dev 1) | Risk engine, MT5 gateways, the execution/flatten chain, `status.md`, `review/FEEDBACK.md` | `review/CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS.md` | project-wide `F-###` |
| **External Agent Integration** (Dev 2) | `agent_gateway/`, the Agent Gateway trust boundary, `review/AGENT_STATUS.md` | `review/CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md` | local `AG-###`, `review/AGENT_FEEDBACK.md` |

Crumblr itself remains the trusted control plane in both tracks: external
agents (or an external Supervisor) never receive MT5 access, broker
credentials, database write access, final lot-size authority, risk-policy
mutation, or HALT-reset authority. `review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`
is the current staged coordination order (Phases 0-F) both tracks work
against on the way to one deliberately constrained DEMO canary order.

## See it run

```bash
uv run python scripts/run_replay.py
```

This drives the full transaction flow from build.md §3 — observe, signal,
intent, risk check, supervisor check, order build, `order_check`, `order_send`,
reconcile, audit — over a deterministic synthetic series. The only simulated
component is the broker adapter; everything upstream is the production code
path, which is what build.md §13.3 requires of a replay.

Watch the guardrails refuse things:

```bash
uv run python scripts/run_replay.py --chaos
```

```bash
uv run python scripts/run_replay.py --max-daily-loss 0.0005
```

```bash
uv run python scripts/run_replay.py --wrong-server
```

The first injects spread spikes, stale ticks and suspect data, and the risk
engine blocks each with its own reason code. The second tightens the daily-loss
gate until the kill switch trips — after which no further order is submitted.
The third points the broker at an unexpected server and the account guard halts
the system outright.

The market data is synthetic, so any P&L those runs report is a property of the
random seed. What they demonstrate is the control flow.

## Give it a memory

By default a replay reports and forgets. With a PostgreSQL behind it, the same
run writes its audit trail as it decides and recovers its safety state from it
on the next start.

```bash
docker run -d --name crumblr-pg \
  -e POSTGRES_USER=crumblr -e POSTGRES_PASSWORD=crumblr -e POSTGRES_DB=crumblr \
  -p 55432:5432 postgres:17-alpine
```

```bash
CRUMBLR_DATABASE_URL="postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr" uv run python scripts/run_replay.py --persist --create-schema
```

The first run against a fresh database **refuses to trade**, and that is the
point: no RUNNING safety state has ever been recorded, so the composite store
of ADR-002 answers `UNKNOWN` and the kill switch starts closed. An unread
database is not permission. Clearing it takes an operator and a note, exactly
as it would in production:

```bash
CRUMBLR_DATABASE_URL="postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr" uv run python scripts/run_replay.py --persist --operator you --incident-note "arming the local development database"
```

Two things are persisted, and keeping them apart is the point:

| | |
|---|---|
| **event journal** | what the system *did* — signal, intent, risk decision, supervisor decision, order, fill, position change, sealed capsule |
| **market store** | what the system *saw* — every tick and bar it observed, including the warm-up windows that produce no decision at all |

Plus the safety state and the risk-session budget, which are what a restart
recovers from — and, since ADR-021 (2026-09-04), a real Postgres advisory
lock (`RiskLedgerLock`) serializing that budget across every process that
reads or writes it, internal or external-agent-driven. A run can be
rebuilt from the `events` table alone, and the rebuilt decision sequence
has to fingerprint identically to the one that ran; there is an
integration test asserting exactly that.

The schema is versioned with Alembic, and `--create-schema` runs the
migrations rather than creating tables from the current code — the ordinary
local path exercises the same mechanism a deployment would. There is a test
that dumps the database, destroys it, restores it, and checks the run still
reconstructs.

Replaying the same series into the same database twice halts, and the report
says why: the recorded trading day is then ahead of the bars being fed in, so
market time is running backwards. In production that cannot happen without
something being wrong with a clock; in replay it is one command away.

## The trading scheme

The configured strategy is **`ict_v1`**, which enforces the ICT entry model as
a set of conditions. A trade is only proposed when all of these hold at once:

| Condition | Meaning |
|---|---|
| Market open | Not in the weekend gap |
| Killzone | Inside London Open or New York AM, in New York local time |
| Liquidity sweep | A still-untouched swing level was pierced and rejected |
| Structure shift | Price closed beyond the swing that was current at the time |
| Fair value gap | A three-bar imbalance made by a bar ranging ≥1.5×ATR |
| Price in zone | Price has retraced back **into** that gap — the entry trigger |
| Discount / premium | On the correct half of the impulse leg being retraced |
| Optimal trade entry | Retraced 62–79% of that leg |

Two further conditions — an order block, and a qualifying liquidity target —
are checked and recorded but not required by default, for reasons documented at
`IctConditions`.

Each condition is separately switchable. That is not a production dial: it is
what makes the model measurable, since turning one off and re-running shows
what it was contributing. Every refusal records which condition failed.

`baseline_v1` remains registered as the benchmark build.md §9.2 requires — a
model that cannot beat a simple baseline after costs should not be promoted,
and is also the strategy F-051 part 2's real-terminal proof used, since
`ict_v1`'s own selectivity (~3 setups per 12,000 bars) made it impractical
to wait out for a wiring-only proof.

**No performance claim attaches to any of this.** ICT rests on a premise about
institutional order flow; synthetic replay data has none, so a run shows the
detection logic works and nothing about whether the premise holds.

An **external-agent** path exists alongside this internal one, strategy-neutral
by explicit architectural decision (review 1.28, F-066): Crumblr never
re-implements or maps onto an external strategy's own vocabulary — it hands an
external Trading Agent a neutral market/context bundle and enforces only
structural shape on whatever `TradeProposal`/reason codes come back, never a
semantic whitelist. `baseline_v1`/`ict_v1` stay the internal-strategy
reference and are not deleted or deprecated by this.

## What the platform refuses to do

Two of these are owner decisions rather than engineering ones, and both are now
refusals the deterministic risk engine makes rather than intentions in a
document.

- **One EUR/USD exposure at a time**, bounded by a real open-risk budget
  (owner risk policy v1: 2% per trade, 3% total open risk, 4% daily loss, 8%
  max drawdown — `review/adr/ADR-011-owner-risk-policy-v1.md`), not a naive
  position count. It holds whether the account turns out to be hedging or
  netting (this account is `RETAIL_HEDGING`, confirmed against the real
  terminal).
- **Nothing held overnight from Friday to Monday.** Owner session policy v1
  (`review/adr/ADR-012-owner-session-policy-v1.md`) made this a *weekly*, not
  daily, rule: Monday-Thursday now permits holding overnight; only Friday
  carries a last-entry cutoff and a mandatory flatten deadline before the
  17:00 America/New_York weekly close. The platform can now genuinely
  *close* a position for this, not merely detect and halt on it — see
  [ADR-020](review/adr/ADR-020-real-flatten-close.md) — but every real-close
  activation flag still defaults closed in every shipped config.
- **Two supervisor checks still announce that they are not in force.** The
  confidence band spans the range the contract already enforces, and the
  frequency threshold has never been calibrated against a real feed. An
  approval names them rather than implying seven rules passed.

## The one architectural rule

> Agent proposes. Risk engine constrains. Supervisor vetoes. Execution service
> executes. Reconciliation verifies.

Three systems, deliberately separated:

| System | May do | May never do |
|---|---|---|
| **Platform (Core)** | market data, orders, risk, storage, kill switches | — |
| **Trading Agent** (internal strategy, or external via the Agent Gateway) | read market state, emit a typed `TradeIntent`/`TradeProposal` | call MT5, hold credentials, choose lot size |
| **Evaluator / Supervisor** (internal deterministic policy, or an external Supervisor) | approve, veto, trip a kill switch | create a trade, change size, reset a halt, waive Risk |

The separation is enforced by the type system where it can be. `TradeIntent`
has no lot-size field at all, so an agent cannot name its own position size;
the risk engine derives it from equity, stop distance and the broker's symbol
specification. There is a test that fails if such a field is ever added. An
external Supervisor's timeout, error or malformed response reads as `UNKNOWN`
— never as an implicit approval — the same fail-closed rule every other
safety-critical state in this codebase already follows.

## Getting started

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv itself.

```bash
uv sync
```

Run the quality gate — the same commands CI runs:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Load and inspect the paper configuration:

```bash
uv run python -c "from pathlib import Path; from crumblr.config import load_config; from crumblr.domain import Environment; c = load_config(Environment.PAPER, config_dir=Path('config')); print(c.environment, c.enabled_symbols(), c.config_version[:12])"
```

## Host requirements

The official `MetaTrader5` Python package is distributed as **Windows x86-64
wheels only**. Development on macOS or Linux is fine — everything except the
gateway itself is host-independent, and the `mt5` extra is marked
`sys_platform == 'win32'` so `uv sync` works everywhere.

Running an actual MT5 connection needs a Windows x86-64 host with the MetaTrader
5 terminal installed. That is a hard external dependency, not a preference —
Windows on ARM will not do, because the wheels do not exist for it. As of
2026-08-26, the development host used for this project *is* that Windows/MT5
host — see `HANDOVER.md` §9.

On that host:

```powershell
uv sync --extra mt5
uv run python scripts/mt5_probe.py --json first-contact.json
```

`scripts/mt5_probe.py` is M1 first contact: it connects, reads the account, the
symbol and the instrument specification, and prints them for a human to compare
with what the code assumes. It cannot trade — it holds a read-only gateway whose
execution methods raise, and a test fails if it reaches for a mutating call.

`scripts/mt5_live_reader.py` is the continuous version: it keeps reading real
EUR/USD ticks and M5 bars, reconnecting and fully revalidating the account,
symbol, instrument spec and broker-clock offset on every reconnect, and
persists everything to PostgreSQL, including durable broker account/position/
pending-order snapshots (F-047, real-terminal-validated).

```powershell
$env:CRUMBLR_DATABASE_URL = "postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr_soak"
uv run python scripts/mt5_live_reader.py --duration 1800 --json var/live_reader_health.json
```

Point it at a database dedicated to real-terminal runs (`crumblr_soak`,
never the shared test database) — see `.env.example` for why, and
`scripts/reset_soak_database.py` for how to reset that database without
drifting `alembic_version` out of sync with it (F-041). Dev-1/Dev-2 each
have their own isolated integration-test databases too
(`crumblr_test_dev1`/`crumblr_test_dev2`) — see the Dev instructions
documents in `review/` for the full workspace-isolation setup.

## Live/shadow decision pipeline — real data, `order_send` unreachable

```powershell
$env:CRUMBLR_DATABASE_URL = "postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr_soak"
uv run python scripts/live_decision.py
```

`LiveDecisionOrchestrator` closes the gap between `LiveReader`'s real MT5
data and the actual decision pipeline: a real closed M5 bar (read from
`MarketDataStore`) drives the same Trading Agent, the same intent-time
Risk Engine, and the same Supervisor replay uses — fed real broker
equity/positions (F-047) and a real reconciliation status
(`application/reconciliation.py`) instead of a simulated broker. Since
ADR-021, every cycle recovers and persists the risk-session ledger fresh
under a real cross-process lock, rather than caching it in memory — the
same authority an external-agent-driven proposal (`agent_gateway/decision_path.py`)
reads through the identical lock. This orchestrator itself still **stops**
after the Supervisor's verdict: no `ApprovedOrder` is ever constructed
from this path, and there is no `order_check`/`order_send` call anywhere
in *this* process's call graph.

A separate, real, non-sending execution chain does exist
(`application/execution.py::ExecutionOrchestrator`) — `order_check`,
`SubmissionGate`'s nine-condition multi-gate, `order_send`
idempotence, ambiguous-outcome recovery, automatic weekly flatten with a
real per-ticket close, post-fill reconciliation and broker-side
stop-loss verification are all built and tested (Phase 4, formally
PASSED; Phase B, all slices shipped — see `status.md`'s compact header
and `review/adr/ADR-006` through `ADR-021`). Every activation flag
(`ExecutionConfig.submission_enabled`, `.flatten_submission_enabled`,
`.feedback_2_0_approved`, `RiskConfig.approved_config_version`) defaults
closed in every shipped config, and `OrderCheckMt5Gateway.order_send`/
`.close_all_positions` remain unconditional raises — the one real
mutating adapter (`DemoOrderSendMt5Gateway`) exists, is tested, and is
constructed by nothing in `src/`/`scripts/` today. `order_send` stays
NO-GO until `feedback.2.0.md` gives an explicit GO.

`scripts/reconcile.py` is the one-shot companion: compares the latest
durable broker-state snapshot (F-047) — account/positions/pending orders
and the instrument spec — against the platform's expected state,
returning `MATCHED`/`MISMATCHED`/`UNKNOWN`, the last never silently
upgraded to the first. The instrument-spec expectation is
`config.MarketConfig.expected_spec_version`, an explicitly pinned value a
human approves after reviewing a real observation — never inferred from
whichever spec happened to be observed first (F-055). Discovery through
reconciliation `MATCHED` has been proven for real against the live
Pepperstone demo terminal (F-051 part 1); a real Trader/Risk/Supervisor
decision from a real closed M5 bar has been proven too (F-051 part 2,
closed 2026-09-01, `baseline_v1`).

## External-agent integration — a second, converging track

Since 2026-08-27 (ADR-005), an authenticated `agent_gateway/` package
exists as the boundary between external agents (a Trading Agent, an
external Supervisor, or a "Static Agent" reference fork) and Crumblr's
own trusted control plane: identity/credential authentication,
assignment authorization, context binding, idempotent proposal claiming,
and a strategy-neutral mapping from an external `TradeProposal` into the
same platform-owned `TradeIntent` → Core Risk → deterministic Policy →
`DecisionCapsule` chain internal strategies already use. It never
receives MT5 access, broker credentials, or database write access to
anything beyond its own audit tables. `review/adr/ADR-005-external-agent-trust-boundary.md`
and `review/THREAT_MODEL_AGENT_GATEWAY.md` are the full design; progress
against it is tracked separately in `review/AGENT_STATUS.md`/
`review/AGENT_FEEDBACK.md`, not narrated continuously here.

## Dashboard v0 — read only

```bash
uv run python scripts/run_dashboard.py
```

Serves a single status page at `http://127.0.0.1:8050/` (`/api/state` for the
JSON it polls) showing MT5 connectivity, the latest tick/bar, HALT state and
the latest Signal/RiskDecision/SupervisorDecision — reading only PostgreSQL
and the `LiveReader` health snapshot file `mt5_live_reader.py --json` writes.
Nothing in `src/crumblr/dashboard/` imports `MetaTrader5`, reads a credential,
or registers a route other than `GET` — enforced by
`tests/integration/test_dashboard.py::TestReadOnlyBoundary`, not only by
intent. Visual scope is frozen (F-042..046); see `review/DEVIATIONS.md`
D-043 for how this differs from build.md §22/Milestone 8's full
operator-dashboard spec (manual HALT, audit search, order/position
detail — none of it here yet).

## Safety guardrails already in force

- **No permissive risk defaults.** Every limit in `config/` is a required
  field. A config that omits `max_daily_loss` fails to load.
- **Paper and shadow require a demo account.** Enforced in config validation,
  not by convention.
- **Live is doubly gated.** A live config must set `live_trading_acknowledged`
  *and* the loader requires `CRUMBLR_ALLOW_LIVE=1`. No `config/live.yaml` is
  shipped; creating one is a deliberate act at gate P4.
- **`order_send` is multi-gated on top of that.** Nine simultaneous
  conditions — environment, verified account/server, reconciliation
  `MATCHED`, market data health, safety state `RUNNING`, owner-approved
  risk policy, execution explicitly enabled, terminal AlgoTrading
  enabled, and `feedback.2.0` GO — any one false or unknown closes the
  gate (`risk/submission_gate.py`, `review/adr/ADR-006-submission-gate.md`).
- **No floats in money.** Prices and volumes are `Decimal`, and the model
  boundary rejects `float` outright.
- **No naive datetimes.** Everything is timezone-aware UTC.
- **Secrets cannot reach config.** The loader rejects credential-shaped keys,
  and CI scans history for leaked secrets. The raw MT5 login is never
  persisted or logged unmasked anywhere in this codebase (F-031).
- **One serialized risk authority.** A real Postgres advisory lock
  (`RiskLedgerLock`, ADR-021) prevents two processes — an internal
  strategy's decision loop and an external-agent proposal — from ever
  racing against the same daily-loss/drawdown budget.

## Layout

```text
config/            environment configuration (base + per-environment overlay)
scripts/
  run_replay.py       the runnable prototype
  mt5_probe.py        M1 first contact — one-shot, read-only
  mt5_live_reader.py  M1 continuous read — real ticks/bars, broker-state,
                       instrument spec; reconnect+revalidate
  live_decision.py    F-048 — live/shadow decision loop, entries never reach order_send
  reconcile.py        one-shot broker-state vs. expected-state check
  run_dashboard.py    Dashboard v0 — read-only status page
  reset_soak_database.py  deliberate, all-Alembic soak-database reset (F-041)
  run_execution_preflight_evidence.py  one-shot real order_check evidence run
src/crumblr/
  domain/          contracts, events, enums, money, hashing — no I/O, no SDKs.
                   ApprovedOrder, ExecutionResult, FlattenInstruction/FlattenPlan,
                   BrokerAccountSnapshot/BrokerPositionSnapshot/BrokerPendingOrderSnapshot,
                   SnapshotCompleteness/ReconciliationStatus (…/UNKNOWN)
  market_data/     synthetic generator with fault injection; normalisation (M2)
  mt5_gateway/     BrokerPort, simulated broker, the read-only M1 adapter,
                   execution.py (OrderCheckMt5Gateway — real order_check,
                   order_send/close always disabled), demo_execution.py
                   (DemoOrderSendMt5Gateway — a real, separate, unwired
                   mutating adapter: order_send, close_position/close_all_positions)
  risk/            sizing, pre-trade policies, kill switch, equity ledger,
                   durable + locked (ADR-021) risk sessions, submission_gate.py,
                   flatten_gate.py, execution_preflight_gate.py, portfolio_risk.py
  trading_agent/   ICT primitives (structure, imbalance, liquidity, sessions),
                   the ict_v1 entry model, the baseline_v1 benchmark, and
                   base.py's FeatureEvidence protocol (persisted by D-031)
  evaluator/       supervisor pre-trade policy; post-trade and drift at M7
  agent_gateway/   external-agent trust boundary (ADR-005) — identity/
                   credential auth, assignment authorization, context
                   binding, proposal claiming, TradeProposal -> TradeIntent
                   mapping, decision_path.py's shared Risk/Policy wiring
  persistence/     PostgreSQL schema, event journal, capsule store, market
                   store, broker-state store, instrument-spec store,
                   feature-snapshot store (D-031), decision-window store
                   (F-054), execution.py (requests/events), flatten.py
                   (requests/events), risk_session.py (session store +
                   RiskLedgerLock, ADR-021), agent_gateway.py, safety
                   and risk-session stores
  backtest/        cost and fill models                   (M3, remaining)
  application/     orchestration.py — the replay §3 transaction flow;
                   recording.py/bootstrap.py (DurableRuntime)/reconstruction.py;
                   live_reader.py (M1 — observes + persists real MT5 state);
                   broker_state.py (F-047); reconciliation.py; decision_window.py
                   (F-054); live_decision.py (F-048 — LiveDecisionOrchestrator);
                   execution.py (ExecutionOrchestrator — the real, non-sending
                   execution/flatten chain); execution_outcome.py; expected_state.py;
                   flatten_plan.py
  dashboard/       Dashboard v0 — read-only FastAPI app, outside the broker
                   execution boundary (review 1.9 F-035), visual scope frozen
  api/             control API — authenticated operator functions (M8, not built)
  observability/   logging, metrics, tracing
tests/             unit, property, replay, integration, chaos
```

## What is implemented, and what is not

| Area | State |
|---|---|
| Domain contracts, events, config | Complete |
| ICT entry model (`ict_v1`) | Working; ten enforced conditions, no live-evidence promotion decision made |
| Benchmark strategy (`baseline_v1`) | Working — also the strategy F-051 part 2's real-terminal proof used |
| Risk engine, sizing, kill switch | Working against simulated *and* real broker state; owner risk policy v1 approved and shipped |
| Supervisor pre-trade policy | Layer 1 (deterministic) only; an external Supervisor exists as a second, converging veto layer (Phase C) |
| Simulated broker, replay orchestrator | Working, documented approximations |
| Real MT5 gateway | **M1 PASSED, MT5-INTEGRATED** — read-only adapter, first contact, a clean continuous-read soak, and deliberate terminal interruptions, all auto-recovered with full revalidation |
| PostgreSQL journal, capsules, safety and risk-session state | Working; a run rebuilds from the journal and survives a restart; risk-session state is now serialized across processes by a real Postgres lock (ADR-021) |
| Raw tick/bar storage and the bar pipeline | Working and real-terminal-validated |
| Schema migrations, backup and restore | Working, including a real pg_dump/restore proof on hosted CI (F-067/F-068) |
| One EUR/USD exposure, exact open-risk budget, weekly session boundary | Enforced by the risk engine (owner risk policy v1, ADR-011/ADR-012) |
| Durable broker account/position/pending-order snapshots (F-047) | Working, **real-terminal-validated** (F-051 part 1) |
| Broker-state freshness as its own health concept (F-050) | Working, same validation status |
| Reconciliation (account/position/pending-order + instrument-spec baseline) | Working, **real-terminal `MATCHED` proven** (F-051 part 1); post-fill reconciliation from durable execution history also built (item 8) |
| Live/shadow decision pipeline (F-048) | Working, **real-terminal decision proven** (F-051 part 2, `baseline_v1`); risk-session ledger now recovered/persisted fresh every cycle under a real lock, not cached (ADR-021) |
| Durable decision-window idempotence (F-054) | Working, fail-closed on a corrupted record |
| `order_check` (real, non-mutating) | Working, **real-terminal evidence gathered** — a genuine `ORDER_CHECK_REJECTED` (AlgoTrading deliberately off), not a workaround |
| `SubmissionGate` (nine-condition submission multi-gate) | Working, wired into the real execution chain, structurally unreachable in every shipped config |
| `order_send` idempotence, ambiguous-outcome recovery | Working — a deterministic MT5 magic-number derivation, and durable broker-state-based recovery for a request stuck mid-submission |
| Automatic weekly flatten, including a real per-ticket close | Working — real close capability exists (`DemoOrderSendMt5Gateway.close_position`); every activation flag defaults closed |
| Feature *values* in storage (D-031) | Working, on both the replay and live paths |
| Post-trade evaluation, drift monitor | Not started |
| Dashboard v0 | Working — read-only status page. Visual scope frozen |
| Control API, manual HALT/FLATTEN from a UI | Not started (the equivalent operator controls exist as a Python API, `risk/operator_controls.py`, not a UI) |
| External-agent trust boundary (Agent Gateway) | Working — identity/credential auth, assignment authorization, `TradeProposal -> TradeIntent` mapping, shared Risk/Policy wiring, a real Postgres-locked shared risk authority (AG-012, closed) |
| `order_send` itself | Not reachable from any shipped config — M5/`order_send` is **NO-GO** until `feedback.2.0.md` gives an explicit GO |

`notebooks/` is research only. Production strategy logic lives in tested modules.
