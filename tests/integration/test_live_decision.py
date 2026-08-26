"""`LiveDecisionOrchestrator` (F-048) end to end, against real PostgreSQL.

`tests/unit/test_live_decision.py` proves the control flow against fakes;
this file proves the wiring — real `MarketDataStore`/`BrokerStateStore`/
`InstrumentSpecStore` reads, a real `JournalRecorder` write, and a real
`KillSwitch`/risk-session round trip — using the same deterministic
synthetic series `ReplayOrchestrator`'s own persistence tests
(`test_orchestrator_persistence.py`) already rely on to reliably produce
real decisions, not just NO_TRADE windows.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from scripts.run_replay import build_instrument_spec
from sqlalchemy import Engine

from crumblr.application.bootstrap import DurableRuntime, build_durable_runtime
from crumblr.application.broker_state import BrokerStateObservation
from crumblr.application.live_decision import LiveDecisionOrchestrator
from crumblr.config import PlatformConfig, load_config
from crumblr.domain.enums import Environment, SnapshotCompleteness
from crumblr.domain.models import BrokerAccountSnapshot, InstrumentSpec
from crumblr.domain.timeutils import UtcDatetime
from crumblr.market_data.synthetic import (
    GeneratedTick,
    SyntheticMarketConfig,
    as_market_bar,
    as_market_tick,
    generate_ticks,
)
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.decision_window import PostgresDecisionWindowStore
from crumblr.persistence.engine import DEFAULT_TEST_URL
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import EventJournal
from crumblr.persistence.market_data import MarketDataStore
from crumblr.risk.kill_switch import KillSwitch

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BALANCE = Decimal("10000")
BARS = 400


@pytest.fixture(scope="module")
def config() -> PlatformConfig:
    """`baseline_v1`, the same substitution `test_orchestrator_persistence.py`

    makes — `ict_v1` is deliberately too selective (three setups in twelve
    thousand bars) for a bounded integration test to reliably exercise the
    risk/supervisor path.
    """
    shipped = load_config(Environment.PAPER, config_dir=REPO_ROOT / "config")
    agent = shipped.trading_agent.model_copy(update={"strategy_id": "baseline_v1"})
    return shipped.model_copy(update={"trading_agent": agent})


def arm(kill_switch: KillSwitch) -> KillSwitch:
    kill_switch.reset(operator="integration-test", incident_note="arming a fresh test database")
    return kill_switch


def flat_account_snapshot(
    config: PlatformConfig, *, observed_at_utc: UtcDatetime
) -> BrokerAccountSnapshot:
    """A flat, config-matching account snapshot — this orchestrator decides

    against one static snapshot per call, so the whole series is judged
    against the same broker state a real, unchanging demo account would
    show.
    """
    return BrokerAccountSnapshot(
        snapshot_id=uuid4(),
        observed_at_utc=observed_at_utc,
        recorded_at_utc=observed_at_utc,
        environment=config.environment,
        server=config.account_guard.expected_server,
        account_ref="0" * 16,
        currency=config.account_guard.expected_currency or "EUR",
        leverage=config.account_guard.expected_leverage or 30,
        margin_mode="RETAIL_HEDGING",
        balance=BALANCE,
        equity=BALANCE,
        profit=Decimal("0"),
        margin=Decimal("0"),
        margin_free=BALANCE,
        margin_level=None,
        account_trade_allowed=True,
        terminal_trade_allowed=True,
        position_set_state=SnapshotCompleteness.COMPLETE,
        pending_order_set_state=SnapshotCompleteness.COMPLETE,
    )


def seed(
    engine: Engine, spec: InstrumentSpec, ticks: list[GeneratedTick], config: PlatformConfig
) -> None:
    """Populate the real stores as if `mt5_live_reader.py` had been running:

    the instrument spec, the full bar/tick history, and one flat broker-state
    snapshot observed after the last bar.
    """
    InstrumentSpecStore(engine).record(spec)
    market_store = MarketDataStore(engine)
    market_store.record_ticks([as_market_tick(tick, spec) for tick in ticks])
    market_store.record_bars([as_market_bar(tick, spec, timeframe="M5") for tick in ticks])
    BrokerStateStore(engine).record(
        BrokerStateObservation(
            account=flat_account_snapshot(config, observed_at_utc=ticks[-1].received_time_utc),
            positions=(),
            pending_orders=(),
        )
    )


def orchestrator_for(
    config: PlatformConfig, runtime: DurableRuntime, ticks: list[GeneratedTick]
) -> LiveDecisionOrchestrator:
    engine = runtime.engine
    return LiveDecisionOrchestrator(
        config,
        market_data=MarketDataStore(engine),
        broker_state=BrokerStateStore(engine),
        instrument_specs=InstrumentSpecStore(engine),
        recorder=runtime.recorder,
        kill_switch=runtime.kill_switch,
        session_store=runtime.session_store,
        decision_window_store=PostgresDecisionWindowStore(engine),
        clock=lambda: ticks[-1].received_time_utc,
    )


class TestLiveDecisionEndToEnd:
    def test_a_full_synthetic_series_produces_real_decisions_and_persists_them(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        # `engine` is not used directly — depending on it (the same fixture
        # `test_orchestrator_persistence.py` depends on) is what makes the
        # schema exist on `DEFAULT_TEST_URL` before `build_durable_runtime`
        # opens its own separate connection to the same database.
        del engine
        runtime = build_durable_runtime(
            environment=config.environment,
            state_file=tmp_path / "safety_state.json",
            url=DEFAULT_TEST_URL,
        )
        arm(runtime.kill_switch)

        spec = build_instrument_spec()
        ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=BARS), spec))
        seed(runtime.engine, spec, ticks, config)

        # One call: the orchestrator decides against the full bar history
        # already persisted, exactly as it would if LiveReader had been
        # running for a while before this process started.
        outcome = orchestrator_for(config, runtime, ticks).decide_once()

        assert not outcome.skipped
        assert outcome.capsule is not None
        assert outcome.capsule.feature_set_version

        events = EventJournal(runtime.engine).recent(limit=10)
        event_types = {event.event_type.value for event in events}
        assert "SignalGenerated" in event_types

        runtime.dispose()

    def test_repeated_calls_only_decide_on_genuinely_new_bars(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        del engine  # see the comment in the test above
        runtime = build_durable_runtime(
            environment=config.environment,
            state_file=tmp_path / "safety_state.json",
            url=DEFAULT_TEST_URL,
        )
        arm(runtime.kill_switch)

        spec = build_instrument_spec()
        ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=80), spec))
        seed(runtime.engine, spec, ticks, config)

        live = orchestrator_for(config, runtime, ticks)
        first = live.decide_once()
        second = live.decide_once()

        assert not first.skipped
        assert second.skipped
        assert second.skipped_reason == "no new closed bar since the last decision"

        runtime.dispose()

    def test_a_fresh_orchestrator_against_the_same_database_does_not_redecide(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """F-054, proven against real PostgreSQL rather than a fake store:

        a second `LiveDecisionOrchestrator`, constructed independently
        against the same database — exactly what a restarted process looks
        like — must not re-decide the window the first one already sealed.
        """
        del engine  # see the comment in the first test above
        runtime = build_durable_runtime(
            environment=config.environment,
            state_file=tmp_path / "safety_state.json",
            url=DEFAULT_TEST_URL,
        )
        arm(runtime.kill_switch)

        spec = build_instrument_spec()
        ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=80), spec))
        seed(runtime.engine, spec, ticks, config)

        first_process = orchestrator_for(config, runtime, ticks)
        first_outcome = first_process.decide_once()
        assert not first_outcome.skipped

        second_process = orchestrator_for(config, runtime, ticks)
        second_outcome = second_process.decide_once()

        assert second_outcome.skipped
        assert second_outcome.skipped_reason == "no new closed bar since the last decision"

        runtime.dispose()
