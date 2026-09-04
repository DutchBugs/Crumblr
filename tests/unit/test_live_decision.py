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
from typing import Any
from uuid import UUID

from crumblr.application.decision_window import InMemoryDecisionWindowStore
from crumblr.application.live_decision import LiveDecisionOrchestrator
from crumblr.config import PlatformConfig
from crumblr.domain.enums import ReasonCode
from crumblr.domain.events import SystemHalted
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
from crumblr.risk.session import InMemoryRiskLedgerLock, InMemoryRiskSessionStore
from crumblr.trading_agent.base import FeatureEvidence
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
        self.recorded_features: list[FeatureEvidence] = []
        self.flush_count = 0

    def record(
        self, payload: Contract, *, correlation_id: UUID, occurred_at_utc: datetime, source: str
    ) -> None:
        self.events.append((payload, correlation_id, occurred_at_utc, source))

    def observe(self, tick: MarketTick, bar: MarketBar | None = None) -> None:
        raise AssertionError(
            "LiveDecisionOrchestrator must never call observe() — see F-048's module docstring"
        )

    def record_features(self, features: FeatureEvidence) -> None:
        self.recorded_features.append(features)

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
    session_store: InMemoryRiskSessionStore | None = None,
    decision_window_store: InMemoryDecisionWindowStore | None = None,
) -> LiveDecisionOrchestrator:
    return LiveDecisionOrchestrator(
        config(),
        market_data=market_data,
        broker_state=broker_state,
        instrument_specs=instrument_specs,
        recorder=recorder,
        kill_switch=kill_switch or KillSwitch(),
        session_store=session_store or InMemoryRiskSessionStore(),
        risk_ledger_lock=InMemoryRiskLedgerLock(),
        decision_window_store=decision_window_store or InMemoryDecisionWindowStore(),
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


class SpyRiskLedgerLock:
    """Counts `held()` calls, otherwise delegates to a real in-memory lock

    (ADR-021) — proves the lock is genuinely acquired every cycle, not
    merely that the redesigned control flow happens to still work without
    it."""

    def __init__(self) -> None:
        self._inner = InMemoryRiskLedgerLock()
        self.held_calls: list[str] = []

    def held(self, canonical_symbol: str) -> Any:
        self.held_calls.append(canonical_symbol)
        return self._inner.held(canonical_symbol)


class TestADR021RiskLedgerPersistsEveryCycle:
    """AG-012/Phase C: before this ADR, `_persist_session()` only ran on a

    cycle that reached a risk-`PASS` decision — a NO_TRADE run of any
    length left the durable checkpoint stale, independent of the AG-012
    race itself (a real, separate gap named and closed by the same
    redesign, `review/adr/ADR-021-single-risk-authority-lock.md` §3).
    """

    def test_a_no_trade_cycle_still_persists_and_acquires_the_lock(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(66)  # biased toward NO_TRADE, as above
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        session_store = InMemoryRiskSessionStore()
        lock = SpyRiskLedgerLock()

        live = LiveDecisionOrchestrator(
            config(),
            market_data=market,
            broker_state=broker,
            instrument_specs=FakeInstrumentSpecSource(),
            recorder=RecordingRunRecorder(),
            kill_switch=KillSwitch(),
            session_store=session_store,
            risk_ledger_lock=lock,
            decision_window_store=InMemoryDecisionWindowStore(),
            clock=lambda: NOW,
        )
        outcome = live.decide_once()

        assert not outcome.skipped
        assert lock.held_calls == ["EUR/USD"]
        assert session_store.saves == 1

    def test_the_ledger_is_recovered_fresh_every_cycle_not_cached(self) -> None:
        """A second `decide_once()` call must observe whatever the first

        one persisted — proves `self._ledger` is no longer retained
        in-process across calls, the actual behavioural change ADR-021
        makes (§3)."""
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(66)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        session_store = InMemoryRiskSessionStore()
        lock = SpyRiskLedgerLock()

        live = LiveDecisionOrchestrator(
            config(),
            market_data=market,
            broker_state=broker,
            instrument_specs=FakeInstrumentSpecSource(),
            recorder=RecordingRunRecorder(),
            kill_switch=KillSwitch(),
            session_store=session_store,
            risk_ledger_lock=lock,
            decision_window_store=InMemoryDecisionWindowStore(),
            clock=lambda: NOW,
        )
        live.decide_once()
        assert session_store.saves == 1

        # A second orchestrator instance, sharing only the durable store
        # and the lock — the same shape two genuinely separate processes
        # would have, not a second call reusing the first's in-memory
        # state.
        market_two = FakeMarketDataSource()
        market_two.bars = synthetic_bars(67)
        market_two.tick = synthetic_tick_for(market_two.bars[-1])
        broker_two = FakeBrokerStateSource()
        broker_two.account = make_broker_account_snapshot(observed_at_utc=NOW)
        second_process = LiveDecisionOrchestrator(
            config(),
            market_data=market_two,
            broker_state=broker_two,
            instrument_specs=FakeInstrumentSpecSource(),
            recorder=RecordingRunRecorder(),
            kill_switch=KillSwitch(),
            session_store=session_store,
            risk_ledger_lock=lock,
            decision_window_store=InMemoryDecisionWindowStore(),
            clock=lambda: NOW,
        )
        second_outcome = second_process.decide_once()

        assert not second_outcome.skipped
        assert session_store.saves == 2
        assert lock.held_calls == ["EUR/USD", "EUR/USD"]


class RaisingRiskLedgerLock:
    """AG-024: simulates the lock's own acquisition (or anything inside its

    held block — most concretely `RiskSessionStore.save()`, which unlike
    `load_latest()` has no internal try/except of its own) failing, e.g.
    a transient database outage. Before AG-024, this had no handling
    anywhere in `decide_once()`'s own call chain and would have propagated
    uncaught."""

    def held(self, canonical_symbol: str) -> Any:
        raise RuntimeError(f"simulated database outage acquiring the lock for {canonical_symbol}")


class TestAG024RiskLedgerLockFailureFailsClosed:
    """A lock/recover/persist failure must halt and skip the cycle, never

    crash the process or proceed on unknown risk-session state (ADR-021
    §8)."""

    def test_a_lock_failure_trips_the_kill_switch_and_is_reported_as_a_skip(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(66)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        kill_switch = KillSwitch()

        live = LiveDecisionOrchestrator(
            config(),
            market_data=market,
            broker_state=broker,
            instrument_specs=FakeInstrumentSpecSource(),
            recorder=RecordingRunRecorder(),
            kill_switch=kill_switch,
            session_store=InMemoryRiskSessionStore(),
            risk_ledger_lock=RaisingRiskLedgerLock(),
            decision_window_store=InMemoryDecisionWindowStore(),
            clock=lambda: NOW,
        )

        # Must not raise — the whole point of AG-024.
        outcome = live.decide_once()

        assert outcome.skipped
        assert outcome.skipped_reason is not None
        assert "risk-ledger lock" in outcome.skipped_reason
        assert kill_switch.is_halted
        assert ReasonCode.RISK_LEDGER_LOCK_UNAVAILABLE in kill_switch.active_reasons

    def test_a_recovered_lock_on_the_next_cycle_proceeds_normally(self) -> None:
        """The halt is real and does not auto-clear (matching every other

        halt in this codebase) — but a fresh orchestrator against the
        same durable store, once the lock is healthy again, is not
        permanently bricked by the earlier failure."""
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(66)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        session_store = InMemoryRiskSessionStore()

        failing = LiveDecisionOrchestrator(
            config(),
            market_data=market,
            broker_state=broker,
            instrument_specs=FakeInstrumentSpecSource(),
            recorder=RecordingRunRecorder(),
            kill_switch=KillSwitch(),
            session_store=session_store,
            risk_ledger_lock=RaisingRiskLedgerLock(),
            decision_window_store=InMemoryDecisionWindowStore(),
            clock=lambda: NOW,
        )
        failing.decide_once()
        assert session_store.saves == 0  # the failed cycle persisted nothing

        recovered = LiveDecisionOrchestrator(
            config(),
            market_data=market,
            broker_state=broker,
            instrument_specs=FakeInstrumentSpecSource(),
            recorder=RecordingRunRecorder(),
            kill_switch=KillSwitch(),  # a fresh switch — an operator reset in practice
            session_store=session_store,
            risk_ledger_lock=InMemoryRiskLedgerLock(),
            decision_window_store=InMemoryDecisionWindowStore(),
            clock=lambda: NOW,
        )
        outcome = recovered.decide_once()

        assert not outcome.skipped
        assert session_store.saves == 1


class TestD031FeatureValuesArePersisted:
    """Review 1.17 §9 / review 1.18 §8: the actual feature values a decision

    was made from, not only their hash, must reach the recorder for every
    window that has features at all — including NO_TRADE.
    """

    def test_a_no_trade_window_still_records_its_features(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(66)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        recorder = RecordingRunRecorder()
        live = orchestrator(market, broker, FakeInstrumentSpecSource(), recorder)

        outcome = live.decide_once()

        assert not outcome.skipped
        assert len(recorder.recorded_features) == 1

    def test_the_recorded_features_match_the_sealed_capsule_hash(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(66)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        recorder = RecordingRunRecorder()
        live = orchestrator(market, broker, FakeInstrumentSpecSource(), recorder)

        outcome = live.decide_once()

        assert outcome.capsule is not None
        assert len(recorder.recorded_features) == 1
        recorded = recorder.recorded_features[0]
        assert recorded.feature_values_hash == outcome.capsule.feature_values_hash
        # The full payload is more than just the hash — the whole point of
        # D-031 is answerable from it directly, not only "does it match".
        assert recorded.model_dump(mode="json")["feature_snapshot_id"] == str(
            recorded.feature_snapshot_id
        )

    def test_no_decision_at_all_records_no_features(self) -> None:
        live = orchestrator(
            FakeMarketDataSource(),
            FakeBrokerStateSource(),
            FakeInstrumentSpecSource(spec=None),
            (recorder := RecordingRunRecorder()),
        )
        outcome = live.decide_once()
        assert outcome.skipped
        assert recorder.recorded_features == []


class TestF054DurableDecisionWindowIdempotence:
    """Review 1.17 §8 / review 1.18 §7: a restart must not re-decide an

    already-decided window, nor forget which decision hashes the risk
    engine's duplicate-protection check has already seen. Simulated here by
    constructing a *second*, independent `LiveDecisionOrchestrator` against
    the same `DecisionWindowStore` — a fresh process reading the same
    durable state, exactly what a real restart looks like.
    """

    def test_a_fresh_orchestrator_against_the_same_store_does_not_redecide(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(80)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        store = InMemoryDecisionWindowStore()

        first_process = orchestrator(
            market,
            broker,
            FakeInstrumentSpecSource(),
            RecordingRunRecorder(),
            decision_window_store=store,
        )
        first_outcome = first_process.decide_once()
        assert not first_outcome.skipped

        # A brand new orchestrator instance — nothing carried over except
        # what `store` durably holds, exactly as a restarted process would
        # see: a fresh `_seen_hashes`/`_last_decided_open_time` in memory,
        # but the same durable checkpoint underneath.
        second_process = orchestrator(
            market,
            broker,
            FakeInstrumentSpecSource(),
            RecordingRunRecorder(),
            decision_window_store=store,
        )
        second_outcome = second_process.decide_once()

        assert second_outcome.skipped
        assert second_outcome.skipped_reason is not None
        assert "no new closed bar" in second_outcome.skipped_reason

    def test_a_fresh_orchestrator_can_still_decide_a_genuinely_new_bar(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(80)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        store = InMemoryDecisionWindowStore()

        first_process = orchestrator(
            market,
            broker,
            FakeInstrumentSpecSource(),
            RecordingRunRecorder(),
            decision_window_store=store,
        )
        first_process.decide_once()

        market.bars = synthetic_bars(81)  # one genuinely new closed bar
        market.tick = synthetic_tick_for(market.bars[-1])
        second_process = orchestrator(
            market,
            broker,
            FakeInstrumentSpecSource(),
            RecordingRunRecorder(),
            decision_window_store=store,
        )
        second_outcome = second_process.decide_once()

        assert not second_outcome.skipped

    def test_a_different_config_version_does_not_inherit_the_old_checkpoint(self) -> None:
        """Review 1.17 §8's own invariant includes "+ same config" — a

        config change is a genuinely new logical-decision space, so it must
        not be silently deduped against the previous config's checkpoint.
        """
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(80)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        store = InMemoryDecisionWindowStore()

        first_process = LiveDecisionOrchestrator(
            config(),
            market_data=market,
            broker_state=broker,
            instrument_specs=FakeInstrumentSpecSource(),
            recorder=RecordingRunRecorder(),
            kill_switch=KillSwitch(),
            session_store=InMemoryRiskSessionStore(),
            risk_ledger_lock=InMemoryRiskLedgerLock(),
            decision_window_store=store,
            clock=lambda: NOW,
        )
        first_process.decide_once()

        different_config = config().model_copy(
            update={
                "trading_agent": config().trading_agent.model_copy(update={"model_version": "v2"})
            }
        )
        assert different_config.config_version != config().config_version
        second_process = LiveDecisionOrchestrator(
            different_config,
            market_data=market,
            broker_state=broker,
            instrument_specs=FakeInstrumentSpecSource(),
            recorder=RecordingRunRecorder(),
            kill_switch=KillSwitch(),
            session_store=InMemoryRiskSessionStore(),
            risk_ledger_lock=InMemoryRiskLedgerLock(),
            decision_window_store=store,
            clock=lambda: NOW,
        )
        second_outcome = second_process.decide_once()

        assert not second_outcome.skipped

    def test_a_decided_intent_durably_records_its_hash_for_the_duplicate_check(self) -> None:
        """Not only the window gate — the risk engine's own

        `DUPLICATE_INTENT` check (`policy.seen_decision_hashes`) must have
        something durable to restore from after a restart, not just an
        empty in-process set. Checked at the store's own public interface
        (`load_latest`), not by reaching into the orchestrator's private
        state.
        """
        market = FakeMarketDataSource()
        # Enough history for baseline_v1 to actually propose a trade, not
        # just a NO_TRADE window — otherwise there is no decision_hash to
        # check durability for.
        market.bars = synthetic_bars(200)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        store = InMemoryDecisionWindowStore()

        first_process = orchestrator(
            market,
            broker,
            FakeInstrumentSpecSource(),
            RecordingRunRecorder(),
            decision_window_store=store,
        )
        outcome = first_process.decide_once()

        record = store.load_latest(
            canonical_symbol="EUR/USD",
            strategy_id=config().trading_agent.strategy_id,
            config_version=config().config_version,
        )
        assert record.is_known
        assert record.state is not None
        capsule = outcome.capsule
        # The hash only joins the risk engine's duplicate-protection set on
        # a PASS verdict (`decide_once()`'s own logic, unchanged by F-054) —
        # a BLOCKed/HALTed intent's capsule still carries the `TradeIntent`
        # for audit purposes, but was never added to `seen_decision_hashes`
        # even before this durability fix, and F-054 does not change that.
        if (
            capsule is not None
            and capsule.trade_intent is not None
            and capsule.risk_decision is not None
            and capsule.risk_decision.verdict.value == "PASS"
        ):
            assert capsule.trade_intent.decision_hash in record.state.seen_decision_hashes
        else:
            # A NO_TRADE, BLOCKed or HALTed window still durably records
            # the window itself as decided (the `record.state is not None`
            # assertion above), with the hash set unchanged from before
            # this, the first ever call.
            assert record.state.seen_decision_hashes == frozenset()


class TestF054FailsClosedOnCorruption:
    """Review 1.19 §5: an unreadable decision-window record must not be

    treated as "nothing recorded" — it must halt, distinguishing a
    corrupted idempotence record from a genuine first start.
    """

    def test_an_unreadable_store_trips_the_kill_switch_before_deciding(self) -> None:
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(66)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        recorder = RecordingRunRecorder()
        kill_switch = KillSwitch()
        store = InMemoryDecisionWindowStore(unreadable="simulated corruption")
        live = orchestrator(
            market,
            broker,
            FakeInstrumentSpecSource(),
            recorder,
            kill_switch=kill_switch,
            decision_window_store=store,
        )

        live.decide_once()

        assert kill_switch.is_halted
        halted_events = [
            payload for payload, *_ in recorder.events if isinstance(payload, SystemHalted)
        ]
        assert len(halted_events) == 1
        assert halted_events[0].reason_codes == (ReasonCode.DECISION_STATE_UNKNOWN,)

    def test_a_genuinely_first_start_does_not_halt(self) -> None:
        """The contrast case: no prior record at all is not corruption."""
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(66)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)
        kill_switch = KillSwitch()
        live = orchestrator(
            market,
            broker,
            FakeInstrumentSpecSource(),
            RecordingRunRecorder(),
            kill_switch=kill_switch,
            decision_window_store=InMemoryDecisionWindowStore(),
        )

        live.decide_once()

        assert not kill_switch.is_halted

    def test_recovery_is_only_attempted_once_per_process(self) -> None:
        """A second `decide_once()` call must not re-read the store — the

        recovery already happened, and re-checking every call would be
        both wasteful and, for a store that flips from unreadable to
        readable mid-session, a source of surprising behaviour change.
        """
        market = FakeMarketDataSource()
        market.bars = synthetic_bars(80)
        market.tick = synthetic_tick_for(market.bars[-1])
        broker = FakeBrokerStateSource()
        broker.account = make_broker_account_snapshot(observed_at_utc=NOW)

        class CountingStore(InMemoryDecisionWindowStore):
            def __init__(self) -> None:
                super().__init__()
                self.load_calls = 0

            def load_latest(self, *, canonical_symbol, strategy_id, config_version):  # type: ignore[no-untyped-def]
                self.load_calls += 1
                return super().load_latest(
                    canonical_symbol=canonical_symbol,
                    strategy_id=strategy_id,
                    config_version=config_version,
                )

        store = CountingStore()
        live = orchestrator(
            market,
            broker,
            FakeInstrumentSpecSource(),
            RecordingRunRecorder(),
            decision_window_store=store,
        )

        live.decide_once()
        live.decide_once()

        assert store.load_calls == 1


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
