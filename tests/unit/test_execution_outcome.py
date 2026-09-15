"""`normalize_execution_result` (Phase B item B3,

`review/adr/ADR-019-execution-outcome-normalization.md`). Pure — no
database, no adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from crumblr.application.execution_outcome import normalize_execution_result
from crumblr.domain.enums import ExecutionEventType, OrderState
from crumblr.domain.models import ExecutionResult

FAKE_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def execution_result(**overrides: Any) -> ExecutionResult:
    fields: dict[str, Any] = {
        "execution_id": uuid4(),
        "order_request_id": uuid4(),
        "intent_id": uuid4(),
        "state": OrderState.FILLED,
        "mt5_order_ticket": 700001,
        "mt5_deal_ticket": 700002,
        "retcode": 0,
        "retcode_comment": "Request executed",
        "requested_price": Decimal("1.08500"),
        "executed_price": Decimal("1.08505"),
        "requested_volume": Decimal("0.10"),
        "executed_volume": Decimal("0.10"),
        "submitted_at_utc": FAKE_NOW,
        "completed_at_utc": FAKE_NOW,
    }
    fields.update(overrides)
    return ExecutionResult(**fields)


class TestNormalizeExecutionResult:
    def test_a_full_fill_normalizes_to_filled(self) -> None:
        result = execution_result(state=OrderState.FILLED)

        event_type, payload = normalize_execution_result(result)

        assert event_type is ExecutionEventType.FILLED
        assert payload["state"] == "FILLED"
        assert payload["execution_id"] == str(result.execution_id)
        assert payload["mt5_order_ticket"] == 700001
        assert payload["mt5_deal_ticket"] == 700002
        assert payload["retcode"] == 0
        assert payload["requested_volume"] == "0.10"
        assert payload["executed_volume"] == "0.10"

    def test_a_partial_fill_also_normalizes_to_filled(self) -> None:
        """Distinguishable from a full fill via the payload's

        requested_volume/executed_volume, not a separate event type."""
        result = execution_result(
            state=OrderState.PARTIALLY_FILLED,
            requested_volume=Decimal("0.10"),
            executed_volume=Decimal("0.05"),
        )

        event_type, payload = normalize_execution_result(result)

        assert event_type is ExecutionEventType.FILLED
        assert payload["state"] == "PARTIALLY_FILLED"
        assert payload["requested_volume"] == "0.10"
        assert payload["executed_volume"] == "0.05"

    def test_a_rejection_normalizes_to_rejected(self) -> None:
        result = execution_result(
            state=OrderState.REJECTED,
            mt5_order_ticket=None,
            mt5_deal_ticket=None,
            retcode=10_019,
            retcode_comment="No money",
            executed_price=None,
            executed_volume=None,
        )

        event_type, payload = normalize_execution_result(result)

        assert event_type is ExecutionEventType.REJECTED
        assert payload["state"] == "REJECTED"
        assert payload["retcode"] == 10_019
        assert payload["retcode_comment"] == "No money"
        assert payload["mt5_order_ticket"] is None
        assert payload["executed_volume"] is None

    def test_a_pending_placement_normalizes_to_submitted(self) -> None:
        """ICT LIMIT DEMO EXECUTION (Slice 1): a real, resting LIMIT order

        placed on the book -- not filled, not rejected -- decodes to the
        event `domain/enums.py` reserved for exactly this.
        """
        result = execution_result(
            state=OrderState.SUBMITTED,
            mt5_order_ticket=800001,
            mt5_deal_ticket=None,
            retcode=10_008,
            retcode_comment="Request placed",
            executed_price=None,
            executed_volume=None,
        )

        event_type, payload = normalize_execution_result(result)

        assert event_type is ExecutionEventType.SUBMITTED
        assert payload["state"] == "SUBMITTED"
        assert payload["mt5_order_ticket"] == 800001
        assert payload["mt5_deal_ticket"] is None
        assert payload["retcode"] == 10_008

    @pytest.mark.parametrize(
        "state",
        [
            OrderState.CREATED,
            OrderState.RISK_APPROVED,
            OrderState.SUPERVISOR_APPROVED,
            OrderState.ORDER_CHECKED,
            OrderState.ACKNOWLEDGED,
            OrderState.RECONCILED,
            OrderState.CLOSED,
            OrderState.CANCELLED,
            OrderState.UNKNOWN,
        ],
    )
    def test_any_other_order_state_is_refused(self, state: OrderState) -> None:
        result = execution_result(state=state)

        with pytest.raises(ValueError, match="order_send never produces"):
            normalize_execution_result(result)


class TestNowWiredIntoTheOrchestratorButOnlyBehindTheCanaryPath:
    def test_execution_orchestrator_now_references_normalize_execution_result(self) -> None:
        """Superseded by FEEDBACK.2.0 DEMO EXECUTION: `_attempt_real_entry_submission`

        (reached only when a caller explicitly constructs and injects an
        `EntrySubmissionSink`/`CanaryPermitStore`/`CanaryEntrySubmissionConfig`
        — never by default) now calls this function to normalize a real
        `order_send` response into a durable `FILLED`/`REJECTED` event.
        This test's own premise ("nothing in `ExecutionOrchestrator` calls
        it") is exactly what this wiring changed — updated to assert the
        new true invariant rather than silently dropped, the same
        transparency `TestNotWiredIntoTheOrchestrator` below still
        provides for the concrete demo gateway class itself, which this
        module still never names."""
        import inspect

        from crumblr.application import execution

        source = inspect.getsource(execution)
        assert "normalize_execution_result" in source


class TestNotWiredIntoTheOrchestrator:
    def test_execution_orchestrator_never_names_the_concrete_demo_gateway(self) -> None:
        """`normalize_execution_result` is now reachable (see the class

        above), but the concrete `DemoOrderSendMt5Gateway` class itself is
        still never named here — only the narrow `EntrySubmissionSink`
        Protocol is. Mirrors `test_demo_order_send_gateway.py
        ::TestNotWiredIntoTheOrchestrator`'s own `inspect.getsource` idiom.
        """
        import inspect

        from crumblr.application import execution

        source = inspect.getsource(execution)
        assert "DemoOrderSendMt5Gateway" not in source
