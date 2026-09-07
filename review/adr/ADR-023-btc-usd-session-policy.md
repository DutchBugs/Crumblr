# ADR-023 — BTC/USD session policy (Enablement Readiness)

**Status:** ACCEPTED 2026-09-08 — Dev 1's own branch,
`dev1/market-universe`, off `main` @ `966bf6a` (post Market Universe
merge). Not merged; stop for owner review per the owner's own work-order
cadence.
**Date:** 2026-09-08
**Drivers:** Owner work order, verbatim: *"BTC/USD Enablement Readiness —
session-policy implementation + terminal validation + fail-closed
acceptance tests; no trading authorization changes."* Directly closes
ADR-022 §4 item 1 (no owner-approved session policy existed for any 24/7
asset class) and item 3 (BTC/USD's real-terminal confirmation) for this
one market specifically.
**Supersedes:** nothing. Narrows the open gap ADR-022 §4/`review
/DEVIATIONS.md` D-060 named, for BTC/USD only.
**Implementation:** `config.py` (`MarketConfig.session_policy_approved`),
`risk/calendars.py` (`TradingCalendar.session_policy_approved`,
`AlwaysOpenCalendar.__init__`, `calendar_for()`), `risk/trading_window.py`
(`phase_at`'s fail-closed branch), `application/{orchestration,
live_decision,execution,paper_lite}.py`, `agent_gateway/decision_path.py`
(the five `calendar_for()` call sites), `config/paper.yaml`.

---

## 1. The decision the owner was actually asked to make

`AlwaysOpenCalendar` (ADR-022) reports `weekly_close() -> None` for any
24/7 asset class — "no weekly-close concept," deliberately not a claim
about whether trading should be permitted. The prior corrective pass
(ADR-022 §0, D-060) made `phase_at` fail closed on that `None` — no entry
permission for any such market, because nobody had decided what the
policy for a continuously-trading asset *should be*. That gap was named,
not resolved: `risk/calendars.py`'s own module docstring asked "should
crypto have a weekly flatten requirement at all? what, if anything,
replaces 'weekend'?"

This work order supplied the answer, but only after being asked directly
— the work order's own text named "session-policy implementation" as a
task without stating what the policy actually is, and every established
precedent in this project (D1.4/D1.5's own risk-fraction and
weekly-close numbers, F-055's spec-pin discipline, D-053's canary-
fraction precedent) holds that an owner risk/session-policy number or
shape must never be invented by the implementer. Presented three options
directly: (1) 24/7, no weekly close at all; (2) a real periodic flatten
deadline, needing actual numbers; (3) build the readiness scaffolding
only, leave the policy decision for later. **The owner chose (1): BTC/USD
trades continuously, no weekly close, no flatten deadline.**

## 2. Design

### 2.1 The approval is per-market, not per-asset-class

The naive fix — flip `AlwaysOpenCalendar`'s own fail-closed default, or
change what `CRYPTO` maps to in `calendar_for()` — would silently extend
the owner's BTC/USD-specific decision to every future crypto market,
including ones the owner has never looked at. The owner decided *for
BTC/USD*, not for "all crypto forever." So the approval lives on
`MarketConfig`, keyed to the exact canonical symbol, the same "explicit,
git-visible act, not inferred" shape `expected_spec_version` (F-055)
already uses:

```python
class MarketConfig(ConfigSection):
    ...
    session_policy_approved: bool = False
```

`TradingCalendar` gained a matching `session_policy_approved: bool`
property. `FxWeekdayCalendar` reports `True` unconditionally (D1.5 is
already a real, owner-approved policy; `phase_at` never actually
consults this for FX since `weekly_close()` never returns `None` there —
reported `True` anyway so nothing downstream could misread an
already-approved calendar as unapproved). `AlwaysOpenCalendar` now takes
`session_policy_approved` in its constructor, defaulting `False`
(fail-closed, unchanged), and reports back whatever it was constructed
with.

`calendar_for()` gained a matching keyword-only parameter:

```python
def calendar_for(
    asset_class: AssetClass, *, session_policy_approved: bool = False
) -> TradingCalendar:
    if asset_class in (AssetClass.FX, AssetClass.METAL):
        return FxWeekdayCalendar()
    return AlwaysOpenCalendar(session_policy_approved=session_policy_approved)
```

`_CALENDARS_BY_ASSET_CLASS`'s prior fixed-singleton-dict design was
retired — `AlwaysOpenCalendar` is now constructed fresh per call, from
the *calling market's own* `session_policy_approved`, not shared across
every market on that asset class. Every one of the five real call sites
(`ReplayOrchestrator`, `LiveDecisionOrchestrator`, `ExecutionOrchestrator`,
`agent_gateway/decision_path.py`, `application/paper_lite.py`) now passes
`session_policy_approved=market.session_policy_approved`.

### 2.2 `phase_at`'s fail-closed branch

```python
close = calendar.weekly_close(moment)
if close is None:
    return SessionPhase.OPEN if calendar.session_policy_approved else SessionPhase.CLOSED
```

Checked before `policy.enabled`, unchanged from the prior corrective
pass's ordering — a globally-enabled `IntradayPolicy` still cannot
accidentally permit entries on an unapproved calendar, and now an
*approved* calendar resolves `OPEN` regardless of whether the platform's
`IntradayPolicy` happens to be enabled or disabled (there is nothing to
measure offsets against either way — `OPEN` unconditionally is the
correct reading of "trades continuously, no restriction").

### 2.3 Scope boundary — what this decision does *not* do

Named explicitly, matching the work order's own "no trading authorization
changes":

- **`MarketConfig.enabled` is unchanged** — still `false` for BTC/USD in
  `config/paper.yaml`. `session_policy_approved` and `enabled` are
  independent flags; approving a session policy does not enable trading.
- **`expected_spec_version` stays unset.** Real terminal validation (§3
  below) confirmed the broker-symbol pin is correct and captured a real
  observed `InstrumentSpec`, but F-055's own discipline — "set this only
  after a real observation has been reviewed and accepted... never
  invented" — was not satisfied by this pass: the owner has not yet
  reviewed and approved that specific observation. `reconcile()`
  therefore still refuses BTC/USD with `RECONCILIATION_UNKNOWN` at
  intent-time, proven directly in §4.
- **`order_send` stays NO-GO.** `ExecutionConfig.submission_enabled`/
  `feedback_2_0_approved` are untouched, platform-wide, unaffected by any
  per-market field.
- **No other market's calendar changed.** `EUR/USD` (`FxWeekdayCalendar`)
  is provably unaffected — every existing regression test for it still
  passes unchanged. A hypothetical second `CRYPTO` market would still
  default to `session_policy_approved: false` and fail closed exactly as
  BTC/USD did before this decision.

## 3. Real terminal validation

Run against the real Pepperstone MT5 terminal on this host (`terminal64
.exe` already running, logged into the real DEMO account via
`.env`-supplied credentials, `PepperstoneUK-Demo`):

```
uv sync --extra mt5
uv run python scripts/mt5_probe.py --canonical-symbol "BTC/USD" --sanitized-json <path>
```

**Read-only, non-sending** — `scripts/mt5_probe.py` uses
`ReadOnlyMt5Gateway`, whose execution methods raise (D-036); there is no
order interface reachable from this script, confirmed by its own module
docstring and unchanged by this pass.

Result: exactly one candidate symbol resolved, `BTCUSD` — matching the
`broker_symbol: BTCUSD` pin already recorded in `config/paper.yaml` since
the original Market Universe slice. Real observed instrument facts
(account number redacted; full sanitized report not committed to the
repository, matching F-031's own discipline — cite these figures directly
rather than attaching the file):

| Field | Observed |
|---|---|
| `path` | `Retail\Cryptocurrencies\Major\BTCUSD` |
| `digits` | 2 |
| `point` | 0.01 |
| `tick_size` | 0.01 |
| `tick_value` | 0.00860244653579478 |
| `contract_size` | 1.0 |
| `volume_min` / `max` / `step` | 0.01 / 22.0 / 0.01 |
| `stops_level` / `freeze_level` | 0 / 0 |
| `filling_modes` | `('IOC',)` |
| `spread_points` (observed, float spread) | 1500 |
| `swap_long` / `swap_short` | -11.5 / 1.09 |
| Account `margin_mode` | `RETAIL_HEDGING` (matches EUR/USD's own, already-recorded value) |
| Terminal `trade_allowed` (AlgoTrading) | `False` — unchanged, never enabled by this or any prior pass |

**Not acted on beyond confirming the symbol pin.** These are the real
numbers a future `expected_spec_version` pin would be reviewed against —
recorded here as the evidence, not silently written into config. Pinning
`expected_spec_version` is a separate, later owner-reviewed act (F-055),
outside this work order's stated scope ("no trading authorization
changes").

## 4. Fail-closed acceptance tests

- `tests/unit/test_risk_calendars.py` —
  `TestAlwaysOpenCalendarSessionPolicyApproval` (defaults unapproved,
  reports exactly what it was constructed with),
  `TestFxWeekdayCalendarIsAlwaysApproved`, `TestCalendarFor` (extended:
  `CRYPTO` defaults unapproved, threads the flag through, `FX`/`METAL`
  ignore it).
- `tests/unit/test_trading_window.py` —
  `TestAnApprovedNoWeeklyCloseCalendarResolvesOpen`: `OPEN` at every
  fixture moment, regardless of `IntradayPolicy.enabled`; entries
  permitted; flatness never required; **a second, unapproved
  `AlwaysOpenCalendar` instance still fails closed** — the core proof
  that approval is per-market, not per-type.
- `tests/unit/test_config.py` — `session_policy_approved` defaults
  `False`; can be set `True`; **does not imply `enabled`** —
  `enabled_symbols()` still excludes a `session_policy_approved: true`
  market that is `enabled: false`.
- `tests/unit/test_risk_engine.py` —
  `test_an_approved_always_open_calendar_no_longer_hits_session_blackout`,
  `test_approving_one_calendar_instance_does_not_approve_another` — the
  core enforcement point (`policies.evaluate()`) every real orchestrator
  funnels through.
- `tests/unit/test_market_universe_wiring.py` —
  `TestAgentDecisionPathUsesTheMarketsOwnConfig` extended: an approved
  BTC/USD intent through `evaluate_agent_trade_intent` no longer carries
  `SESSION_BLACKOUT`; the unapproved case (kept, retitled) still does.
  `TestReplayOrchestratorUsesTheMarketsOwnConfig`: the real constructed
  `._calendar.session_policy_approved` matches the market's own config,
  both ways.
- `tests/integration/test_market_universe_wiring.py` —
  `TestBtcUsdEnablementReadiness` (real PostgreSQL, real fake-MT5, a real
  `ExecutionOrchestrator.run_once()` pass): an approved BTC/USD capsule
  clears the session-policy gate (no longer `INELIGIBLE`/
  `SESSION_BLACKOUT`) but is still correctly refused one gate later,
  `RECONCILIATION_BLOCKED`/`RECONCILIATION_UNKNOWN` (the deliberately
  unpinned instrument-spec baseline, §2.3) — proving this pass touched
  exactly the one gate it should have and no other. The unapproved case
  is retested identically for contrast.

## 5. Verification

```
uv run ruff check . && uv run ruff format --check .   # pass
uv run mypy                                            # pass, 201 source files
uv run pytest --ignore=tests/integration               # 1271 passed, 1 skipped (pre-existing, unrelated)
uv run pytest tests/integration                        # 273 passed, 2 skipped (pre-existing, unrelated), 0 errors
```

While iterating, repeated rapid back-to-back `pytest tests/integration
/test_market_universe_wiring.py` invocations reproduced the same
real-Postgres connection-pool/test-isolation flakiness already
documented in ADR-022 §0a/§0b — confirmed cleared on spaced-out runs,
not a regression from this change. One clean, single full-suite
integration run (above) confirms zero errors against the real commit.

## 6. What remains open — named, not silently resolved

1. **`expected_spec_version` pin.** The real observation in §3 is
   evidence, not an approval — F-055's own discipline requires a human
   to review and accept it first. Until then, BTC/USD's intent-time
   reconciliation stays `RECONCILIATION_UNKNOWN` (proven in §4), and no
   capsule for it can reach `SUBMISSION_STARTED` regardless of the
   session-policy gate now being open.
2. **`MarketConfig.enabled`.** Still `false`. Flipping it is a separate,
   later owner decision, not implied by a session-policy approval.
3. **Every other item ADR-022 §4 already named** — the Market Capability
   Matrix build.md §24 calls for, concurrent multi-market orchestration —
   unaffected by this pass, still open.

## 7. Deliverable / stop point

Not merged. Stop for owner review before merge, matching this session's
established cadence.
