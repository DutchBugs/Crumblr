# Handover

Everything a developer needs to pick this up cold.

**Written 2026-08-18. Rewritten 2026-08-24, when a Windows host became
available** — which changes what happens next more than anything since the
project started. Section 4 is the runbook for it.

Read this first, then `CLAUDE.md` §1 for the session protocol.

---

## 1. Where the project actually stands

| | |
|---|---|
| **Gate** | M0 GO WITH CONDITIONS · M2 implemented · M1 code written, never run · M5 and P2 **NO-GO** |
| **Capital at risk** | €0. Nothing in this codebase can reach a broker's order interface. |
| **Tests** | 663 passing (`uv run pytest`, with PostgreSQL up) |
| **Strategy** | `ict_v1` configured, feature-frozen. `baseline_v1` retained as benchmark |
| **Data** | Synthetic only, but *stored*: every tick and bar a run observes is persisted. No real EUR/USD has ever been processed. |
| **Reviews** | `feedback.1.0` … `1.6` processed, F-001…F-025 all CLOSED. `feedback.2.0.md` is mandatory before any `order_send`, demo included |

The honest one-line summary: **the decision pipeline and its audit trail work;
nothing has met a broker.**

The single most valuable thing the next developer can do is change that — see
§4. Not by building more features against synthetic data, which is what the
reviewer has now warned about twice (`feedback.1.3.md` §9).

---

## 2. Getting it running in five minutes

### macOS or Linux — the development host

```bash
uv sync
```

Start a database. The persistence tests need a real PostgreSQL and **skip**
without one, so a green run on a machine with no database is not a green run:

```bash
docker run -d --name crumblr-pg \
  -e POSTGRES_USER=crumblr -e POSTGRES_PASSWORD=crumblr -e POSTGRES_DB=crumblr \
  -p 55432:5432 postgres:17-alpine
```

The quality gate, which is also what CI runs:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

See the platform work:

```bash
uv run python scripts/run_replay.py --bars 4000
```

Determinism is part of the gate. Two runs must produce the same hash:

```bash
uv run python scripts/run_replay.py --bars 2000 2>/dev/null | md5
```

### Windows — the MT5 host

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
uv sync --extra mt5
```

`--extra mt5` is what pulls in the `MetaTrader5` package. It resolves to
nothing on macOS and Linux — the marker is `sys_platform == 'win32'` — so the
same command is safe everywhere.

Check the host is the right shape before anything else. The MT5 wheels are
**x86-64 only**, so a Windows-on-ARM machine cannot run the gateway at all:

```powershell
uv run python -c "import platform; print(platform.machine())"   # expect AMD64
uv run python -c "import MetaTrader5; print(MetaTrader5.__version__)"
```

The determinism check has no `md5` on Windows:

```powershell
uv run python scripts/run_replay.py --bars 2000 2>$null |
  uv run python -c "import hashlib,sys; print(hashlib.md5(sys.stdin.buffer.read()).hexdigest())"
```

That hash is comparable **between runs on the same host**, not between Windows
and macOS: the report is text, and the two platforms terminate lines
differently. `.gitattributes` pins the checkout to LF so the source itself
cannot drift, but stdout is produced by Python at run time and is not covered
by that.

Logs go to **stderr**, the report to **stdout**. That separation is deliberate —
the determinism check hashes stdout, and log lines in it would break the check
non-deterministically.

---

## 3. How to read the codebase

Start at `src/crumblr/domain/`. Everything else is written in the vocabulary it
defines, and the safety properties are enforced there rather than by convention.

```text
domain/          contracts, events, money, time, hashing. No I/O, no SDKs.
  models.py      TradeIntent, RiskDecision, DecisionCapsule … all frozen
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
mt5_gateway/     port.py (the contract), simulated.py (replay),
                 client.py (connection), readonly.py (M1 — reads only)
market_data/     synthetic generator; tick → bar pipeline
persistence/     PostgreSQL journal, capsules, market store, safety and
                 risk-session state, Alembic migrations
application/     orchestration.py — the §3 transaction flow, end to end
                 recording.py, bootstrap.py, reconstruction.py
