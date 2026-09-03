"""A real, but separate and unwired, `order_send` adapter (Phase B item B1,

`review/adr/ADR-016-demo-order-send-adapter.md`).

`OrderCheckMt5Gateway` (`mt5_gateway/execution.py`) stays order-check-only,
per B1's own instruction — this module adds a *separate* class rather than
turning that one from non-sending to sending invisibly. `DemoOrderSendMt5Gateway`
wraps an `OrderCheckMt5Gateway` by composition (delegating every read and
`order_check` to it, unchanged) and adds exactly one real capability:
`order_send`, permanently scoped to demo accounts via the existing
`AccountGuardConfig`/`ReadOnlyMt5Gateway._verify_account` mechanism — no new
demo-check logic was needed, only calling `self.account()` first.

**Not constructed or referenced anywhere in `application/execution.py`
today.** Phase B item B2's own required chain includes two steps that do not
exist yet (Phase C/AG-012's shared execution/Risk authority, item B8's
one-shot canary permit) — rather than wiring the real call site behind
throwaway placeholder gates for those, this class exists, fully real and
tested, but genuinely unreachable: nothing in the orchestrator's own code
holds a reference to it. Wiring it in is deferred to a later slice, once the
account pin (B7), the permit (B8) and AG-012 actually exist.

`cancel_pending_orders`/`close_all_positions` still refuse — Phase B item B5's
scope, not this one's.
"""

from __future__ import annotations

from typing import NoReturn
from uuid import uuid4

from crumblr.domain.enums import OrderState
from crumblr.domain.events import OrderCheckCompleted
from crumblr.domain.models import (
    AccountState,
    ApprovedOrder,
    ExecutionResult,
    InstrumentSpec,
    PositionState,
)
from crumblr.domain.timeutils import utc_now
from crumblr.mt5_gateway.client import Mt5CallFailedError, Mt5Client
from crumblr.mt5_gateway.execution import (
    MissingFinalRiskDecisionError,
    OrderCheckMt5Gateway,
    build_market_order_request,
    decimal_from_mt5,
)
from crumblr.mt5_gateway.readonly import ReadOnlyMt5Gateway
from crumblr.observability.logging import get_logger

_log = get_logger("mt5_gateway")


class DemoOrderSendMt5Gateway:
    """Implements `BrokerPort`. Adds exactly one real capability on top of

    `OrderCheckMt5Gateway`: a real `order_send`. See this module's own
    docstring for the demo-only guard and the deliberate "built but not
    wired" scope of Phase B items B1+B2.
    """

    def __init__(self, order_check_gateway: OrderCheckMt5Gateway, client: Mt5Client) -> None:
        self._order_check_gateway = order_check_gateway
        self._client = client

    @property
    def reader(self) -> ReadOnlyMt5Gateway:
        return self._order_check_gateway.reader

    # ------------------------------------------------------------------ #
    # Read operations and order_check — delegated, unchanged
    # ------------------------------------------------------------------ #

    def account(self) -> AccountState:
        return self._order_check_gateway.account()

    def instrument(self, canonical_symbol: str) -> InstrumentSpec:
        return self._order_check_gateway.instrument(canonical_symbol)

    def positions(self) -> tuple[PositionState, ...]:
        return self._order_check_gateway.positions()

    def order_check(self, order: ApprovedOrder) -> OrderCheckCompleted:
        return self._order_check_gateway.order_check(order)

    # ------------------------------------------------------------------ #
    # order_send — real, live, mutating; demo-only
    # ------------------------------------------------------------------ #

    def order_send(self, order: ApprovedOrder) -> ExecutionResult:
        """Submit `order` for real.

        `self.account()` runs first, deliberately, before anything else —
        `ReadOnlyMt5Gateway._verify_account` (already exercised by every
        other real call this platform makes) raises `AccountGuardError`
        on any account/server/currency/leverage mismatch, including a
        non-demo account, so a caller that got the environment wrong is
        refused here regardless of what any upstream gate already
        checked (Phase B item B1's own requirement). No new demo-check
        mechanism exists — this reuses the one every other real call
        already trusts.

        `state` is a best-effort three-way classification
        (FILLED/PARTIALLY_FILLED/REJECTED), matching `order_check`'s own
        DONE-vs-not-DONE precision level — Phase B item B3 is the
        separate, later slice that turns a real broker response into
        full, durable, orchestrator-level outcome semantics (including
        transport exceptions/timeouts as distinct from a broker
        rejection). This method's only job is to make one real, honest,
        correctly-shaped call and decode it faithfully.
        """
        self.account()
        if order.final_risk_decision_id is None:
            _log.error(
                "mt5.order_send_missing_final_risk",
                order_request_id=str(order.order_request_id),
            )
            raise MissingFinalRiskDecisionError(
                f"order_send refused for order_request_id={order.order_request_id}: "
                "final_risk_decision_id is None, meaning no FINAL execution-time Risk "
                "revalidation is on record for this order (ADR-001) — the real broker "
                "boundary will not submit an order it cannot prove FINAL Risk approved"
            )

        module = self._client.module
        request = build_market_order_request(module, order)
        now = utc_now()

        _log.info(
            "mt5.order_send",
            order_request_id=str(order.order_request_id),
            broker_symbol=order.broker_symbol,
            side=order.side.value,
            volume=str(order.volume),
            magic=order.magic_number,
        )
        result = module.order_send(request)
        if result is None:
            code, message = module.last_error()
            raise Mt5CallFailedError("order_send", code, message)

        retcode = int(result.retcode)
        if retcode == module.TRADE_RETCODE_DONE:
            state = OrderState.FILLED
        elif retcode == module.TRADE_RETCODE_DONE_PARTIAL:
            state = OrderState.PARTIALLY_FILLED
        else:
            state = OrderState.REJECTED
        comment = str(getattr(result, "comment", "")) or None
        raw_order = getattr(result, "order", 0)
        raw_deal = getattr(result, "deal", 0)
        raw_price = getattr(result, "price", 0.0)
        raw_volume = getattr(result, "volume", 0.0)
        payload = {
            "retcode": retcode,
            "order": int(raw_order),
            "deal": int(raw_deal),
            "price": float(raw_price),
            "volume": float(raw_volume),
            "bid": float(getattr(result, "bid", 0.0)),
            "ask": float(getattr(result, "ask", 0.0)),
            "comment": comment,
        }
        _log.info(
            "mt5.order_send_result",
            order_request_id=str(order.order_request_id),
            retcode=retcode,
            state=state.value,
        )
        return ExecutionResult(
            execution_id=uuid4(),
            order_request_id=order.order_request_id,
            intent_id=order.intent_id,
            state=state,
            mt5_order_ticket=int(raw_order) if raw_order else None,
            mt5_deal_ticket=int(raw_deal) if raw_deal else None,
            retcode=retcode,
            retcode_comment=comment,
            requested_price=order.price,
            executed_price=decimal_from_mt5(raw_price) if raw_price else None,
            requested_volume=order.volume,
            executed_volume=decimal_from_mt5(raw_volume),
            submitted_at_utc=now,
            completed_at_utc=now,
            order_send_payload=payload,
            request_payload=request,
        )

    # ------------------------------------------------------------------ #
    # Everything else — still structurally disabled (Phase B item B5)
    # ------------------------------------------------------------------ #

    def cancel_pending_orders(self) -> NoReturn:
        self._order_check_gateway.cancel_pending_orders()

    def close_all_positions(self, *, reason: str) -> NoReturn:
        self._order_check_gateway.close_all_positions(reason=reason)
