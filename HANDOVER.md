# Handover

Everything a developer needs to pick this up cold.

**Written 2026-08-18. Rewritten 2026-08-24** when a Windows host became
available and the repository got a remote. **Rewritten 2026-08-26** after
MT5 first contact, M1/M2 passing, and the platform being wired end to end
from real market data through to a (deliberately unreachable) order.
**Updated twice more the same day**: once after reconciliation,
decision-window durability and feature-value persistence were completed
(F-053/F-054/D-031), and again after a second review the same day found —
and this session fixed — two real correctness gaps in that work: F-054's
recovery treated a corrupted record the same as an absent one, and F-053's
instrument-spec comparison trusted whichever spec was observed first
instead of an approved baseline. **Everything left open now genuinely
needs either a Windows/MT5 host, GitHub Actions access, or a human
reviewer; nothing is blocked on engineering alone any more.**

**Start at §0.** It is the exact point of handover. §4 tells you what is
already proven against the real broker and what the very next session
should do. Then `CLAUDE.md` §1 for the mandatory session-start protocol.

---

## 0. Start here — the exact point this was handed over

**Handed over 2026-08-26**, after review `feedback.1.19.md` was processed:
it accepted F-053/F-054/D-031 as material progress but found two
execution-grade gaps and reopened both the same day they were reported
shipped — both fixed before this handover.

The last few things that happened, in order:

1. MT5 first contact succeeded (2026-08-24): the read-only gateway met the
   real Pepperstone demo terminal for the first time, followed the same day
   by a clean 30-minute continuous-read soak (Phase A) and two deliberate
   terminal closures that both recovered automatically (Phase B).
   **M1 PASSED** (`feedback.1.12.md`).
2. Dashboard v0 was built, then visually rebuilt into its current dark
   ops-console layout. Its visual scope is now **frozen** — only
   operational data panels (broker state, reconciliation, live decisions)
   may still be added, no more layout/framework work.
3. The owner's direction (relayed via review 1.15) reprioritized engineering
   onto one concrete near-term target: **one controlled, `feedback.2.0`-gated
   autonomous MT5 **DEMO** canary order** — real decisioning, real demo
   fills, zero live-money exposure (O-006). Not live trading, not a
   profitability claim.
4. Durable broker account/position/pending-order snapshots (F-047), a
   coherent single-read account snapshot (F-052), a separate
   `BrokerStateHealth` freshness concept (F-050), read-only reconciliation
   v0, and `LiveDecisionOrchestrator` (F-048) — real MT5 market data
   attached to the actual Trading Agent / Risk Engine / Supervisor chain,
   execution left structurally unreachable.
5. Review 1.17 named the next real checkpoint (F-051) and two engineering
   gaps unblocked by F-048's own work (F-053, F-054); review 1.18, arriving
   the same day, reopened a documentation-accuracy finding (F-033, a sixth
   time) and gave an explicit instruction: build F-053/F-054 now rather
   than deferring them again, since neither needs an MT5 host.
6. F-053, F-054 and D-031 were all built and shipped the same day.
7. Review 1.19 arrived the same day and found two real gaps in that work:
   **F-054's recovery collapsed "record unreadable/corrupt" into "nothing
   recorded"** — a genuinely dangerous conflation once an execution service
   exists, since a corrupted duplicate-protection record would then look
   exactly like a legitimate fresh start. **F-053's instrument-spec
   comparison trusted whichever spec was observed first** as its baseline
   (`InstrumentSpecStore.earliest()`) — trust-on-first-use, not authority: a
   database reset (a workflow this project already has,
   `scripts/reset_soak_database.py`) could observe an already-wrong spec
   and reconciliation would call it `MATCHED` for comparing the broker to
   its own new first observation. Both reopened as `F-054` (same ID,
   hardened) and a new finding `F-055`, and both fixed the same day: F-054
   now mirrors `RiskSessionStore`'s three-state recovery shape and trips
   the kill switch on a corrupted record; F-055 replaced the
   first-observation baseline with `config.MarketConfig.expected_spec_version`
   — an explicit, human-approved, git-reviewable pin, `None` (→ `UNKNOWN`)
   until a real F-051 observation is reviewed and accepted.

**The single most valuable thing the next developer can do is F-051.**
Everything from F-047 through F-055 — broker-state capture, reconciliation
(account/position **and** instrument-spec, against a pinned baseline), the
live decision pipeline, its now-durably-and-safely-recovered idempotence,
and feature-value persistence — has only ever run against a fake/scripted
MT5 terminal (`FakeMt5`/`ScriptedMt5`) or a real PostgreSQL with synthetic
data. Not one line of this has met the real Pepperstone terminal yet. §4
below is the runbook. **The first real run is also the first opportunity to
actually pin a baseline** — see §4.4 step 9.

### 0.1 If you are a new session (human or agent) picking this up

Follow `CLAUDE.md` §1 before anything else: read `review/FEEDBACK.md`,
resolve or explicitly answer everything under `## Open`, then start. At
handover time, open findings are:

