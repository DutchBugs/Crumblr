"""A second market must not silently trade against EUR/USD's platform

defaults (Market Universe, ADR-022, owner correction 2026-09-07).

`config.risk_for(symbol)`/`.execution_for(symbol)`/`risk.calendars
.calendar_for(asset_class)` existing as functions was never the claim in
question — `tests/unit/test_config.py` already proves the merge/lookup
logic in isolation. What these tests prove instead is that the real
orchestrators actually *call* them for the market they were constructed
against, rather than reading `config.risk`/`config.execution` (the
platform-wide default) or defaulting every calendar to FX — the gap the
owner's 2026-09-07 review named directly: "een tweede markt mag niet stil
de globale EUR/USD-defaults gebruiken."
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from crumblr.agent_gateway.evidence import build_agent_context_evidence
from crumblr.application.decision_window import InMemoryDecisionWindowStore
from crumblr.application.live_decision import LiveDecisionOrchestrator
from crumblr.application.orchestration import ReplayOrchestrator
from crumblr.config import PlatformConfig
from crumblr.domain.enums import DataQuality, Environment, IncidentStatus, ReasonCode, SessionState
from crumblr.domain.models import InstrumentSpec
from crumblr.mt5_gateway.simulated import SimulatedBroker
from crumblr.risk.calendars import AlwaysOpenCalendar, FxWeekdayCalendar
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.session import InMemoryRiskLedgerLock, InMemoryRiskSessionStore, RiskSessionState
from crumblr.trading_agent.sessions import trading_day
from tests.conftest import (
    FIXED_NOW,
    make_account_state,
    make_instrument_spec,
    make_intent,
    make_snapshot,
    paper_config_payload,
)
from tests.unit.test_agent_decision_path import FakePortfolioStateProvider
from tests.unit.test_agent_decision_path import RecordingRunRecorder as AgentRecordingRunRecorder
from tests.unit.test_live_decision import (
    FakeBrokerStateSource,
    FakeInstrumentSpecSource,
    FakeMarketDataSource,
    RecordingRunRecorder,
)

BTC_OVERRIDE_DAILY_LOSS = Decimal("0.01")
"""Deliberately tighter than the platform default (0.04) so a test can

prove *which* threshold was actually enforced by observing the outcome,
not by reaching into private state."""

BTC_OVERRIDE_SLIPPAGE = 5
"""Deliberately different from the platform default (20)."""


def two_market_payload() -> dict[str, Any]:
    """The platform default (EUR/USD, unchanged) plus a second market,

    BTC/USD, `enabled: true` **for this test payload only** — the shipped
    `config/paper.yaml` keeps BTC/USD `enabled: false` per the owner's
    explicit instruction; this is a local, in-memory config object that
    never touches disk.
    """
    payload = paper_config_payload()
    payload["markets"].append(
        {
            "canonical_symbol": "BTC/USD",
            "enabled": True,
            "asset_class": "CRYPTO",
            "broker_symbol": "BTCUSD",
            "risk_overrides": {"max_daily_loss": str(BTC_OVERRIDE_DAILY_LOSS)},
            "execution_overrides": {"max_slippage_points": BTC_OVERRIDE_SLIPPAGE},
        }
    )
    return payload


def two_market_config() -> PlatformConfig:
    return PlatformConfig.model_validate(two_market_payload())


def btc_spec() -> InstrumentSpec:
    return make_instrument_spec(
        canonical_symbol="BTC/USD",
        broker_symbol="BTCUSD",
        currency_base="BTC",
        currency_profit="USD",
        digits=2,
        point=Decimal("0.01"),
        tick_size=Decimal("0.01"),
    )


class TestReplayOrchestratorUsesTheMarketsOwnConfig:
    def test_a_second_markets_risk_overrides_reach_the_orchestrator(self) -> None:
        config = two_market_config()
        spec = btc_spec()
        broker = SimulatedBroker(spec, starting_balance=Decimal("10000"), server="DemoBroker-Demo")
        orchestrator = ReplayOrchestrator(config, spec, broker, starting_equity=Decimal("10000"))

        assert orchestrator._risk_config.max_daily_loss == BTC_OVERRIDE_DAILY_LOSS
        assert orchestrator._risk_config.max_daily_loss != config.risk.max_daily_loss
        assert orchestrator._execution_config.max_slippage_points == BTC_OVERRIDE_SLIPPAGE
        assert (
            orchestrator._execution_config.max_slippage_points
            != config.execution.max_slippage_points
        )

    def test_a_crypto_markets_calendar_is_not_the_fx_default(self) -> None:
        config = two_market_config()
        spec = btc_spec()
        broker = SimulatedBroker(spec, starting_balance=Decimal("10000"), server="DemoBroker-Demo")
        orchestrator = ReplayOrchestrator(config, spec, broker, starting_equity=Decimal("10000"))

        assert isinstance(orchestrator._calendar, AlwaysOpenCalendar)

    def test_eur_usd_is_unaffected_and_still_uses_the_fx_calendar(self) -> None:
        """The regression guard: adding a second market must not change

        the first market's own resolved config/calendar."""
        config = two_market_config()
        spec = make_instrument_spec()
        broker = SimulatedBroker(spec, starting_balance=Decimal("10000"), server="DemoBroker-Demo")
        orchestrator = ReplayOrchestrator(config, spec, broker, starting_equity=Decimal("10000"))

        assert orchestrator._risk_config.max_daily_loss == config.risk.max_daily_loss
        assert isinstance(orchestrator._calendar, FxWeekdayCalendar)


