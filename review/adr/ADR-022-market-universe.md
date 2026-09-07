# ADR-022 — Market Universe: multi-market Core, Risk & Policy

**Status:** ACCEPTED 2026-09-06, CORRECTED 2026-09-07/08 (three times) —
Dev 1's own branch, `dev1/market-universe`, off `origin/main` @ `1971d7a`.
Not merged; stop for owner review per the owner's own work-order cadence
(see §7).
**Date:** 2026-09-04 (work order) — 2026-09-06 (this record) —
2026-09-07 (owner corrective review #1, wiring + fail-closed, §0) —
2026-09-07/08 (owner corrective review #2, cross-market capsule routing,
§0a) — 2026-09-08 (owner corrective review #3, reconciliation history +
order-frequency scoping, §0b)

## 0. Owner corrective review, 2026-09-07 — what changed and why

The 2026-09-06 version of this branch added `risk_for()`/`execution_for()`/
`calendar_for()` as real, tested functions (§3.2, §3.4) but did **not**
finish wiring them into the real orchestrators — every live/agent/execution
call site still read `config.risk`/`config.execution` (the platform-wide
default) directly, and every `trading_window`/`policies` call left
`calendar` at its default `FxWeekdayCalendar`. A second enabled market
would have silently traded against EUR/USD's thresholds and calendar
despite the config layer supporting per-market values — the config-layer
work was real, but it was not reachable from anything that runs. The owner
caught this directly and required a corrective pass, verbatim (Dutch):

> Wire PlatformConfig.risk_for(symbol) en execution_for(symbol) werkelijk
> door de relevante live/agent/execution paths... Wire calendar_for(market
> .asset_class) daadwerkelijk door de session/trading-window checks...
> Voor een asset class zonder owner-approved session policy: fail closed,
> niet SessionPhase.OPEN.

Two corrections landed in this pass:

1. **Real wiring**, not just the API surface. Every `RiskContext`
   construction site (`ReplayOrchestrator`, `LiveDecisionOrchestrator`,
   `ExecutionOrchestrator`, `agent_gateway/decision_path.py`, and
   `application/paper_lite.py`, Dev-2/3-owned — mechanical fix applied
   here, same as the AG-024 precedent) now resolves `config.risk_for
   (canonical_symbol)`/`config.execution_for(canonical_symbol)`/
   `calendar_for(market.asset_class)` once, at construction, and reads
   from that resolved value everywhere — never `config.risk`/
   `config.execution` directly, and never the default calendar for a
   market whose config says otherwise. Full inventory in §3 below.
2. **Fail closed, not open, on an unapproved session policy.**
   `risk/trading_window.py::phase_at` previously resolved a calendar with
   no weekly-close concept (`AlwaysOpenCalendar`, for a 24/7 asset class
   with no owner-approved session policy) to `SessionPhase.OPEN` — treating
   "no policy" the same as "policy disabled," on the reasoning that there
   was nothing to measure `IntradayPolicy`'s offsets against. **The owner
   rejected this**: absence of an approved policy must refuse entries, not
   permit them by default. `phase_at` now resolves this case to
   `SessionPhase.CLOSED` **unconditionally** — checked before
   `policy.enabled`, so a globally-enabled `IntradayPolicy` cannot
   accidentally permit entries on a calendar with no approved policy
   either. See §3.4 (updated) and `review/DEVIATIONS.md` D-060 (updated).

New regression tests proving both corrections, added this pass:
`tests/unit/test_market_universe_wiring.py` (`ReplayOrchestrator`,
`LiveDecisionOrchestrator`, `agent_gateway/decision_path.py` — a second
market's own risk/execution overrides and calendar demonstrably reach
each real orchestrator, proved behaviourally where the class has no
inspectable attribute to reach into),
`tests/integration/test_market_universe_wiring.py`
(`ExecutionOrchestrator`, real PostgreSQL), and
`tests/unit/test_risk_engine.py::TestMarketUniverseCalendarFailsClosed`
(the core enforcement point: `policies.evaluate()` always refuses with
`SESSION_BLACKOUT` on a calendar with no approved policy, regardless of
whether the platform's `IntradayPolicy` is itself enabled). `PaperLiteOrchestrator`
got the identical wiring fix (§3.5's per-call-site list) but not a
dedicated construction-level unit test — its constructor needs a full
`PaperLiteSettings`/`TradingAssignment`/`AgentGateway`/`DurablePaperBroker`
graph, materially heavier than the other four call sites; the fix there
is verified by mypy (identical `risk_for`/`execution_for`/`calendar_for`
call shape, type-checked against the same signatures the tested call
sites use) and the full test suite staying green, not by a dedicated
proof. Named here rather than silently left uncovered.
**Drivers:** Owner work order 2026-09-04 (verbatim, Dutch): "Dev 1 — Core /
Market Universe / Risk & Policy... Maak Crumblr multi-market aan de
Core-kant: expliciete approved Market Universe; per-market
instrument-spec pinning; per-market risk/execution parameters; geen
globale EUR/USD point/spread-aannames hergebruiken voor BTC/XAU/JPY/etc.;
asset-class-aware trading calendars; broker-symbol mapping blijft
Crumblr-owned en fail-closed; geen Agent-selected willekeurige broker
symbols buiten de approved universe... `order_send` blijft NO-GO."
**Supersedes:** nothing. Narrows/qualifies `build.md` §30 recommendation
#12 — see §1 below.
**Implementation:** `domain/enums.py` (`AssetClass`), `config.py`
(`RiskOverrides`/`ExecutionOverrides`, enriched `MarketConfig`,
`PlatformConfig.risk_for()`/`.execution_for()`), `mt5_gateway/readonly.py`
(`resolve_symbol()` pin-first fail-closed, `discover_candidate_symbols()`
split out), `mt5_gateway/execution.py`/`application/live_reader.py`
(thread `expected_broker_symbol` through), `risk/calendars.py` (new —
`TradingCalendar` Protocol, `FxWeekdayCalendar`, `AlwaysOpenCalendar`),
`risk/trading_window.py` (`calendar` parameter, default-preserving),
`persistence/schema.py` + migration `8801080869a6` (`risk_session_states
.canonical_symbol`), `risk/session.py` + `persistence/risk_session.py`
(`RiskSessionState`/`RiskSessionStore` per-symbol scoping),
`application/{orchestration,live_decision,execution,paper_lite}.py`,
`agent_gateway/decision_path.py` (call-site updates).

---

## 0a. Owner corrective review #2, 2026-09-07/08 — cross-market capsule routing

A second, independent owner review of the same branch found a real
routing gap the first corrective pass did not touch:
`ExecutionOrchestrator.run_once()` read every sealed capsule for the
environment via `self._capsules.read_all(environment=...)` and never
bound that read to `self._canonical_symbol` — `_process()` never checked
`capsule.canonical_symbol`/`intent.symbol` against the worker's own
market either. Fresh broker state, `InstrumentSpec`, the risk session and
market ticks were all then read for `self._canonical_symbol` regardless
of which market the capsule actually belonged to. For multi-market, an
execution worker bound to one market must never be able to claim or
process a capsule sealed for another. Fixed per the owner's own stated
preference, all four points:

1. **Filter before claim, at the database.**
   `persistence/journal.py::CapsuleStore.read_all()` gained an optional
   `canonical_symbol: str | None = None` parameter — a `WHERE
   canonical_symbol = ...` clause, not an application-level filter after
   the fact. `decision_capsules.canonical_symbol` was already a real,
   indexed-adjacent column (populated since the capsule schema's own
   original design); no migration needed. `run_once()` now calls
   `self._capsules.read_all(environment=..., canonical_symbol=self
   ._canonical_symbol)` — a capsule for another market is never fetched
   from PostgreSQL at all, let alone iterated over or claimed.
2. **Defensive invariant in `_process()`.** Immediately after the
   existing `assert capsule.trade_intent is not None` block, two more
   assertions: `capsule.canonical_symbol == self._canonical_symbol` and
   `intent.symbol == self._canonical_symbol`. These should be
   unreachable given fix 1 — they exist as a second, independent layer,
   the same "prove it, don't just trust it" discipline the three existing
   asserts in this method already use. A routing bug that ever bypasses
   the `read_all` filter (a direct `_process()` call, a future refactor)
   crashes loudly instead of silently claiming/executing the wrong
   market's order — an invariant violation, not a business refusal, so no
   new `ExecutionEventType`/`ReasonCode` was added for it.
3. **`CapsuleSource` Protocol updated to match** (`application/execution
   .py`) — the one other implementation, `scripts
   /run_execution_preflight_evidence.py::_SingleCapsuleSource`, updated
   for Protocol conformance (mypy caught this automatically).
4. **Regression tests, both directions, at the persistence layer, not
   just the return value:** `tests/integration/test_market_universe_wiring
   .py`. `TestCapsuleStoreReadAllIsBoundedBySymbol` proves the raw filter.
   `TestExecutionOrchestratorNeverClaimsAnotherMarketsCapsule` seals one
   EUR/USD and one BTC/USD capsule into the same environment, runs a
   EUR/USD-bound worker's `run_once()`, and proves the BTC/USD capsule
   left **no** `execution_requests` claim and **no** `execution_events`
   row — a fresh probe claim against its `order_request_id` wins outright
   (nothing claimed it first), and `events_for()` returns empty — then
   proves the mirror case (a BTC/USD-bound worker never touches the
   EUR/USD capsule).

**Two pre-existing, unrelated flakiness episodes were investigated and
ruled out** during this pass, both named plainly rather than quietly
re-run past:

1. `tests/integration/test_execution_orchestrator.py` (completely
   unmodified) intermittently fails 2-3 of its own tests with a
   `DROP TABLE ... does not exist` / mid-query `UndefinedTable` error on
   a fast repeated run — reproduced identically on the new test file and
   on the unmodified pre-existing file, then confirmed to pass cleanly on
   a subsequent run with zero code changes in between. Same
   "real-Postgres-connection-pool/test-isolation" class of flakiness
   named in slice 2's commit message.
2. The first *full-suite* integration run after this fix landed came
   back with 22 failed, 57 errors, spread across files with no
   relationship to this change — nearly all pytest `ERROR`s (fixture
   setup failures, pointing at the `engine` fixture itself). Checked
   `docker ps`/`pg_isready` (healthy), `pg_stat_activity` (7 of 100
   connections, not exhaustion), disk space (17G free) — nothing pointed
   at a real cause. Two immediate re-runs came back clean (268 passed, 2
   skipped, 0-1 errors) with zero code changes in between; treated as a
   transient environmental event (most likely Docker Desktop/WSL2 under
   load from several heavy back-to-back Postgres runs earlier in the same
   session), not a regression.

Neither caused by this fix. The full suite passes reliably: 268 passed, 2
skipped, confirmed across repeated clean runs.

`config/paper.yaml` untouched — BTC/USD stays `enabled: false`. No scope
expansion: this fix only bounds an existing read, adds two assertions,
and extends one Protocol; it does not touch risk/execution/calendar
logic, `order_send` (still NO-GO), or anything outside
`ExecutionOrchestrator`'s own capsule-claiming path. `LiveDecisionOrchestrator`
and `ReplayOrchestrator` were checked and do not have the equivalent
exposure: neither reads a collection of capsules by environment alone —
each processes one `MarketSnapshot`/`GeneratedTick` stream already scoped
to its own `canonical_symbol` from construction, so there is no analogous
"read many, filter none" step for them to leak across.

---

## 0b. Owner corrective review #3, 2026-09-08 — reconciliation history and order-frequency scoping

A third owner review, on the same branch, found two more unscoped reads
in `ExecutionOrchestrator`, both the same shape as §0a's finding — a bulk
read across the whole environment, feeding logic already bound to
`self._canonical_symbol` downstream:

1. **`reconcile_once()`'s candidate read.** `self._events
   .request_ids_with_event(ExecutionEventType.SUBMISSION_STARTED)` read
   every request with a `SUBMISSION_STARTED` event across *every* market,
   while the broker observation and `ExpectedState` built from it a few
   lines later are bound to `self._canonical_symbol` — a EUR/USD worker
   could derive expected exposure from a BTC/USD request's history, or
   append a `RECONCILED` event to it.
2. **FINAL Risk's order-frequency count.** `self._events
   .count_events_since(ExecutionEventType.SUBMISSION_STARTED, ...)` fed
   `PortfolioState.orders_in_last_hour`, compared against
   `RiskConfig.max_orders_per_hour` — a genuinely per-market
   `RiskOverrides` field (§3.2). An unscoped count would let one market's
   submissions exhaust another's hourly budget, or vice versa.

Fixed, per the owner's own stated preference (no schema change — both
join through the existing `capsule_id`/`order_request_id` foreign keys):

- `persistence/execution.py::ExecutionEventStore.request_ids_with_event()`
  and `.count_events_since()` both gained optional `environment`/
  `canonical_symbol` keyword parameters. Passing either joins
  `execution_events -> execution_requests -> decision_capsules`
  (`execution_events` itself carries neither column) and filters on it;
  passing neither preserves the prior unscoped query exactly — a shared
  `_events_joined_to_capsules()` helper builds the join once for both
  methods.
- `ExecutionOrchestrator.reconcile_once()` and the FINAL-Risk order-count
  read (`_process()`) now pass `environment=self._config.environment,
  canonical_symbol=self._canonical_symbol`.
- `scripts/reconcile.py` — a standalone read-only reconciliation tool
  with the *identical* unscoped-`request_ids_with_event()` bug, found
  while checking every real caller of the method (it already binds
  `flatten_histories`/`expectation` to `args.canonical_symbol`/
  `args.environment` two lines below the bug, exactly like
  `reconcile_once()` did) — fixed the same way. Not a scope expansion:
  the identical fix applied to a second call site of the identical
  pre-existing bug, found and fixed rather than left standing next to
  the one under review.

New regression tests, `tests/integration/test_market_universe_wiring.py
::TestReconciliationHistoryIsBoundedBySymbol`:

- `test_request_ids_with_event_is_bounded_by_symbol` /
  `test_count_events_since_is_bounded_by_symbol` — the raw persistence-
  layer join/filter, two markets' seeded `SUBMISSION_STARTED` events,
  each query returns only its own market's request/count, an unscoped
  call still returns both (regression guard for every existing caller
  that never passes the new parameters).
- `test_a_eur_usd_worker_never_reconciles_or_derives_exposure_from_a_btc_usd_request` —
  end to end, real PostgreSQL, a real two-market `run_once()`/
  `reconcile_once()` sequence. EUR/USD runs the real, unmodified pipeline
  (claim → `SUBMISSION_STARTED` → ambiguity resolves → `RECONCILED`).
  BTC/USD's request is seeded directly at the `SUBMISSION_STARTED`/
  `AMBIGUOUS_OUTCOME_RESOLVED{submitted:false}` state
  `_recover_ambiguous_submission` itself would produce on a real pass —
  **not** run through the real entry pipeline, because BTC/USD
  structurally cannot reach eligibility today (`SESSION_BLACKOUT`,
  correctly, per §0's fail-closed calendar fix) — a deliberately
  separate concern from the one under test here: whether reconciliation
  itself, given a request that has somehow reached `SUBMISSION_STARTED`,
  stays bounded to its own market. Both workers reach `RECONCILED` for
  their own request; each worker's `events_for()` on the *other*
  market's request is asserted unchanged, before and after.

`config/paper.yaml` untouched — BTC/USD stays `enabled: false`. No
schema change: both new query parameters join through foreign keys that
already exist.

`build.md` §30's own critical recommendation #12 states: *"Do not add more
markets until the EUR/USD lifecycle is operationally boring."* By the
spec's own definition — "reconnects, restarts, rejected orders, stale
data and reconciliation should all have predictable outcomes" — EUR/USD
is not fully there: real `order_send` has never fired once in this
codebase's history (`feedback_2_0_approved` has never been `true`), so
"rejected orders" has zero real-world evidence.

`build.md` §24 ("Multi-market expansion design") is the spec's *own*
forward-looking blueprint for exactly this work, naming the right seams
(`Instrument`/`MarketDataAdapter`/`ExecutionAdapter`/`CostModel`/
`SessionCalendar`/`RiskModel`/`Strategy`) and requiring a **Market
Capability Matrix** before a new market trades.

This is the owner's own call to make on their own spec. Recorded here,
transparently, as a deliberate, dated deviation (mirrored in
`review/DEVIATIONS.md`) — not silently treated as though §30 never said
it, and not silently overridden either. What keeps it safe: `order_send`
stays globally NO-GO throughout this branch and every branch before it.
Nothing here brings a new market closer to live execution; it makes the
**config/persistence/risk/calendar/broker-mapping layer** genuinely
multi-market-capable, which is a precondition for a Market Capability
Matrix to ever be evaluated honestly, not a substitute for one.

## 2. Scope boundary

This slice makes the config/persistence/risk/calendar/broker-mapping
layer multi-market-capable and enforces an approved Market Universe. It
does **not** make any single running process/script handle multiple
markets simultaneously — `scripts/live_decision.py` etc. still target one
`--canonical-symbol` per invocation, now validated against the Universe
with that market's own real parameters and calendar. Orchestrating N
markets concurrently in one process is a separate, future operational
change, named here rather than silently assumed. CLI/script
`= "EUR/USD"` defaults are untouched — still the only operationally
enabled market; an operator must explicitly pass a different one once
approved.

## 3. Design

### 3.1 `AssetClass` (`domain/enums.py`)

`FX` / `CRYPTO` / `METAL`, additive-only. Drives calendar selection
(§3.4) only — no per-asset-class strategy dispatch exists yet.

### 3.2 Per-market config (`config.py`)

`MarketConfig` gained `asset_class: AssetClass`, a **required**
`broker_symbol` (every market, including EUR/USD — the real, already-known
Pepperstone mapping, made explicit and reviewable instead of
runtime-guessed), and optional `risk_overrides`/`execution_overrides`.
`RiskOverrides`/`ExecutionOverrides` mirror `RiskConfig`/`ExecutionConfig`
field-for-field, every field `| None = None` — a market states only what
it changes from the platform default. `PlatformConfig.risk_for(symbol)`/
`.execution_for(symbol)` merge overrides onto the platform default and
re-validate as a real `RiskConfig`/`ExecutionConfig`, so cross-field
ordering rules (e.g. `last_entry_offset >= flatten_offset`) still apply to
the merged result, not just to the platform default in isolation.

`config/paper.yaml` gained a `BTC/USD` entry: `enabled: false`,
`broker_symbol: BTCUSD`, `asset_class: CRYPTO`,
`expected_spec_version: null`. **Provenance, stated plainly:** the work
order's own fixture — "Eerste observed fixture: BTC/USD -> BTCUSD op
PepperstoneUK-Demo, gebaseerd op de sanitized probe" — does not correspond
to any artifact found in this repository (`review/`, `status.md`,
`build.md`, `src/`, `scripts/`, `tests/`, repo root: zero hits for
`BTC`/`BTCUSD`/`XAU`; no `var/` directory exists in this worktree).
Treated as **owner-asserted, not independently repo-verified** — seeded
`enabled: false` with `expected_spec_version: null` (the same "must begin
unpinned, then pin only after a human inspects a real observation"
discipline F-055 already established for EUR/USD). Real-terminal
confirmation via `scripts/mt5_probe.py --canonical-symbol "BTC/USD"
--sanitized-json ...` is still outstanding before this market could ever
be enabled.

### 3.2a Real wiring: every orchestrator resolves its own market's config (2026-09-07 correction)

`risk_for()`/`execution_for()`/`calendar_for()` existing was not the same
claim as them being *called* by anything that runs — the owner's 2026-09-07
review caught exactly this gap. Each of the following now resolves
`self._risk_config`/`self._execution_config`/`self._calendar` once, at
construction, from the orchestrator's own `canonical_symbol`, and reads
from those resolved values everywhere in the class — never
`config.risk`/`config.execution` directly, and never a bare default
`calendar` on any `trading_window`/`policies` call:

- `application/orchestration.py::ReplayOrchestrator` — `RiskContext`
  construction, `AgentContext`'s risk hints, `ApprovedOrder.max_slippage_points`,
  `recover_session()`'s loss/drawdown thresholds, the loss-gate check, and
  `overnight_breach()`'s `calendar` argument.
- `application/live_decision.py::LiveDecisionOrchestrator` — identical
  shape (`RiskContext`, `AgentContext`, `recover_session()`, the loss
  gate, `overnight_breach()`).
- `application/execution.py::ExecutionOrchestrator` — `_risk_context()`,
  `ApprovedOrder.max_slippage_points`, `recover_session()`'s thresholds,
  `evaluate_execution_eligibility()`'s `calendar` argument, the automatic-
  flatten machinery (`phase_at`/`requires_flat`/`has_crossed_weekly_close`/
  `build_flatten_plan()`, all now calendar-aware — including the flatten
  deadline itself, `self._calendar.weekly_close(now)`, guarded for `None`
  rather than falling back to the bare FX `weekly_close()` function), and
  every governance-field read (`approved_config_version`,
  `flatten_submission_enabled`, `feedback_2_0_approved`,
  `submission_enabled`, `approved_canary_account_ref`,
  `max_market_data_age_ms`) — routed through the resolved config objects
  for internal consistency, even though the four approval fields are
  deliberately excluded from `RiskOverrides`/`ExecutionOverrides` (§3.2)
  and so always equal the platform-wide value regardless of market.
- `agent_gateway/decision_path.py::_risk_context` (Dev-2-owned,
  mechanical fix) — gained a required `canonical_symbol` parameter,
  called with `snapshot.symbol` at its one call site.
- `application/paper_lite.py::PaperLiteOrchestrator` (Dev-2/3-owned,
  mechanical fix, same pattern as the slice-4 `canonical_symbol` fix) —
  `PolicyHints.min_stop_distance_points_hint`, `ApprovedOrder
  .max_slippage_points`, `recover_session()`'s thresholds, and
  `phase_at()`'s `calendar` argument.

`risk/policies.py::RiskContext` gained a `calendar: TradingCalendar`
field (default `FxWeekdayCalendar()`, matching every real call site's own
default-preserving shape) — `evaluate()` passes `context.calendar` to
`permits_new_entry()` and the (now calendar-aware) `overnight_breach()`.
`risk/execution_eligibility.py::evaluate_execution_eligibility()` and
`application/flatten_plan.py::build_flatten_plan()` each gained the same
default-preserving `calendar` parameter.

### 3.3 Crumblr-owned, fail-closed broker-symbol resolution (`mt5_gateway/readonly.py`)

Before this branch, **every** market's broker-symbol mapping — including
EUR/USD's — was runtime-discovered by fuzzy prefix match against
`symbols_get()`, never pinned. `resolve_symbol()` now reads
`MarketConfig.broker_symbol` for the canonical symbol in play: if set, it
confirms the symbol exists **verbatim** in the broker's real symbol table
(exact match only, no fuzzy fallback) and raises `SymbolNotFoundError` if
absent — fail-closed, not "something close." The old fuzzy-match
heuristic survives as `discover_candidate_symbols()`, an explicitly
separate onboarding helper for finding a *new* market's mapping before it
has a pin — structurally proven (`tests/unit/test_mt5_readonly_gateway
.py::TestPinnedSymbolResolution`, an `inspect.getsource()` boundary proof)
never to be called from the pinned trading path. Zero behaviour change
for EUR/USD's actual resolved symbol (`EURUSD`); the path is now a
config-verified lookup instead of a guess. `expected_broker_symbol`
threads through as an optional, default-`None` parameter on
`ReadOnlyMt5Gateway`/`OrderCheckMt5Gateway`/`LiveReader` — every real
script call site (`scripts/mt5_live_reader.py`,
`scripts/run_execution_preflight_evidence.py`) now passes
`market.broker_symbol` from `PlatformConfig.market_for()`.
`scripts/mt5_probe.py` is deliberately unchanged — it is the onboarding/
discovery tool a new market's pin is established *from*.

### 3.4 Asset-class-aware trading calendars (`risk/calendars.py`, `risk/trading_window.py`)

`trading_agent/sessions.py::is_market_open`/`trading_day`/`weekly_close`
implement **owner risk policy v1** (ADR-012, D1.5 — "weekend holding
verboden") — a real, deliberate decision for FX specifically, not an
assumption that leaked into the code. **There is no equivalent
owner-approved session policy for a 24/7 asset class** — should crypto
have a weekly flatten requirement at all? what, if anything, replaces
"weekend"? Nobody has decided that, and this ADR does not decide it
either.

`risk/calendars.py` introduces a `TradingCalendar` Protocol
(`is_market_open`/`trading_day`/`weekly_close`), `FxWeekdayCalendar` (a
thin wrapper, zero behaviour change, regression-guarded byte-for-byte
against `trading_agent.sessions` in `tests/unit/test_risk_calendars.py`),
and `AlwaysOpenCalendar` (always open; `weekly_close()` returns `None` —
not "far away," an actual absence of the concept). `calendar_for()` maps
`FX`/`METAL` → `FxWeekdayCalendar` (this broker trades metals on FX-like
hours — a starting assumption stated explicitly, revisit with real
evidence if ever wrong), `CRYPTO` → `AlwaysOpenCalendar`.

`risk/trading_window.py`'s five public functions
(`phase_at`/`permits_new_entry`/`requires_flat`/`has_crossed_weekly_close`/
`time_until_weekly_close`) gained an optional, keyword-only
`calendar: TradingCalendar = _DEFAULT_CALENDAR` parameter
(`_DEFAULT_CALENDAR = FxWeekdayCalendar()`) — default-preserving, so every
existing caller (`application/execution.py`, `application/flatten_plan
.py`, `application/paper_lite.py`, `risk/execution_eligibility.py`,
`risk/policies.py`) needs no change and no behaviour change.

**Fail-closed, corrected 2026-09-07 (see §0).** When
`calendar.weekly_close()` returns `None`, `phase_at` now resolves to
`SessionPhase.CLOSED` **unconditionally** — checked before
`policy.enabled`, so a globally-enabled `IntradayPolicy` cannot
accidentally permit entries on a calendar with no approved policy either.
An earlier version of this branch resolved this case to `OPEN` (treating
"no calendar policy" the same as "policy disabled" — nothing to measure
offsets against). The owner rejected that reasoning outright: absence of
an approved session policy must refuse entries, not permit them by
default. `time_until_weekly_close` still returns `timedelta | None` (there
genuinely is no boundary to report a remaining time against), but
`permits_new_entry`/`requires_flat` now resolve through the corrected
`phase_at`, so no asset class trades until an owner makes a real
session-policy decision for it and a new `TradingCalendar` encodes it —
every non-FX market's config is fail-closed by construction, not merely
"behaves as disabled."

### 3.5 Per-market risk ledger (`persistence/schema.py`, migration `8801080869a6`, `risk/session.py`, `persistence/risk_session.py`)

Before this branch, `risk_session_states` had **no `canonical_symbol`
column at all** — `PostgresRiskSessionStore.load_latest()` read "the
single latest row in the table," full stop. A second enabled market would
have silently shared EUR/USD's equity/drawdown/loss ledger — the one
genuine safety gap this ADR closes, not merely a modeling nicety.

Migration `8801080869a6` (`down_revision = e91f4a7c2b53`, the branch
point) adds `canonical_symbol String(64)`, backfills every existing row to
`'EUR/USD'` (the only symbol that has ever written one), makes it
`NOT NULL`, and replaces `ix_risk_session_order` (`sequence` alone) with a
composite `(canonical_symbol, sequence)` index — matching how
`ix_risk_session_day` is already keyed. `RiskSessionState` gained a
required `canonical_symbol: str` field. `RiskSessionStore.load_latest()`
gained a required `canonical_symbol: str` keyword parameter, filtering the
query (`WHERE canonical_symbol = ...`) before ordering by sequence — the
same key `RiskLedgerLock.held()` (ADR-021) already locks on, now finally
reaching the store, not just the lock. `save()` was **not** given a
separate `canonical_symbol` parameter — `RiskSessionState` already carries
it as a field, and threading it twice would let the two disagree; `save()`
reads `state.canonical_symbol` directly. This is a small, deliberate
deviation from this ADR's own original plan wording ("`.load_latest()`/
`.save()` gain a required `canonical_symbol: str` parameter") in favor of
the safer shape — noted here rather than silently diverging from the
plan without saying so.

`InMemoryRiskSessionStore` now keeps one state per `canonical_symbol` (a
plain dict) rather than a single scalar, so a test seeding two markets
cannot have one overwrite the other in memory the same way the real store
now refuses to on disk. Every Dev-1-owned call site
(`application/orchestration.py`, `application/live_decision.py`,
`application/execution.py`, `application/bootstrap.py`) updated to pass
the canonical symbol already in local scope. Two Dev-2/Dev-3-owned call
sites — `agent_gateway/decision_path.py::evaluate_agent_trade_intent`
(reads `snapshot.symbol`, already in scope) and
`application/paper_lite.py::PaperLiteOrchestrator` (reads
`self._assignment.canonical_symbol`, already in scope) — got the minimal
mechanical fix to keep compiling and passing, mirroring the AG-012/AG-024
precedent; see §5 for the coordination note sent to Dev 2.

Cross-contamination proof, real PostgreSQL, not a fake:
`tests/integration/test_risk_session_per_market.py` —
`TestTwoMarketsNeverCrossContaminate` (a write to one symbol never moves
another's `load_latest`, and an unwritten symbol reports absent, not
another symbol's state). Migration round-trip:
`tests/integration/test_migrations.py::TestRiskSessionCanonicalSymbolBackfill`
(a pre-migration row backfills to `'EUR/USD'`; the column is `NOT NULL`
post-upgrade; downgrade drops the column and restores the old index).

### 3.6 Market Universe enforcement stays Core-owned

No new persistence store — the Market Universe *is*
`PlatformConfig.markets` (config-driven, git-reviewed, already validated
for uniqueness — "approved" semantics a runtime-mutable DB table would not
give for free). `risk/policies.py`'s existing `SYMBOL_NOT_ALLOWED` check
against `RiskContext.allowed_symbols` (fed from `config.enabled_symbols()`
at every call site) remains the enforcement point — unchanged, confirmed
still correctly wired.

## 4. What this ADR deliberately leaves open — named, not silently resolved

1. **The crypto (and any other 24/7 asset class) session policy.** §3.4:
   no owner-approved weekly-close/flatten concept exists for
   `AlwaysOpenCalendar`. Corrected 2026-09-07 (§0): every such market now
   fails closed (`SessionPhase.CLOSED` unconditionally, not an implicit
   `IntradayPolicy.disabled()`-style permissive default) until a real
   owner decision exists and a new `TradingCalendar` encodes it.
2. ~~**Registration-time Market Universe validation.**~~ **Closed
   2026-09-06/07 by Dev 2 — AG-026.** `AgentGateway.issue_assignment()`
   now requires `platform_config: PlatformConfig` and refuses (new
   `MarketNotApprovedError`) any `canonical_symbol` that `PlatformConfig
   .market_for()` doesn't resolve, or resolves to `enabled=False`, before
   the assignment is ever durably registered — an earlier, additional
   gate layered on top of `SYMBOL_NOT_ALLOWED`'s existing intent-time
   check (§3.6), not a replacement for it. Pushed to `agent/contracts`
   (`7b15c48`). `dev1/market-universe` will need to pass `platform_config=`
   at its own `AgentGateway(...)` construction sites once it rebases on
   or merges with a `main` that includes AG-026 — this branch currently
   constructs none itself, confirmed by grep.
3. **The BTC/USD fixture's real-terminal confirmation.** §3.2 — outstanding
   before that market can ever be `enabled: true`.
4. **Concurrent multi-market orchestration in one process.** §2 — a
   separate, future operational change.

## 5. Dev-2 coordination

Sent 2026-09-06: description of the registration-time Universe-validation
gap (item 2 above), the exact minimal mechanical fix already applied to
`agent_gateway/decision_path.py` to keep it compiling against the new
`RiskSessionStore.load_latest(canonical_symbol=...)` signature, and a
request to confirm `snapshot.symbol` is the correct value threaded there
(mirrors the AG-012/AG-024 precedent: describe, do not unilaterally decide
on Dev-2-owned semantics). Dev 2 replied 2026-09-06/07: confirmed both
mechanical fixes (`decision_path.py`'s `snapshot.symbol`,
`paper_lite.py`'s `self._assignment.canonical_symbol`) are semantically
correct against their own intent, and closed the registration-time gap
themselves as AG-026 (item 2 above) — acknowledged, no further action
needed on this branch until it converges with `agent/contracts`.

## 6. Verification

```
uv run ruff check . && uv run ruff format --check .   # pass
uv run mypy                                            # pass, 201 source files
uv run pytest --ignore=tests/integration               # 1253 passed, 1 skipped (pre-existing, unrelated)
uv run pytest tests/integration                        # 271 passed, 2 skipped (pre-existing, unrelated) — see §0a for two flakiness episodes investigated and ruled out during that pass; this pass's own full run was clean, zero errors
uv run alembic heads                                   # single head: 8801080869a6
```

Zero behaviour change for EUR/USD anywhere: `FxWeekdayCalendar`,
`resolve_symbol()`'s pinned path, and every corrected orchestrator's
own resolved config/calendar for EUR/USD specifically all carry explicit
regression tests proving this (`test_market_universe_wiring.py`'s own
"EUR/USD is unaffected" tests, alongside the pre-existing suite staying
green unchanged).

## 7. Deliverable / stop point

Pushed to `origin/dev1/market-universe` — not `main`, not merged, not
stacked on the dashboard branch. Stop for owner review before merge,
matching this session's established review cadence.