| Finding | What it needs | Blocked on |
|---|---|---|
| **F-051** | Real-terminal proof of F-047/F-052/reconciliation (incl. F-053/F-055)/F-048/F-054, one checklist session (`feedback.1.17.md` §6 / `feedback.1.19.md` §8, 18 steps) | A Windows host with the MT5 terminal and the logged-in Pepperstone demo account |
| **CI** | Confirm the workflow actually ran green on a runner and record the result | `gh` CLI or GitHub Actions web access (this environment has neither) |
| **Domain-contract review** | A human actually reads `review/domain_contracts.md` and approves or challenges it | A human reviewer |

Everything that was previously listed here as unblocked engineering
(F-053, F-054, F-055, D-031) shipped 2026-08-26 — see §0 above. **All
three remaining items are genuinely blocked on something this environment
cannot produce on its own.** If you have MT5/Windows host access, **do
F-051 first** — it is explicitly the highest-priority item and unblocks
the most (it is also the trigger for the next regular review,
`feedback.1.20.md`). If you don't, there is currently no unblocked
engineering work queued — review the trackers for anything a fresh review
may have raised since this was written.

### 0.2 Initialise the Windows host

```powershell
winget install Git.Git
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/DutchBugs/Crumblr.git
cd Crumblr
uv sync --extra mt5
```

The repository is private, so the clone asks for a username and a
fine-grained token as the password; Windows Credential Manager keeps it
afterwards. The token needs **Contents: read and write** and
**Workflows: read and write** — a push is rejected outright without the
second, because the repository carries a CI workflow.

**Git identity does not travel with a clone.** Set it locally, before
committing anything:

```powershell
git config --local user.name "Levi IJkema"
git config --local user.email "<the address on the DutchBugs account>"
git config --local credential.https://github.com.username DutchBugs
```

### 0.3 Prove the transfer before trusting it

```powershell
uv run python -c "import platform; print(platform.machine())"
uv run python -c "import MetaTrader5; print(MetaTrader5.__version__)"
uv run ruff check . ; uv run ruff format --check . ; uv run mypy ; uv run pytest
```

| Command | Expected |
|---|---|
| `platform.machine()` | `AMD64`. `ARM64` means **stop** — no MT5 wheels exist for it |
| `MetaTrader5.__version__` | a version string. An ImportError means `--extra mt5` was missed |
| ruff, mypy | clean, 125 source files (2026-08-26 count — check `status.md` §13 for the latest) |
| pytest, **no database running** | most persistence tests skip silently — a green run here proves nothing about persistence |
| pytest, **with `crumblr-pg` up** | **877 passed, 3 skipped** as of 2026-08-26. The 3 skips are two POSIX-permission-bit tests that don't apply on this platform's filesystem, and one `MetaTrader5` import-availability test — all expected, not failures |

```powershell
docker run -d --name crumblr-pg `
  -e POSTGRES_USER=crumblr -e POSTGRES_PASSWORD=crumblr -e POSTGRES_DB=crumblr `
  -p 55432:5432 postgres:17-alpine
```

Then watch the platform work against synthetic data, which needs no broker:

```powershell
uv run python scripts/run_replay.py --bars 4000
```

### 0.4 Then — the F-051 real-terminal checklist

`review/feedback.1.17.md` §6 lays out the exact sequence. In short:

1. Start `scripts/mt5_live_reader.py` against the real, already-logged-in
   terminal, pointed at `crumblr_soak` (never the shared test database —
   §9 below explains why).
2. Confirm it persists a real `InstrumentSpec`, a real `BrokerAccountSnapshot`
   (correct balance/equity/margin/currency/leverage/`RETAIL_HEDGING`), and
   that a flat account's positions/pending orders come back `COMPLETE` with
   zero child rows — not `UNKNOWN`.
3. Run `scripts/reconcile.py` against that snapshot and confirm it reports
   `MATCHED` while the account is flat.
4. Let at least one new real M5 bar close, then run
   `scripts/live_decision.py` and confirm it persists a real Signal → Risk →
   Supervisor chain, with the decision context correctly flagged as real
   shadow output, not a replay artifact — and confirm execution remains
   structurally unreachable (no `order_check`/`order_send` anywhere in the
   call graph, which is true by construction, not by configuration).
5. If the strategy produces `NO_TRADE`, **that is valid evidence.** Do not
   force a setup to make the run "interesting" — review 1.17 §6 says so
   explicitly.
6. Record what happened in `status.md` §13 with the evidence attached, and
   open a deviation for every disagreement between the terminal and the
   code — same discipline as first contact, `APP-014`.

### 0.5 Two things that will look broken and are not

- **The system refuses to trade on a fresh machine.** The safety latch at
  `.crumblr/safety_state.json` is per-host, git-ignored, and starts closed.
  A machine that has never recorded a RUNNING state is not permitted to act
  on that absence. Arming it takes an operator and a note.
- **`scripts/live_decision.py` prints a clean "skipped" reason and exits**
  if no instrument spec/bars/broker snapshot has been observed yet on that
  database. That is the orchestrator refusing to guess, not a bug — see
  `LiveDecisionOutcome.skipped_reason`.

