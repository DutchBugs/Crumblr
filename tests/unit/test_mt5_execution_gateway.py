"""`OrderCheckMt5Gateway` (Phase 4), against a fake terminal.

**What these tests prove and what they do not.** They exercise the adapter:
a real `order_check` call reaches the fake module and its result decodes
correctly, reads delegate to `ReadOnlyMt5Gateway`, and `order_send` /
`cancel_pending_orders` / `close_all_positions` are unconditionally refused
— even given a request that looks entirely valid. They prove nothing about
how Pepperstone's real `order_check` actually behaves; that stays unproven
until this adapter runs against the real demo account, deliberately not yet
in this Phase-4 slice (`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import EntryType, Environment, Side
from crumblr.domain.models import ApprovedOrder
from crumblr.mt5_gateway.client import Mt5CallFailedError, Mt5Client, Mt5Credentials
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.mt5_gateway.port import ExecutionDisabledError

GUARD = AccountGuardConfig.model_validate(
    {
        "expected_server": "PepperstoneUK-Demo",
        "expected_login": None,
        "require_demo_account": True,
        "expected_currency": "EUR",
        "expected_leverage": 30,
    }
)

FAKE_NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def account_info(**overrides: Any) -> Any:
    from types import SimpleNamespace

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


def order_check_result(**overrides: Any) -> Any:
    from types import SimpleNamespace

    fields: dict[str, Any] = {
        "retcode": 0,  # TRADE_RETCODE_DONE, per FakeMt5 below
        "balance": 10_000.0,
        "equity": 10_012.5,
        "profit": 12.5,
        "margin": 43.2,
        "margin_free": 9_969.3,
        "margin_level": 23176.0,
        "comment": "Done",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeMt5:
    """A stand-in terminal exposing exactly the surface `OrderCheckMt5Gateway` uses."""

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
        account: Any | None = None,
        order_check_response: Any | None = None,
        error: tuple[int, str] = (1, "Success"),
    ) -> None:
        self._account = account if account is not None else account_info()
        self._order_check_response = order_check_response
        self._error = error
        self.order_check_requests: list[dict[str, Any]] = []
        self.order_send_calls = 0

    def initialize(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def login(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def shutdown(self) -> None:
        pass

    def last_error(self) -> tuple[int, str]:
        return self._error

    def version(self) -> tuple[Any, ...]:
        return (500, 4620, "20 Aug 2026")

    def terminal_info(self) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(connected=True, trade_allowed=True, ping_last=32)

    def account_info(self) -> Any:
        return self._account

    def symbols_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        from types import SimpleNamespace

        return (SimpleNamespace(name="EURUSD"),)

    def symbol_select(self, _symbol: str, _enable: bool) -> bool:
        return True

    def symbol_info(self, _symbol: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            currency_base="EUR",
            currency_profit="USD",
            trade_contract_size=100_000.0,
            digits=5,
            point=1e-05,
            trade_tick_size=1e-05,
            trade_tick_value=1.0,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_stops_level=10,
            trade_freeze_level=0,
            filling_mode=3,
            trade_mode=4,
        )

    def symbol_info_tick(self, _symbol: str) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(bid=1.08500, ask=1.08512, time=int(FAKE_NOW.timestamp()))

    def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()

    def copy_ticks_from(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()

    def positions_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...] | None:
        return ()

    def orders_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...] | None:
        return ()

    def order_check(self, request: dict[str, Any]) -> Any:
        self.order_check_requests.append(request)
        if self._order_check_response is _MISSING:
            return None
        return self._order_check_response if self._order_check_response else order_check_result()

    def order_send(self, *_args: Any, **_kwargs: Any) -> Any:
        self.order_send_calls += 1
        raise AssertionError("order_send must never be called by OrderCheckMt5Gateway")


_MISSING = object()


def approved_order(**overrides: Any) -> ApprovedOrder:
    fields: dict[str, Any] = {
        "order_request_id": uuid4(),
        "intent_id": uuid4(),
        "risk_decision_id": uuid4(),
        "supervisor_decision_id": uuid4(),
        "broker_symbol": "EURUSD",
        "side": Side.BUY,
        "entry_type": EntryType.MARKET,
        "volume": "0.10",
        "stop_loss_price": "1.08000",
        "max_slippage_points": 20,
        "created_at_utc": FAKE_NOW,
        "expires_at_utc": FAKE_NOW + timedelta(minutes=5),
        "environment": Environment.PAPER,
    }
    fields.update(overrides)
    return ApprovedOrder.model_validate(fields)


def gateway(fake: FakeMt5) -> OrderCheckMt5Gateway:
    client = Mt5Client(fake)
    client.connect(Mt5Credentials(login=5_000_123, password="x", server="PepperstoneUK-Demo"))
    return OrderCheckMt5Gateway(client, GUARD)


# --------------------------------------------------------------------------- #
# order_check — the one real capability
# --------------------------------------------------------------------------- #


class TestOrderCheck:
    def test_a_buy_reaches_the_real_order_check_call(self) -> None:
        fake = FakeMt5()
        gate = gateway(fake)
        order = approved_order(side=Side.BUY)

        result = gate.order_check(order)

        assert len(fake.order_check_requests) == 1
        request = fake.order_check_requests[0]
        assert request["type"] == FakeMt5.ORDER_TYPE_BUY
        assert request["action"] == FakeMt5.TRADE_ACTION_DEAL
        assert request["symbol"] == "EURUSD"
        assert request["volume"] == pytest.approx(0.10)
        assert result.order_request_id == order.order_request_id
        assert result.intent_id == order.intent_id
        assert result.accepted is True
        assert result.retcode == 0

    def test_a_sell_requests_the_sell_order_type(self) -> None:
        fake = FakeMt5()
        gate = gateway(fake)
        order = approved_order(side=Side.SELL, stop_loss_price="1.09000", take_profit_price=None)

        gate.order_check(order)

        assert fake.order_check_requests[0]["type"] == FakeMt5.ORDER_TYPE_SELL

    def test_a_rejected_check_is_reported_as_not_accepted(self) -> None:
        fake = FakeMt5(order_check_response=order_check_result(retcode=10_019, comment="No money"))
        gate = gateway(fake)

        result = gate.order_check(approved_order())

        assert result.accepted is False
        assert result.retcode == 10_019
        assert result.comment == "No money"

    def test_a_missing_response_raises_with_the_terminal_reason(self) -> None:
        fake = FakeMt5(order_check_response=_MISSING, error=(4, "No connection"))
        gate = gateway(fake)

        with pytest.raises(Mt5CallFailedError, match="order_check"):
            gate.order_check(approved_order())

    def test_order_send_is_never_called_by_order_check(self) -> None:
        fake = FakeMt5()
        gate = gateway(fake)

        gate.order_check(approved_order())

        assert fake.order_send_calls == 0


# --------------------------------------------------------------------------- #
# Everything else — structurally disabled
# --------------------------------------------------------------------------- #


class TestExecutionStaysDisabled:
    """order_send stays technically impossible, even for a well-formed order."""

    def test_order_send_always_raises(self) -> None:
        gate = gateway(FakeMt5())
        with pytest.raises(ExecutionDisabledError, match="order_send"):
            gate.order_send(approved_order())

    def test_cancel_pending_orders_always_raises(self) -> None:
        gate = gateway(FakeMt5())
        with pytest.raises(ExecutionDisabledError, match="cancel_pending_orders"):
            gate.cancel_pending_orders()

    def test_close_all_positions_always_raises(self) -> None:
        gate = gateway(FakeMt5())
        with pytest.raises(ExecutionDisabledError, match="close_all_positions"):
            gate.close_all_positions(reason="test")


# --------------------------------------------------------------------------- #
# Reads — delegated to ReadOnlyMt5Gateway
# --------------------------------------------------------------------------- #


class TestReadsDelegate:
    def test_account_matches_the_read_only_gateway(self) -> None:
        gate = gateway(FakeMt5())
        state = gate.account()
        assert state.server == "PepperstoneUK-Demo"
        assert state.is_demo is True

    def test_terminal_health_exposes_trade_allowed(self) -> None:
        gate = gateway(FakeMt5())
        health = gate.terminal_health()
        assert health["trade_allowed"] is True
