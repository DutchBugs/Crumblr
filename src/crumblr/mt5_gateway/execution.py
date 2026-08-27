"""The order-check-capable, order-send-disabled MT5 adapter (Phase 4, ADR-001).

Deliberately a separate class from `ReadOnlyMt5Gateway` (D-036: execution must
be a separate adapter satisfying the same `BrokerPort`, not a modification of
the read-only one). `order_check` is the one real, live broker interaction
this class performs — MT5's `order_check` is a server-side dry run: it
validates a request and reports margin/rejection information, but creates no
ticket and no market exposure.

`order_send`, `cancel_pending_orders` and `close_all_positions` always raise
`ExecutionDisabledError`, unconditionally. There is no config flag read
inside any of those three methods — nothing here could switch them on by
mistake, because the code simply does not implement them. That is what
"non-sending" means structurally rather than as a promise: see
`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` point 8 and ADR-001.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any, NoReturn

from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import Side
from crumblr.domain.events import OrderCheckCompleted
from crumblr.domain.models import AccountState, ApprovedOrder, InstrumentSpec, PositionState
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.mt5_gateway.client import Mt5CallFailedError, Mt5Client
from crumblr.mt5_gateway.port import ExecutionDisabledError
from crumblr.mt5_gateway.readonly import ReadOnlyMt5Gateway
from crumblr.observability.logging import get_logger

_log = get_logger("mt5_gateway")

_ORDER_TYPE_CONSTANT_BY_SIDE: dict[Side, str] = {
    Side.BUY: "ORDER_TYPE_BUY",
    Side.SELL: "ORDER_TYPE_SELL",
}
"""Which `Mt5Module` constant name encodes a side's market order type.