---

## 1. Where the project actually stands

| | |
|---|---|
| **Gate** | M0 open only on CI confirmation + human domain-contract review · **M1 PASSED** · **M2 PASSED** · M3/M4/M6/M7/M8 not passed (implemented, replay-tested) · **M5 and P2 NO-GO** |
| **Capital at risk** | €0. No `order_check`/`order_send` call exists anywhere in this codebase's call graph — checked structurally by tests, not only by intent. |
| **Tests** | 877 passing, 3 explained skips, `uv run pytest` with PostgreSQL up (2026-08-26 count) |
| **Strategy** | `ict_v1` configured, feature-frozen since review F-004. `baseline_v1` retained as benchmark and used in integration tests (it triggers far more often than `ict_v1` on a short synthetic series) |
| **Real MT5 data** | Yes, since 2026-08-24: real EUR/USD ticks and M5 bars have been observed, persisted, and (as of F-048, not yet real-terminal-run) fed all the way through the Trading Agent / Risk Engine / Supervisor chain. Nothing has ever submitted an order. |
| **Reviews** | `feedback.1.0` … `1.19` all processed. `feedback.1.20.md` is the next expected review, triggered by F-051 real evidence + a real shadow decision + CI result + the domain-contract package actually being supplied — though review 1.19 also explicitly accepts a real integration defect exposed by F-051 as its own trigger. `feedback.2.0.md` is mandatory before any `order_send`, demo included, and is a separate, larger review from the numbered 1.x sequence |

The honest one-line summary: **the full decide-and-audit pipeline now runs**
**on real market data; nothing has ever submitted an order, and the new**
**broker-state/reconciliation/decision code has only met a fake terminal.**

The single most valuable next step is closing that last gap — F-051, §0.4
above and §4 below.

---

## 2. Getting it running in five minutes

### macOS or Linux — the development host

```bash
uv sync
```

Start a database. The persistence tests need a real PostgreSQL and **skip**
without one, so a green run on a machine with no database is not a green
run:

```bash
docker run -d --name crumblr-pg \
  -e POSTGRES_USER=crumblr -e POSTGRES_PASSWORD=crumblr -e POSTGRES_DB=crumblr \
  -p 55432:5432 postgres:17-alpine
```

The quality gate, which is also what CI runs:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

See the platform work against synthetic data:

```bash
uv run python scripts/run_replay.py --bars 4000
```

Determinism is part of the gate. Two runs must produce the same hash:

```bash
uv run python scripts/run_replay.py --bars 2000 2>/dev/null | md5
```

See the read-only status dashboard (reads PostgreSQL and a `LiveReader`
health JSON file; imports no MT5 module at all):

```bash
uv run python scripts/run_dashboard.py
```

### Windows — the MT5 host

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
git clone https://github.com/DutchBugs/Crumblr.git
cd Crumblr
uv sync --extra mt5
```

`--extra mt5` is what pulls in the `MetaTrader5` package. It resolves to
nothing on macOS and Linux — the marker is `sys_platform == 'win32'` — so
the same command is safe everywhere.

Check the host is the right shape before anything else. The MT5 wheels are
**x86-64 only**:

```powershell
uv run python -c "import platform; print(platform.machine())"   # expect AMD64
uv run python -c "import MetaTrader5; print(MetaTrader5.__version__)"
```

The determinism check has no `md5` on Windows:

```powershell
uv run python scripts/run_replay.py --bars 2000 2>$null |
  uv run python -c "import hashlib,sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())"
```

That hash is comparable **between runs on the same host**, not between
Windows and macOS — the report is text, and the two platforms terminate
lines differently. `.gitattributes` pins the checkout to LF so the source
itself cannot drift, but stdout is produced at run time and is not covered
by that.

Logs go to **stderr**, the report to **stdout** — deliberate, so the
determinism check (which hashes stdout) is not broken by a log line.

---

## 3. How to read the codebase

Start at `src/crumblr/domain/`. Everything else is written in the
vocabulary it defines, and the safety properties are enforced there rather
than by convention.

```text
domain/          contracts, events, money, time, hashing. No I/O, no SDKs.
  models.py      TradeIntent, RiskDecision, DecisionCapsule, BrokerAccountSnapshot,
                 BrokerPositionSnapshot, BrokerPendingOrderSnapshot, InstrumentSpec … all frozen
  enums.py       Environment, SnapshotCompleteness (COMPLETE/FAILED/UNKNOWN),
                 ReconciliationStatus (MATCHED/MISMATCHED/UNKNOWN), …
  events.py      the journal vocabulary; typed envelopes
  money.py       Decimal only — floats are rejected at the boundary
  timeutils.py   UTC only — naive datetimes are rejected

trading_agent/   features and strategies. Produces TradeIntent, nothing else.
  base.py        the Strategy protocol; both strategies plug in here
  registry.py    strategy_id → implementation; unknown ids fail loudly
  ict.py         the ICT entry model, ten enforceable conditions
  structure.py, imbalance.py, liquidity.py, sessions.py   ICT primitives
  baseline.py    the §9.2 benchmark

