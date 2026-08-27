"""The read-only MT5 gateway (M1), against a fake terminal.

**What these tests prove and what they do not.** They exercise the adapter:
error handling, symbol discovery, account verification, type conversion, and
that execution is structurally impossible. They prove nothing about how
Pepperstone actually behaves — spread, fills, reconnects and symbol conventions
are broker facts and stay unproven until the gateway runs against the real
demo account on the Windows host.

That distinction is the same one `status.md` draws between REPLAY-TESTED and
MT5-INTEGRATED, and it is why this file lives in `tests/unit`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import BarOrigin, Side
from crumblr.domain.models import ApprovedOrder
from crumblr.mt5_gateway.client import (
    Mt5CallFailedError,
    Mt5Client,
    Mt5Credentials,
    Mt5UnavailableError,
    load_mt5_module,
)
from crumblr.mt5_gateway.readonly import (
    AccountGuardError,
    ClockOffsetUnavailableError,
    ReadOnlyMt5Gateway,
    ReadOnlyViolationError,
    SymbolNotFoundError,
)

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
    """A demo account as MT5 reports one: floats, ints, no types."""
    fields: dict[str, Any] = {
        "login": 5_000_123,
        "server": "PepperstoneUK-Demo",
        "currency": "EUR",
        "trade_mode": 0,  # ACCOUNT_TRADE_MODE_DEMO
        "trade_allowed": True,
        "trade_expert": True,
        "balance": 10_000.0,
        "equity": 10_012.5,
        "margin": 120.0,
        "margin_free": 9_892.5,
        "margin_level": 8343.75,
        "leverage": 30,
        "profit": 12.5,
        "margin_mode": 2,  # ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
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
        "filling_mode": 3,  # SYMBOL_FILLING_FOK | SYMBOL_FILLING_IOC — D-037
        "trade_mode": 4,  # SYMBOL_TRADE_MODE_FULL — D-037
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeMt5:
    """A stand-in terminal.

    Mimics MT5's actual convention: failures return `None`/`False` and leave the
    reason in `last_error()`. Anything that pretended failures raise would test
    a terminal that does not exist.
    """

    # Arbitrary but distinct — these fakes never care what the values mean,
    # only that Mt5Module's Protocol has something to find.
    COPY_TICKS_ALL = 3
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 0

    def __init__(
        self,
        *,
        symbols: tuple[str, ...] = ("EURUSD",),
        account: SimpleNamespace | None = None,
        positions: tuple[SimpleNamespace, ...] | None = (),
        orders: tuple[SimpleNamespace, ...] | None = (),
        error: tuple[int, str] = (1, "Success"),
    ) -> None:
        self._symbols = symbols
        self._account = account if account is not None else account_info()
        self._positions = positions
        self._orders = orders
        self._error = error
        self.selected: list[str] = []
        self.shutdown_calls = 0
        self.login_calls: list[dict[str, Any]] = []
        self.account_info_calls = 0
        self.initialize_ok = True
        self.login_ok = True

    def initialize(self, *_args: Any, **_kwargs: Any) -> bool:
        return self.initialize_ok

    def login(self, login: int, **kwargs: Any) -> bool:
        self.login_calls.append({"login": login, **kwargs})
        return self.login_ok

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def last_error(self) -> tuple[int, str]:
        return self._error

    def version(self) -> tuple[Any, ...]:
        return (500, 4620, "20 Aug 2026")

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(connected=True, trade_allowed=True, ping_last=32)

    def account_info(self) -> SimpleNamespace | None:
        self.account_info_calls += 1
        return self._account

    def symbols_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return tuple(SimpleNamespace(name=name) for name in self._symbols)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self.selected.append(symbol)
        return True

    def symbol_info(self, _symbol: str) -> SimpleNamespace:
        return symbol_info()

    def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
        return SimpleNamespace(bid=1.08500, ask=1.08512, time=1_767_000_000)

    def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()

    def copy_ticks_from(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()

    def positions_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...] | None:
        return self._positions

    def orders_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...] | None:
        return self._orders

    def order_check(self, request: dict[str, Any]) -> Any:
        """M1 never calls this — `ReadOnlyMt5Gateway.order_check` refuses
        before reaching the module. Present only so this fake still
        structurally satisfies `Mt5Module`."""
        raise AssertionError("order_check must never be called through the M1 read-only gateway")


FAKE_NOW = datetime.fromtimestamp(1_767_000_000, tz=UTC)
"""Matches `FakeMt5.symbol_info_tick`'s default `time`, so `gateway()`'s
default clock detects a zero broker-clock offset (D-039) unless a test
deliberately shifts one side of that pair to exercise the offset itself."""


def gateway(
    fake: FakeMt5,
    *,
    guard: AccountGuardConfig = GUARD,
    clock: Callable[[], datetime] = lambda: FAKE_NOW,
) -> ReadOnlyMt5Gateway:
    client = Mt5Client(fake)
    client.connect(Mt5Credentials(login=5_000_123, password="x", server="PepperstoneUK-Demo"))
    return ReadOnlyMt5Gateway(client, guard, clock=clock)


# --------------------------------------------------------------------------- #
# The property that defines M1
# --------------------------------------------------------------------------- #


class TestExecutionIsStructurallyImpossible:
    """M1 is read-only, and that is a property of the type, not a promise."""

    @pytest.mark.parametrize("operation", ["order_check", "order_send"])
    def test_order_submission_is_refused(self, operation: str) -> None:
        gate = gateway(FakeMt5())
        order = cast("ApprovedOrder", None)
        with pytest.raises(ReadOnlyViolationError, match=operation):
            getattr(gate, operation)(order)

    def test_cancelling_pending_orders_is_refused(self) -> None:
        gate = gateway(FakeMt5())
        with pytest.raises(ReadOnlyViolationError, match="cancel_pending_orders"):
            gate.cancel_pending_orders()

    def test_flatten_is_refused_too(self) -> None:
        gate = gateway(FakeMt5())
        with pytest.raises(ReadOnlyViolationError, match="close_all_positions"):
            gate.close_all_positions(reason="anything")

    def test_the_refusal_says_where_execution_arrives(self) -> None:
        """An operator reading the error should learn what is missing, not just that it failed."""
        gate = gateway(FakeMt5())
        with pytest.raises(ReadOnlyViolationError, match="M5"):
            gate.order_send(cast("ApprovedOrder", None))


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #


class TestConnection:
    def test_a_failed_initialize_raises_with_the_terminal_reason(self) -> None:
        fake = FakeMt5(error=(-10005, "IPC timeout"))
        fake.initialize_ok = False
        client = Mt5Client(fake)
        with pytest.raises(Mt5CallFailedError, match="IPC timeout"):
            client.connect(Mt5Credentials(login=1, password="x", server="s"))
        assert not client.is_connected

    def test_a_failed_login_shuts_the_terminal_down_again(self) -> None:
        """Leaving an initialised terminal behind after a failed login leaks a handle."""
        fake = FakeMt5(error=(-6, "Authorization failed"))
        fake.login_ok = False
        client = Mt5Client(fake)
        with pytest.raises(Mt5CallFailedError, match="Authorization failed"):
            client.connect(Mt5Credentials(login=1, password="x", server="s"))
        assert fake.shutdown_calls == 1
        assert not client.is_connected

    def test_initialize_succeeding_does_not_imply_login_succeeded(self) -> None:
        """The failure mode: reading an account that belongs to somebody else."""
        fake = FakeMt5(error=(-6, "Authorization failed"))
        fake.login_ok = False
        with pytest.raises(Mt5CallFailedError):
            Mt5Client(fake).connect(Mt5Credentials(login=1, password="x", server="s"))

    def test_disconnect_is_safe_when_never_connected(self) -> None:
        fake = FakeMt5()
        Mt5Client(fake).disconnect()
        assert fake.shutdown_calls == 0

    def test_the_context_manager_disconnects(self) -> None:
        fake = FakeMt5()
        with Mt5Client(fake) as client:
            client.connect(Mt5Credentials(login=1, password="x", server="s"))
        assert fake.shutdown_calls == 1

    def test_credentials_never_render_the_password(self) -> None:
        """build.md §21: a password must not reach a log line or a traceback."""
        credentials = Mt5Credentials(login=5_000_123, password="hunter2", server="X")
        assert "hunter2" not in repr(credentials)
        assert "redacted" in repr(credentials)


# --------------------------------------------------------------------------- #
# Account guard
# --------------------------------------------------------------------------- #


class TestAccountGuard:
    def test_a_matching_demo_account_is_accepted(self) -> None:
        state = gateway(FakeMt5()).account()
        assert state.is_demo
        assert state.server == "PepperstoneUK-Demo"
        assert state.login == 5_000_123

    def test_a_live_account_is_refused(self) -> None:
        """`trade_mode` 2 is a real account. This is the halt that matters most."""
        fake = FakeMt5(account=account_info(trade_mode=2))
        with pytest.raises(AccountGuardError, match="not a demo account"):
            gateway(fake).account()

    def test_a_contest_account_is_also_not_a_demo(self) -> None:
        fake = FakeMt5(account=account_info(trade_mode=1))
        with pytest.raises(AccountGuardError, match="not a demo account"):
            gateway(fake).account()

    def test_the_wrong_server_is_refused(self) -> None:
        fake = FakeMt5(account=account_info(server="PepperstoneEU-Demo"))
        with pytest.raises(AccountGuardError, match="server"):
            gateway(fake).account()

    def test_the_wrong_currency_is_refused(self) -> None:
        fake = FakeMt5(account=account_info(currency="USD"))
        with pytest.raises(AccountGuardError, match="currency"):
            gateway(fake).account()

    def test_the_wrong_leverage_is_refused(self) -> None:
        """A different entity often means a different leverage cap — see APP-013."""
        fake = FakeMt5(account=account_info(leverage=500))
        with pytest.raises(AccountGuardError, match="leverage"):
            gateway(fake).account()

    def test_a_configured_login_is_enforced(self) -> None:
        guard = AccountGuardConfig.model_validate(
            {
                "expected_server": "PepperstoneUK-Demo",
                "expected_login": 999,
                "require_demo_account": True,
                "expected_currency": "EUR",
                "expected_leverage": 30,
            }
        )
        with pytest.raises(AccountGuardError, match="login"):
            gateway(FakeMt5(), guard=guard).account()

    def test_every_mismatch_is_reported_not_just_the_first(self) -> None:
        """One of four problems sends an operator down one of four paths."""
        fake = FakeMt5(account=account_info(server="Other", currency="USD", leverage=500))
        with pytest.raises(AccountGuardError) as raised:
            gateway(fake).account()
        message = str(raised.value)
        assert "server" in message
        assert "currency" in message
        assert "leverage" in message

    def test_a_login_mismatch_does_not_put_the_full_number_in_the_exception(self) -> None:
        """review 1.11 F-031: exception formatting must not re-introduce the login.

        `AccountGuardError`'s message is what `LiveReader` copies verbatim into
        `ReaderHealth.last_error`/`detail`, which `mt5_live_reader.py --json`
        writes to disk - so this is also the "sanitized health/evidence" case
        the review names, not only console logging.
        """
        guard = AccountGuardConfig.model_validate(
            {
                "expected_server": "PepperstoneUK-Demo",
                "expected_login": 999_999,
                "require_demo_account": True,
                "expected_currency": "EUR",
                "expected_leverage": 30,
            }
        )
        with pytest.raises(AccountGuardError) as raised:
            gateway(FakeMt5(), guard=guard).account()
        message = str(raised.value)
        assert "login" in message
        assert "5000123" not in message
        assert "5_000_123" not in message
        assert "999999" not in message
        assert "999_999" not in message

    def test_a_login_mismatch_does_not_put_the_full_number_in_the_log(self) -> None:
        """review 1.11 F-031: the `mt5.account_guard_failed` error/reconnect log."""
        import io

        from crumblr.observability.logging import configure_logging

        stream = io.StringIO()
        configure_logging(stream=stream, level="DEBUG")
        guard = AccountGuardConfig.model_validate(
            {
                "expected_server": "PepperstoneUK-Demo",
                "expected_login": 999_999,
                "require_demo_account": True,
                "expected_currency": "EUR",
                "expected_leverage": 30,
            }
        )
        with pytest.raises(AccountGuardError):
            gateway(FakeMt5(), guard=guard).account()

        rendered = stream.getvalue()
        assert "mt5.account_guard_failed" in rendered
        assert "5000123" not in rendered
        assert "999999" not in rendered


# --------------------------------------------------------------------------- #
# Symbol discovery — O-001: never hard-code the broker symbol
# --------------------------------------------------------------------------- #


class TestSymbolDiscovery:
    def test_a_plain_symbol_is_found(self) -> None:
        assert gateway(FakeMt5(symbols=("EURUSD", "GBPUSD"))).resolve_symbol() == "EURUSD"

    def test_a_suffixed_symbol_is_found(self) -> None:
        """Pepperstone and others use suffixes; a hard-coded name would miss this."""
        fake = FakeMt5(symbols=("GBPUSD.a", "EURUSD.a", "USDJPY.a"))
        assert gateway(fake).resolve_symbol() == "EURUSD.a"

    def test_an_exact_match_wins_over_a_suffixed_one(self) -> None:
        fake = FakeMt5(symbols=("EURUSD.a", "EURUSD", "EURUSD.raw"))
        assert gateway(fake).resolve_symbol() == "EURUSD"

    def test_the_shortest_candidate_wins_when_none_is_exact(self) -> None:
        """`EURUSD.a` is the instrument; `EURUSD.a.cfd` would be something else."""
        fake = FakeMt5(symbols=("EURUSD.a.cfd", "EURUSD.a"))
        assert gateway(fake).resolve_symbol() == "EURUSD.a"

    def test_a_missing_symbol_raises_rather_than_guessing(self) -> None:
        with pytest.raises(SymbolNotFoundError, match="EUR/USD"):
            gateway(FakeMt5(symbols=("GBPUSD", "USDJPY"))).resolve_symbol()

    def test_the_symbol_is_selected_in_market_watch(self) -> None:
        """An unselected symbol returns no ticks, which reads as a dead feed."""
        fake = FakeMt5(symbols=("EURUSD.a",))
        gateway(fake).resolve_symbol()
        assert fake.selected == ["EURUSD.a"]

    def test_resolution_is_cached(self) -> None:
        fake = FakeMt5(symbols=("EURUSD",))
        gate = gateway(fake)
        gate.resolve_symbol()
        gate.resolve_symbol()
        assert fake.selected == ["EURUSD"], "the symbol should be selected once"


# --------------------------------------------------------------------------- #
# Instrument specification
# --------------------------------------------------------------------------- #


class TestInstrumentSpec:
    def test_the_spec_is_read_from_the_terminal(self) -> None:
        spec = gateway(FakeMt5(symbols=("EURUSD.a",))).instrument("EUR/USD")
        assert spec.broker_symbol == "EURUSD.a"
        assert spec.canonical_symbol == "EUR/USD"
        assert spec.digits == 5

    def test_floats_from_mt5_become_exact_decimals(self) -> None:
        """`Decimal(1e-05)` carries the binary error; `Decimal(repr(...))` does not."""
        spec = gateway(FakeMt5()).instrument("EUR/USD")
        assert spec.point == Decimal("0.00001")
        assert spec.tick_size == Decimal("0.00001")
        assert spec.volume_step == Decimal("0.01")
        assert spec.contract_size == Decimal("100000")

    def test_the_spec_version_is_a_content_hash(self) -> None:
        first = gateway(FakeMt5()).instrument("EUR/USD")
        second = gateway(FakeMt5()).instrument("EUR/USD")
        assert first.spec_version == second.spec_version

    def test_a_changed_spec_produces_a_new_version(self) -> None:
        """build.md §7: detect when the broker changes a symbol's specification."""
        baseline = gateway(FakeMt5()).instrument("EUR/USD")

        class WiderStops(FakeMt5):
            def symbol_info(self, _symbol: str) -> SimpleNamespace:
                return symbol_info(trade_stops_level=30)

        assert gateway(WiderStops()).instrument("EUR/USD").spec_version != baseline.spec_version

    def test_another_symbol_is_refused(self) -> None:
        with pytest.raises(SymbolNotFoundError, match="GBP/USD"):
            gateway(FakeMt5()).instrument("GBP/USD")

    def test_filling_mode_is_decoded_from_the_bitmask_not_stringified(self) -> None:
        """D-037: MT5 hands back an int bitmask, not a name. `str(3)` is `"3"`.

        Confirmed against a real Pepperstone terminal 2026-08-24: a raw
        `filling_mode` of 2 decoded to `IOC` (status.md §13). This fixture
        uses 3 (`FOK | IOC`) to exercise more than one bit at once.
        """
        spec = gateway(FakeMt5()).instrument("EUR/USD")
        assert spec.filling_modes == ("FOK", "IOC")

    def test_trade_mode_is_decoded_from_the_enum_not_stringified(self) -> None:
        """D-037, the other half: `trade_mode` is an int enum, not a name."""
        spec = gateway(FakeMt5()).instrument("EUR/USD")
        assert spec.trade_mode == "FULL"

    def test_an_unrecognised_trade_mode_says_so_rather_than_guessing(self) -> None:
        class OddTradeMode(FakeMt5):
            def symbol_info(self, _symbol: str) -> SimpleNamespace:
                return symbol_info(trade_mode=99)

        spec = gateway(OddTradeMode()).instrument("EUR/USD")
        assert spec.trade_mode == "UNKNOWN(99)"


