"""What the system saw, kept where it can be checked (review 1.6 F-022).

Two claims are under test.

**The store keeps raw data immutable, and says so when something tries not
to.** build.md §26 lists "raw data immutable" as an M2 acceptance criterion. A
store that silently keeps whichever bar arrived first satisfies the wording and
loses the contradiction, so the assertion here is that a conflicting bar for an
interval already stored *raises*.

**The running replay uses it.** The gap review 1.4 caught was a persistence
layer built beside the application rather than inside it. These tests read back
what an ordinary replay left behind, including for the warm-up windows that
produce no event at all and would otherwise be invisible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from scripts.run_replay import build_instrument_spec
from sqlalchemy import Engine

from crumblr.application.bootstrap import build_durable_runtime
from crumblr.application.orchestration import ReplayOrchestrator
from crumblr.config import PlatformConfig, load_config
from crumblr.domain.enums import BarOrigin, DataQuality, Environment
from crumblr.domain.models import Bar, MarketBar, MarketTick
from crumblr.market_data.synthetic import (
    SYNTHETIC_SOURCE,
    SyntheticMarketConfig,
    generate_ticks,
)
from crumblr.mt5_gateway.simulated import SimulatedBroker
from crumblr.persistence.engine import DEFAULT_TEST_URL, database_url
from crumblr.persistence.journal import JournalIntegrityError
from crumblr.persistence.market_data import (
    _TICK_INSERT_CHUNK_SIZE,
    MarketDataStore,
    bar_identity,
    tick_identity,
)

pytestmark = pytest.mark.integration

TEST_URL = database_url(DEFAULT_TEST_URL)
"""Resolved once, honouring `CRUMBLR_DATABASE_URL` — see `test_live_decision.py`'s