risk/            the deterministic gate. Nothing bypasses this.
  policies.py    the full §8.1 pre-trade checklist
  sizing.py      equity + stop distance + broker spec → volume, rounded down
  kill_switch.py durable halt; fails closed on startup
  safety_state.py  the store protocol + atomic file implementation
  session.py     the daily-loss budget, which survives a restart
  operator_controls.py  halt / cancel / flatten, deliberately decoupled

evaluator/       the supervisor. May veto or halt; may not trade.
  pretrade.py    layer 1, deterministic — the only layer built so far

mt5_gateway/     port.py (the contract), simulated.py (replay),
                 client.py (connection), readonly.py (M1 — reads only,
                 including account_with_extras(), pending_orders())
market_data/     synthetic generator; tick → bar pipeline
persistence/     PostgreSQL schema, event journal, capsule store, market
                 store, broker-state store, instrument-spec store, safety
                 and risk-session state, Alembic migrations
application/     orchestration.py — the replay §3 transaction flow, end to end
                 recording.py, bootstrap.py, reconstruction.py
                 live_reader.py     — M1: observes + persists real MT5 state
                                       (market data, broker account/position/
                                       pending-order snapshots, instrument spec)
                 broker_state.py   — composes one gateway read into a
                                       durable broker-state observation (F-047)
                 reconciliation.py — compares durable observed broker state
                                       against expected platform state (v0:
                                       flat, pre-execution)
                 live_decision.py — LiveDecisionOrchestrator (F-048): real
                                       closed M5 bar -> Trading Agent -> Risk
                                       -> Supervisor -> persist. Execution
                                       structurally unreachable
dashboard/       Dashboard v0 — read-only FastAPI app, outside the broker
                 execution boundary (F-035). Visual scope frozen (F-042..046)