# --------------------------------------------------------------------------- #
# Positions
# --------------------------------------------------------------------------- #


def a_position(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "ticket": 123_456,
        "symbol": "EURUSD.a",
        "type": 0,
        "volume": 0.05,
        "price_open": 1.08512,
        "sl": 1.08012,
        "tp": 1.09512,
        "time": 1_767_000_000,
        "profit": 12.5,
        "swap": -0.35,
        "magic": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestPositions:
    def test_an_empty_book_reads_as_flat(self) -> None:
        assert gateway(FakeMt5(positions=())).positions() == ()

    def test_a_long_is_read_correctly(self) -> None:
        positions = gateway(FakeMt5(positions=(a_position(),))).positions()
        assert len(positions) == 1
        assert positions[0].side is Side.BUY
        assert positions[0].volume == Decimal("0.05")
        assert positions[0].open_price == Decimal("1.08512")

    def test_a_short_is_read_correctly(self) -> None:
        positions = gateway(FakeMt5(positions=(a_position(type=1),))).positions()
        assert positions[0].side is Side.SELL

    def test_an_absent_stop_is_none_not_zero(self) -> None:
        """MT5 reports "no stop" as 0.0, which is not a price."""
        positions = gateway(FakeMt5(positions=(a_position(sl=0.0, tp=0.0),))).positions()
        assert positions[0].stop_loss_price is None
        assert positions[0].take_profit_price is None

    def test_the_open_time_is_utc(self) -> None:
        positions = gateway(FakeMt5(positions=(a_position(),))).positions()
        assert positions[0].opened_at_utc.tzinfo is not None
        assert positions[0].opened_at_utc == datetime.fromtimestamp(1_767_000_000, tz=UTC)

    def test_a_failed_call_raises_rather_than_reading_as_flat(self) -> None:
        """The dangerous ambiguity: an empty result and a failed call look alike."""
        fake = FakeMt5(positions=None, error=(-10004, "No IPC connection"))
        with pytest.raises(Mt5CallFailedError, match="No IPC connection"):
            gateway(fake).positions()

    def test_a_none_result_with_success_reads_as_flat(self) -> None:
        """MT5 returns None with RES_S_OK when the book is genuinely empty."""
        assert gateway(FakeMt5(positions=None, error=(1, "Success"))).positions() == ()

    def test_the_current_price_is_read_when_present(self) -> None:
        positions = gateway(FakeMt5(positions=(a_position(price_current=1.08600),))).positions()
        assert positions[0].current_price == Decimal("1.08600")

    def test_a_missing_current_price_is_none_not_a_crash(self) -> None:
        """`a_position()` does not set `price_current` — an older/partial fake terminal."""
        positions = gateway(FakeMt5(positions=(a_position(),))).positions()
        assert positions[0].current_price is None


# --------------------------------------------------------------------------- #
# Account with extras — review 1.15 F-047 (profit, margin mode), review 1.16
# F-052 (one account_info() read, not two, per snapshot)
# --------------------------------------------------------------------------- #


class TestAccountWithExtras:
    def test_profit_and_margin_mode_are_read(self) -> None:
        _state, extras = gateway(FakeMt5()).account_with_extras()
        assert extras.profit == Decimal("12.5")
        assert extras.margin_mode == "RETAIL_HEDGING"

    def test_it_returns_the_same_account_state_as_account(self) -> None:
        fake = FakeMt5()
        state, _extras = gateway(fake).account_with_extras()
        assert state == gateway(fake).account()

    def test_it_verifies_the_account_the_same_way_account_does(self) -> None:
        """A live account must still be refused, not just leave `extras` unread."""
        fake = FakeMt5(account=account_info(trade_mode=2))
        with pytest.raises(AccountGuardError, match="not a demo account"):
            gateway(fake).account_with_extras()

    def test_only_one_account_info_call_is_made(self) -> None:
        """The defect review 1.16 F-052 found: two reads could straddle a

        real change at the broker. One call for one snapshot, always.
        """
        fake = FakeMt5()
        gateway(fake).account_with_extras()
        assert fake.account_info_calls == 1

    def test_an_unrecognised_margin_mode_is_visible_not_guessed(self) -> None:
        fake = FakeMt5(account=account_info(margin_mode=99))
        _state, extras = gateway(fake).account_with_extras()
        assert extras.margin_mode == "UNKNOWN(99)"

    def test_a_missing_margin_mode_is_none(self) -> None:
        fake = FakeMt5(account=account_info(margin_mode=None))
        _state, extras = gateway(fake).account_with_extras()
        assert extras.margin_mode is None

    def test_a_failed_call_raises(self) -> None:
        fake = FakeMt5(error=(-10004, "No IPC connection"))
        fake.account_info = lambda: None  # type: ignore[method-assign]
        with pytest.raises(Mt5CallFailedError, match="No IPC connection"):
            gateway(fake).account_with_extras()


# --------------------------------------------------------------------------- #
# Pending orders — review 1.15 F-047
# --------------------------------------------------------------------------- #


def a_pending_order(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "ticket": 654_321,
        "symbol": "EURUSD.a",
        "type": 2,  # ORDER_TYPE_BUY_LIMIT
        "state": 1,  # ORDER_STATE_PLACED
        "volume_current": 0.05,
        "price_open": 1.08000,
        "sl": 1.07500,
        "tp": 1.09000,
        "time_expiration": 0,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestPendingOrders:
    def test_an_empty_book_reads_as_no_pending_orders(self) -> None:
        assert gateway(FakeMt5(orders=())).pending_orders() == ()

    def test_a_pending_order_is_read_and_decoded(self) -> None:
        orders = gateway(FakeMt5(orders=(a_pending_order(),))).pending_orders()
        assert len(orders) == 1
        order = orders[0]
        assert order.order_id == 654_321
        assert order.broker_symbol == "EURUSD.a"
        assert order.order_type == "BUY_LIMIT"
        assert order.state == "PLACED"
        assert order.volume == Decimal("0.05")
        assert order.price == Decimal("1.08000")
        assert order.stop_loss_price == Decimal("1.07500")
        assert order.take_profit_price == Decimal("1.09000")

    def test_an_unrecognised_order_type_is_visible_not_guessed(self) -> None:
        orders = gateway(FakeMt5(orders=(a_pending_order(type=77),))).pending_orders()
        assert orders[0].order_type == "UNKNOWN(77)"

    def test_no_expiration_reads_as_none(self) -> None:
        orders = gateway(FakeMt5(orders=(a_pending_order(time_expiration=0),))).pending_orders()
        assert orders[0].expires_at_utc is None

    def test_an_absent_stop_is_none_not_zero(self) -> None:
        orders = gateway(FakeMt5(orders=(a_pending_order(sl=0.0, tp=0.0),))).pending_orders()
        assert orders[0].stop_loss_price is None
        assert orders[0].take_profit_price is None

    def test_a_failed_call_raises_rather_than_reading_as_empty(self) -> None:
        fake = FakeMt5(orders=None, error=(-10004, "No IPC connection"))
        with pytest.raises(Mt5CallFailedError, match="No IPC connection"):
            gateway(fake).pending_orders()

    def test_a_none_result_with_success_reads_as_no_pending_orders(self) -> None:
        assert gateway(FakeMt5(orders=None, error=(1, "Success"))).pending_orders() == ()


# --------------------------------------------------------------------------- #
# Ticks and bars — HANDOVER.md §4.5, the continuous-read half of M1
# --------------------------------------------------------------------------- #


def a_tick_row(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "time": 1_767_000_300,
        "time_msc": 1_767_000_300_123,
        "bid": 1.16700,
        "ask": 1.16706,
        "last": 0.0,
        "volume": 0,
        "volume_real": 0.0,
        "flags": 6,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def a_bar_row(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        # An hour before FAKE_NOW/gateway()'s default clock, so a plain
        # a_bar_row() is a closed bar by default (D-042) rather than sitting
        # right at the boundary of "still forming".
        "time": 1_767_000_000 - 3600,
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


class TestTicks:
    def test_ticks_are_read_and_converted(self) -> None:
        class WithTicks(FakeMt5):
            def copy_ticks_from(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return (
                    a_tick_row(),
                    a_tick_row(
                        time=1_767_000_301, time_msc=1_767_000_301_500, bid=1.16702, ask=1.16708
                    ),
                )

        ticks = gateway(WithTicks()).ticks(
            "EUR/USD",
            since=datetime.fromtimestamp(1_767_000_000, tz=UTC),
            count=10,
            source="mt5:PepperstoneUK-Demo",
        )
        assert len(ticks) == 2
        assert ticks[0].bid == Decimal("1.167")
        assert ticks[0].ask == Decimal("1.16706")
        assert ticks[0].source == "mt5:PepperstoneUK-Demo"
        assert ticks[0].canonical_symbol == "EUR/USD"
        assert ticks[0].broker_symbol == "EURUSD"

    def test_time_msc_gives_millisecond_precision(self) -> None:
        class WithTicks(FakeMt5):
            def copy_ticks_from(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return (a_tick_row(time_msc=1_767_000_300_500),)

        ticks = gateway(WithTicks()).ticks(
            "EUR/USD", since=datetime.fromtimestamp(0, tz=UTC), count=10, source="s"
        )
        assert ticks[0].event_time_utc.microsecond == 500_000

    def test_a_missing_time_msc_falls_back_to_second_precision(self) -> None:
        class WithTicks(FakeMt5):
            def copy_ticks_from(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return (a_tick_row(time_msc=0),)

        ticks = gateway(WithTicks()).ticks(
            "EUR/USD", since=datetime.fromtimestamp(0, tz=UTC), count=10, source="s"
        )
        assert ticks[0].event_time_utc == datetime.fromtimestamp(1_767_000_300, tz=UTC)

    def test_zero_last_is_treated_as_absent(self) -> None:
        """MT5 reports 0.0 for `last` on a symbol with no last-trade price."""

        class WithTicks(FakeMt5):
            def copy_ticks_from(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return (a_tick_row(last=0.0),)

        ticks = gateway(WithTicks()).ticks(
            "EUR/USD", since=datetime.fromtimestamp(0, tz=UTC), count=10, source="s"
        )
        assert ticks[0].last is None

    def test_real_numpy_structured_rows_convert_without_crashing(self) -> None:
        """Found on the first real soak, 2026-08-24: `copy_ticks_from` returns a
        numpy structured array, not `SimpleNamespace` rows of plain floats.
        `numpy.float64` subclasses `float`, so `isinstance` did not catch it,
        but numpy 2.x's own scalar `__repr__` — `"np.float64(1.167)"` — is not
        something `Decimal()` can parse. Every tick crashed the reader before
        this was fixed; this test uses an actual structured array, not a
        stand-in, so the fix cannot silently regress.
        """
        import numpy as np

        dtype = np.dtype(
            [
                ("time", "i8"),
                ("time_msc", "i8"),
                ("bid", "f8"),
                ("ask", "f8"),
                ("last", "f8"),
                ("volume", "i8"),
                ("flags", "i4"),
            ]
        )
        rows = np.array(
            [(1_767_000_300, 1_767_000_300_123, 1.16700, 1.16706, 0.0, 0, 6)], dtype=dtype
        )

        class WithTicks(FakeMt5):
            def copy_ticks_from(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return tuple(rows)

        ticks = gateway(WithTicks()).ticks(
            "EUR/USD", since=datetime.fromtimestamp(0, tz=UTC), count=10, source="s"
        )
        assert ticks[0].bid == Decimal("1.167")
        assert ticks[0].ask == Decimal("1.16706")


class TestBars:
    def test_bars_are_read_converted_and_normalized(self) -> None:
        class WithBars(FakeMt5):
            def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return (
                    a_bar_row(time=1_767_000_000 - 3600),
                    a_bar_row(time=1_767_000_000 - 3300, open=1.16720, close=1.16740),
                )

        result = gateway(WithBars()).bars(
            "EUR/USD", timeframe="M5", count=10, source="mt5:PepperstoneUK-Demo"
        )
        assert len(result.bars) == 2
        assert result.bars[0].origin is BarOrigin.BROKER
        assert result.bars[0].source == "mt5:PepperstoneUK-Demo"
        assert result.bars[0].bar.open == Decimal("1.167")
        assert result.is_clean

    def test_bars_delivered_out_of_order_are_sorted_before_normalizing(self) -> None:
        """Documented delivery order is not trusted any more than a stringified enum was."""

        class WithBars(FakeMt5):
            def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return (
                    a_bar_row(time=1_767_000_000 - 3300),
                    a_bar_row(time=1_767_000_000 - 3600),
                )

        result = gateway(WithBars()).bars("EUR/USD", timeframe="M5", count=10, source="s")
        assert result.is_clean
        opens = [bar.bar.open_time_utc for bar in result.bars]
        assert opens == sorted(opens)

    def test_an_unsupported_timeframe_fails_loudly(self) -> None:
        with pytest.raises(KeyError, match="M2"):
            gateway(FakeMt5()).bars("EUR/USD", timeframe="M2", count=10, source="s")

    def test_the_still_forming_current_bar_is_not_returned(self) -> None:
        """Found on the third real soak: `copy_rates_from_pos(..., 0, count)`

        position 0 is MT5's current, not-yet-closed bar. Its close moves every
        poll until the interval ends, and persisting it as though it were a
        finished, immutable bar is exactly what made `record_bars` see a
        contradiction and halt the live reader. A bar only belongs in what
        this gateway returns once its own interval has actually finished.
        """
        now = FAKE_NOW
        closed_open = now - timedelta(minutes=15)  # M5: closed 10 minutes ago
        forming_open = now - timedelta(minutes=1)  # M5: only 1 minute in, still open

        class WithBars(FakeMt5):
            def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return (
                    a_bar_row(time=int(closed_open.timestamp())),
                    a_bar_row(time=int(forming_open.timestamp())),
                )

        result = gateway(WithBars()).bars("EUR/USD", timeframe="M5", count=10, source="s")

        assert len(result.bars) == 1
        assert result.bars[0].bar.open_time_utc.timestamp() == int(closed_open.timestamp())

    def test_a_bar_that_closes_between_two_polls_is_returned_once_settled(self) -> None:
        """The other half of the same fix: nothing is lost, only delayed."""
        now = FAKE_NOW
        settled = now - timedelta(minutes=5) - timedelta(seconds=31)  # past the settle buffer

        class WithBars(FakeMt5):
            def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return (a_bar_row(time=int(settled.timestamp())),)

        result = gateway(WithBars()).bars("EUR/USD", timeframe="M5", count=10, source="s")

        assert len(result.bars) == 1

    def test_a_bar_still_inside_the_settle_buffer_is_not_yet_returned(self) -> None:
        """D-042 addendum, fifth real soak: MT5 revised a bar's tick_volume a

        few seconds after its raw boundary closed. A bar just past the raw
        boundary but still inside `_BAR_SETTLE_BUFFER` must not be returned
        yet — that revision window is exactly what it exists to absorb.
        """
        now = FAKE_NOW
        just_past_boundary = now - timedelta(minutes=5, seconds=1)  # closed, but not settled

        class WithBars(FakeMt5):
            def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return (a_bar_row(time=int(just_past_boundary.timestamp())),)

        result = gateway(WithBars()).bars("EUR/USD", timeframe="M5", count=10, source="s")

        assert len(result.bars) == 0

    def test_real_numpy_structured_rows_convert_without_crashing(self) -> None:
        """The bar half of the same numpy 2.x repr defect as `TestTicks`'s."""
        import numpy as np

        dtype = np.dtype(
            [
                ("time", "i8"),
                ("open", "f8"),
                ("high", "f8"),
                ("low", "f8"),
                ("close", "f8"),
                ("tick_volume", "i8"),
                ("spread", "i4"),
                ("real_volume", "i8"),
            ]
        )
        rows = np.array(
            [(1_767_000_000 - 3600, 1.16700, 1.16750, 1.16680, 1.16720, 120, 6, 0)], dtype=dtype
        )

        class WithBars(FakeMt5):
            def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                return tuple(rows)

        result = gateway(WithBars()).bars("EUR/USD", timeframe="M5", count=10, source="s")
        assert result.bars[0].bar.open == Decimal("1.167")
        assert result.bars[0].bar.high == Decimal("1.1675")


# --------------------------------------------------------------------------- #
# D-039: the terminal's clock is not assumed to be UTC
# --------------------------------------------------------------------------- #


class TestClockOffset:
    """Settled by observation on the fourth real soak, 2026-08-24: Pepperstone's

    MT5 server clock ran a stable ~2:59:39-2:59:40 ahead of true UTC once the
    reader had caught up to live data — not latency jitter, a genuine
    UTC-labelling bug. `_clock_offset()` measures the gap between the
    terminal's own current tick and this platform's clock, once per gateway
    instance (i.e. once per `LiveReader` reconnect), and every timestamp this
    gateway converts is corrected by it.
    """

    def test_a_broker_clock_ahead_of_utc_is_detected_and_corrected(self) -> None:
        three_hours_ahead = FAKE_NOW + timedelta(hours=3)

        class ShiftedClock(FakeMt5):
            def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
                return SimpleNamespace(
                    bid=1.16700, ask=1.16706, time=int(three_hours_ahead.timestamp())
                )

            def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
                # A bar the broker's clock calls "closed" 10 minutes ago —
                # i.e. genuinely closed only once its mislabelled timestamp
                # is corrected back to true UTC.
                broker_open = three_hours_ahead - timedelta(minutes=15)
                return (a_bar_row(time=int(broker_open.timestamp())),)

        result = gateway(ShiftedClock()).bars("EUR/USD", timeframe="M5", count=10, source="s")

        assert len(result.bars) == 1
        expected_true_utc = three_hours_ahead - timedelta(minutes=15, hours=3)
        assert result.bars[0].bar.open_time_utc == expected_true_utc

    def test_the_offset_rounds_to_the_nearest_thirty_minutes(self) -> None:
        """A live measurement carries a little call latency; GMT offsets never do."""
        almost_three_hours = FAKE_NOW + timedelta(hours=3) - timedelta(seconds=20)

        class ShiftedClock(FakeMt5):
            def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
                return SimpleNamespace(
                    bid=1.16700, ask=1.16706, time=int(almost_three_hours.timestamp())
                )

        gw = gateway(ShiftedClock())
        assert gw._clock_offset() == timedelta(hours=3)

    def test_the_since_parameter_is_shifted_into_broker_clock_terms(self) -> None:
        """Not correcting `since` too is what produced a several-hour tick

        backlog on the fourth real soak: asking for ticks "since true-UTC
        now minus 5 minutes" is, on a clock running 3 hours ahead, a request
        for data starting just over 3 hours in that clock's own past.
        """
        three_hours_ahead = FAKE_NOW + timedelta(hours=3)
        seen_since: list[datetime] = []

        class ShiftedClock(FakeMt5):
            def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
                return SimpleNamespace(
                    bid=1.16700, ask=1.16706, time=int(three_hours_ahead.timestamp())
                )

            def copy_ticks_from(
                self, _symbol: str, since: datetime, *_args: Any
            ) -> tuple[Any, ...]:
                seen_since.append(since)
                return ()

        requested_since = three_hours_ahead - timedelta(hours=3, minutes=5)

        gateway(ShiftedClock()).ticks("EUR/USD", since=requested_since, count=10, source="s")

        assert seen_since == [three_hours_ahead - timedelta(minutes=5)]

    def test_a_zero_offset_leaves_timestamps_untouched(self) -> None:
        """The ordinary case, exercised everywhere else in this file — named
        once, explicitly, so the zero case is not only ever implicit."""
        assert gateway(FakeMt5())._clock_offset() == timedelta(0)

    def test_a_stale_reference_tick_is_rejected_rather_than_mis_measured(self) -> None:
        """F-040: a tick whose timestamp has drifted away from a clean

        half-hour multiple — e.g. the terminal handing back the last quote
        it ever saw, from well before now — must not be silently accepted
        as this session's broker-clock offset.
        """
        stale = FAKE_NOW + timedelta(hours=3) - timedelta(minutes=10)

        class StaleClock(FakeMt5):
            def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
                return SimpleNamespace(bid=1.16700, ask=1.16706, time=int(stale.timestamp()))

        with pytest.raises(ClockOffsetUnavailableError):
            gateway(StaleClock())._clock_offset()

    def test_a_wildly_implausible_offset_is_rejected(self) -> None:
        """No real GMT offset is 20 hours; that is bad input, not a timezone."""
        implausible = FAKE_NOW + timedelta(hours=20)

        class ImplausibleClock(FakeMt5):
            def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
                return SimpleNamespace(bid=1.16700, ask=1.16706, time=int(implausible.timestamp()))

        with pytest.raises(ClockOffsetUnavailableError):
            gateway(ImplausibleClock())._clock_offset()

    def test_a_rejected_measurement_is_retried_fresh_not_cached(self) -> None:
        """A stale reading must not poison every later call on this gateway

        instance — the next attempt, with a fresh tick, should succeed
        normally once the feed recovers.
        """
        stale = FAKE_NOW + timedelta(hours=3) - timedelta(minutes=10)
        fresh = FAKE_NOW + timedelta(hours=3)

        class RecoveringClock(FakeMt5):
            def __init__(self) -> None:
                super().__init__()
                self._calls = 0

            def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
                self._calls += 1
                when = stale if self._calls == 1 else fresh
                return SimpleNamespace(bid=1.16700, ask=1.16706, time=int(when.timestamp()))

        gw = gateway(RecoveringClock())
        with pytest.raises(ClockOffsetUnavailableError):
            gw._clock_offset()
        assert gw._clock_offset() == timedelta(hours=3)


# --------------------------------------------------------------------------- #
# Health and availability
# --------------------------------------------------------------------------- #


class TestTerminalHealth:
    def test_health_reports_the_terminal_build(self) -> None:
        health = gateway(FakeMt5()).terminal_health()
        assert health["connected"] is True
        assert health["build"] == 4620

    def test_health_carries_a_utc_timestamp(self) -> None:
        assert gateway(FakeMt5()).terminal_health()["observed_at_utc"].endswith("+00:00")


class TestModuleAvailability:
    def test_importing_the_real_package_explains_itself_when_absent(self) -> None:
        """This host is macOS; the package ships Windows wheels only.

        Deliberately checked via ``platform.system()``, not ``sys.platform``:
        mypy statically resolves ``sys.platform`` comparisons against the
        platform it runs on, so on a Windows run it would treat the branch
        below as always taken and flag the `with` block as unreachable.
        """
        import platform

        if platform.system() == "Windows":  # pragma: no cover - not this host
            pytest.skip("MetaTrader5 may genuinely be importable on Windows")
        with pytest.raises(Mt5UnavailableError, match="Windows"):
            load_mt5_module()