def _live_orchestrator(
    config: PlatformConfig, *, canonical_symbol: str
) -> LiveDecisionOrchestrator:
    return LiveDecisionOrchestrator(
        config,
        market_data=FakeMarketDataSource(),
        broker_state=FakeBrokerStateSource(),
        instrument_specs=FakeInstrumentSpecSource(spec=btc_spec()),
        recorder=RecordingRunRecorder(),
        kill_switch=KillSwitch(),
        session_store=InMemoryRiskSessionStore(),
        risk_ledger_lock=InMemoryRiskLedgerLock(),
        decision_window_store=InMemoryDecisionWindowStore(),
        canonical_symbol=canonical_symbol,
        clock=lambda: FIXED_NOW,
    )


class TestLiveDecisionOrchestratorUsesTheMarketsOwnConfig:
    def test_a_second_markets_risk_overrides_reach_the_orchestrator(self) -> None:
        config = two_market_config()
        live = _live_orchestrator(config, canonical_symbol="BTC/USD")

        assert live._risk_config.max_daily_loss == BTC_OVERRIDE_DAILY_LOSS
        assert live._execution_config.max_slippage_points == BTC_OVERRIDE_SLIPPAGE
        assert isinstance(live._calendar, AlwaysOpenCalendar)

    def test_eur_usd_is_unaffected(self) -> None:
        config = two_market_config()
        live = _live_orchestrator(config, canonical_symbol="EUR/USD")

        assert live._risk_config.max_daily_loss == config.risk.max_daily_loss
        assert isinstance(live._calendar, FxWeekdayCalendar)


def _evaluate_btc_with(config: PlatformConfig, *, prior_loss_session_store: bool) -> Any:
    """One BTC/USD intent, optionally with a recorded prior-session loss

    between the platform default `max_daily_loss` (0.04) and BTC/USD's
    own override (0.01) — `0.02`, computed the same way
    `test_agent_decision_path.py
    ::test_a_recorded_prior_loss_this_session_reaches_the_daily_loss_gate`
    already does (`session_start` sized so live equity implies exactly
    that loss fraction).
    """
    from crumblr.agent_gateway.decision_path import evaluate_agent_trade_intent

    spec = btc_spec()
    snapshot = make_snapshot(symbol="BTC/USD", symbol_spec_version=spec.spec_version)
    equity_now = Decimal("10000")

    if prior_loss_session_store:
        session_start = equity_now / (Decimal("1") - Decimal("0.02"))
        session_store = InMemoryRiskSessionStore(
            initial=RiskSessionState(
                canonical_symbol="BTC/USD",
                trading_day=trading_day(FIXED_NOW),
                session_start_equity=session_start,
                current_equity=session_start,
                peak_equity=session_start,
                realized_pnl=Decimal("0"),
                max_drawdown_fraction=Decimal("0"),
                max_session_loss_fraction=Decimal("0"),
                open_risk_fraction=Decimal("0"),
                open_position_count=0,
                recorded_at_utc=FIXED_NOW,
            )
        )
    else:
        session_store = InMemoryRiskSessionStore()

    kill_switch = KillSwitch()
    intent = make_intent(strategy_id="external_agent", symbol="BTC/USD", model_version=None)
    result = evaluate_agent_trade_intent(
        intent,
        outcome_id=uuid4(),
        strategy_version="assignment-artifact-hash-v1",
        snapshot=snapshot,
        spec=spec,
        features=build_agent_context_evidence(
            symbol="BTC/USD",
            computed_at_utc=snapshot.event_time_utc,
            market_snapshot_id=snapshot.snapshot_id,
            instrument_spec_version=spec.spec_version,
            session_state=SessionState.OPEN,
            data_quality=DataQuality.GOOD,
        ),
        config=config,
        portfolio_state=FakePortfolioStateProvider(
            account=make_account_state(equity=equity_now, balance=equity_now)
        ),
        session_store=session_store,
        risk_ledger_lock=InMemoryRiskLedgerLock(),
        kill_switch=kill_switch,
        recorder=AgentRecordingRunRecorder(),
        environment=Environment.PAPER,
        code_commit="test-commit",
        now=FIXED_NOW,
        incident_status=IncidentStatus.CLEAR,
    )
    return kill_switch, result


class TestAgentDecisionPathUsesTheMarketsOwnConfig:
    """`agent_gateway/decision_path.py::_risk_context` (Dev-2-owned file,

    mechanical fix applied by Dev 1) — proved behaviourally through the
    public `evaluate_agent_trade_intent` entry point rather than by
    reaching into the private helper, matching this file's own existing
    style (`tests/unit/test_agent_decision_path.py`).
    """

    def test_a_btc_intent_is_refused_for_session_blackout(self) -> None:
        """BTC/USD has no owner-approved session policy

        (`AlwaysOpenCalendar`), so every intent for it is refused with
        `SESSION_BLACKOUT` regardless of anything else — proving the
        calendar wiring reaches this module."""
        config = two_market_config()
        _, result = _evaluate_btc_with(config, prior_loss_session_store=False)

        assert result.risk_decision is not None
        assert ReasonCode.SESSION_BLACKOUT in result.risk_decision.reason_codes

    def test_a_recorded_prior_loss_only_halts_against_the_markets_own_override(
        self,
    ) -> None:
        """A 2% recorded prior-session loss: tolerated by the platform

        default `max_daily_loss` (4%), but must halt against BTC/USD's
        own override (1%) — proving `risk_overrides` actually reached
        this module's `RiskContext`, not merely that the function exists.
        """
        config = two_market_config()
        kill_switch, result = _evaluate_btc_with(config, prior_loss_session_store=True)

        assert kill_switch.is_halted
        assert ReasonCode.DAILY_LOSS_LIMIT in kill_switch.active_reasons
        assert result.risk_decision is not None
        assert ReasonCode.DAILY_LOSS_LIMIT in result.risk_decision.reason_codes