observability/   structured logging
```

**Three rules that explain most design choices:**

1. *The agent proposes, the risk engine constrains.* `TradeIntent` has no field
   for position size, so a strategy cannot name one. A test fails if such a
   field is ever added.
2. *Absence of evidence is not evidence of safety.* Safety-critical state is
   `MATCHED`/`MISMATCHED`/`UNKNOWN`, never a boolean, and `UNKNOWN` fails closed.
3. *`build.md` is the specification and is never edited to match the code.*
   Gaps go in `review/DEVIATIONS.md`.

---

## 4. First contact with MetaTrader 5 — the runbook

This is the next real step, and it is **discovery, not integration**. Every
broker fact the code holds was written from documentation. Where the terminal
disagrees, the terminal is right and the code is a deviation to record —
`APP-014`, `D-035`.

### 4.1 What has to exist first

| | Who | State |
|---|---|---|
| Windows x86-64 host with the MT5 terminal installed | owner | **available since 2026-08-24** |
| Pepperstone MT5 demo account, logged in once in the terminal | owner | not created |
| The entity question resolved — EU or UK (`APP-013`, `D-034`) | owner | open |

The account is the remaining blocker. The terminal must have logged in
interactively at least once: `MetaTrader5.login()` attaches to a terminal that
already knows the server, it does not enrol a new account.

### 4.2 Credentials

They go in the environment, never in `config/` — the loader actively rejects
credential-shaped keys, so a password in YAML fails to load rather than leaking
quietly.

```powershell
Copy-Item .env.example .env
```

Fill in `CRUMBLR_MT5_LOGIN`, `CRUMBLR_MT5_PASSWORD`, `CRUMBLR_MT5_SERVER` and
optionally `CRUMBLR_MT5_TERMINAL_PATH`. `.env` is git-ignored and must stay
that way. In production these come from the Windows Credential Manager or a
secret manager; the file is a workstation convenience.

The password never leaves the gateway process. `Mt5Credentials.__repr__`
redacts it, so it cannot reach a log line, a traceback or a debugger frame.

### 4.3 Run the probe

```powershell
uv run python scripts/mt5_probe.py --json first-contact.json
```

`scripts/mt5_probe.py` connects, reads and prints. It cannot trade: it holds a
`ReadOnlyMt5Gateway`, whose execution methods raise (`D-036`), and there is no
order interface reachable from the file. A test asserts it touches no mutating
call.

Its exit code is `1` when the account guard disagrees with `config/paper.yaml`.
That is the expected first result while `APP-013` is open, and the report still
prints — a guard mismatch is a finding, not a crash.

### 4.4 What to do with what it prints

Work through these in order. Each one is a claim in the code today.

1. **`resolved_symbol`.** Pepperstone suffixes its symbols. If it comes back
   `EURUSD.a` or similar, every place that assumed `EURUSD` was wrong — which
   is why the gateway discovers the name instead of holding one.
2. **`margin_mode`** — owner question Q2, hedging or netting. Deliberately
   never guessed. The one-exposure rule is written to hold under both, but
   reconciliation and the position model need the real answer.
3. **`company` and `server`** settle `APP-013`. Then reconcile
   `config/paper.yaml`'s `expected_currency` and `expected_leverage`.
4. **`digits`, `point`, `tick_size`, `tick_value`, `contract_size`,
   `volume_min/max/step`.** Sizing rounds down against these. A wrong tick
   value misprices every position the risk engine builds, silently.
5. **`filling_modes` and `stops_level`.** The stops level is the broker's floor
   for a stop distance; the platform's own floor must not sit under it. The
   filling mask is where the code is knowingly imprecise — see `D-037`.
6. **`swap_long` / `swap_short`.** The fill model has no swap and no commission
   (`D-010`). These are the first real numbers towards fixing that.

Record the results in `status.md` §13 with the JSON attached, open a deviation
for each disagreement, and only then change code.

### 4.5 What M1 still needs after the probe

The probe proves a connection and reads a snapshot. M1 as `build.md` defines it
also wants continuous read: bar and tick retrieval through
`copy_rates_from_pos` / `copy_ticks_from` into the existing pipeline, and
reconnect behaviour that is observed rather than assumed. Both are declared on
the `Mt5Module` protocol and neither is implemented in `readonly.py` yet.

That is the natural next commit after first contact, and it should be written
against what the terminal actually did, not ahead of it.

---

## 5. The review loop — do not skip this

An independent reviewing agent files versioned reviews in `review/`.

**At the start of every session, read `review/FEEDBACK.md`** and resolve
anything still open before starting new work. The full protocol is `CLAUDE.md`
§1. Nothing is open at the time of writing.

```text
review/
  FEEDBACK.md        the tracker — start here
  feedback.1.0.md    … 1.6.md — the reviews themselves, never edited
  DEVIATIONS.md      every departure from build.md, keyed D-NNN
  adr/               architecture decisions, keyed ADR-NNN
