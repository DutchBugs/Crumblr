"""`LiveDecisionOrchestrator` (review 1.15 §7, review 1.16 §9), against

in-memory fakes for its three read sources and a recording fake for the
journal. Proves the control flow — skip conditions, a NO_TRADE window
persisting correctly, a halt tripping and flushing immediately — without
PostgreSQL. The end-to-end wiring against real stores and a real synthetic
series is `tests/integration/test_live_decision.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from crumblr.application.live_decision import LiveDecisionOrchestrator
from crumblr.config import PlatformConfig
from crumblr.domain.enums import ReasonCode
from crumblr.domain.models import (
    BrokerAccountSnapshot,
    BrokerPendingOrderSnapshot,
    BrokerPositionSnapshot,
    Contract,
    InstrumentSpec,
    MarketBar,
    MarketTick,
)
from crumblr.market_data.synthetic import SyntheticMarketConfig, as_market_bar, generate_ticks
from crumblr.persistence.market_data import tick_identity
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.session import InMemoryRiskSessionStore
from tests.conftest import make_broker_account_snapshot, paper_config_payload

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

SPEC = InstrumentSpec(
    canonical_symbol="EUR/USD",
    broker_symbol="EURUSD",
    currency_base="EUR",
    currency_profit="USD",
    contract_size=Decimal("100000"),
    digits=5,
    point=Decimal("0.00001"),
    tick_size=Decimal("0.00001"),
    tick_value=Decimal("1"),
    volume_min=Decimal("0.01"),
    volume_max=Decimal("100"),
    volume_step=Decimal("0.01"),
    stops_level=10,
    freeze_level=0,
    filling_modes=("IOC", "FOK"),
    trade_mode="FULL",
    captured_at_utc=NOW,
)


def config() -> PlatformConfig:
    return PlatformConfig.model_validate(paper_config_payload())


class FakeMarketDataSource:
    def __init__(self) -> None:
        self.bars: tuple[MarketBar, ...] = ()
        self.tick: MarketTick | None = None

    def recent_bars(
        self, *, canonical_symbol: str, timeframe: str, limit: int
    ) -> tuple[MarketBar, ...]:
        return self.bars[-limit:]

    def latest_tick(self, *, canonical_symbol: str) -> MarketTick | None:
        return self.tick


class FakeBrokerStateSource:
    def __init__(self) -> None:
        self.account: BrokerAccountSnapshot | None = None
        self.positions: tuple[BrokerPositionSnapshot, ...] = ()
        self.pending_orders: tuple[BrokerPendingOrderSnapshot, ...] = ()

    def latest_account_snapshot(self) -> BrokerAccountSnapshot | None:
        return self.account

    def positions_for(self, snapshot_id: UUID) -> tuple[BrokerPositionSnapshot, ...]:
        return self.positions

    def pending_orders_for(self, snapshot_id: UUID) -> tuple[BrokerPendingOrderSnapshot, ...]:
        return self.pending_orders


class FakeInstrumentSpecSource:
    def __init__(self, spec: InstrumentSpec | None = SPEC) -> None:
        self.spec = spec

    def latest(self, *, canonical_symbol: str) -> InstrumentSpec | None:
        return self.spec


class RecordingRunRecorder:
    def __init__(self) -> None:
        self.events: list[tuple[Contract, UUID, datetime, str]] = []
        self.sealed: list[object] = []
        self.flush_count = 0

    def record(
        self, payload: Contract, *, correlation_id: UUID, occurred_at_utc: datetime, source: str
    ) -> None:
        self.events.append((payload, correlation_id, occurred_at_utc, source))

    def observe(self, tick: MarketTick, bar: MarketBar | None = None) -> None:
        raise AssertionError(
            "LiveDecisionOrchestrator must never call observe() — see F-048's module docstring"
        )

    def seal(self, capsule: object) -> None:
        self.sealed.append(capsule)

    def flush(self) -> None:
        self.flush_count += 1


def synthetic_bars(count: int, *, source: str = "test:synthetic") -> tuple[MarketBar, ...]:
    """Real-shaped `MarketBar`s, generated the same way `ReplayOrchestrator`'s

    own tests get realistic history — deterministic, and with enough trend
    / volatility structure for a strategy to eventually produce a signal.
    """
    ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=count), SPEC))
    return tuple(as_market_bar(tick, SPEC, timeframe="M5") for tick in ticks)


def synthetic_tick_for(bar: MarketBar) -> MarketTick:
    """A plausible current tick priced at the last bar's close."""
    return MarketTick(
        tick_id=tick_identity(
            source=bar.source,
            canonical_symbol=bar.canonical_symbol,
            event_time_utc=bar.bar.open_time_utc,
            bid=bar.bar.close,
            ask=bar.bar.close + Decimal("0.00006"),
        ),
        source=bar.source,
        canonical_symbol=bar.canonical_symbol,
        broker_symbol=bar.broker_symbol,
        event_time_utc=bar.bar.open_time_utc + timedelta(minutes=5),
        received_time_utc=bar.bar.open_time_utc + timedelta(minutes=5),
        bid=bar.bar.close,
        ask=bar.bar.close + Decimal("0.00006"),
    )


