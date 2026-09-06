# ADR-022 — Market Universe: multi-market Core, Risk & Policy

**Status:** ACCEPTED 2026-09-06 — Dev 1's own branch, `dev1/market-universe`,
off `origin/main` @ `1971d7a`. Not merged; stop for owner review per the
owner's own work-order cadence (see §7).
**Date:** 2026-09-04 (work order) — 2026-09-06 (this record)
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

## 1. The tension this ADR names rather than silently resolves

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
`risk/policies.py`) needs no change and no behaviour change. When
`calendar.weekly_close()` returns `None`, `phase_at` resolves to `OPEN`
whenever the market is open, without ever evaluating `IntradayPolicy`'s
offsets — the same "a stated choice, not a default" discipline
`IntradayPolicy.disabled()` already uses, for the same reason: there is
nothing owner-approved to measure those offsets against yet.
`time_until_weekly_close` now returns `timedelta | None` for the same
reason. This is the one genuine owner-policy gap this branch leaves open
on purpose, named here rather than papered over — every non-FX market's
config implicitly behaves as `IntradayPolicy.disabled()` until an owner
makes a real decision and a new `TradingCalendar` encodes it.

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
   `AlwaysOpenCalendar`. Every such market trades under an implicit
   `IntradayPolicy.disabled()` until a real owner decision exists.
2. **Registration-time Market Universe validation.**
   `TradingAssignmentStore.register()` (Dev-2-owned,
   `agent_gateway/gateway.py`/`stores.py`) never checks a proposed
   `canonical_symbol` against `PlatformConfig.enabled_symbols()`/
   `market_for()` at registration time — the only existing enforcement is
   late, at intent-time (§3.6). Not fixed here (out of this branch's file
   scope); a coordination message describing the gap and recommending
   Dev 2 add the check in their own file was sent 2026-09-06 (see §5).
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
on Dev-2-owned semantics).

## 6. Verification

```
uv run ruff check . && uv run ruff format --check .   # pass
uv run mypy                                            # pass, 199 source files
uv run pytest --ignore=tests/integration               # 1242 passed, 1 skipped (pre-existing, unrelated)
uv run pytest tests/integration                        # see status.md for the run this ADR shipped with
uv run alembic heads                                   # single head: 8801080869a6
```

Zero behaviour change for EUR/USD anywhere: `FxWeekdayCalendar` and
`resolve_symbol()`'s pinned path both carry explicit regression tests
proving this.

## 7. Deliverable / stop point

Pushed to `origin/dev1/market-universe` — not `main`, not merged, not
stacked on the dashboard branch. Stop for owner review before merge,
matching this session's established review cadence.
