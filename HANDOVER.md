# Handover

Everything a developer (or reviewing agent) needs to pick this up cold.

**Written 2026-08-18. Rewritten 2026-08-24, 2026-08-26** (see git history
for those versions' own detail — first MT5 contact, M1/M2 passing, F-047
through F-055 and F-051's own progress). **Rewritten again 2026-09-04**,
after Phase 4 formally passed, all of Phase B shipped, and Phase C
(AG-012, a real cross-process risk-authority lock) closed on both the
Core and the external-agent track. This version reflects where the
project actually stands today, not a point-in-time snapshot from a
specific incident the way earlier versions did — read `status.md`'s own
compact "Current state" header if you suspect this document has drifted
since; it is the one place that always wins on disagreement.

**Start at §0.** Then `CLAUDE.md` §1 for the mandatory session-start
protocol — it is not optional, and skipping it is how work gets
compounded on top of an unaddressed reviewer finding.

---

## 0. Start here — the exact point this was handed over

**Handed over 2026-09-04.** In order, the state of the world:

1. **M0** is open only on two things: a hosted CI run actually confirmed
   green on a real runner (the last known blocker, `UV_FROZEN`/`uv sync`
   conflicts and a `ruff format`/reviewer-Markdown collision, were fixed
   and owner-reported green — run #106, 1341 collected, 1339 passed, 2
   known-unrelated failures — but this environment has no `gh`/Actions
   access to independently re-pull that result), and a human actually
   reading `review/domain_contracts.md` (reviewer-approved at the
   technical level already, review 1.24 §7).
2. **M1 PASSED, M2 PASSED.** Both real-terminal-validated; nothing new
   here since the 2026-08-26 version of this document.
3. **Phase 4 (non-sending execution engineering) formally PASSED**
   (review 1.24, 2026-08-27) — the full intent-time-Risk → deterministic
   Policy → `DecisionCapsule` → `ExecutionOrchestrator` → fresh broker
   state → reconciliation → FINAL Risk → `ApprovedOrder` → `order_check`
   chain is real, tested, and structurally stops before `order_send`.
4. **The owner adopted an external-agent product direction** (review
   1.25, 2026-08-27): Crumblr stays the trusted control plane; two
   coordinated development tracks now exist — **Dev 1 (Core/Execution)**
   and **Dev 2 (External Agent Integration)** — each with their own
   instructions document in `review/`, their own worktree, and their own
   local finding register (`F-###` project-wide for Dev 1,
   `AG-###` in `review/AGENT_FEEDBACK.md` for Dev 2). §9 below has the
   workspace-isolation setup.
5. **Phase 5 (Convergence, Observability & DEMO Readiness)** opened
   2026-09-01 (review 1.26, an owner-requested exception to the "no more
   routine numbered reviews" cadence review 1.25 set). Three lanes: Lane
   A (Dev 1, core submission safety), Lane B (Dev 2, external agent
   integration), Lane C (read-only observability).
6. **Phase B — Dev 1's core submission-safety critical path — is
   complete**, all shipped 2026-08-28 through 2026-09-04: `SubmissionGate`
   wired for real (F-049), durable execution-activation, `SUBMISSION_STARTED`
   emitted at the correct pre-side-effect point, `order_send`
   idempotence (a deterministic MT5 magic-number derivation), ambiguous-
   outcome recovery, automatic weekly flatten **with a real per-ticket
   close** (`review/adr/ADR-020-real-flatten-close.md`), post-fill
   reconciliation, broker-side stop-loss verification, and an owner
   account-reference pin plus a one-shot DEMO canary permit. Every real
   mutating call site exists and is tested; every activation flag
   defaults closed in every shipped config, and the one real mutating
   MT5 adapter (`DemoOrderSendMt5Gateway`) is constructed by nothing in
   `src/`/`scripts/` today — the same "real but genuinely unreachable"
   discipline every slice used.
7. **F-051 is fully closed, both parts** (2026-08-26 / 2026-09-01):
   discovery through reconciliation `MATCHED` was proven for real against
   the live Pepperstone demo terminal, and a real Trader/Risk/Supervisor
   decision from a real closed M5 bar was proven too (`baseline_v1`,
   per review 1.25 §8's explicit instruction not to wait for `ict_v1`'s
   higher bar-count threshold).
8. **Phase C (AG-012, a single serialized risk authority) closed on
   both sides, 2026-09-04** — the last item the owner's coordination
   order named before entries could ever be wired into a real submission
   chain. `review/adr/ADR-021-single-risk-authority-lock.md` is the full
   design: a real Postgres advisory lock (`pg_advisory_xact_lock`,
   reusing the exact primitive `agent_gateway`'s own `lock_assignment()`
   already proved out) now serializes `risk_session_states` across
   `LiveDecisionOrchestrator` (internal strategies),
   `agent_gateway/decision_path.py` (external-agent proposals) and
   `ExecutionOrchestrator`'s FINAL Risk read. Found and closed a second,
   independent bug in the same change: `LiveDecisionOrchestrator` used to
   only persist its risk-session checkpoint on a cycle that reached a
   full risk-`PASS` decision, so a run of `NO_TRADE` decisions never
   updated the durable record at all — now persists every cycle. **One
   known, deliberately-not-fixed gap remains**: `application/paper_lite.py`
   (Dev-3-owned, a separate PAPER_LITE track) reads and writes the same
   table completely unlocked — tracked as **AG-023**, open, not a live
   safety gap (PAPER_LITE never reaches `order_send` either), but the
   ADR's own stated guarantee is narrower than its title until whoever
   owns `paper_lite.py` picks this up.
9. **Dev 2's side of AG-012 landed on `agent/contracts`, not yet merged
   to `main`.** `origin/agent/contracts` is currently *ahead* of `main`
   with this fix (plus everything else that track has built —
   `agent_gateway/`, the Static Agent bridge work, PAPER_LITE's own
   convergence). Merging it is an explicit owner/Dev-2 decision, not
   automatic, and has not been made as of this handover.
10. **PAPER_LITE** (a separate, Dev-3-owned, self-contained track —
    `application/paper_lite*.py`, its own worklog
    `review/PAPER_LITE_DEV3_WORKLOG.md`) merged to `main` 2026-09-03,
    zero file overlap with Dev 1's own slices.

**The single most valuable thing the next session can do** depends on
who you are: if you are continuing **Dev 1's** critical path, there is
currently **nothing queued** — Phase B and Phase C are both done on this
side, and the next real gate is the `feedback.2.0` readiness bundle
(§6 below) or a fresh owner work order. If you are continuing **Dev 2's**
track, the immediate items are landing `agent/contracts` on `main` (an
owner decision to request, not to make unilaterally) and whatever Phase
5 Lane B/Static Agent items `review/AGENT_STATUS.md` currently names. If
you are the **reviewer**, `feedback.2.0.md` is the next routine target,
and review 1.25 §10 lists exactly what the readiness bundle must contain
before it can be requested.

### 0.1 If you are a new session (human or agent) picking this up

Follow `CLAUDE.md` §1 before anything else: read `review/FEEDBACK.md`,
resolve or explicitly answer everything genuinely open, then start. As of
this handover, every `F-###` finding is `CLOSED` — the remaining open
items are all evidence/owner-decision gates, not engineering defects:

| Item | What it needs | Blocked on |
|---|---|---|
| **Hosted CI confirmation** | Someone with `gh`/Actions access to look at the current `main`'s Actions tab and confirm all jobs green | A human, or a future session with that access — this environment has neither |
| **Domain-contract human countersign** | Optional — only if M0's "reviewed by a human" wording is read literally; already technically approved (review 1.24 §7) | A human, if the literal reading is wanted |
| **`agent/contracts` → `main` merge** | An explicit decision that the accumulated agent-track work is ready to converge | Owner, or a joint Dev-1/Dev-2 decision |
| **AG-023 (PAPER_LITE's unlocked risk-session access)** | An architectural call on whether/how `paper_lite.py` should acquire `RiskLedgerLock` | Whoever owns `application/paper_lite.py` next |
| **Owner activation decisions** (AlgoTrading enable timing, first-canary risk cap, exact permit window) | Explicit owner acts, named in review 1.24 §12/1.25 §10's readiness-bundle checklist | Owner |

**This machine is the Windows/MT5 host** — AMD64, `Pepperstone MetaTrader 5`
installed and running, confirmed since 2026-08-26. Do not assume
otherwise; check `platform.machine()` and for a running `terminal64.exe`
first if in doubt.

### 0.2 Which workspace am I in?

Since the DEV1/DEV2 track split (2026-08-28), **the top-level checkout is
not necessarily where active work happens.** Each track works in its own
dedicated Git worktree:

```powershell
git worktree list
```

typically shows something like:

```text
<repo-root>                               [main]
<repo-root>/.claude/worktrees/core        [core/<topic>]   <- Dev 1
<repo-root>/.claude/worktrees/agent-dev2  [agent/contracts] <- Dev 2
```

**If the top-level checkout's own `main` looks stale relative to
`origin/main` — check before trusting it.** Local branch refs are shared
across worktrees but are not automatically fast-forwarded when a topic
branch is pushed straight to the remote's `main`; a top-level checkout
that was set up once and never revisited can sit many commits behind
while the real work, visible via `git log origin/main`, has moved on
entirely through the dedicated worktrees. Always `git fetch origin` and
compare against `origin/main`, not local `main`, when orienting.

Read `review/CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS.md` (Core) or
`review/CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md` (external
agent) for the full workspace-isolation setup: separate branch prefix
(`core/*`/`agent/*`), separate Python environment, separate integration-
test database (`crumblr_test_dev1`/`crumblr_test_dev2` — never the shared
`crumblr` or `crumblr_soak` databases for routine test runs).

### 0.3 Initialise the Windows host

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

### 0.4 Prove the transfer before trusting it

```powershell
uv run python -c "import platform; print(platform.machine())"
uv run python -c "import MetaTrader5; print(MetaTrader5.__version__)"
uv run ruff check . ; uv run ruff format --check . ; uv run mypy ; uv run pytest
```

| Command | Expected |
|---|---|
| `platform.machine()` | `AMD64`. `ARM64` means **stop** — no MT5 wheels exist for it |
| `MetaTrader5.__version__` | a version string. An ImportError means `--extra mt5` was missed |
| ruff, mypy | clean — check `status.md`'s compact header for the current source-file count |
| pytest, **no database running** | most persistence tests skip silently — a green run here proves nothing about persistence |
| pytest, **with `crumblr-pg` up**, `CRUMBLR_DATABASE_URL` pointed at your own isolated test database | check `status.md`'s compact header for the current exact pass count (well over 1400 as of this handover) — a materially lower number means something regressed or your database is stale/partially migrated |

```powershell
docker run -d --name crumblr-pg `
  -e POSTGRES_USER=crumblr -e POSTGRES_PASSWORD=crumblr -e POSTGRES_DB=crumblr `
  -p 55432:5432 postgres:17-alpine
```

Then watch the platform work against synthetic data, which needs no broker:

```powershell
uv run python scripts/run_replay.py --bars 4000
```

### 0.5 Two things that will look broken and are not

- **The system refuses to trade on a fresh machine.** The safety latch at
  `.crumblr/safety_state.json` is per-host, git-ignored, and starts closed.
  A machine that has never recorded a RUNNING state is not permitted to act
  on that absence. Arming it takes an operator and a note.
- **`scripts/live_decision.py` prints a clean "skipped" reason and exits**
  if no instrument spec/bars/broker snapshot has been observed yet on that
  database. That is the orchestrator refusing to guess, not a bug — see
  `LiveDecisionOutcome.skipped_reason`.
- **`order_send` will always refuse, however permissive the config.**
  This is by construction, not configuration: `OrderCheckMt5Gateway
  .order_send`/`.close_all_positions` are unconditional raises, and the
  one real adapter that can genuinely submit
  (`DemoOrderSendMt5Gateway`) is never constructed by any shipped script.
  A test (`TestNotWiredIntoTheOrchestrator`) asserts this structurally.

---

## 1. Where the project actually stands

| | |
|---|---|
| **Gate** | M0 open only on hosted-CI confirmation + optional human countersign · **M1 PASSED** · **M2 PASSED** · **Phase 4 formally PASSED** · M3/M6/M7/M8 not passed (implemented/partial, replay-tested) · **M5/`order_send` NO-GO until `feedback.2.0` GO** |
| **Capital at risk** | €0. `order_send` is structurally unreachable from every real construction path in `src/`/`scripts/` — checked by tests, not only by intent |
| **Tests** | Well over 1400 passing (real PostgreSQL, isolated per-workspace database) — check `status.md`'s compact header for today's exact count, it changes every session with meaningful progress |
| **Strategy** | `ict_v1` configured, feature-frozen since F-004. `baseline_v1` retained as benchmark and is the strategy F-051 part 2's real-terminal proof actually used. An external-agent path exists alongside both, strategy-neutral by explicit architectural decision (F-066) |
| **Real MT5 data** | Yes, since 2026-08-24. F-051 (both parts) proved the whole chain — discovery, reconciliation `MATCHED`, and a real Trader/Risk/Supervisor decision — against the real terminal. Nothing has ever submitted an order |
| **Execution chain** | Real and tested end to end through `order_check` and every non-sending gate (Phase 4 PASSED, Phase B complete); `order_send` itself remains structurally unreachable |
| **Reviews** | `feedback.1.0` … `1.28.md` all processed. **Review cadence has changed** (review 1.25 §9): no more routine numbered reviews for ordinary progress — the next *routine* target is `feedback.2.0.md` directly. A numbered review only returns for a material safety defect, a proposed Phase-4-invariant change, an authority-boundary dispute, or the complete `feedback.2.0` bundle being ready |

The honest one-line summary: **the full decide-and-audit-and-preflight
pipeline now runs on real market data and has met the real broker through
`order_check`; the real submission chain (close/flatten included) is
built, tested and structurally inert; nothing has ever submitted an
order or genuinely closed one.**

The single most valuable next step depends on role — see §0 above and §6
below.

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
  models.py      TradeIntent, RiskDecision, DecisionCapsule, ApprovedOrder,
                 ExecutionResult, FlattenInstruction/FlattenPlan,
                 BrokerAccountSnapshot, BrokerPositionSnapshot,
                 BrokerPendingOrderSnapshot, InstrumentSpec … all frozen
  enums.py       Environment, ReasonCode, ExecutionEventType, FlattenEventType,
                 SnapshotCompleteness (COMPLETE/FAILED/UNKNOWN),
                 ReconciliationStatus (MATCHED/MISMATCHED/UNKNOWN), …
  events.py      the journal vocabulary; typed envelopes
  hashing.py     content-derived identity, incl. mt5_magic_number()
                 (order_send idempotence, ADR-007)
  money.py       Decimal only — floats are rejected at the boundary
  timeutils.py   UTC only — naive datetimes are rejected

trading_agent/   features and strategies. Produces TradeIntent, nothing else.
  base.py        the Strategy protocol; both strategies plug in here
  registry.py    strategy_id → implementation; unknown ids fail loudly
  ict.py         the ICT entry model, ten enforceable conditions
  structure.py, imbalance.py, liquidity.py, sessions.py   ICT primitives
  baseline.py    the §9.2 benchmark; also F-051 part 2's real-terminal proof

risk/            the deterministic gate. Nothing bypasses this.
  policies.py    the full §8.1 pre-trade checklist, incl. execution-time
                 revalidation (ADR-001)
  sizing.py      equity + stop distance + broker spec → volume, rounded down
  portfolio_risk.py  exact open-risk assessment (owner risk policy v1, D1.4)
  kill_switch.py durable halt; fails closed on startup
  safety_state.py  the store protocol + atomic file implementation
  session.py     the daily-loss budget (survives a restart) + RiskLedgerLock
                 (ADR-021 — a real cross-process serialization primitive)
  submission_gate.py   the real order_send multi-gate (ADR-006, F-049)
  flatten_gate.py      the real automatic-flatten multi-gate (ADR-009)
  execution_preflight_gate.py, execution_eligibility.py
  operator_controls.py  halt / cancel / flatten, deliberately decoupled

evaluator/       the supervisor. May veto or halt; may not trade.
  pretrade.py    layer 1, deterministic — the only internal layer built so far

agent_gateway/   the external-agent trust boundary (ADR-005, Dev-2-owned).
                 Identity/credential auth, assignment authorization,
                 context binding, idempotent proposal claiming,
                 TradeProposal -> platform TradeIntent mapping,
                 decision_path.py's shared Risk/Policy wiring

mt5_gateway/     port.py (the BrokerPort contract), simulated.py (replay),
                 client.py (connection), readonly.py (M1 — reads only,
                 incl. account_with_extras(), pending_orders()),
                 execution.py (OrderCheckMt5Gateway — real order_check,
                 order_send/close always unconditional raises),
                 demo_execution.py (DemoOrderSendMt5Gateway — the one real,
                 separate, genuinely unwired mutating adapter: order_send,
                 close_position/close_all_positions, Phase B)
market_data/     synthetic generator; tick → bar pipeline
persistence/     PostgreSQL schema, event journal, capsule store, market
                 store, broker-state store, instrument-spec store,
                 execution.py (requests/events), flatten.py (requests/events),
                 risk_session.py (session store + PostgresRiskLedgerLock,
                 ADR-021), agent_gateway.py, safety and risk-session state,
                 Alembic migrations
application/     orchestration.py — the replay §3 transaction flow, end to end
                 recording.py, bootstrap.py (DurableRuntime — the real
                 composition root for a durable run), reconstruction.py
                 live_reader.py     — M1: observes + persists real MT5 state
                                       (market data, broker account/position/
                                       pending-order snapshots, instrument spec)
                 broker_state.py   — composes one gateway read into a
                                       durable broker-state observation (F-047)
                 reconciliation.py — compares durable observed broker state
                                       against expected platform state
                 live_decision.py — LiveDecisionOrchestrator (F-048): real
                                       closed M5 bar -> Trading Agent -> Risk
                                       -> Supervisor -> persist. This class
                                       itself never reaches order_send
                 execution.py      — ExecutionOrchestrator: the real,
                                       non-sending Phase-4 execution chain,
                                       plus the real automatic-flatten
                                       machinery (Phase B)
                 execution_outcome.py, expected_state.py, flatten_plan.py
                 paper_lite.py     — a separate, Dev-3-owned, self-contained
                                       track; see its own worklog
dashboard/       Dashboard v0 — read-only FastAPI app, outside the broker
                 execution boundary (F-035). Visual scope frozen (F-042..046)
api/             control API — authenticated operator functions (M8, not built)
observability/   structured logging
```

**Three rules that explain most design choices:**

1. *The agent proposes, the risk engine constrains.* `TradeIntent` has no
   field for position size, so a strategy — internal or external — cannot
   name one. A test fails if such a field is ever added.
2. *Absence of evidence is not evidence of safety.* Safety-critical state is
   `MATCHED`/`MISMATCHED`/`UNKNOWN` or `COMPLETE`/`FAILED`/`UNKNOWN`, never a
   boolean, and `UNKNOWN` fails closed — never silently upgraded to the safe
   value. An external Supervisor's timeout/error/malformed response reads
   the same way — `UNKNOWN`, never an implicit approval.
3. *`build.md` is the specification and is never edited to match the code.*
   Gaps go in `review/DEVIATIONS.md`.

A fourth rule arrived with F-048 and is still load-bearing: **`LiveReader`
observes and persists; `LiveDecisionOrchestrator` decides from what was
persisted; `ExecutionOrchestrator` is the only thing with a real (if
still unreachable) path to a broker mutation.** These responsibilities
stay in separate classes — `LiveDecisionOrchestrator` never talks to MT5
directly, only to `MarketDataStore`/`BrokerStateStore`/`InstrumentSpecStore`.

A fifth rule arrived with the external-agent direction (O-007) and Phase
C: **Crumblr is strategy-neutral, and there is exactly one risk
authority.** Core never re-implements or maps onto an external strategy's
own vocabulary (F-066), and every process that reads or writes the
risk-session ledger — internal or external-agent-driven — goes through
the same real, symbol-keyed Postgres lock (`RiskLedgerLock`, ADR-021),
not an independent in-memory cache.

---

## 4. MetaTrader 5 — what is proven, and what is next

M1 first contact happened 2026-08-24 (`feedback.1.12.md`, PASSED). F-051
(both parts) closed the remaining real-terminal gap for the read/decide
side 2026-08-26/2026-09-01. This section is now "what has actually met
the real broker, and what genuinely has not yet."

### 4.1 What is real-terminal-validated today

| Capability | Status |
|---|---|
| Connect, discover symbol/account/instrument spec | **Proven** — first contact, 2026-08-24 |
| Continuous tick/bar read into the pipeline, reconnect with full revalidation | **Proven** — Phase A/B, 2026-08-24 |
| Broker-clock offset detection | **Proven** — measured, not hard-coded |
| Durable broker account/position/pending-order snapshots (F-047) | **Proven** — F-051 part 1, 2026-08-26 |
| One coherent account read per snapshot (F-052) | **Proven**, same run |
| Reconciliation, including the instrument-spec pinned baseline | **Proven — real `MATCHED`**, F-051 part 1 |
| `LiveDecisionOrchestrator` (F-048) — a real Trader/Risk/Supervisor decision | **Proven** — F-051 part 2, 2026-09-01, `baseline_v1`, real EUR/USD data |
| Real `order_check` (Phase 4) | **Proven** — 2026-08-27, one genuine `ORDER_CHECK_REJECTED` (AlgoTrading deliberately off at the terminal, not a defect) |
| `RiskLedgerLock` cross-process serialization (ADR-021) | Proven against **real PostgreSQL concurrency** (real threads, real advisory lock) — never against two genuinely separate real MT5-connected processes simultaneously, since neither pipeline can reach `order_send` for that to matter yet |
| Real `order_send`, real per-ticket close, real flatten | **Not proven and not reachable.** The real adapter (`DemoOrderSendMt5Gateway`) exists and is unit-tested against a fake terminal only — nothing in `src/`/`scripts/` constructs it |
| AlgoTrading enabled at the terminal | **Deliberately never done.** APP-016: an explicit owner act, never automatic, never "to make a check pass" |

Everything in the last two rows is what `feedback.2.0.md`'s readiness
bundle (§6 below) and an eventual owner activation decision exist to
close — not an engineering gap in the ordinary sense; every piece behind
it is built and tested, only genuinely never turned on.

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

### 4.3 The MT5 scripts

| Script | Purpose | Trades? |
|---|---|---|
| `scripts/mt5_probe.py` | One-shot first contact: connect, read, print | No — holds a `ReadOnlyMt5Gateway`, whose execution methods raise |
| `scripts/mt5_live_reader.py` | Continuous read: ticks/bars/broker-state, reconnect+revalidate, writes a JSON health snapshot | No — same read-only gateway |
| `scripts/live_decision.py` | Runs `LiveDecisionOrchestrator.decide_once()` in a loop: real closed bar → Signal → Risk → Supervisor → persist | **No** — no `order_check`/`order_send` call exists in this process's own call graph |
| `scripts/run_execution_preflight_evidence.py` | One-shot: wires `ExecutionOrchestrator` to real dependencies for a labeled evidence-only capsule | Reaches real `order_check` under explicit, logged, temporary owner authorization only — never `order_send` |
| `scripts/reconcile.py` | One-shot CLI over `application/reconciliation.py` | No |

**The raw `--json` output of `mt5_probe.py`/`mt5_live_reader.py` carries the
real MT5 account number** and must stay local — `var/` is git-ignored for
exactly this. Only a `--sanitized-json` copy (account number redacted,
everything else kept) may be attached to a status entry, a review document,
or pasted into chat (F-031).

Point every real-terminal run at `crumblr_soak`, **never** the shared
`crumblr` test/dev database or either track's own `crumblr_test_dev1`/
`crumblr_test_dev2` isolated database — those get bootstrapped and torn
down by test fixtures. `scripts/mt5_live_reader.py` refuses to start at
all unless `CRUMBLR_DATABASE_URL` is explicitly set.

```powershell
$env:CRUMBLR_DATABASE_URL = "postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr_soak"
uv run python scripts/mt5_live_reader.py --duration 1800 --json var/live_reader_health.json
```

---

## 5. The review loop — do not skip this

An independent reviewing agent files versioned reviews in `review/`.

**At the start of every session, read `review/FEEDBACK.md`** and resolve
(or explicitly answer with a reason) anything still genuinely open before
starting new work. The full protocol is `CLAUDE.md` §1.

`feedback.1.0.md` through `feedback.1.28.md` are all processed — every
`F-###` finding is `CLOSED`. **The review cadence changed 2026-08-27**
(review 1.25 §9): routine engineering progress no longer triggers a new
numbered review. The next *routine* target is `feedback.2.0.md` directly
— the formal readiness review before `order_send` can ever become
reachable, demo included. Bring a numbered reviewer artifact back early
only for: a material safety defect, a proposed change to a Phase-4
invariant, an unresolved Dev-1/Dev-2 authority dispute, an unexpected
path that could reach execution, or the complete `feedback.2.0` bundle
being ready. (2026-09-01's `feedback.1.26`/`1.27`/`1.28` were deliberate
owner-requested exceptions to this rule, not a reversion of it.)

```text
review/
  FEEDBACK.md        the project-wide tracker — start here
  feedback.1.0.md    … 1.28.md — the reviews themselves, never edited
  AGENT_FEEDBACK.md  Dev 2's own local AG-### finding register
  DEVIATIONS.md      every departure from build.md, keyed D-NNN
  INTEGRATION_NOTICES.md  the Dev-1/Dev-2 shared-contract change log
  domain_contracts.md  the M0 contract package — reviewer-approved,
                        optional human countersign only
  adr/               architecture decisions, keyed ADR-NNN (through
                      ADR-021 as of this handover)
  CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS.md   Dev 1's own operating rules
  CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md   Dev 2's own operating rules
  OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md   the current staged
                      coordination order (Phases 0-F) toward one DEMO canary
```

The tracker uses **two** status fields, and the distinction matters:

- **Finding** — is the reviewer's concern resolved?
- **Implementation** — what actually exists? `SHIPPED`, `DECIDED` (an ADR
  with no code), `PENDING M5`.

Do not read "every finding CLOSED" as "`order_send` may now be enabled."
`feedback.2.0.md` is **mandatory before the first real or demo
`order_send`** and must rely on integration evidence, not tracker claims
— review 1.25 §10 lists the exact required bundle.

---

## 6. What to build next, in order

### The immediate gate — evidence and owner acts, not engineering

1. **Confirm hosted CI is actually green** on a real runner (this
   environment has no `gh`/Actions access — needs a human or a future
   session with it). Owner-reported green as of run #106, not
   independently re-pulled.
2. **`agent/contracts` → `main`.** An explicit owner/Dev-2 decision on
   merge timing, not automatic just because the branch is green.
3. **AG-023** — `application/paper_lite.py`'s own unlocked
   `risk_session_states` access. Not urgent (PAPER_LITE never reaches
   `order_send`), but a real gap in ADR-021's stated guarantee until
   whoever owns that file picks it up.
4. **Optional domain-contract human countersign** — only if M0's
   "reviewed by a human" wording is read literally.

### Toward `feedback.2.0` — the formal readiness bundle

Review 1.25 §10 (and review 1.24 §12) name the full required bundle:
hosted CI green, owner-approved risk policy (done, O-008/O-009), F-051
evidence (done, both parts), the real `SubmissionGate`/execution-safety
chain (done, Phase B), real `order_check` evidence (done, 2026-08-27),
exact approved DEMO account/server pin (done, B7), the one-shot canary
permit mechanism (done, B8), and — per the owner's Phase-D dry-drill
requirement (`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`) — a
full run of the intended agent-driven path up to (never past) the
`order_send` boundary, with a genuine external Supervisor and AG-012's
shared authority both actually exercised for real, not only unit-tested.
That dry drill has not yet been run.

### Blocked on a human decision

- Whether/when to enable terminal AlgoTrading, and under what conditions
  (APP-016).
- The first canary's exact maximum requested-risk fraction and one-shot
  permit expiry/window (reviewer recommendation: 0.25% of equity or
  lower, explicitly not yet owner-approved policy).
- Whether the `feedback.2.0` bundle should be the narrower "Crumblr
  execution proof" (may use `baseline_v1`, internal-strategy-driven) or
  the wider "agent-driven MVP" bundle, per review 1.25's own Milestone
  A/B split.

### After `feedback.2.0` gives an explicit GO

- The owner confirms the exact approved risk/config hash, DEMO account
  reference, immutable Static Agent assignment/artifact (if agent-
  driven), first-canary risk cap, permit window, and whether/when to
  enable AlgoTrading.
- Only then may the relevant execution/flatten activation flags be set
  true for the canary. **Merging code must never enable them by itself**
  — every flag in every shipped config defaults closed today, and that
  is a property this session's own tests assert structurally, not a
  promise.

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

**`LiveReader` observes; `LiveDecisionOrchestrator` decides;
`ExecutionOrchestrator` is the one class with a real (if unreachable) path
to a broker mutation.** Keep these responsibilities in separate classes.
`LiveDecisionOrchestrator` never imports `MetaTrader5` or talks to a
gateway directly — only to the durable stores `LiveReader` already wrote
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
just because `UNKNOWN` is inconvenient for a caller.

**A restart may never hand back headroom.** `risk/session.py` recovers the
daily-loss and drawdown state, and every value it restores is seeded so
that recovery can only tighten. Since ADR-021, this recovery also happens
under a real cross-process lock, every cycle, not once cached in memory —
if you touch `LiveDecisionOrchestrator.decide_once()`, read that ADR
first, the caching removal was a deliberate, reviewed behavioural change,
not a simplification to casually reintroduce.

**Do not give a journalled event a random id.** `event_id`/`order_request_id`/
`flatten_request_id` are all derived from content, never `uuid4()` — the
store's append/claim is idempotent on that id, so a rerun after a crash
converges instead of writing history twice or resubmitting a broker
action. This discipline runs all the way through the execution/flatten
chain, not only the original replay journal.

**Do not order journal reads by insertion time.** Three clocks exist:
`occurred_at_utc` (market time — order by this), `recorded_at_utc` (write
time), `sequence` (tie-break). Ordering by insertion time reorders events
after a reconnect backfill, which is exactly when order matters.

**Do not let logging into stdout.** See §2.

**The FX day ends at 17:00 New York on Friday, not at midnight UTC every
day.** Owner session policy v1 (2026-09-03, `ADR-012`) made the intraday/
overnight rule *weekly*, not daily — Monday-Thursday now permits holding
overnight; only Friday carries the last-entry cutoff and mandatory
flatten. If you find code or a comment describing this as a daily rule,
it predates that change and is stale.

**A bar's origin is part of the bar.** A bar the broker sent and one this
platform built from ticks are not interchangeable evidence. `MarketBar.origin`
and `pipeline_version` exist so nobody has to guess which is which months
from now.

**A halt is not a flatten, and a flatten is not the same as closing.**
`HALT NEW ORDERS`, `CANCEL PENDING` and `FLATTEN POSITIONS` are three
separate operator controls and stay decoupled. The *automatic* flatten
(ADR-009/ADR-020) is a fourth, distinct, policy-driven path — it must
never be implemented by reusing the operator's own button, and a test
(`test_the_operator_flatten_control_is_never_reached`) asserts this
structurally.

**The supervisor's default context is UNSAFE by design.** `SupervisorContext()`
defaults both safety fields to `UNKNOWN`, which halts. An external
Supervisor's own unreachable/malformed/timed-out response reads the same
way. Tests that want to exercise a policy rule must say explicitly that
reconciliation and incidents were checked.

**Two supervisor checks are still inert** — the confidence band and the
signal frequency threshold are configured to values nothing can fall
outside. Tracked as `EV-002` and `D-015`. They need recalibrating once real
observations exist, not deleting.

**The live decision path's `AccountState.login` is a placeholder `0`, on
purpose.** `BrokerAccountSnapshot` never carries the raw MT5 login
(build.md §21 — never persist it); account identity for the live path is
verified through reconciliation's `account_ref` fingerprint comparison
instead (D-046).

**M5 was not built by relaxing `ReadOnlyMt5Gateway`.** The real mutating
capability lives in a separate adapter (`OrderCheckMt5Gateway` for
`order_check`, `DemoOrderSendMt5Gateway` for the one real `order_send`/
close capability) satisfying the same `BrokerPort`, so the read-only one
stays available for shadow mode. `D-036`.

**A close is not a widened `close_all_positions`.** Real per-ticket
closes (`DemoOrderSendMt5Gateway.close_position`) always name the exact
MT5 `position` (ticket) explicitly — on a hedging account, closing by
symbol/side alone would let the broker net or open ambiguously against
whichever other position happens to exist on that symbol. Never simplify
this back to a symbol-only close.

**`RiskLedgerLock` opens its own transaction and yields the connection
out — it does not take one in.** Neither `LiveDecisionOrchestrator` nor
`agent_gateway/decision_path.py` owns a raw `Engine`; a required-external-
`connection` design was tried first for ADR-021 and reverted before
implementation for exactly this reason. If you add a new reader/writer of
`risk_session_states`, follow this same shape, not `lock_assignment()`'s
literal signature.

**Crumblr does not compute or emulate an external strategy's own setup
detection.** F-066 (review 1.28): Core hands an external agent a neutral
market/context bundle and enforces only structural shape on whatever
comes back — never a semantic mapping onto `ict_v1`'s own vocabulary,
never a fabricated shared vocabulary. If a change to Core starts
requiring knowledge of a specific external strategy's internal states,
that is the wrong layer for it.

---

## 8. What the evidence does and does not support

Ranked by how much weight it can bear.

| Evidence | Strength |
|---|---|
| Contract invariants, ICT primitives | Strong — hand-constructed cases with known answers |
| Persistence invariants | Strong — real PostgreSQL, all ten ADR-003 criteria, plus a real pg_dump/restore proof on hosted CI |
| Durable halt across restart | Strong — real child processes, not two objects in one interpreter |
| A run rebuilt from the journal | Strong — same decision fingerprint, same tally, read back through a fresh connection |
| Restart-safe risk budget, now cross-process-locked | Strong for the local record — broker history itself is not consulted (`D-032`); the lock itself is proven under real concurrent PostgreSQL connections (ADR-021), not yet under two genuinely separate real MT5-connected processes |
| Replay determinism | Strong — byte-identical, checked in the gate |
| Real MT5 connectivity, continuous read, reconnect | **Strong** — Phase A/B, real Pepperstone demo, 2026-08-24 |
| Discovery through reconciliation `MATCHED`, a real Trader/Risk/Supervisor decision | **Strong** — F-051, both parts, real terminal, real market data |
| Real `order_check` | **Strong for what it tested** — one real, honest `ORDER_CHECK_REJECTED` (AlgoTrading off); never yet tested with AlgoTrading on, deliberately |
| The full non-sending execution/flatten chain (Phase 4 + Phase B) | **Strong on architecture and fake-terminal test coverage; zero real-broker evidence for `order_send`/a real close**, since neither is reachable from any real process today |
| Broker-state persistence, reconciliation (F-047/F-050/F-052) | **Strong — real-terminal-validated**, F-051 part 1 |
| MT5 broker `order_send`/close execution behaviour | **None.** No process can reach either today |
| Fill model | **Weak** — intrabar ordering is an assumption; swap and commission are not modelled at all |
| Strategy performance, internal or external-agent | **None.** No number from this system is decision-grade evidence |

The fill model is the softest link in the *replay* evidence chain
(`D-010`). The freshest real gap in the *real-broker* evidence chain is
`order_send`/a real close itself — everything built since Phase 4 started
is architecturally sound and fake-terminal-tested, but has never met the
thing it is meant to eventually do for real, by design, until
`feedback.2.0` and an explicit owner activation decision.

---

## 9. Local environment notes

- **This working copy runs on Windows x86-64 (AMD64) with the MT5 terminal
  already installed and running** (`Pepperstone MetaTrader 5`, confirmed
  2026-08-26) — the same machine does development *and* real MT5 work.
  Windows-on-ARM does not work for the MT5 half — the wheels do not exist
  for it.
- **Two development tracks, two dedicated worktrees, since 2026-08-28.**
  `.claude/worktrees/core` (Dev 1, branch prefix `core/*`) and
  `.claude/worktrees/agent-dev2` (Dev 2, branch prefix `agent/*`) are the
  real active workspaces — see §0.2 above. The top-level checkout can
  drift stale; always `git fetch origin` and compare against
  `origin/main`, not local `main`, before trusting what you see there.
- **Docker Desktop** does not start automatically. Most persistence tests
  skip silently without a database, so check before believing a green run.
- **Several PostgreSQL databases matter, and mixing them up causes real
  incidents.** `crumblr` is the original shared dev/test database.
  `crumblr_test_dev1`/`crumblr_test_dev2` are each track's own isolated
  integration-test database (never share these between tracks, and never
  use them for a real soak/live run). `crumblr_soak` is dedicated to
  real-MT5 runs and must be migrated manually (`alembic upgrade head`, or
  `scripts/reset_soak_database.py` to reset it cleanly without drifting
  `alembic_version` — F-041). `scripts/mt5_live_reader.py` refuses to
  start without `CRUMBLR_DATABASE_URL` explicitly set, specifically to
  prevent this mistake.
- **The local safety latch** lives at `.crumblr/safety_state.json` and is
  git-ignored — a property of the host, never of the repository.
- **History starts at one commit.** The owner held commits until a working
  prototype existed (F-006); `fd6a890` on 2026-08-24 is the initial import
  of everything through M2. `status.md` §13 is the detailed record of how
  the code got here since — it is long (80+ chronological entries as of
  this handover) but is the authoritative "what actually happened, with
  evidence" record when this document's own summary is not enough.
- **The remote is `DutchBugs/Crumblr`, private, on a personal account kept
  separate from the owner's work account.** Identity and credentials are
  pinned **repo-locally**: `user.email`, and
  `credential.https://github.com.username`. Do not move either to the
  global config, and do not switch the remote to SSH — the default key on
  the macOS host is a deploy key belonging to an unrelated work repository.
- **Hosted CI has run for real since 2026-08-26.** Three real defects were
  found and fixed on the first few hosted runs (an undeclared `numpy` test
  dependency, a `UV_FROZEN`/`--locked` conflict, `ruff format` rewriting
  immutable reviewer Markdown, a PostgreSQL client/server major-version
  mismatch on the restore-proof test) — each fixed the same day it was
  found, per `review/FEEDBACK.md`'s F-056/F-063/F-065/F-067/F-068 rows.
  The fixes are pushed and owner-reported green (run #106); this
  environment still has no `gh`/Actions access to independently confirm.
- **`.gitattributes` pins the checkout to LF.** With two operating systems
  in play, a CRLF checkout would change the determinism hash and the
  format check without changing any code.

---

## 10. Where to look when something is confusing

| Question | Answer lives in |
|---|---|
| Where does the project stand *right now*, more current than this document? | `status.md`'s own compact "Current state" header at the top |
| Why is the code like this? | `review/DEVIATIONS.md`, keyed `D-NNN` |
| Why was this decided? | `review/adr/`, and `status.md` §10 |
| What happened when? | `status.md` §13, chronological with evidence |
| What is broken or open? | `review/FEEDBACK.md`'s finding register (project-wide `F-###`), `review/AGENT_FEEDBACK.md` (Dev-2-local `AG-###`) |
| What does the spec require? | `build.md` — and it is never edited to match the code |
| What did the reviewer say? | `review/feedback.1.*.md`, newest first; cadence changed 2026-08-27, see §5 above |
| Has this been proven against the real broker, or only against a fake one? | §4/§8 above, and the Implementation column in `review/FEEDBACK.md` |
| How do I connect to MT5? | §4 above, and `scripts/mt5_probe.py` / `scripts/mt5_live_reader.py` |
| Which workspace/worktree should I be in? | §0.2 above |
| What is the very next thing to do? | §0/§6 above — depends on which track you are continuing |
| What is a shared-contract change, and how does it get coordinated between tracks? | `review/CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS.md` §4, `review/INTEGRATION_NOTICES.md` for the actual log of every such change made so far |
