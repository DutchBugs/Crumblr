# Crumblr

Autonomous EUR/USD trading platform on MetaTrader 5.

**Current gate: M1 PASSED (MT5-INTEGRATED, read-only) · M2 PASSED · M0/M3
otherwise a working end-to-end prototype against simulated market data. No
order-submission path exists anywhere in the code, and no live trading is
permitted.** See [status.md](status.md) for the live picture and
[build.md](build.md) for the architecture and risk specification.

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
recovers from. A run can be rebuilt from the `events` table alone, and the
rebuilt decision sequence has to fingerprint identically to the one that ran;
there is an integration test asserting exactly that.

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
model that cannot beat a simple baseline after costs should not be promoted.

**No performance claim attaches to any of this.** ICT rests on a premise about
institutional order flow; synthetic replay data has none, so a run shows the
detection logic works and nothing about whether the premise holds.

## What the platform refuses to do

Two of these are owner decisions rather than engineering ones, and both are now
refusals the deterministic risk engine makes rather than intentions in a
document.

- **One EUR/USD exposure at a time.** Not a configuration field: raising it is
  a code change, a review and a recorded decision. It holds whether the account
  turns out to be hedging or netting, because a netting account would net a
  second order into the first position and a hedging account would open a
  parallel one, and v1 is permitted to do neither.
- **Nothing held overnight.** The trading day ends at 17:00 America/New_York —
  the rollover that charges swap — not at midnight UTC. Entries stop an hour
  before it, and exposure that survives the flatten deadline halts the system.
  The platform does not yet *close* the position; that needs the execution path
  and is recorded as [ADR-004](review/adr/ADR-004-intraday-session-boundary.md) §5.
- **Two supervisor checks announce that they are not in force.** The
  confidence band spans the range the contract already enforces, and the
  frequency threshold has never been calibrated against a real feed. An
  approval names them rather than implying seven rules passed.

## The one architectural rule

> Agent proposes. Risk engine constrains. Supervisor vetoes. Execution service
> executes. Reconciliation verifies.

Three systems, deliberately separated:

| System | May do | May never do |
|---|---|---|
| **Platform** | market data, orders, risk, storage, kill switches | — |
| **Trading Agent** | read market state, emit a typed `TradeIntent` | call MT5, hold credentials, choose lot size |
| **Evaluator / Supervisor** | approve, veto, trip a kill switch | create a trade, change size, reset a halt |

The separation is enforced by the type system where it can be. `TradeIntent`
has no lot-size field at all, so an agent cannot name its own position size;
the risk engine derives it from equity, stop distance and the broker's symbol
specification. There is a test that fails if such a field is ever added.

## Getting started

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv itself.

```bash
uv sync
```

Run the quality gate — the same three commands CI runs:

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
Windows on ARM will not do, because the wheels do not exist for it.

On that host:

```powershell
uv sync --extra mt5
uv run python scripts/mt5_probe.py --json first-contact.json
```

`scripts/mt5_probe.py` is M1 first contact: it connects, reads the account, the
symbol and the instrument specification, and prints them for a human to compare
with what the code assumes. It cannot trade — it holds a read-only gateway whose
execution methods raise, and a test fails if it reaches for a mutating call.
The step-by-step runbook, including what to do with each field it reports, is
[HANDOVER.md](HANDOVER.md) §4.

`scripts/mt5_live_reader.py` is the continuous version: it keeps reading real
EUR/USD ticks and M5 bars, reconnecting and fully revalidating the account,
symbol, instrument spec and broker-clock offset on every reconnect, and
persists everything to PostgreSQL. Real-terminal-validated 2026-08-24 (M1
PASSED, `review/feedback.1.12.md`) — a clean 30-minute run and two deliberate
terminal interruptions, both recovered automatically.

```powershell
$env:CRUMBLR_DATABASE_URL = "postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr_soak"
uv run python scripts/mt5_live_reader.py --duration 1800 --json var/live_reader_health.json
```

Point it at a database dedicated to real-terminal runs (`crumblr_soak`,
never the shared test database) — see `.env.example` for why, and
`scripts/reset_soak_database.py` for how to reset that database without
drifting `alembic_version` out of sync with it (F-041).

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
intent. See `review/DEVIATIONS.md` D-043 for how this differs from build.md
§22/Milestone 8's full operator-dashboard spec (manual HALT, audit search,
order/position detail — none of it here yet).

