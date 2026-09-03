"""`DemoOrderSendMt5Gateway` (Phase B item B1), against a fake terminal.

**What these tests prove and what they do not.** They exercise the adapter:
a real `order_send` call reaches the fake module and its result decodes
correctly, reads/`order_check` delegate to the wrapped `OrderCheckMt5Gateway`
unchanged, a non-demo/mismatched account is refused before any broker write
is attempted, and `cancel_pending_orders`/`close_all_positions` stay
unconditionally refused (Phase B item B5's scope, not this one's). They
prove nothing about how Pepperstone's real `order_send` actually behaves —
that stays unproven until this adapter runs against the real demo account.
**This class is not constructed or referenced anywhere in
`application/execution.py` — see `TestNotWiredIntoTheOrchestrator` below.**
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import EntryType, Environment, OrderState, Side
from crumblr.domain.models import ApprovedOrder
from crumblr.mt5_gateway.client import Mt5CallFailedError, Mt5Client, Mt5Credentials
from crumblr.mt5_gateway.demo_execution import DemoOrderSendMt5Gateway
from crumblr.mt5_gateway.execution import MissingFinalRiskDecisionError, OrderCheckMt5Gateway
from crumblr.mt5_gateway.port import ExecutionDisabledError
from crumblr.mt5_gateway.readonly import AccountGuardError

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

ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_REAL = 2


def account_info(**overrides: Any) -> Any:
    from types import SimpleNamespace

    fields: dict[str, Any] = {
        "login": 5_000_123,
        "server": "PepperstoneUK-Demo",
        "currency": "EUR",
        "trade_mode": ACCOUNT_TRADE_MODE_DEMO,
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
        "retcode": 0,
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


def order_send_result(**overrides: Any) -> Any:
    from types import SimpleNamespace

    fields: dict[str, Any] = {
        "retcode": 0,  # TRADE_RETCODE_DONE
        "order": 700001,
        "deal": 700002,
        "volume": 0.10,
        "price": 1.08505,
        "bid": 1.08500,
        "ask": 1.08512,
        "comment": "Request executed",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeMt5:
    """A stand-in terminal exposing exactly the surface both

    `OrderCheckMt5Gateway` and `DemoOrderSendMt5Gateway` use."""

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
    TRADE_RETCODE_DONE_PARTIAL = 1

    def __init__(
        self,
        *,
        account: Any | None = None,
        order_check_response: Any | None = None,
        order_send_response: Any | None = None,
        error: tuple[int, str] = (1, "Success"),
    ) -> None:
        self._account = account if account is not None else account_info()
        self._order_check_response = order_check_response
        self._order_send_response = order_send_response
        self._error = error
        self.order_check_requests: list[dict[str, Any]] = []
        self.order_send_requests: list[dict[str, Any]] = []
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

    def order_send(self, request: dict[str, Any]) -> Any:
        self.order_send_calls += 1
        self.order_send_requests.append(request)
        if self._order_send_response is _MISSING:
            return None
        return self._order_send_response if self._order_send_response else order_send_result()


_MISSING = object()


def approved_order(**overrides: Any) -> ApprovedOrder:
    fields: dict[str, Any] = {
        "order_request_id": uuid4(),
        "intent_id": uuid4(),
        "intent_risk_decision_id": uuid4(),
        "final_risk_decision_id": uuid4(),
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


def gateway(fake: FakeMt5) -> DemoOrderSendMt5Gateway:
    client = Mt5Client(fake)
    client.connect(Mt5Credentials(login=5_000_123, password="x", server="PepperstoneUK-Demo"))
    order_check_gateway = OrderCheckMt5Gateway(client, GUARD)
    return DemoOrderSendMt5Gateway(order_check_gateway, client)


# --------------------------------------------------------------------------- #
# order_send — the one real, new capability
# --------------------------------------------------------------------------- #


class TestOrderSend:
    def test_a_buy_reaches_the_real_order_send_call_with_the_same_shape_as_order_check(
        self,
    ) -> None:
        fake = FakeMt5()
        gate = gateway(fake)
        order = approved_order(side=Side.BUY)

        # Same order, both calls — proves order_check validates exactly
        # what order_send would submit (the shared build_market_order_request).
        gate.order_check(order)
        gate.order_send(order)

        assert len(fake.order_send_requests) == 1
        checked = fake.order_check_requests[0]
        sent = fake.order_send_requests[0]
        assert sent == checked
        assert sent["magic"] == order.magic_number
        assert sent["symbol"] == "EURUSD"
        assert sent["volume"] == pytest.approx(0.10)

    def test_a_sell_requests_the_sell_order_type(self) -> None:
        fake = FakeMt5()
        gate = gateway(fake)
        order = approved_order(side=Side.SELL, stop_loss_price="1.09000", take_profit_price=None)

        gate.order_send(order)

        assert fake.order_send_requests[0]["type"] == FakeMt5.ORDER_TYPE_SELL

    def test_a_full_fill_decodes_as_filled(self) -> None:
        fake = FakeMt5()
        gate = gateway(fake)
        order = approved_order()

        result = gate.order_send(order)

        assert result.order_request_id == order.order_request_id
        assert result.intent_id == order.intent_id
        assert result.state is OrderState.FILLED
        assert result.retcode == 0
        assert result.mt5_order_ticket == 700001
        assert result.mt5_deal_ticket == 700002
        assert result.executed_volume == Decimal("0.10")
        assert result.requested_volume == order.volume
        assert result.order_send_payload is not None
        assert result.request_payload == fake.order_send_requests[0]

    def test_a_partial_fill_decodes_as_partially_filled(self) -> None:
        fake = FakeMt5(order_send_response=order_send_result(retcode=1, volume=0.05))
        gate = gateway(fake)

        result = gate.order_send(approved_order())

        assert result.state is OrderState.PARTIALLY_FILLED

    def test_any_other_retcode_decodes_as_rejected(self) -> None:
        fake = FakeMt5(
            order_send_response=order_send_result(retcode=10_019, comment="No money", order=0)
        )
        gate = gateway(fake)

        result = gate.order_send(approved_order())

        assert result.state is OrderState.REJECTED
        assert result.retcode_comment == "No money"
        assert result.mt5_order_ticket is None

    def test_a_missing_response_raises_with_the_terminal_reason(self) -> None:
        fake = FakeMt5(order_send_response=_MISSING, error=(4, "No connection"))
        gate = gateway(fake)

        with pytest.raises(Mt5CallFailedError, match="order_send"):
            gate.order_send(approved_order())

    def test_an_order_with_no_final_risk_linkage_is_refused(self) -> None:
        fake = FakeMt5()
        gate = gateway(fake)
        order = approved_order(final_risk_decision_id=None)

        with pytest.raises(MissingFinalRiskDecisionError):
            gate.order_send(order)

        assert not fake.order_send_requests


# --------------------------------------------------------------------------- #
# The demo-only guard — B1's central safety requirement
# --------------------------------------------------------------------------- #


class TestDemoOnlyGuard:
    """Refusal happens before any broker write is attempted, even for an

    otherwise well-formed order — no new demo-check mechanism, this
    reuses `ReadOnlyMt5Gateway._verify_account`'s existing, already-tested
    guard by calling `self.account()` first.
    """

    def test_a_live_account_is_refused_before_any_send_is_attempted(self) -> None:
        fake = FakeMt5(account=account_info(trade_mode=ACCOUNT_TRADE_MODE_REAL))
        gate = gateway(fake)

        with pytest.raises(AccountGuardError, match="not a demo account"):
            gate.order_send(approved_order())

        assert fake.order_send_calls == 0

    def test_a_mismatched_server_is_refused_before_any_send_is_attempted(self) -> None:
        fake = FakeMt5(account=account_info(server="SomeOtherBroker-Demo"))
        gate = gateway(fake)

        with pytest.raises(AccountGuardError, match="server"):
            gate.order_send(approved_order())

        assert fake.order_send_calls == 0

    def test_a_genuine_demo_account_is_not_refused(self) -> None:
        fake = FakeMt5()
        gate = gateway(fake)

        result = gate.order_send(approved_order())

        assert result.state is OrderState.FILLED


# --------------------------------------------------------------------------- #
# Reads and order_check — delegated to OrderCheckMt5Gateway, unchanged
# --------------------------------------------------------------------------- #


class TestReadsAndOrderCheckDelegate:
    def test_account_matches_the_wrapped_gateway(self) -> None:
        gate = gateway(FakeMt5())
        state = gate.account()
        assert state.server == "PepperstoneUK-Demo"
        assert state.is_demo is True

    def test_reader_is_the_same_underlying_gateway(self) -> None:
        fake = FakeMt5()
        client = Mt5Client(fake)
        client.connect(Mt5Credentials(login=5_000_123, password="x", server="PepperstoneUK-Demo"))
        order_check_gateway = OrderCheckMt5Gateway(client, GUARD)
        gate = DemoOrderSendMt5Gateway(order_check_gateway, client)

        assert gate.reader is order_check_gateway.reader

    def test_order_check_delegates_and_stays_non_mutating(self) -> None:
        fake = FakeMt5()
        gate = gateway(fake)

        result = gate.order_check(approved_order())

        assert result.accepted is True
        assert fake.order_send_calls == 0


# --------------------------------------------------------------------------- #
# Everything else — still structurally disabled (Phase B item B5)
# --------------------------------------------------------------------------- #


class TestStillDisabled:
    def test_cancel_pending_orders_still_refuses(self) -> None:
        gate = gateway(FakeMt5())
        with pytest.raises(ExecutionDisabledError, match="cancel_pending_orders"):
            gate.cancel_pending_orders()

    def test_close_all_positions_still_refuses(self) -> None:
        gate = gateway(FakeMt5())
        with pytest.raises(ExecutionDisabledError, match="close_all_positions"):
            gate.close_all_positions(reason="test")


# --------------------------------------------------------------------------- #
# Structural proof this slice does not wire the new adapter in
# --------------------------------------------------------------------------- #


class TestNotWiredIntoTheOrchestrator:
    def test_execution_orchestrator_never_references_the_new_adapter(self) -> None:
        """Phase B items B1+B2's own scope decision: this class exists,

        fully real and tested, but nothing in `ExecutionOrchestrator`'s
        own code holds a reference to it — wiring the real call site in
        is deferred until the account pin (B7), the one-shot canary
        permit (B8) and the shared execution/Risk authority (Phase
        C/AG-012) actually exist. A direct, mechanical proof, mirroring
        `test_execution_reconciliation.py::TestStillInert`'s own
        `inspect.getsource` idiom.
        """
        import inspect

        from crumblr.application import execution

        source = inspect.getsource(execution)
        assert "DemoOrderSendMt5Gateway" not in source