api/             control API — authenticated operator functions (M8, not built)
observability/   structured logging
```

**Three rules that explain most design choices:**

1. *The agent proposes, the risk engine constrains.* `TradeIntent` has no
   field for position size, so a strategy cannot name one. A test fails if
   such a field is ever added.
2. *Absence of evidence is not evidence of safety.* Safety-critical state is
   `MATCHED`/`MISMATCHED`/`UNKNOWN` or `COMPLETE`/`FAILED`/`UNKNOWN`, never a
   boolean, and `UNKNOWN` fails closed — never silently upgraded to the safe
   value.
3. *`build.md` is the specification and is never edited to match the code.*
   Gaps go in `review/DEVIATIONS.md`.

A fourth rule arrived with F-048 and is worth knowing before touching this
code: **`LiveReader` observes and persists; `LiveDecisionOrchestrator`
decides from what was persisted; an eventual execution service (M5) is the
only thing that will ever execute.** These three responsibilities must stay
in three different classes — review 1.16 §9 was explicit about this, and it
is now load-bearing: `LiveDecisionOrchestrator` never talks to MT5 directly,
only to `MarketDataStore`/`BrokerStateStore`/`InstrumentSpecStore`.

---

## 4. MetaTrader 5 — what is proven, and what is next

M1 first contact happened 2026-08-24 (`feedback.1.12.md`, PASSED). This
section used to be "the runbook to first contact"; it is now "what first
contact and everything since actually proved, and the one thing left to
prove."

### 4.1 What is real-terminal-validated today

| Capability | Status |
|---|---|
| Connect, discover symbol/account/instrument spec | **Proven** — first contact, 2026-08-24 |
| Continuous tick/bar read into the pipeline | **Proven** — Phase A, 30 clean minutes, 2,920 real ticks + 17 real M5 bars, `GOOD` quality, zero gaps |
| Reconnect with full revalidation (symbol, account, spec, clock offset) | **Proven** — Phase B, two deliberate terminal closures, owner present, both recovered automatically |
| Broker-clock offset detection, not hard-coded | **Proven** — measured ~2:59:39-2:59:40 ahead of true UTC, stable across both phases |
| Durable broker account/position/pending-order snapshots (F-047) | **Built and unit/integration-tested against `FakeMt5`/`ScriptedMt5` only — never against the real terminal** |
| One coherent account read per snapshot (F-052) | Same — fake-terminal-tested only |
| Broker-state freshness as its own health concept (F-050) | Same — fake-terminal-tested only |
| Read-only reconciliation v0 | Same — the one database-only smoke test against `crumblr_soak` correctly returned `UNKNOWN` (no real broker-state observation existed yet), which is the fail-closed result working as intended, not evidence of a match |
| `LiveDecisionOrchestrator` (F-048) | Same — unit-tested against fakes, integration-tested against real PostgreSQL with a synthetic bar series, never against a real closed M5 bar |
| Instrument-spec durable persistence | Same |

Everything in the second column of that table is exactly what **F-051**
exists to close. Read §0.4 above for the exact sequence.

### 4.2 Credentials

They go in the environment, never in `config/` — the loader actively
rejects credential-shaped keys, so a password in YAML fails to load rather
than leaking quietly.

```powershell
Copy-Item .env.example .env
```

Fill in `CRUMBLR_MT5_LOGIN`, `CRUMBLR_MT5_PASSWORD`, `CRUMBLR_MT5_SERVER`
and optionally `CRUMBLR_MT5_TERMINAL_PATH`. `.env` is git-ignored and must
stay that way. The password never leaves the gateway process —
`Mt5Credentials.__repr__` redacts it, so it cannot reach a log line, a
traceback or a debugger frame. The raw account login is masked
(`mask_login`) at every log call site and by a structural backstop in
`observability/logging.py` that redacts any `login=` field regardless of
call site (F-031) — the account number should never appear unmasked
anywhere in this repository's logs, status entries, or review artifacts.

### 4.3 The three MT5 scripts

| Script | Purpose | Trades? |
|---|---|---|
| `scripts/mt5_probe.py` | One-shot first contact: connect, read, print | No — holds a `ReadOnlyMt5Gateway`, whose execution methods raise |
| `scripts/mt5_live_reader.py` | Continuous read: ticks/bars/broker-state, reconnect+revalidate, writes a JSON health snapshot | No — same read-only gateway |
| `scripts/live_decision.py` | Runs `LiveDecisionOrchestrator.decide_once()` in a loop: real closed bar → Signal → Risk → Supervisor → persist | **No** — prints an "EXECUTION DISABLED" banner; no `order_check`/`order_send` call exists in its call graph |

`scripts/reconcile.py` is a one-shot CLI over `application/reconciliation.py`
— compares the latest durable broker snapshot against the expected
(currently: flat, pre-execution) platform state.

**The raw `--json` output of `mt5_probe.py`/`mt5_live_reader.py` carries the
real MT5 account number** and must stay local — `var/` is git-ignored for
exactly this. Only a `--sanitized-json` copy (account number redacted,
everything else kept) may be attached to a status entry, a review document,
or pasted into chat (F-031).

Point every real-terminal run at `crumblr_soak`, **never** the shared
`crumblr` test/dev database — that database's schema gets dropped by the
integration test fixture teardown, and mixing them crashed a real soak
attempt early on (see `status.md` §13, ninth entry). `scripts/mt5_live_reader.py`
refuses to start at all unless `CRUMBLR_DATABASE_URL` is explicitly set.

```powershell
$env:CRUMBLR_DATABASE_URL = "postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr_soak"
uv run python scripts/mt5_live_reader.py --duration 1800 --json var/live_reader_health.json
```

### 4.4 What to do with what it prints

If you have never done this before, work through these in order — each is
a claim the code makes today, discovered from documentation before first
contact and confirmed or corrected by the real terminal on 2026-08-24.
Anything already settled is marked; anything still open needs a real read.

1. **`resolved_symbol`** — settled: `EURUSD`, no suffix.
2. **`margin_mode`** — settled: `RETAIL_HEDGING`, read from `account_info()`.
3. **`company`/`server`** — settled: `Pepperstone Limited`,
   `PepperstoneUK-Demo` (O-005, demo scope only — a future live account
   needs its own determination).
4. **`digits`/`point`/`tick_size`/`tick_value`/`contract_size`/`volume_min/max/step`** —
   settled from first contact; `tick_value` is deliberately excluded from
   `InstrumentSpec.spec_version`'s hash because it drifts live with the
   account/quote cross-currency rate (F-039) — not broker policy.
5. **`filling_modes`/`stops_level`** — settled: `filling_mode=2` → IOC,
   `trade_mode=4` → FULL, matching the documented mapping (F-032).
6. **`swap_long`/`swap_short`** — recorded, still not modelled in the fill
   model (`D-010`).
7. **F-047's broker account balance/equity/margin/positions/pending orders,
   `SnapshotCompleteness` per side** — **not yet observed against the real
   terminal.** This is the open item.
8. **Approve and pin the instrument-spec baseline (F-055).** After
   confirming the observed spec's fields against what steps 1-6 above
   already settled, set `expected_spec_version` on the `EUR/USD` entry in
   `config/paper.yaml`'s `markets` list to the observed `spec_version` —
   this is the explicit, git-reviewed act F-055 requires, not something
   the code does for you. Nothing is pinned yet in any shipped config.
9. **Reconciliation's `MATCHED` verdict on a real flat account, including
   the pinned instrument spec** — **not yet observed.** Open, and cannot
   read `MATCHED` for the spec dimension until step 8 above is done.
10. **A real Signal/Risk/Supervisor decision from `LiveDecisionOrchestrator`
    against a real closed M5 bar** — **not yet observed.** Open.

Record the results in `status.md` §13 with evidence attached, and open a
deviation for each disagreement between the terminal and the code — do not
edit code to match the terminal before writing down what differed. That
discipline is `APP-014` and it is the whole point of this step.

---

## 5. The review loop — do not skip this

An independent reviewing agent files versioned reviews in `review/`.

**At the start of every session, read `review/FEEDBACK.md`** and resolve
(or explicitly answer with a reason) anything still open before starting
new work. The full protocol is `CLAUDE.md` §1.

`feedback.1.0.md` through `feedback.1.17.md` are all processed.
**`feedback.1.18.md` is the next expected review** — review 1.17 §19 names
its trigger: F-051 real evidence + a real shadow Agent decision + the CI
result + `review/domain_contracts.md` actually supplied, arriving together.

```text
review/
  FEEDBACK.md        the tracker — start here
  feedback.1.0.md    … 1.17.md — the reviews themselves, never edited
  DEVIATIONS.md      every departure from build.md, keyed D-NNN
  domain_contracts.md  the M0 contract package, assembled but not yet
                        reviewed by an actual human — review 1.17 §10
  adr/               architecture decisions, keyed ADR-NNN