```

The tracker uses **two** status fields, and the distinction matters:

- **Finding** — is the reviewer's concern resolved?
- **Implementation** — what actually exists? `SHIPPED`, `DECIDED` (an ADR with
  no code), `PENDING M5`.

Every finding reads CLOSED. **Three are `DECIDED` with no code** — F-007 and
F-011 (execution-time risk revalidation, ADR-001) and the flatten half of
F-025. Do not read those as done.

`feedback.2.0.md` is **mandatory before the first real or demo `order_send`**
and must rely on integration evidence, not tracker claims. The probe in §4 is
read-only precisely so it does not require that review first.

---

## 6. What to build next, in order

### Now unblocked by the Windows host

1. **MT5 first contact.** §4. Blocked only on the demo account existing.
2. **Continuous read at M1** — bars and ticks from the terminal into the
   pipeline, plus observed reconnect behaviour. §4.5.
3. **Reconciliation against real positions.** The contracts and the `UNKNOWN`
   states exist; nothing has ever compared them with a broker.

### Immediately available — nothing blocks these

4. **Store feature values, not only their hash.** A capsule carries the feature
   set version and a hash; the values exist only in the process that computed
   them. The hash proves a later recomputation matches, which is not the same
   as being able to see what the strategy saw. `D-031`.
5. **Post-trade evaluation (M7).** `EvaluationCompleted` exists as a contract
   with no producer. The simulated broker already records slippage and MAE/MFE
   per trade; nothing consumes them.
6. **A backup schedule.** The restore is proven; the routine that would produce
   something to restore is not written. `D-029`.
7. **The M0 loose ends** review 1.6 §5 names: a recorded CI exception (or CI on
   a runner), and human approval of the domain contracts.

### Blocked on a human decision

8. **Create the Pepperstone demo account** (blocks item 1).
9. **The Pepperstone entity** — `APP-013`, `D-034`.
10. **Confirm the risk budget** in `config/paper.yaml` (§29 Q7-Q8). The current
    values are placeholders nobody has agreed to, and sitting in YAML is not
    approval — `D-013`.
11. **The intraday offsets** — `config/paper.yaml` carries provisional values
    of 60 and 15 minutes that nobody has agreed. `ADR-004` §3.

### Then, and only then

12. ADR-001 execution-time revalidation → M5 paper execution, behind
    `feedback.2.0.md`.

---

## 7. Traps a newcomer will otherwise walk into

**Do not tune the strategy against synthetic data.** The data is a seeded random
walk. Any P&L is an artefact of the seed. `ict_v1` produces ~3 setups per 12,000
M5 bars, which looks broken and is not — see `D-023`. It is feature-frozen by
review finding F-004.

**Do not add a float anywhere near money.** The domain rejects binary floats at
its boundary and the database has no float columns; there is a test asserting
the latter. `Decimal(1.1)` is not `Decimal("1.1")`. MT5 hands back floats, and
the gateway converts them with `Decimal(repr(x))` — `Decimal(x)` would preserve
the binary error rather than remove it.

**MT5 signals failure by returning `None` or `False`**, leaving the reason in
`last_error()`. Every call goes through `Mt5Client.checked` for that reason. The
sharp edge is `positions_get`: `None` means either an empty book or a failed
call, and they are told apart only by the error code. Reading a failed call as
"flat" is exactly how a reconciliation check passes while the terminal is down.

**A restart may never hand back headroom.** `risk/session.py` recovers the
daily-loss and drawdown state, and every value it restores is seeded so that
recovery can only tighten. If you add a field there, ask which direction losing
it moves the limits — and if the answer is "outwards", it has to halt instead.

**Do not give a journalled event a random id.** `event_id` is derived from the
event type, its window and its payload. The journal's append is idempotent on
that id, so a rerun after a crash converges instead of writing history twice.
A `uuid4` there would silently double a run.

**Do not order journal reads by insertion time.** Three clocks exist:
`occurred_at_utc` (market time — order by this), `recorded_at_utc` (write time),
`sequence` (tie-break). Ordering by insertion time reorders events after a
reconnect backfill, which is exactly when order matters.

**Do not let logging into stdout.** See §2.

**The FX day ends at 17:00 New York, not at midnight UTC.** Everything about
the intraday policy and the daily-loss baseline hangs off that. A position
closed at midnight UTC has already been through a rollover and paid swap for
it. See `risk/trading_window.py` and `ADR-004`.

**A bar's origin is part of the bar.** A bar the broker sent and one this
platform built from ticks are not interchangeable evidence. `MarketBar.origin`
and `pipeline_version` exist so that nobody has to guess which is which six
months from now.

**A halt is not a flatten.** `HALT NEW ORDERS`, `CANCEL PENDING` and `FLATTEN
POSITIONS` are three separate controls and must stay decoupled — there are tests
asserting the decoupling in both directions.

**The supervisor's default context is UNSAFE by design.** `SupervisorContext()`
defaults both safety fields to `UNKNOWN`, which halts. Tests that want to
exercise a policy rule must say explicitly that reconciliation and incidents
were checked. See `known_good_context` in `tests/unit/test_supervisor.py`.

**Two supervisor checks are still inert** — the confidence band and the signal
frequency threshold are configured to values nothing can fall outside. Tracked
as `EV-002` and `D-015`. They need recalibrating once real observations exist,
not deleting.

**M5 must not be built by relaxing `ReadOnlyMt5Gateway`.** Execution belongs in
a separate adapter satisfying the same port, so the read-only one stays
available for shadow mode, where reading without submitting is the whole point.
`D-036`.

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
| Risk engine behaviour | Moderate — correct in replay, never met a broker |
| MT5 adapter logic | Moderate — tested against a fake written from documentation, not observation (`D-035`) |
| MT5 broker behaviour | **None.** Nothing has run against a terminal |
| Fill model | **Weak** — intrabar ordering is an assumption; swap and commission are not modelled at all |
| Strategy performance | **None.** No number from this system is decision-grade evidence |

The fill model is the softest link and is documented as `D-010`. Until
commission, swap and credible intrabar assumptions exist, P&L is engineering
output rather than trading evidence.

---

## 9. Local environment notes

- **The development host is macOS arm64.** Everything except the MT5 gateway is
  host-independent; the `mt5` extra is marked `sys_platform == 'win32'` so
  `uv sync` works there.
- **The MT5 host is Windows x86-64**, available since 2026-08-24. It needs
  `uv sync --extra mt5` and the MetaTrader 5 terminal. Windows-on-ARM does not
  work — the wheels do not exist for it.
- **Docker Desktop** does not start automatically on either host. On macOS,
  `open -a Docker`, then wait for `docker info` to succeed. 73 tests skip
  silently without a database, so check before believing a green run.
- **The local safety latch** lives at `.crumblr/safety_state.json` and is
  git-ignored. It records whether *this machine* is halted, which is a property
  of the host and never of the repository. **The Windows host has its own**, and
  it starts closed — a fresh machine refuses to trade until an operator arms it,
  which is the intended behaviour and not a fault.
- **Nothing is committed.** The repository is initialised and everything is
  staged, at the owner's request — they are holding commits until a working
  prototype satisfies them. Local Git is allowed, a remote is deferred (F-006).
  Moving the code to the Windows machine therefore means copying a working
  tree, or finally creating that remote.
- **CI has never run on a runner.** Every quality figure in `status.md` was
  produced on one developer machine. The workflow is written, includes a
  PostgreSQL service and defines a Windows job that installs the `mt5` extra;
  it needs a remote to execute against. `D-019`.
- **`.gitattributes` pins the checkout to LF.** With two operating systems in
  play, a CRLF checkout would change the determinism hash and the format check
  without changing any code.

---

## 10. Where to look when something is confusing

| Question | Answer lives in |
|---|---|
| Why is the code like this? | `review/DEVIATIONS.md`, keyed `D-NNN` |
| Why was this decided? | `review/adr/`, and `status.md` §10 |
| What happened when? | `status.md` §13, chronological with evidence |
| What is broken or open? | `status.md` §3 (`APP-NNN`), §5 (`EV-NNN`) |
| What does the spec require? | `build.md` — and it is never edited to match the code |
| What did the reviewer say? | `review/feedback.1.*.md` |
| How do I connect to MT5? | §4 above, and `scripts/mt5_probe.py` |
