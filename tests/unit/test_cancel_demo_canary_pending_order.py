"""`scripts/cancel_demo_canary_pending_order.py`'s pure logic, in isolation --

no MT5, no network. Mirrors `test_close_demo_canary_position.py` as closely
as possible: these pin the exact match/mismatch and outcome-decision rules
a real cancel depends on, since a defect here would be found live, against
a real broker pending order, if it were found at all.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from scripts import cancel_demo_canary_pending_order
from scripts.cancel_demo_canary_pending_order import (
    _decide_outcome,
    _find_target_pending_order,
    _verify_target_pending_order,
)

from crumblr.domain.hashing import mt5_magic_number
from crumblr.domain.models import PendingOrderState
from crumblr.mt5_gateway.client import Mt5CallFailedError
from crumblr.mt5_gateway.demo_execution import PendingOrderCancelResult

_NOW = datetime(2026, 9, 15, 9, 38, 23, tzinfo=UTC)
_ORDER_REQUEST_ID = UUID("e845ddac-028e-5bf7-9f86-785119674752")
_MAGIC = mt5_magic_number(_ORDER_REQUEST_ID)


def _pending_order(**overrides: Any) -> PendingOrderState:
    fields: dict[str, Any] = {
        "order_id": 800001,
        "broker_symbol": "EURUSD",
        "order_type": "BUY_LIMIT",
        "state": "PLACED",
        "volume": "0.02",
        "price": "1.08000",
        "stop_loss_price": "1.07800",
        "take_profit_price": "1.08400",
        "expires_at_utc": None,
        "magic": _MAGIC,
        "observed_at_utc": _NOW,
    }
    fields.update(overrides)
    return PendingOrderState(**fields)


class TestFindTargetPendingOrder:
    def test_a_single_match_is_returned_with_no_reason(self) -> None:
        target = _pending_order()
        other = _pending_order(order_id=1, magic=999)
        order, reason = _find_target_pending_order((other, target), order_id=800001)
        assert order is target
        assert reason is None

    def test_no_match_refuses_with_a_count(self) -> None:
        order, reason = _find_target_pending_order((_pending_order(order_id=1),), order_id=800001)
        assert order is None
        assert reason is not None
        assert "not found among 1 pending order" in reason

    def test_zero_pending_orders_refuses_cleanly(self) -> None:
        order, reason = _find_target_pending_order((), order_id=800001)
        assert order is None
        assert reason is not None
        assert "not found among 0 pending order" in reason

    def test_more_than_one_match_refuses_rather_than_picking_one(self) -> None:
        """Two pending orders sharing an order_id should never happen, but

        if MT5 ever reported it, silently picking the first would be
        exactly the kind of ambiguity this runner must never resolve on
        its own.
        """
        duplicate_a = _pending_order()
        duplicate_b = _pending_order(volume="0.03")
        order, reason = _find_target_pending_order((duplicate_a, duplicate_b), order_id=800001)
        assert order is None
        assert reason is not None
        assert "matched 2 pending orders" in reason


class TestVerifyTargetPendingOrder:
    def _verify(self, order: PendingOrderState) -> list[str]:
        return _verify_target_pending_order(
            order,
            expected_broker_symbol="EURUSD",
            originating_order_request_id=_ORDER_REQUEST_ID,
        )

    def test_a_clean_match_has_no_problems(self) -> None:
        assert self._verify(_pending_order()) == []

    def test_a_wrong_symbol_is_reported(self) -> None:
        problems = self._verify(_pending_order(broker_symbol="GBPUSD"))
        assert any("broker_symbol mismatch" in p for p in problems)

    def test_a_wrong_magic_is_reported(self) -> None:
        """The single most important check: an order id is not proof of

        provenance. A pending order that merely happens to share the
        expected symbol but was placed by something else entirely must
        still be refused.
        """
        problems = self._verify(_pending_order(magic=1))
        assert any("magic mismatch" in p for p in problems)

    def test_a_magic_of_none_is_reported_not_silently_accepted(self) -> None:
        problems = self._verify(_pending_order(magic=None))
        assert any("magic mismatch" in p for p in problems)

    def test_every_mismatch_is_collected_not_short_circuited(self) -> None:
        problems = self._verify(_pending_order(broker_symbol="GBPUSD", magic=1))
        assert len(problems) == 2


def _cancel_result(*, accepted: bool, retcode: int = 10009) -> PendingOrderCancelResult:
    return PendingOrderCancelResult(
        order_id=800001, retcode=retcode, retcode_comment="Request executed", accepted=accepted
    )


def _transport_error() -> Mt5CallFailedError:
    return Mt5CallFailedError("cancel_pending_order", 1, "no reply from terminal")


class TestDecideOutcome:
    """The one-shot cancel's PASS/BLOCKED verdict must come from the fresh

    post-cancel readback together with the cancel response, never from
    either alone -- and a transport-ambiguous cancel must never report
    PASS/0, however the readback looks.
    """

    def test_accepted_plus_ticket_absent_is_pass(self) -> None:
        code, message = _decide_outcome(_cancel_result(accepted=True), None, ticket_absent=True)
        assert code == 0
        assert "PASS" in message

    def test_accepted_but_ticket_still_resting_is_blocked(self) -> None:
        """A confirmed-accepted response is not enough on its own -- if the

        fresh readback still shows the exact ticket resting, this must not
        report PASS.
        """
        code, message = _decide_outcome(_cancel_result(accepted=True), None, ticket_absent=False)
        assert code != 0
        assert "PASS" not in message

    def test_rejected_is_blocked_even_with_ticket_absent(self) -> None:
        code, message = _decide_outcome(_cancel_result(accepted=False), None, ticket_absent=True)
        assert code != 0
        assert "PASS" not in message

    def test_transport_ambiguous_with_ticket_absent_is_never_pass(self) -> None:
        """The same defect class the close runner review already found: a

        reassuring readback after an ambiguous transport response is not
        proof the cancel it just attempted is what produced it.
        """
        code, message = _decide_outcome(None, _transport_error(), ticket_absent=True)
        assert code != 0
        assert "PASS" not in message
        assert "TRANSPORT AMBIGUOUS" in message
        assert "TICKET ABSENT" in message
        assert "TICKET STILL RESTING" not in message

    def test_transport_ambiguous_with_ticket_still_resting_says_so_explicitly(self) -> None:
        code, message = _decide_outcome(None, _transport_error(), ticket_absent=False)
        assert code != 0
        assert "PASS" not in message
        assert "TRANSPORT AMBIGUOUS" in message
        assert "TICKET STILL RESTING" in message

    def test_transport_ambiguous_report_names_the_actual_error(self) -> None:
        error = _transport_error()
        _, message = _decide_outcome(None, error, ticket_absent=True)
        assert str(error) in message


class TestNoRetryStructurally:
    """Source-level guard: exactly one `cancel_pending_order()` call site,

    no loop around it, and the cancel-all plural variant is never used --
    mirrors this codebase's other "structurally, not just by intent"
    proofs (e.g. `test_execution_reconciliation.py
    ::TestStillInert::test_no_broker_fact_event_is_ever_emitted`).
    """

    def test_cancel_pending_order_is_called_exactly_once(self) -> None:
        """Checks the real call site (`gateway.cancel_pending_order(`), not

        prose mentions of the method elsewhere in this module's own
        docstrings/comments."""
        source = inspect.getsource(cancel_demo_canary_pending_order)
        assert source.count("gateway.cancel_pending_order(") == 1

    def test_the_plural_cancel_all_variant_is_never_called(self) -> None:
        """Checks the real call site, not this module's own docstring/

        comment prose explaining that the plural variant is deliberately
        never used."""
        source = inspect.getsource(cancel_demo_canary_pending_order)
        assert "gateway.cancel_pending_orders(" not in source
        assert ".cancel_pending_orders()" not in source

    def test_no_raw_module_order_send_call(self) -> None:
        source = inspect.getsource(cancel_demo_canary_pending_order)
        assert "module.order_send" not in source
        assert ".order_send(" not in source