## Safety guardrails already in force

- **No permissive risk defaults.** Every limit in `config/` is a required
  field. A config that omits `max_daily_loss` fails to load.
- **Paper and shadow require a demo account.** Enforced in config validation,
  not by convention.
- **Live is doubly gated.** A live config must set `live_trading_acknowledged`
  *and* the loader requires `CRUMBLR_ALLOW_LIVE=1`. No `config/live.yaml` is
  shipped; creating one is a deliberate act at gate P4.
- **No floats in money.** Prices and volumes are `Decimal`, and the model
  boundary rejects `float` outright.
- **No naive datetimes.** Everything is timezone-aware UTC.
- **Secrets cannot reach config.** The loader rejects credential-shaped keys,
  and CI scans history for leaked secrets.

## Layout

```text
config/            environment configuration (base + per-environment overlay)
scripts/
  run_replay.py       the runnable prototype
  mt5_probe.py        M1 first contact — one-shot, read-only
  mt5_live_reader.py  M1 continuous read — real ticks/bars, reconnect+revalidate
  run_dashboard.py    Dashboard v0 — read-only status page
  reset_soak_database.py  deliberate, all-Alembic soak-database reset (F-041)
src/crumblr/
  domain/          contracts, events, enums, money, hashing — no I/O, no SDKs
  market_data/     synthetic generator with fault injection; normalisation (M2)
  mt5_gateway/     BrokerPort, simulated broker, and the read-only M1 adapter
  risk/            sizing, pre-trade policies, kill switch, equity ledger,
                   durable safety state, restart-safe risk sessions
  trading_agent/   ICT primitives (structure, imbalance, liquidity, sessions),
                   the ict_v1 entry model, and the baseline_v1 benchmark
  evaluator/       supervisor pre-trade policy; post-trade and drift at M7
  persistence/     PostgreSQL schema, event journal, capsule store, safety and
                   risk-session stores
  backtest/        cost and fill models                   (M3, remaining)
  application/     orchestration of the §3 transaction flow, the recorder that
                   journals it, and reconstruction of a run from the journal
  dashboard/       Dashboard v0 — read-only FastAPI app, outside the broker
                   execution boundary (review 1.9 F-035)
  api/             control API — authenticated operator functions (M8, not built)
  observability/   logging, metrics, tracing
tests/             unit, property, replay, integration, chaos
```

## What is implemented, and what is not

| Area | State |
|---|---|
| Domain contracts, events, config | Complete |
| ICT entry model (`ict_v1`) | Working; ten enforced conditions, no evidence yet |
| Benchmark strategy (`baseline_v1`) | Working, deliberately simple |
| Risk engine, sizing, kill switch | Working against simulated data |
| Supervisor pre-trade policy | Layer 1 (deterministic) only |
| Simulated broker, replay orchestrator | Working, documented approximations |
| Real MT5 gateway | **M1 PASSED, MT5-INTEGRATED** (`review/feedback.1.12.md`, 2026-08-24). Read-only adapter, first contact, a clean 30-minute continuous-read soak, and two deliberate terminal interruptions — both auto-recovered with full account/symbol/spec/clock revalidation. `order_send` remains structurally unreachable |
| PostgreSQL journal, capsules, safety and risk-session state | Working, and written by the running orchestrator; a run rebuilds from the journal and survives a restart |
| Raw tick/bar storage and the bar pipeline | Working, and real-terminal-validated: 30 clean minutes produced 2,920 real ticks and 17 real M5 bars, all `GOOD` quality, zero gaps |
| Schema migrations, backup and restore | Working; no backup *schedule* yet. A dedicated, all-Alembic soak-database reset exists (`scripts/reset_soak_database.py`, F-041) |
| One EUR/USD exposure, intraday entry cut-off | Enforced by the risk engine |
| Automatic flatten at the session boundary | Not started — detection halts instead (M5, ADR-004) |
| Feature *values* in storage | Not started — only their hash and version (D-031) |
| Post-trade evaluation, drift monitor | Not started |
| Dashboard v0 | Working — read-only status page (`scripts/run_dashboard.py`), see above. Not the full build.md §22/M8 dashboard (D-043) |
| Control API, manual HALT/FLATTEN from a UI, reconciliation | Not started |

`notebooks/` is research only. Production strategy logic lives in tested modules.