```

The tracker uses **two** status fields, and the distinction matters:

- **Finding** — is the reviewer's concern resolved?
- **Implementation** — what actually exists? `SHIPPED`, `DECIDED` (an ADR
  with no code), `PENDING M5`.

Do not read "most findings CLOSED" as "all work reviewed" or "all work
real-terminal-proven." As of this handover: **F-047, F-048, F-050, F-052
and reconciliation v0 are all SHIPPED/CLOSED and all still fake-terminal-only.**
F-051 exists precisely to close that gap, and until it does, treat every
claim above the M1 line as REPLAY-TESTED at best, exactly the same maturity
label the ladder in `status.md` §1 already uses.

`feedback.2.0.md` is **mandatory before the first real or demo `order_send`**
and must rely on integration evidence, not tracker claims.

---

## 6. What to build next, in order

### The one blocking checkpoint

1. **F-051** — the real-terminal checklist, §0.4/§4 above
   (`feedback.1.19.md` §8, 18 steps, now covering F-047 through F-055 and
   D-031 together). Needs a Windows/MT5 host, which is the only thing
   blocking it. As of 2026-08-26 there is no unblocked engineering work
   queued ahead of it — F-053, F-054, F-055 and D-031 (the things that
   used to be listed here) are all shipped.

### Evidence/approval tasks — not engineering

2. **CI** — confirm the workflow actually ran green on a runner (commit
   SHA, Linux job, Windows job, PostgreSQL tests, gitleaks, unexpected
   skips) and record the result. Needs `gh` CLI or GitHub Actions web
   access.
3. **Domain-contract human review** — supply `review/domain_contracts.md`
   unchanged to the reviewer; it has been assembled but never actually
   read by the reviewer (review 1.17 §10, reconfirmed 1.19 §10).

### Blocked on a human decision

4. Confirm the risk budget in `config/paper.yaml` (build.md §29 Q7-Q8) —
   placeholders, not policy (`D-013`).
5. Confirm the intraday cut-off and flatten offsets (`ADR-004` §3).
6. Production/demo HALT-reset authority (Q12).

### After F-051 succeeds

7. **Pin the instrument-spec baseline (F-055).** The first real,
   human-verified observation from step 1 becomes
   `config.MarketConfig.expected_spec_version` — see §4.4 step 8 above.
   Without this, reconciliation reads `UNKNOWN` for the instrument-spec
   dimension forever, by design.
8. **Dashboard operational data** — balance, equity, open P/L, free
   margin, open positions, pending orders, broker-state age,
   reconciliation status, live/shadow pipeline (review 1.17 §15's exact
   list, reconfirmed 1.19 §11). No further visual redesign — the layout is
   frozen.
9. **Phase 4 — execution engineering, may be prepared in parallel with
   F-051, but nothing may become order-capable until F-054's fail-closed
   fix is in — it now is (review 1.19 §5, fixed 2026-08-26).** A separate
   execution-capable MT5 adapter, `order_check`, an `ApprovedOrder`
   contract, an `ExecutionResult` contract, a durable `order_request_id`
   (build directly on `DecisionWindowState`'s identity, per D-046's own
   "watch for"), ADR-001's final execution-time risk revalidation, the
   automatic intraday flatten, post-result reconciliation. None of this is
   built yet, correctly — see the note in §3 above about keeping
   `LiveDecisionOrchestrator` and any future execution adapter as separate
   classes, the same way `LiveReader` stays separate from both.
10. **F-049** — the multi-gated execution enablement rule (environment,
    account/server, reconciliation, data health, safety state, risk
    policy, execution adapter, terminal AlgoTrading, `feedback.2.0` — all
    simultaneously true). Not built, correctly, since M5 is NO-GO.
11. `feedback.2.0.md`, then one deliberately constrained canary DEMO order
    (O-006) — a technical proof, not a profitability claim.

---

## 7. Traps a newcomer will otherwise walk into

**Do not tune the strategy against synthetic data.** The data is a seeded
random walk. Any P&L is an artefact of the seed. `ict_v1` produces ~3 setups
per 12,000 M5 bars, which looks broken and is not — see `D-023`. It is
feature-frozen by review finding F-004.

**Do not add a float anywhere near money.** The domain rejects binary
floats at its boundary and the database has no float columns; there is a
test asserting the latter. `Decimal(1.1)` is not `Decimal("1.1")`. MT5 hands
back floats, and the gateway converts them with `Decimal(repr(x))` —
`Decimal(x)` would preserve the binary error rather than remove it.

**MT5 signals failure by returning `None` or `False`**, leaving the reason
in `last_error()`. Every call goes through `Mt5Client.checked` for that
reason. The sharp edge is `positions_get`/`orders_get`: `None` means either
an empty book or a failed call, told apart only by the error code — this is
exactly why `SnapshotCompleteness` exists as `COMPLETE`/`FAILED`/`UNKNOWN`
rather than a boolean. Reading a failed call as "flat" is exactly how a
reconciliation check would pass while the terminal is down.

**`LiveReader` observes; `LiveDecisionOrchestrator` decides; a future
execution service executes.** Keep these three responsibilities in three
classes. `LiveDecisionOrchestrator` never imports `MetaTrader5` or talks to
a gateway directly — only to the durable stores `LiveReader` already wrote
to. Merging any two of these back together undoes the entire point of
building them separately (review 1.16 §9).

**Fresh price data is not fresh broker/account truth.** `BrokerStateHealth`
is deliberately its own type, separate from `ReaderStatus`/`ReaderHealth` —
a healthy tick stream says nothing about how old the last account/position
snapshot is. `is_usable(now, max_age)` is the one place that rule is
encoded; do not re-derive it ad hoc elsewhere (F-050).

**Reconciliation's `UNKNOWN` must never be upgraded to `MATCHED`.** A
missing, stale, or incomplete broker-state observation is not the same
fact as "confirmed flat," and the two must never collapse into each other
just because `UNKNOWN` is inconvenient for a caller. This is the same
fail-closed shape as `SnapshotCompleteness` and `IncidentStatus`.

**A restart may never hand back headroom.** `risk/session.py` recovers the
daily-loss and drawdown state, and every value it restores is seeded so
that recovery can only tighten. If you add a field there, ask which
direction losing it moves the limits — and if the answer is "outwards," it
has to halt instead.

**Do not give a journalled event a random id.** `event_id` is derived from
the event type, its window and its payload. The journal's append is
idempotent on that id, so a rerun after a crash converges instead of
writing history twice. A `uuid4` there would silently double a run. The
same discipline is why `LiveDecisionOrchestrator`'s eventual durable
decision-window identity (F-054) must be content-derived, not random.

**Do not order journal reads by insertion time.** Three clocks exist:
`occurred_at_utc` (market time — order by this), `recorded_at_utc` (write
time), `sequence` (tie-break). Ordering by insertion time reorders events
after a reconnect backfill, which is exactly when order matters.

**Do not let logging into stdout.** See §2.

**The FX day ends at 17:00 New York, not at midnight UTC.** Everything
about the intraday policy and the daily-loss baseline hangs off that. A
position closed at midnight UTC has already been through a rollover and
paid swap for it. See `risk/trading_window.py` and `ADR-004`.

**A bar's origin is part of the bar.** A bar the broker sent and one this
platform built from ticks are not interchangeable evidence. `MarketBar.origin`
and `pipeline_version` exist so nobody has to guess which is which months
from now.

**A halt is not a flatten.** `HALT NEW ORDERS`, `CANCEL PENDING` and
`FLATTEN POSITIONS` are three separate controls and must stay decoupled —
there are tests asserting the decoupling in both directions.

**The supervisor's default context is UNSAFE by design.** `SupervisorContext()`
defaults both safety fields to `UNKNOWN`, which halts. Tests that want to
exercise a policy rule must say explicitly that reconciliation and
incidents were checked. See `known_good_context` in
`tests/unit/test_supervisor.py`.

**Two supervisor checks are still inert** — the confidence band and the
signal frequency threshold are configured to values nothing can fall
outside. Tracked as `EV-002` and `D-015`. They need recalibrating once real
observations exist, not deleting.

**The live decision path's `AccountState.login` is a placeholder `0`, on
purpose.** `BrokerAccountSnapshot` never carries the raw MT5 login
(build.md §21 — never persist it), so `LiveDecisionOrchestrator` forces
`RiskContext.expected_login=None` and verifies account identity through
reconciliation's `account_ref` fingerprint comparison instead (D-046). If
you ever wire up a real `expected_login` check, revisit
`_account_state_from_snapshot` first — otherwise it will silently `BLOCK`
every live intent, safely but confusingly.

**M5 must not be built by relaxing `ReadOnlyMt5Gateway`.** Execution
belongs in a separate adapter satisfying the same port, so the read-only
one stays available for shadow mode, where reading without submitting is
the whole point. `D-036`.

---

## 8. What the evidence does and does not support

Ranked by how much weight it can bear.

| Evidence | Strength |
|---|---|
| Contract invariants, ICT primitives | Strong — hand-constructed cases with known answers |
| Persistence invariants | Strong — real PostgreSQL, all ten ADR-003 criteria |
| Durable halt across restart | Strong — real child processes, not two objects in one interpreter |
| A run rebuilt from the journal | Strong — same decision fingerprint, same tally, read back through a fresh connection |
| Restart-safe risk budget | Strong for the local record — broker history is not consulted (`D-032`) |
| Replay determinism | Strong — byte-identical, checked in the gate |
| Real MT5 connectivity, continuous read, reconnect | **Strong** — Phase A/B, real Pepperstone demo, 2026-08-24 |
| Risk/Supervisor decision logic | Moderate — correct in replay and now wired to real market data (F-048), but that wiring itself has only run against synthetic bars through real PostgreSQL, never a real closed M5 bar |
| Broker-state persistence, reconciliation v0 (F-047/F-050/F-052) | **Moderate at best — tested only against `FakeMt5`/`ScriptedMt5`, never the real terminal.** This is exactly F-051's gap |
| MT5 broker execution behaviour | **None.** No execution path exists at all |
| Fill model | **Weak** — intrabar ordering is an assumption; swap and commission are not modelled at all |
| Strategy performance | **None.** No number from this system is decision-grade evidence |

The fill model is the softest link in the *replay* evidence chain and is
documented as `D-010`. The freshest gap in the *real-broker* evidence chain
is F-051 — everything built since first contact (F-047 onward) is
architecturally sound and unit/integration-tested, but has never met the
thing it is meant to observe.

---

## 9. Local environment notes

- **The development host is macOS arm64.** Everything except the MT5
  gateway is host-independent; the `mt5` extra is marked
  `sys_platform == 'win32'` so `uv sync` works there.
- **The MT5 host is Windows x86-64**, available since 2026-08-24. It needs
  `uv sync --extra mt5` and the MetaTrader 5 terminal, logged into the
  Pepperstone demo account once, interactively. Windows-on-ARM does not
  work — the wheels do not exist for it.
- **Docker Desktop** does not start automatically on either host. On
  macOS, `open -a Docker`, then wait for `docker info` to succeed. Most
  persistence tests skip silently without a database, so check before
  believing a green run.
- **Two separate PostgreSQL databases matter.** `crumblr` is the shared
  dev/test database — its schema gets bootstrapped and torn down by test
  fixtures, so it must never be used for a real soak/live run.
  `crumblr_soak` is dedicated to real-MT5 runs and must be migrated
  manually (`alembic upgrade head`, or `scripts/reset_soak_database.py` to
  reset it cleanly without drifting `alembic_version` — F-041).
  `scripts/mt5_live_reader.py` refuses to start without
  `CRUMBLR_DATABASE_URL` explicitly set, specifically to prevent this
  mistake.
- **The local safety latch** lives at `.crumblr/safety_state.json` and is
  git-ignored — a property of the host, never of the repository. **The
  Windows host has its own**, and it starts closed.
- **History starts at one commit.** The owner held commits until a working
  prototype existed (F-006); `fd6a890` on 2026-08-24 is the initial import
  of everything through M2. `status.md` §13 is the detailed record of how
  the code got here since.
- **The remote is `DutchBugs/Crumblr`, private, on a personal account kept
  separate from the owner's work account.** Identity and credentials are
  pinned **repo-locally**: `user.email`, and
  `credential.https://github.com.username`. Do not move either to the
  global config, and do not switch the remote to SSH — the default key on
  the macOS host is a deploy key belonging to an unrelated work repository.
