"""The durable event a real `order_send` response normalizes to (Phase B

item B3, `review/adr/ADR-019-execution-outcome-normalization.md`). Pure —
no I/O, no clock of its own, mirroring `application/expected_state.py`/
`application/flatten_plan.py`'s own "pure derivation, the driver decides
what to do with it" discipline applied one step further.

**Not called by `ExecutionOrchestrator` yet.** Nothing in
`application/execution.py` constructs an `ExecutionResult` in the first
place — `DemoOrderSendMt5Gateway.order_send()` (Phase B item B1) is real
and tested, but genuinely unreachable from the live orchestrator (Phase
C/AG-012's shared execution/Risk authority does not exist yet, same
reasoning as every Phase B slice before this one). This module exists,
real and tested, ahead of the wiring that will eventually call it.

Narrowed deliberately to what a MARKET IOC `order_send` response can
actually produce — `FILLED` (full or partial, distinguished by payload,
not a separate event type) or `REJECTED`. `SUBMITTED`/`BROKER_ACK` stay
reserved: a market order has no separate "acked, not yet filled" phase
the way a pending LIMIT/STOP order would, and pending-order support is
out of scope for the first, MARKET-only canary (the same boundary
`application/execution.py::ExecutionOrchestrator
._recover_ambiguous_submission`'s own docstring already names).

**`FILLED`'s own exposure meaning stays `UNDETERMINED`, unchanged by this
module** (`application/expected_state.py::_EXPOSURE_BY_EVENT`).
`DemoOrderSendMt5Gateway.order_send()` deliberately never claims to know
which resulting broker position belongs to this request —
`ExecutionResult.mt5_position_ticket` is left `None` on purpose
(`review/adr/ADR-016-demo-order-send-adapter.md` §2.5). Attributing a
ticket is the *existing* magic-number search's job
(`_recover_ambiguous_submission`, items 6/B4) — this module durably
records what the broker's own response said, verbatim, for audit; it is
not, and does not claim to be, an exposure determination.

Transport exceptions/timeouts at `order_send` time need no handling
here either: if the call raises, this function is simply never reached,
`SUBMISSION_STARTED` stays the last durable event, and the *existing*
ambiguous-recovery mechanism already resolves that case via broker-state
recovery — exactly as it does today, no differently (B2's own "no
automatic retry... uncertainty goes to broker-state recovery" rule).
"""

from __future__ import annotations

from typing import Any

from crumblr.domain.enums import ExecutionEventType, OrderState
from crumblr.domain.models import ExecutionResult


def normalize_execution_result(
    result: ExecutionResult,
) -> tuple[ExecutionEventType, dict[str, Any]]:
    """The `(event_type, payload)` a real `order_send` response

    durably normalizes to. Pure — the caller decides when/whether to
    actually append this; nothing here does I/O.
    """
    if result.state in (OrderState.FILLED, OrderState.PARTIALLY_FILLED):
        event_type = ExecutionEventType.FILLED
    elif result.state is OrderState.REJECTED:
        event_type = ExecutionEventType.REJECTED
    else:
        raise ValueError(
            f"order_send never produces OrderState.{result.state.value} -- only "
            "FILLED, PARTIALLY_FILLED and REJECTED are reachable from a real "
            "MARKET order_send response"
        )

    payload: dict[str, Any] = {
        "execution_id": str(result.execution_id),
        "state": result.state.value,
        "mt5_order_ticket": result.mt5_order_ticket,
        "mt5_deal_ticket": result.mt5_deal_ticket,
        "retcode": result.retcode,
        "retcode_comment": result.retcode_comment,
        "requested_volume": str(result.requested_volume) if result.requested_volume else None,
        "executed_volume": str(result.executed_volume) if result.executed_volume else None,
        "requested_price": str(result.requested_price) if result.requested_price else None,
        "executed_price": str(result.executed_price) if result.executed_price else None,
    }
    return event_type, payload
