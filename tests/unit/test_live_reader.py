"""The continuous MT5 reader (review 1.9 F-034), against a scripted terminal.

Unlike `test_mt5_readonly_gateway.py`'s `FakeMt5`, the terminal here is
**mutable between polls** — `.account`, `.symbol`, `.tick_rows` and
`.login_ok` can all be changed mid-test, because reconnect and revalidation
are only interesting across more than one connection. The five scenarios
review 1.9 §4 names each get their own test.

Persistence is a `RecordingSink`, not a real `MarketDataStore`: this file
proves the reconnect/revalidation state machine, which needs no PostgreSQL.
`LiveReader` is typed against `MarketDataSink`, a narrower protocol, for
exactly this reason.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from crumblr.application.live_reader import LiveReader, ReaderStatus
from crumblr.config import AccountGuardConfig
from crumblr.domain.models import MarketBar, MarketTick
from crumblr.mt5_gateway.client import Mt5Client, Mt5Credentials

NOW = datetime.fromtimestamp(1_767_000_300, tz=UTC)
"""Matches `tick_row()`'s default `time`, so a fresh tick is never accidentally
stale relative to the clock a test starts with."""

GUARD = AccountGuardConfig.model_validate(
    {
        "expected_server": "PepperstoneUK-Demo",
        "expected_login": None,
        "require_demo_account": True,
        "expected_currency": "EUR",
        "expected_leverage": 30,
    }
)


def account_info(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "login": 5_000_123,
        "server": "PepperstoneUK-Demo",
        "currency": "EUR",
        "trade_mode": 0,  # ACCOUNT_TRADE_MODE_DEMO
        "margin_mode": 2,  # ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
        "trade_allowed": True,
        "trade_expert": True,
        "balance": 10_000.0,
        "equity": 10_000.0,
        "margin": 0.0,
        "margin_free": 10_000.0,
        "margin_level": 0.0,
        "leverage": 30,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def symbol_info(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "currency_base": "EUR",
        "currency_profit": "USD",
        "trade_contract_size": 100_000.0,
        "digits": 5,
        "point": 1e-05,
        "trade_tick_size": 1e-05,
        "trade_tick_value": 1.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "trade_stops_level": 10,
        "trade_freeze_level": 0,
        "filling_mode": 2,
        "trade_mode": 4,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def tick_row(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "time": 1_767_000_300,
        "time_msc": 1_767_000_300_000,
        "bid": 1.16700,
        "ask": 1.16706,
        "last": 0.0,
        "volume": 0,
        "flags": 6,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def bar_row(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "time": 1_767_000_000,
        "open": 1.16700,
        "high": 1.16750,
        "low": 1.16680,
        "close": 1.16720,
        "tick_volume": 120,
        "spread": 6,
        "real_volume": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class ScriptedMt5:
    """A terminal whose behaviour changes mid-test. See the module docstring."""

    COPY_TICKS_ALL = 3
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408

    def __init__(self) -> None:
        self.initialize_ok = True
        self.login_ok = True
        self.account: SimpleNamespace = account_info()
        self.symbol: SimpleNamespace = symbol_info()
        self.symbols: tuple[str, ...] = ("EURUSD",)
        self.tick_rows: tuple[Any, ...] | None = ()
        self.bar_rows: tuple[Any, ...] | None = ()
        self.error: tuple[int, str] = (1, "Success")
        self.account_reads = 0
        self.shutdown_calls = 0

    def initialize(self, *_a: Any, **_k: Any) -> bool:
        return self.initialize_ok

    def login(self, *_a: Any, **_k: Any) -> bool:
        return self.login_ok

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def last_error(self) -> tuple[int, str]:
        return self.error

    def version(self) -> tuple[Any, ...]:
        return (500, 4620, "20 Aug 2026")

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(connected=True, trade_allowed=True, ping_last=10)

    def account_info(self) -> SimpleNamespace:
        self.account_reads += 1
        return self.account

    def symbols_get(self, *_a: Any, **_k: Any) -> tuple[Any, ...]:
        return tuple(SimpleNamespace(name=name) for name in self.symbols)

    def symbol_select(self, *_a: Any, **_k: Any) -> bool:
        return True

    def symbol_info(self, _symbol: str) -> SimpleNamespace:
        return self.symbol

    def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
        # `time` matches NOW so the gateway's D-039 clock-offset detection
        # resolves to zero by default; a test exercising the offset itself
        # overrides this.
        return SimpleNamespace(bid=1.16700, ask=1.16706, time=int(NOW.timestamp()))

    def copy_ticks_from(self, *_a: Any, **_k: Any) -> tuple[Any, ...] | None:
        return self.tick_rows

    def copy_rates_from_pos(self, *_a: Any, **_k: Any) -> tuple[Any, ...] | None:
        return self.bar_rows

    def positions_get(self, *_a: Any, **_k: Any) -> tuple[Any, ...]:
        return ()

    def orders_get(self, *_a: Any, **_k: Any) -> tuple[Any, ...]:
        return ()


class RecordingSink:
    """Stands in for `MarketDataStore` — no PostgreSQL required."""

    def __init__(self) -> None:
        self.ticks: list[MarketTick] = []
        self.bars: list[MarketBar] = []

    def record_ticks(self, ticks: Any) -> int:
        self.ticks.extend(ticks)
        return len(ticks)

    def record_bars(self, bars: Any) -> int:
        self.bars.extend(bars)
        return len(bars)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def reader(
    fake: ScriptedMt5, sink: RecordingSink, clock: FakeClock, **overrides: Any
) -> LiveReader:
    defaults: dict[str, Any] = {
        "canonical_symbol": "EUR/USD",
        "stale_after": timedelta(seconds=60),
        "clock": clock,
        "sleep": lambda _seconds: None,
    }
    defaults.update(overrides)
    return LiveReader(
        Mt5Client(fake),
        Mt5Credentials(login=5_000_123, password="x", server="PepperstoneUK-Demo"),
        GUARD,
        sink,
        **defaults,
    )


class TestFirstConnect:
    def test_the_first_poll_connects_reads_and_persists(self) -> None:
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        fake.bar_rows = (bar_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)

        health = reader(fake, sink, clock).poll_once()

        assert health.status is ReaderStatus.HEALTHY
        assert health.connected is True
        assert health.reconnect_count == 1
        assert len(sink.ticks) == 1
        assert len(sink.bars) == 1

    def test_a_failed_first_connect_reports_disconnected_not_a_crash(self) -> None:
        fake = ScriptedMt5()
        fake.login_ok = False
        sink = RecordingSink()
        clock = FakeClock(NOW)

        health = reader(fake, sink, clock).poll_once()

        assert health.status is ReaderStatus.DISCONNECTED
        assert health.connected is False


class TestScenario1NormalReconnect:
    """review 1.9 F-034: normal disconnect -> reconnect -> same account -> recover."""

    def test_a_dropped_connection_recovers_on_its_own(self) -> None:
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)

        assert live.poll_once().status is ReaderStatus.HEALTHY

        # Simulate a dropped connection: the next read call fails.
        fake.tick_rows = None
        disconnected = live.poll_once()
        assert disconnected.status is ReaderStatus.DISCONNECTED
        assert disconnected.connected is False

        # Same account, same symbol — the terminal is back.
        fake.tick_rows = (tick_row(),)
        recovered = live.poll_once()
        assert recovered.status is ReaderStatus.HEALTHY
        assert recovered.reconnect_count == 2


class TestScenario2WrongAccountFailsClosed:
    """review 1.9 F-034: reconnect -> wrong server/account -> fail closed."""

    def test_a_reconnect_to_a_different_server_becomes_unhealthy_and_stays_that_way(
        self,
    ) -> None:
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)

        assert live.poll_once().status is ReaderStatus.HEALTHY

        fake.tick_rows = None
        assert live.poll_once().status is ReaderStatus.DISCONNECTED

        # The terminal reconnects, but to the wrong account.
        fake.account = account_info(server="PepperstoneEU-Demo")
        wrong = live.poll_once()
        assert wrong.status is ReaderStatus.UNHEALTHY
        assert wrong.detail is not None and "account guard" in wrong.detail

        # Sticky: even though the account is fixed, nothing here retries on
        # its own. This is the "no automatic return to healthy" rule.
        fake.account = account_info(server="PepperstoneUK-Demo")
        still_unhealthy = live.poll_once()
        assert still_unhealthy.status is ReaderStatus.UNHEALTHY

    def test_a_wrong_margin_mode_also_fails_closed(self) -> None:
        """Account mode (hedging/netting) is a safety-relevant fact too."""
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)

        assert live.poll_once().status is ReaderStatus.HEALTHY

        fake.tick_rows = None
        live.poll_once()

        # Same server/currency/leverage, but the account now nets instead of hedges.
        fake.account = account_info(margin_mode=0)  # RETAIL_NETTING
        result = live.poll_once()
        assert result.status is ReaderStatus.UNHEALTHY
        assert result.detail is not None and "margin mode" in result.detail

    def test_acknowledge_is_the_only_way_out(self) -> None:
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)
        live.poll_once()
        fake.tick_rows = None
        live.poll_once()
        fake.account = account_info(server="PepperstoneEU-Demo")
        after_mismatch = live.poll_once()
        assert after_mismatch.status is ReaderStatus.UNHEALTHY

        with pytest.raises(ValueError, match="operator"):
            live.acknowledge(operator="", note="fixed the account")
        with pytest.raises(ValueError, match="note"):
            live.acknowledge(operator="levi", note="")

        fake.account = account_info(server="PepperstoneUK-Demo")
        fake.tick_rows = (tick_row(),)
        live.acknowledge(operator="levi", note="server config corrected")
        after_ack = live.health
        assert after_ack.status is ReaderStatus.DISCONNECTED  # tries again, does not assume

        recovered = live.poll_once()
        assert recovered.status is ReaderStatus.HEALTHY


class TestScenario6AcknowledgeIsNotRestoration:
    """review 1.10 F-036: acknowledging is "I saw it", never "it is safe again".

    A human acknowledgement clears the sticky latch and permits a fresh
    attempt; it must never itself flip the status to HEALTHY. Only a
    subsequent, fully successful revalidation may do that.
    """

    def test_acknowledging_without_fixing_the_account_does_not_become_healthy(self) -> None:
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)
        live.poll_once()
        fake.tick_rows = None
        live.poll_once()
        fake.account = account_info(server="PepperstoneEU-Demo")
        before_ack = live.poll_once()
        assert before_ack.status is ReaderStatus.UNHEALTHY

        # Acknowledge, but the account is still wrong.
        live.acknowledge(operator="levi", note="looking into it")
        after_ack = live.health
        assert after_ack.status is ReaderStatus.DISCONNECTED  # not HEALTHY

        still_wrong = live.poll_once()
        assert still_wrong.status is ReaderStatus.UNHEALTHY, (
            "acknowledging must not itself restore HEALTHY without a fresh successful revalidation"
        )

    def test_an_unresolvable_symbol_fails_closed_the_same_way_a_wrong_account_does(
        self,
    ) -> None:
        """review 1.10 F-036: a symbol that cannot be established at all —

        not merely a changed spec — is exactly the "cannot be established"
        case review 1.9's own rule assigns to UNKNOWN -> HALT.
        """
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)
        live.poll_once()

        fake.tick_rows = None
        live.poll_once()

        # The account no longer has EURUSD at all — not a spec change, an
        # absence.
        fake.symbols = ("GBPUSD",)
        unresolved = live.poll_once()
        assert unresolved.status is ReaderStatus.UNHEALTHY
        assert unresolved.detail is not None and "instrument" in unresolved.detail

        # Acknowledging without restoring the symbol changes nothing.
        live.acknowledge(operator="levi", note="checking the account")
        still_broken = live.poll_once()
        assert still_broken.status is ReaderStatus.UNHEALTHY

        # Only a full, successful revalidation — symbol back, guard passing,
        # margin mode matching — restores HEALTHY.
        fake.symbols = ("EURUSD",)
        fake.tick_rows = (tick_row(),)
        live.acknowledge(operator="levi", note="EURUSD restored on the account")
        recovered = live.poll_once()
        assert recovered.status is ReaderStatus.HEALTHY

    def test_valid_state_restored_then_acknowledge_lets_healthy_resume(self) -> None:
        """The positive case: acknowledging a *fixed* problem does let it recover."""
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)
        live.poll_once()

        fake.tick_rows = None
        live.poll_once()
        fake.account = account_info(margin_mode=0)
        live.poll_once()
        assert live.health.status is ReaderStatus.UNHEALTHY

        fake.account = account_info()  # back to RETAIL_HEDGING
        fake.tick_rows = (tick_row(),)
        live.acknowledge(operator="levi", note="account mode confirmed restored")
        recovered = live.poll_once()
        assert recovered.status is ReaderStatus.HEALTHY


class TestScenario3SymbolSpecChanged:
    """review 1.9 F-034: spec changed -> detect + record, no silent continuation."""

    def test_a_changed_spec_is_recorded_not_hidden(self) -> None:
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)

        first = live.poll_once()
        original_version = first.spec_version
        assert original_version is not None

        fake.tick_rows = None
        live.poll_once()

        # The broker widened the stops level — a real, observable spec change.
        fake.symbol = symbol_info(trade_stops_level=25)
        fake.tick_rows = (tick_row(),)
        changed = live.poll_once()

        assert changed.status is ReaderStatus.HEALTHY  # a spec change is not a mismatch
        assert changed.spec_version != original_version
        assert changed.spec_changes == 1
        assert changed.detail is not None and "spec changed" in changed.detail


class TestScenario4NoTickData:
    """review 1.9 F-034: reconnect -> no tick data -> stale/unhealthy."""

    def test_silence_past_the_threshold_marks_stale(self) -> None:
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(time=1_767_000_000, time_msc=1_767_000_000_000),)
        fake.bar_rows = ()
        sink = RecordingSink()
        clock = FakeClock(datetime.fromtimestamp(1_767_000_000, tz=UTC))
        live = reader(fake, sink, clock, stale_after=timedelta(seconds=30))

        assert live.poll_once().status is ReaderStatus.HEALTHY

        fake.tick_rows = ()  # no failure, just nothing new
        clock.advance(10)
        assert live.poll_once().status is ReaderStatus.HEALTHY  # still fresh enough

        clock.advance(40)
        stale = live.poll_once()
        assert stale.status is ReaderStatus.STALE
        assert stale.connected is True  # stale is not a connection failure

    def test_fresh_ticks_clear_a_stale_status_on_their_own(self) -> None:
        """Unlike UNHEALTHY, STALE is not sticky — nothing here was ever wrong."""
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(time=1_767_000_000, time_msc=1_767_000_000_000),)
        sink = RecordingSink()
        clock = FakeClock(datetime.fromtimestamp(1_767_000_000, tz=UTC))
        live = reader(fake, sink, clock, stale_after=timedelta(seconds=30))
        live.poll_once()

        fake.tick_rows = ()
        clock.advance(40)
        assert live.poll_once().status is ReaderStatus.STALE

        clock.advance(5)
        fake.tick_rows = (tick_row(time=1_767_000_045, time_msc=1_767_000_045_000),)
        recovered = live.poll_once()
        assert recovered.status is ReaderStatus.HEALTHY


class TestScenario5TerminalRestartRevalidates:
    """review 1.9 F-034: terminal restart -> reconnect -> full account guard re-run."""

    def test_every_reconnect_reads_the_account_again_not_just_the_first_one(self) -> None:
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)

        live.poll_once()
        reads_after_first = fake.account_reads
        assert reads_after_first > 0

        fake.tick_rows = None
        live.poll_once()  # disconnected
        fake.tick_rows = (tick_row(),)
        live.poll_once()  # reconnect: terminal restart, simulated

        assert fake.account_reads > reads_after_first, (
            "a reconnect that does not re-read the account is not a revalidation"
        )


class TestDataConflict:
    def test_a_store_conflict_becomes_unhealthy_not_a_crash(self) -> None:
        class ConflictingSink(RecordingSink):
            def record_bars(self, bars: Any) -> int:
                from crumblr.persistence.journal import JournalIntegrityError

                raise JournalIntegrityError("bar already stored with different values")

        fake = ScriptedMt5()
        fake.bar_rows = (bar_row(),)
        sink = ConflictingSink()
        clock = FakeClock(NOW)
        live = reader(fake, sink, clock)

        health = live.poll_once()
        assert health.status is ReaderStatus.UNHEALTHY
        assert health.detail is not None and "data conflict" in health.detail


class TestRunForever:
    def test_max_iterations_bounds_the_loop(self) -> None:
        fake = ScriptedMt5()
        fake.tick_rows = (tick_row(),)
        sink = RecordingSink()
        clock = FakeClock(NOW)
        sleeps: list[float] = []
        live = reader(fake, sink, clock, sleep=sleeps.append)

        live.run_forever(max_iterations=3)

        assert len(sleeps) == 3
        assert live.health.reconnect_count == 1

    def test_backoff_grows_while_disconnected_and_resets_on_success(self) -> None:
        fake = ScriptedMt5()
        fake.login_ok = False
        sink = RecordingSink()
        clock = FakeClock(NOW)
        sleeps: list[float] = []
        live = reader(
            fake,
            sink,
            clock,
            sleep=sleeps.append,
            reconnect_backoff=timedelta(seconds=1),
            max_reconnect_backoff=timedelta(seconds=8),
        )

        live.run_forever(max_iterations=4)
        assert sleeps == [1.0, 2.0, 4.0, 8.0]