`TEST_URL` for the full reasoning (workspace database isolation)."""

REPO_ROOT = Path(__file__).resolve().parents[2]
BALANCE = Decimal("10000")
BARS = 200
START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
SOURCE = "test:feed"
SPEC = build_instrument_spec()


@pytest.fixture(scope="module")
def config() -> PlatformConfig:
    shipped = load_config(Environment.PAPER, config_dir=REPO_ROOT / "config")
    agent = shipped.trading_agent.model_copy(update={"strategy_id": "baseline_v1"})
    return shipped.model_copy(update={"trading_agent": agent})


def a_tick(minute: int, bid: str = "1.08500") -> MarketTick:
    at = START + timedelta(minutes=minute)
    bid_price = Decimal(bid)
    ask_price = bid_price + Decimal("0.00010")
    return MarketTick(
        tick_id=tick_identity(
            source=SOURCE,
            canonical_symbol=SPEC.canonical_symbol,
            event_time_utc=at,
            bid=bid_price,
            ask=ask_price,
        ),
        source=SOURCE,
        canonical_symbol=SPEC.canonical_symbol,
        broker_symbol=SPEC.broker_symbol,
        event_time_utc=at,
        received_time_utc=at,
        bid=bid_price,
        ask=ask_price,
    )


def a_bar(minute: int, close: str = "1.08500") -> MarketBar:
    open_time = START + timedelta(minutes=minute)
    return MarketBar(
        bar_id=bar_identity(
            source=SOURCE,
            canonical_symbol=SPEC.canonical_symbol,
            timeframe="M5",
            open_time_utc=open_time,
        ),
        source=SOURCE,
        canonical_symbol=SPEC.canonical_symbol,
        broker_symbol=SPEC.broker_symbol,
        timeframe="M5",
        bar=Bar(
            open_time_utc=open_time,
            open=Decimal("1.08400"),
            high=Decimal("1.08600"),
            low=Decimal("1.08300"),
            close=Decimal(close),
            tick_volume=12,
        ),
        origin=BarOrigin.BROKER,
        received_time_utc=open_time,
    )


class TestTicksRoundTrip:
    def test_a_tick_survives_storage_exactly(self, engine: Engine) -> None:
        store = MarketDataStore(engine)
        store.record_ticks([a_tick(0)])

        restored = MarketDataStore(engine).read_ticks()

        assert len(restored) == 1
        assert restored[0] == a_tick(0)

    def test_prices_come_back_as_decimals_not_floats(self, engine: Engine) -> None:
        """The same rule the rest of the schema follows, one level down."""
        store = MarketDataStore(engine)
        store.record_ticks([a_tick(0, bid="1.08512")])

        restored = store.read_ticks()[0]
        assert isinstance(restored.bid, Decimal)
        assert restored.bid == Decimal("1.08512")

    def test_ticks_read_back_in_market_time_order(self, engine: Engine) -> None:
        store = MarketDataStore(engine)
        store.record_ticks([a_tick(10), a_tick(0), a_tick(5)])

        times = [tick.event_time_utc for tick in store.read_ticks()]
        assert times == sorted(times)

    def test_an_identical_tick_stored_twice_is_one_row(self, engine: Engine) -> None:
        store = MarketDataStore(engine)

        assert store.record_ticks([a_tick(0)]) == 1
        assert store.record_ticks([a_tick(0)]) == 0
        assert len(store.read_ticks()) == 1

    def test_two_different_quotes_at_one_instant_are_two_rows(self, engine: Engine) -> None:
        """Feeds do this legitimately; collapsing them would lose observations."""
        store = MarketDataStore(engine)
        store.record_ticks([a_tick(0, bid="1.08500"), a_tick(0, bid="1.08501")])

        assert len(store.read_ticks()) == 2

    def test_a_batch_larger_than_one_insert_chunk_does_not_exceed_postgresqls_parameter_limit(
        self, engine: Engine
    ) -> None:
        """D-040 sibling, found the same soak: `market_ticks` has 14 columns, so
        one bulk `.values(rows)` INSERT for a large-enough batch exceeds
        PostgreSQL's 65535-bound-parameter ceiling outright — observed on the
        first real continuous-read soak, 2026-08-24, not a hypothetical.
        """
        store = MarketDataStore(engine)
        many = [a_tick(minute) for minute in range(_TICK_INSERT_CHUNK_SIZE * 2 + 1)]

        inserted = store.record_ticks(many)

        assert inserted == len(many)
        assert len(store.read_ticks()) == len(many)


class TestLatestReads:
    """Dashboard v0 (review 1.12 §8) wants the newest row, not the oldest —

    `read_ticks`/`read_bars` order ascending for replay, so `latest_tick`/
    `latest_bar` exist rather than asking a caller to remember to reverse.
    """

    def test_latest_tick_is_the_most_recently_observed_one(self, engine: Engine) -> None:
        store = MarketDataStore(engine)
        store.record_ticks([a_tick(0), a_tick(10), a_tick(5)])

        latest = store.latest_tick(canonical_symbol=SPEC.canonical_symbol)

        assert latest is not None
        assert latest.event_time_utc == a_tick(10).event_time_utc

    def test_latest_tick_is_none_for_an_unseen_symbol(self, engine: Engine) -> None:
        store = MarketDataStore(engine)
        store.record_ticks([a_tick(0)])

        assert store.latest_tick(canonical_symbol="GBP/USD") is None

    def test_latest_bar_is_the_most_recently_opened_one(self, engine: Engine) -> None:
        store = MarketDataStore(engine)
        store.record_bars([a_bar(0), a_bar(10), a_bar(5)])

        latest = store.latest_bar(canonical_symbol=SPEC.canonical_symbol, timeframe="M5")

        assert latest is not None
        assert latest.bar.open_time_utc == a_bar(10).bar.open_time_utc

    def test_latest_bar_is_none_for_an_unseen_timeframe(self, engine: Engine) -> None:
        store = MarketDataStore(engine)
        store.record_bars([a_bar(0)])

        assert store.latest_bar(canonical_symbol=SPEC.canonical_symbol, timeframe="M15") is None

    def test_recent_bars_are_the_newest_n_in_chronological_order(self, engine: Engine) -> None:
        store = MarketDataStore(engine)
        store.record_bars([a_bar(minute) for minute in range(0, 50, 5)])

        recent = store.recent_bars(canonical_symbol=SPEC.canonical_symbol, timeframe="M5", limit=3)

        opens = [bar.bar.open_time_utc for bar in recent]
        assert opens == sorted(opens), "chart data must read left-to-right in time order"
        assert opens[-1] == a_bar(45).bar.open_time_utc, "must be the newest bars, not the oldest"
        assert len(recent) == 3


class TestChunkedInsertFailureSemantics:
    """F-038 (review 1.11): the D-041 chunking must have a proven, not assumed,
    failure contract.

    Chunking a batch into several `INSERT`s only stays safe if a failure
    partway through cannot leave a logical batch half-persisted. `_record_ticks`
    runs every chunk against the *same* `Connection`, and a caller-supplied
    connection is never committed inside the store (`record_ticks` only opens
    its own transaction when the caller did not hand one in) — so the actual
    contract is **contract A, batch atomic**: one failing chunk must roll the
    whole logical batch back, not merely stop after however much had already
    been sent to the server. That is a claim about transaction boundaries, not
    about `on_conflict_do_nothing`, so it is proven here by injecting a failure
    straight into the connection rather than via a real constraint violation.
    """

    def test_a_failure_partway_through_a_multi_chunk_batch_rolls_back_the_whole_batch(
        self, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = MarketDataStore(engine)
        many = [a_tick(minute) for minute in range(_TICK_INSERT_CHUNK_SIZE + 5)]
        assert len(many) > _TICK_INSERT_CHUNK_SIZE, "must span at least two chunks to prove this"

        with engine.connect() as connection:
            real_execute = connection.execute
            calls = {"count": 0}

            def flaky_execute(*args: Any, **kwargs: Any) -> Any:
                calls["count"] += 1
                if calls["count"] == 2:
                    raise RuntimeError("simulated failure inside the second chunk")
                return real_execute(*args, **kwargs)

            monkeypatch.setattr(connection, "execute", flaky_execute)

            # The exception must propagate out of `connection.begin()` itself,
            # not be caught inside it - only then does the transaction context
            # manager see a failure and issue the rollback under test. Catching
            # it inside the `with connection.begin():` block would let the
            # block exit "successfully" and commit chunk 1 regardless.
            with pytest.raises(RuntimeError, match="simulated failure"), connection.begin():
                store.record_ticks(many, connection=connection)

            assert calls["count"] == 2, "the fake must have been reached mid-batch, not at the end"

        assert len(store.read_ticks()) == 0, (
            "chunk 1 must not survive when chunk 2 fails: a half-persisted "
            "logical batch is the exact failure mode F-038 requires a proof against"
        )


class TestRawDataIsImmutable:
    """build.md §26 M2 — and a rewrite must be loud, not merely prevented."""

    def test_the_same_bar_stored_twice_is_one_row(self, engine: Engine) -> None:
        store = MarketDataStore(engine)

        assert store.record_bars([a_bar(0)]) == 1
        assert store.record_bars([a_bar(0)]) == 0
        assert len(store.read_bars()) == 1

    def test_a_different_bar_for_a_stored_interval_raises(self, engine: Engine) -> None:
        """The contradiction is the finding. Keeping either one silently hides it."""
        store = MarketDataStore(engine)
        store.record_bars([a_bar(0, close="1.08500")])

        with pytest.raises(JournalIntegrityError, match="already stored with different"):
            store.record_bars([a_bar(0, close="1.08550")])

    def test_the_first_bar_is_the_one_that_survives(self, engine: Engine) -> None:
        store = MarketDataStore(engine)
        store.record_bars([a_bar(0, close="1.08500")])

        with pytest.raises(JournalIntegrityError):
            store.record_bars([a_bar(0, close="1.08550")])

        assert store.read_bars()[0].bar.close == Decimal("1.08500")


class TestTheReplayRecordsWhatItSaw:
    """The wiring, not the store in isolation."""

    def persisted_replay(self, config: PlatformConfig, state_file: Path) -> int:
        runtime = build_durable_runtime(
            environment=config.environment, state_file=state_file, url=TEST_URL
        )
        runtime.kill_switch.reset(operator="test", incident_note="arming a fresh test database")
        spec = build_instrument_spec()
        broker = SimulatedBroker(
            spec, starting_balance=BALANCE, server=config.account_guard.expected_server
        )
        ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=BARS), spec))
        result = ReplayOrchestrator(
            config,
            spec,
            broker,
            starting_equity=BALANCE,
            recorder=runtime.recorder,
            kill_switch=runtime.kill_switch,
            session_store=runtime.session_store,
        ).run(ticks)
        return len(result.capsules)

    def test_every_window_the_run_observed_is_stored(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        self.persisted_replay(config, tmp_path / "safety.json")

        assert MarketDataStore(engine).counts() == {"ticks": BARS, "bars": BARS}

    def test_the_warm_up_windows_are_visible_here_and_nowhere_else(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """The gap D-031 named.

        A window that ends before the strategy has enough history seals no
        capsule and emits no event. Until the market store existed, the system
        had no record of ever having seen those bars.
        """
        capsules = self.persisted_replay(config, tmp_path / "safety.json")
        stored = MarketDataStore(engine).counts()

        assert capsules < BARS, "this replay must contain warm-up windows to be the right test"
        assert stored["bars"] == BARS
        assert stored["bars"] - capsules > 0

    def test_stored_observations_name_the_generator_that_produced_them(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """Synthetic history must never be poolable with a broker feed."""
        self.persisted_replay(config, tmp_path / "safety.json")

        bars = MarketDataStore(engine).read_bars()
        assert {bar.source for bar in bars} == {SYNTHETIC_SOURCE}
        assert {bar.origin for bar in bars} == {BarOrigin.SYNTHETIC}
        assert all(bar.pipeline_version is None for bar in bars), (
            "the generator emits bars directly; claiming an aggregation would be false"
        )

    def test_the_stored_bars_are_the_bars_the_run_decided_on(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """The point of the whole exercise: the decision input is recoverable."""
        self.persisted_replay(config, tmp_path / "safety.json")

        spec = build_instrument_spec()
        expected = [
            tick.bar for tick in generate_ticks(SyntheticMarketConfig(bar_count=BARS), spec)
        ]
        stored = [record.bar for record in MarketDataStore(engine).read_bars()]

        assert stored == expected

    def test_replaying_the_same_series_stores_it_once(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        self.persisted_replay(config, tmp_path / "safety.json")
        before = MarketDataStore(engine).counts()

        self.persisted_replay(config, tmp_path / "safety.json")

        assert MarketDataStore(engine).counts() == before

    def test_a_quality_flag_survives_to_storage(
        self, engine: Engine, config: PlatformConfig, tmp_path: Path
    ) -> None:
        """A degraded quote is evidence and must not be normalised away."""
        runtime = build_durable_runtime(
            environment=config.environment,
            state_file=tmp_path / "safety.json",
            url=TEST_URL,
        )
        runtime.kill_switch.reset(operator="test", incident_note="arming a fresh test database")
        spec = build_instrument_spec()
        broker = SimulatedBroker(
            spec, starting_balance=BALANCE, server=config.account_guard.expected_server
        )
        from crumblr.market_data.synthetic import FaultInjection

        market = SyntheticMarketConfig(
            bar_count=BARS,
            faults=FaultInjection(stale_tick_every=17, suspect_quality_every=23),
        )
        ReplayOrchestrator(
            config,
            spec,
            broker,
            starting_equity=BALANCE,
            recorder=runtime.recorder,
            kill_switch=runtime.kill_switch,
            session_store=runtime.session_store,
        ).run(list(generate_ticks(market, spec)))
        runtime.dispose()

        qualities = {tick.data_quality for tick in MarketDataStore(engine).read_ticks()}
        assert qualities - {DataQuality.GOOD}, "the injected faults left no trace in storage"