Read off the real module at call time by name (D-037 discipline: values
passed *to* MT5 get the same "never hardcode the integer" treatment as
values decoded *from* it) — see `Mt5Module.ORDER_TYPE_BUY`/`ORDER_TYPE_SELL`
in `mt5_gateway/client.py`.
"""


def _decimal_from_mt5(value: Any) -> Decimal:
    """Convert an `order_check` result field to `Decimal` without binary float.

    Mirrors `readonly.py::_to_decimal`'s technique (`Decimal(repr(float(...)))`,
    never `Decimal(float(...))`) for the same reason: `OrderCheckResult`'s
    numeric fields are plain Python floats, and going through `repr` avoids
    a binary-float artifact leaking into a persisted Decimal. Pydantic
    coerces the result into `OrderCheckCompleted.margin_required`'s
    `ExactDecimal` field on construction below.
    """
    return Decimal(repr(float(value)))


class OrderCheckMt5Gateway:
    """Implements `BrokerPort`. Real `order_check`; everything else refuses.

    Every read operation delegates to an internally held `ReadOnlyMt5Gateway`
    (composition, not duplication) — this class adds exactly one real
    capability on top of it, and only that one.
    """

    def __init__(
        self,
        client: Mt5Client,
        guard: AccountGuardConfig,
        *,
        canonical_symbol: str = "EUR/USD",
        clock: Callable[[], UtcDatetime] = utc_now,
    ) -> None:
        self._client = client
        self._reader = ReadOnlyMt5Gateway(
            client, guard, canonical_symbol=canonical_symbol, clock=clock
        )

    @property
    def reader(self) -> ReadOnlyMt5Gateway:
        """The underlying M1 gateway, for callers that need it by that type

        — `application/broker_state.py::capture_broker_state` takes a
        `ReadOnlyMt5Gateway` specifically. Exposed rather than duplicated:
        every read this class offers already delegates to the same
        instance."""
        return self._reader

    # ------------------------------------------------------------------ #
    # Read operations — delegated to the M1 read-only gateway
    # ------------------------------------------------------------------ #

    def account(self) -> AccountState:
        return self._reader.account()

    def instrument(self, canonical_symbol: str) -> InstrumentSpec:
        return self._reader.instrument(canonical_symbol)

    def positions(self) -> tuple[PositionState, ...]:
        return self._reader.positions()

    def terminal_health(self) -> dict[str, Any]:
        """Facts about the terminal, including `trade_allowed` — the
        execution multi-gate's "terminal AlgoTrading enabled" leg."""
        return self._reader.terminal_health()

    # ------------------------------------------------------------------ #
    # order_check — real, live, non-mutating
    # ------------------------------------------------------------------ #

    def order_check(self, order: ApprovedOrder) -> OrderCheckCompleted:
        """Ask the broker to validate `order` without placing it.

        The filling mode is requested as IOC — broadly supported for FX
        majors, and if the connected symbol genuinely does not support it,
        that is exactly the kind of thing `order_check` exists to catch: the
        broker reports it in `retcode`/`comment` rather than this adapter
        silently picking a different one. `spec.filling_modes` is what a
        caller should consult if that is ever observed for real; nothing
        here guesses on its behalf.
        """
        module = self._client.module
        order_type_constant = _ORDER_TYPE_CONSTANT_BY_SIDE.get(order.side)
        if order_type_constant is None:
            raise ValueError(f"order_check has no MT5 market order type for side {order.side}")

        request: dict[str, Any] = {
            "action": module.TRADE_ACTION_DEAL,
            "symbol": order.broker_symbol,
            "volume": float(order.volume),
            "type": getattr(module, order_type_constant),
            "sl": float(order.stop_loss_price),
            "deviation": order.max_slippage_points,
            "type_time": module.ORDER_TIME_GTC,
            "type_filling": module.ORDER_FILLING_IOC,
        }
        if order.price is not None:
            request["price"] = float(order.price)
        if order.take_profit_price is not None:
            request["tp"] = float(order.take_profit_price)

        _log.info(
            "mt5.order_check",
            order_request_id=str(order.order_request_id),
            broker_symbol=order.broker_symbol,
            side=order.side.value,
            volume=str(order.volume),
        )
        result = module.order_check(request)
        if result is None:
            code, message = module.last_error()
            raise Mt5CallFailedError("order_check", code, message)

        retcode = int(result.retcode)
        accepted = retcode == module.TRADE_RETCODE_DONE
        comment = str(getattr(result, "comment", "")) or None
        payload = {
            "retcode": retcode,
            "balance": float(getattr(result, "balance", 0.0)),
            "equity": float(getattr(result, "equity", 0.0)),
            "profit": float(getattr(result, "profit", 0.0)),
            "margin": float(getattr(result, "margin", 0.0)),
            "margin_free": float(getattr(result, "margin_free", 0.0)),
            "margin_level": float(getattr(result, "margin_level", 0.0)),
            "comment": comment,
        }
        _log.info(
            "mt5.order_check_result",
            order_request_id=str(order.order_request_id),
            retcode=retcode,
            accepted=accepted,
        )
        return OrderCheckCompleted(
            order_request_id=order.order_request_id,
            intent_id=order.intent_id,
            accepted=accepted,
            retcode=retcode,
            comment=comment,
            margin_required=_decimal_from_mt5(getattr(result, "margin", 0.0)),
            payload=payload,
        )

    # ------------------------------------------------------------------ #
    # Execution — structurally disabled
    # ------------------------------------------------------------------ #

    def _refuse(self, operation: str) -> NoReturn:
        _log.error("mt5.execution_disabled", operation=operation)
        raise ExecutionDisabledError(
            f"{operation} is not available on the order-check-only execution "
            "gateway. order_send stays technically impossible until a "
            "future, human-approved SubmissionGate exists — see ADR-001 and "
            "review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md."
        )

    def order_send(self, order: ApprovedOrder) -> NoReturn:
        self._refuse("order_send")

    def cancel_pending_orders(self) -> NoReturn:
        self._refuse("cancel_pending_orders")

    def close_all_positions(self, *, reason: str) -> NoReturn:
        self._refuse("close_all_positions")