- **CI has never been confirmed to run on a runner.** The workflow is
  written, includes a PostgreSQL service and a Windows job that installs
  the `mt5` extra. Multiple pushes to `main` should have triggered it
  automatically; no session so far has had `gh` CLI or GitHub Actions web
  access to check the result. Review 1.17 §11: this is now purely an
  evidence-retrieval task for a human, not an engineering blocker.
- **`.gitattributes` pins the checkout to LF.** With two operating systems
  in play, a CRLF checkout would change the determinism hash and the
  format check without changing any code.

---

## 10. Where to look when something is confusing

| Question | Answer lives in |
|---|---|
| Why is the code like this? | `review/DEVIATIONS.md`, keyed `D-NNN` |
| Why was this decided? | `review/adr/`, and `status.md` §10 |
| What happened when? | `status.md` §13, chronological with evidence |
| What is broken or open? | `status.md` §3 (`APP-NNN`), §5 (`EV-NNN`), `review/FEEDBACK.md`'s finding register |
| What does the spec require? | `build.md` — and it is never edited to match the code |
| What did the reviewer say? | `review/feedback.1.*.md`, newest first |
| Has this been proven against the real broker, or only against a fake one? | §4/§8 above, and the Implementation column in `review/FEEDBACK.md` |
| How do I connect to MT5? | §4 above, and `scripts/mt5_probe.py` / `scripts/mt5_live_reader.py` |
| What is the very next thing to do? | §0/§6 above — F-051 if you have a Windows/MT5 host; otherwise CI evidence, the domain-contract supply, or an owner-policy decision are the only unblocked items |
