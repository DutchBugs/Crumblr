"""The durable event a real `order_send` response normalizes to (Phase B

item B3, `review/adr/ADR-019-execution-outcome-normalization.md`). Pure —
no I/O, no clock of its own, mirroring `application/expected_state.py`/
`application/flatten_plan.py`'s own "pure derivation, the driver decides
what to do with it" discipline applied one step further.

**Now called by `ExecutionOrchestrator`** (FEEDBACK.2.0 DEMO EXECUTION):
`application/execution.py::ExecutionOrchestrator
._attempt_real_entry_submission()` constructs a real `ExecutionResult` via
the canary-scoped `EntrySubmissionSink`/`DemoOrderSendMt5Gateway.order_send()`
and consults this classification directly. This module's own contents
stay pure regardless of who calls it.

Originally narrowed to what a MARKET IOC `order_send` response can
produce — `FILLED` (full or partial, distinguished by payload, not a
separate event type) or `REJECTED`. ICT LIMIT DEMO EXECUTION (Slice 1)
adds the one case a resting pending order needs: `OrderState.SUBMITTED`
(a LIMIT order successfully placed on the book, not yet filled) normalizes
to `ExecutionEventType.SUBMITTED` — the event `domain/enums.py` has
reserved for exactly this since before either existed. `BROKER_ACK` stays
reserved: nothing in this slice produces a distinct "acknowledged, not yet
placed" phase to normalize.

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

`close_result_fully_closed()` below (Phase B item B5,
`review/adr/ADR-020-real-flatten-close.md`) is the one exception to this
module's "not called yet" framing: `application/execution.py`'s flatten
driver does construct a real `ExecutionResult` (via
`mt5_gateway/demo_execution.py::DemoOrderSendMt5Gateway.close_position()`)
and does consult this classification — closes are wired ahead of entries,
since a policy-driven close needs no `TradeIntent`/Phase-C shared Risk
authority the way a new entry does. `normalize_execution_result()` above
stays entry-only and stays unreached, unchanged.
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
    elif result.state is OrderState.SUBMITTED:
        event_type = ExecutionEventType.SUBMITTED
    else:
        raise ValueError(
            f"order_send never produces OrderState.{result.state.value} -- only "
            "FILLED, PARTIALLY_FILLED, REJECTED (MARKET) and SUBMITTED (LIMIT, "
            "ICT LIMIT DEMO EXECUTION Slice 1) are reachable from a real "
            "order_send response"
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


def close_result_fully_closed(result: ExecutionResult) -> bool:
    """Whether one `close_position()` response means this exact ticket is

    now flat (Phase B item B5, `review/adr/ADR-020-real-flatten-close.md`).
    **Deliberately conservative**: `OrderState.PARTIALLY_FILLED` returns
    `False`, not `True` — a partial close leaves real volume still open on
    the same ticket, and the caller's own fresh broker re-observation (never
    this classification alone) is what makes the final call on whether a
    flatten occurrence is resolved (mirrors `normalize_execution_result`'s
    own "this module durably records what the broker said; it is not an
    exposure determination" boundary). Full-volume-only close handling is a
    named, deliberate scope limit — see `review/DEVIATIONS.md` D-050 and
    `review/adr/ADR-020-real-flatten-close.md` §3.
    """
    return result.state is OrderState.FILLED
