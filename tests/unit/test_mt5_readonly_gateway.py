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

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import Side
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
        "filling_mode": "IOC",
        "trade_mode": "FULL",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeMt5:
    """A stand-in terminal.

    Mimics MT5's actual convention: failures return `None`/`False` and leave the
    reason in `last_error()`. Anything that pretended failures raise would test
    a terminal that does not exist.
    """

    def __init__(
        self,
        *,
        symbols: tuple[str, ...] = ("EURUSD",),
        account: SimpleNamespace | None = None,
        positions: tuple[SimpleNamespace, ...] | None = (),
        error: tuple[int, str] = (1, "Success"),
    ) -> None:
        self._symbols = symbols
        self._account = account if account is not None else account_info()
        self._positions = positions
        self._error = error
        self.selected: list[str] = []
        self.shutdown_calls = 0
        self.login_calls: list[dict[str, Any]] = []
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

    def orders_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()


def gateway(fake: FakeMt5, *, guard: AccountGuardConfig = GUARD) -> ReadOnlyMt5Gateway:
    client = Mt5Client(fake)
    client.connect(Mt5Credentials(login=5_000_123, password="x", server="PepperstoneUK-Demo"))
    return ReadOnlyMt5Gateway(client, guard)


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
        """This host is macOS; the package ships Windows wheels only."""
        import sys

        if sys.platform == "win32":  # pragma: no cover - not this host
            pytest.skip("MetaTrader5 may genuinely be importable on Windows")
        with pytest.raises(Mt5UnavailableError, match="Windows"):
            load_mt5_module()