def orchestrator(
    market_data: FakeMarketDataSource,
    broker_state: FakeBrokerStateSource,
    instrument_specs: FakeInstrumentSpecSource,
    recorder: RecordingRunRecorder,
    *,
    kill_switch: KillSwitch | None = None,
) -> LiveDecisionOrchestrator:
    return LiveDecisionOrchestrator(
        config(),
        market_data=market_data,
        broker_state=broker_state,
        instrument_specs=instrument_specs,
        recorder=recorder,
        kill_switch=kill_switch or KillSwitch(),
        session_store=InMemoryRiskSessionStore(),
        clock=lambda: NOW,
    )


class TestSkipConditions:
    """Every "nothing to decide yet" case must be a quiet skip, not a crash."""

    def test_no_instrument_spec_yet(self) -> None:
        live = orchestrator(
            FakeMarketDataSource(),
            FakeBrokerStateSource(),
            FakeInstrumentSpecSource(spec=None),
            RecordingRunRecorder(),
        )
        outcome = live.decide_once()
        assert outcome.skipped
        assert outcome.skipped_reason is not None
        assert "instrument spec" in outcome.skipped_reason

    def test_not_enough_bars(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(10)  # well under baseline_v1's minimum_bars
        live = orchestrator(
            market, FakeBrokerStateSource(), FakeInstrumentSpecSource(), RecordingRunRecorder()
        )
        outcome = live.decide_once()
        assert outcome.skipped
        assert outcome.skipped_reason is not None
        assert "bars stored" in outcome.skipped_reason

    def test_no_tick_yet(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(80)
        live = orchestrator(
            market, FakeBrokerStateSource(), FakeInstrumentSpecSource(), RecordingRunRecorder()
        )
        outcome = live.decide_once()
        assert outcome.skipped
        assert outcome.skipped_reason is not None
        assert "tick" in outcome.skipped_reason

    def test_no_broker_state_snapshot_yet(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(80)
        market.tick = synthetic_tick_for(market.bars[-1])
        live = orchestrator(
            market, FakeBrokerStateSource(), FakeInstrumentSpecSource(), RecordingRunRecorder()
        )
        outcome = live.decide_once()
        assert outcome.skipped
        assert outcome.skipped_reason is not None
        assert "broker-state snapshot" in outcome.skipped_reason

    def test_no_new_bar_since_the_last_decision_is_skipped(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(80)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        recorder = RecordingRunRecorder()
        live = orchestrator(market, broker, FakeInstrumentSpecSource(), recorder)

        first = live.decide_once()
        second = live.decide_once()

        assert not first.skipped
        assert second.skipped
        assert second.skipped_reason is not None
        assert "no new closed bar" in second.skipped_reason


class TestNoTradeWindow:
    def test_a_no_trade_window_still_persists_a_signal_and_a_capsule(self) -> None:
        market = FakeMarketDataSource()
        # Fewer bars than a trend needs to develop from a flat start — biases
        # toward NO_TRADE without depending on exact strategy internals.
        market.bars = synthetic_bars(66)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        recorder = RecordingRunRecorder()
        live = orchestrator(market, broker, FakeInstrumentSpecSource(), recorder)

        outcome = live.decide_once()

        assert not outcome.skipped
        assert outcome.capsule is not None
        assert len(recorder.sealed) == 1
        signal_events = [
            payload
            for payload, *_ in recorder.events
            if type(payload).__name__ == "SignalGenerated"
        ]
        assert len(signal_events) == 1


class TestHaltPropagation:
    def test_a_breached_loss_gate_trips_the_kill_switch_and_flushes(self) -> None:
        """A fresh session's `session_start_equity` is set from the first

        equity it ever sees (`risk_session.recover_session` cannot invent a
        loss nobody recorded) — so the drawdown only becomes visible on a
        *second* observation, after a peak has actually been established.
        """
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(80)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(
            observed_at_utc=NOW, balance=Decimal("10000"), equity=Decimal("10000")
        )
        recorder = RecordingRunRecorder()
        kill_switch = KillSwitch()
        live = orchestrator(
            market, broker, FakeInstrumentSpecSource(), recorder, kill_switch=kill_switch
        )

        live.decide_once()  # establishes the peak at 10000

        market.bars = synthetic_bars(81)  # a new closed bar, so the next call is not a no-op skip
        market.tick = synthetic_tick_for(market.bars[-1])
        broker.account = make_broker_account_snapshot(
            observed_at_utc=NOW, balance=Decimal("9000"), equity=Decimal("9000")
        )
        live.decide_once()  # a 10% drop from the established peak

        assert kill_switch.is_halted
        assert recorder.flush_count >= 1
        halted_events = [
            payload for payload, *_ in recorder.events if type(payload).__name__ == "SystemHalted"
        ]
        assert len(halted_events) == 1

    def test_a_sticky_halt_is_never_re_tripped(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(80)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        recorder = RecordingRunRecorder()
        kill_switch = KillSwitch()
        kill_switch.trip(
            reason_codes=(ReasonCode.MAX_DRAWDOWN,),
            tripped_by="test-setup",
            occurred_at_utc=NOW,
        )
        live = orchestrator(
            market, broker, FakeInstrumentSpecSource(), recorder, kill_switch=kill_switch
        )

        live.decide_once()

        halted_events = [
            payload for payload, *_ in recorder.events if type(payload).__name__ == "SystemHalted"
        ]
        assert halted_events == []
