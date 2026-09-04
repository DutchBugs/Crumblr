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

`close_position`/`close_all_positions` are real (Phase B item B5,
`review/adr/ADR-020-real-flatten-close.md`) — the same demo-only guard, the
same "genuinely unreachable until a real caller wires it in" discipline.
`cancel_pending_orders` still refuses: no pending-order support exists
anywhere in this platform yet (MARKET-only canary, the same boundary
`application/execution.py::ExecutionOrchestrator._recover_ambiguous_submission`'s
own docstring already names), so there is nothing for it to honestly act on.
"""

from __future__ import annotations

from typing import Any, NoReturn
from uuid import uuid4

from crumblr.domain.enums import OrderState, Side
from crumblr.domain.events import OrderCheckCompleted
from crumblr.domain.models import (
    AccountState,
    ApprovedOrder,
    ExecutionResult,
    FlattenInstruction,
    InstrumentSpec,
    PositionState,
)
from crumblr.domain.timeutils import utc_now
from crumblr.mt5_gateway.client import Mt5CallFailedError, Mt5Client
from crumblr.mt5_gateway.execution import (
    MissingFinalRiskDecisionError,
    OrderCheckMt5Gateway,
    build_close_order_request,
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

        _log.info(
            "mt5.order_send",
            order_request_id=str(order.order_request_id),
            broker_symbol=order.broker_symbol,
            side=order.side.value,
            volume=str(order.volume),
            magic=order.magic_number,
        )
        result = module.order_send(request)
        return self._decode_order_send_result(
            "order_send",
            module,
            result,
            request,
            order_request_id=order.order_request_id,
            intent_id=order.intent_id,
            requested_price=order.price,
            requested_volume=order.volume,
        )

    # ------------------------------------------------------------------ #
    # close_position / close_all_positions — real, live, mutating; demo-only
    # ------------------------------------------------------------------ #

    def close_position(self, instruction: FlattenInstruction) -> ExecutionResult:
        """Close exactly one open position at market (Phase B item B5).

        MT5 has no separate "close" call — a close *is* an opposite-side
        `order_send` that names the target `position` explicitly
        (`build_close_order_request`). Same demo-only guard as `order_send`:
        `self.account()` runs first, unconditionally, before anything else.
        `ExecutionResult.order_request_id` carries `instruction.flatten_request_id`
        — this method has no `order_request_id` of its own to report, and the
        flatten occurrence is the only identity a close attempt is meaningfully
        scoped to.
        """
        self.account()
        module = self._client.module
        request = build_close_order_request(module, instruction)

        _log.info(
            "mt5.close_position",
            flatten_request_id=str(instruction.flatten_request_id),
            ticket=instruction.ticket,
            broker_symbol=instruction.broker_symbol,
            close_side=instruction.close_side.value,
            volume=str(instruction.volume),
        )
        result = module.order_send(request)
        return self._decode_order_send_result(
            "close_position",
            module,
            result,
            request,
            order_request_id=instruction.flatten_request_id,
            intent_id=None,
            requested_price=None,
            requested_volume=instruction.volume,
        )

    def close_all_positions(self, *, reason: str) -> tuple[int, ...]:
        """Close every currently open position at market (build.md §8.2,

        `risk/operator_controls.py::OperatorControls.flatten_positions()`'s
        broker call). `self.account()` runs first via `self.positions()` ->
        `self.account()`'s own guard chain, same as every other real call.
        One position's close failing (a transport exception, a rejection)
        never blocks the rest — each is attempted independently, exactly
        the discipline `application/execution.py`'s own per-instruction
        flatten-close attempt uses. Returns the tickets that actually
        closed, per `BrokerPort.close_all_positions`'s own contract —
        never the tickets merely *attempted*.
        """
        self.account()
        closed: list[int] = []
        for position in self.positions():
            close_side = Side.SELL if position.side is Side.BUY else Side.BUY
            instruction = FlattenInstruction(
                flatten_request_id=uuid4(),
                ticket=position.ticket,
                broker_symbol=position.broker_symbol,
                position_side=position.side,
                close_side=close_side,
                volume=position.volume,
                open_price=position.open_price,
                opened_at_utc=position.opened_at_utc,
                magic=position.magic,
                crossed_weekly_close=False,
                observed_at_utc=utc_now(),
            )
            try:
                result = self.close_position(instruction)
            except Mt5CallFailedError as exc:
                # A transport-level failure for this one ticket — never
                # blocks the rest. An account-guard mismatch is not caught
                # here: that already raised from the unconditional
                # `self.account()` call above, before this loop started.
                _log.error(
                    "mt5.close_all_positions_ticket_failed",
                    ticket=position.ticket,
                    error=str(exc),
                )
                continue
            if result.state in (OrderState.FILLED, OrderState.PARTIALLY_FILLED):
                closed.append(position.ticket)
            else:
                _log.error(
                    "mt5.close_all_positions_ticket_rejected",
                    ticket=position.ticket,
                    retcode=result.retcode,
                )
        return tuple(closed)

    def _decode_order_send_result(
        self,
        operation: str,
        module: Any,
        result: Any,
        request: dict[str, Any],
        *,
        order_request_id: Any,
        intent_id: Any,
        requested_price: Any,
        requested_volume: Any,
    ) -> ExecutionResult:
        """Decode a raw `order_send` response into `ExecutionResult`, shared

        between `order_send` (an entry) and `close_position` (a close) —
        both are the same MT5 call shape, differing only in the request
        dict's content, so this is one decode, not two similar-looking
        copies (`mt5_gateway/execution.py::build_market_order_request`'s own
        "one function, not two dict literals" reasoning, applied to the
        response side)."""
        if result is None:
            code, message = module.last_error()
            raise Mt5CallFailedError(operation, code, message)

        now = utc_now()
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
            f"mt5.{operation}_result",
            retcode=retcode,
            state=state.value,
        )
        return ExecutionResult(
            execution_id=uuid4(),
            order_request_id=order_request_id,
            intent_id=intent_id,
            state=state,
            mt5_order_ticket=int(raw_order) if raw_order else None,
            mt5_deal_ticket=int(raw_deal) if raw_deal else None,
            retcode=retcode,
            retcode_comment=comment,
            requested_price=requested_price,
            executed_price=decimal_from_mt5(raw_price) if raw_price else None,
            requested_volume=requested_volume,
            executed_volume=decimal_from_mt5(raw_volume),
            submitted_at_utc=now,
            completed_at_utc=now,
            order_send_payload=payload,
            request_payload=request,
        )

    # ------------------------------------------------------------------ #
    # Everything else — still structurally disabled
    # ------------------------------------------------------------------ #

    def cancel_pending_orders(self) -> NoReturn:
        self._order_check_gateway.cancel_pending_orders()
