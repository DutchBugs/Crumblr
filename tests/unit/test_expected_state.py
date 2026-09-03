"""Core critical path item 8's pure derivation: `application/expected_state.py`.

Hand-constructed `ExecutionEventRecord`/`FlattenEventRecord` tuples — no
database, the same pattern item 6's own fixtures use one layer up.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from crumblr.application.expected_state import (
    _EXPOSURE_BY_EVENT,
    DerivedExposure,
    derive_expected_exposure,
)
from crumblr.application.reconciliation import ExpectedState
from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import ExecutionEventType, FlattenEventType
from crumblr.persistence.execution import ExecutionEventRecord
from crumblr.persistence.flatten import FlattenEventRecord
from tests.conftest import FIXED_NOW

GUARD = AccountGuardConfig.model_validate(
    {
        "expected_server": "Test-Demo",
        "expected_login": None,
        "require_demo_account": True,
        "expected_currency": "EUR",
        "expected_leverage": 30,
    }
)


def event(
    event_type: ExecutionEventType, *, payload: dict[str, Any] | None = None
) -> ExecutionEventRecord:
    return ExecutionEventRecord(
        event_id=uuid4(),
        order_request_id=uuid4(),
        event_type=event_type,
        occurred_at_utc=FIXED_NOW,
        reason_codes=(),
        detail=None,
        payload=payload,
    )


def flatten_event(
    event_type: FlattenEventType, *, payload: dict[str, Any] | None = None
) -> FlattenEventRecord:
    return FlattenEventRecord(
        event_id=uuid4(),
        flatten_request_id=uuid4(),
        event_type=event_type,
        occurred_at_utc=FIXED_NOW,
        reason_codes=(),
        detail=None,
        payload=payload,
    )


class TestPerRequestExposure:
    def test_every_refusal_terminal_expects_no_exposure(self) -> None:
        for event_type in (
            ExecutionEventType.INELIGIBLE,
            ExecutionEventType.GATE_CLOSED,
            ExecutionEventType.RECONCILIATION_BLOCKED,
            ExecutionEventType.FINAL_RISK_BLOCKED,
            ExecutionEventType.ORDER_CHECK_REJECTED,
            ExecutionEventType.SUBMISSION_GATE_BLOCKED,
        ):
            request_id = uuid4()
            history = [event(ExecutionEventType.REQUEST_CLAIMED), event(event_type)]
            exposure = derive_expected_exposure([(request_id, history)])
            assert exposure.expected_position_tickets == frozenset()
            assert exposure.undetermined_reasons == ()
            assert request_id not in exposure.determined_request_ids

    def test_a_request_that_only_passed_the_gate_expects_no_exposure(self) -> None:
        request_id = uuid4()
        history = [
            event(ExecutionEventType.REQUEST_CLAIMED),
            event(ExecutionEventType.SUBMISSION_GATE_PASSED),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_position_tickets == frozenset()
        assert exposure.undetermined_reasons == ()

    def test_an_order_check_is_never_exposure(self) -> None:
        request_id = uuid4()
        history = [event(ExecutionEventType.ORDER_CHECKED)]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_position_tickets == frozenset()
        assert exposure.undetermined_reasons == ()

    def test_a_request_stuck_at_submission_started_is_undetermined_not_flat(self) -> None:
        request_id = uuid4()
        history = [
            event(ExecutionEventType.REQUEST_CLAIMED),
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_position_tickets == frozenset()
        assert len(exposure.undetermined_reasons) == 1
        assert str(request_id) in exposure.undetermined_reasons[0]
        assert request_id not in exposure.determined_request_ids

    def test_a_resolution_of_not_submitted_expects_no_exposure(self) -> None:
        request_id = uuid4()
        history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED, payload={"submitted": False}),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_position_tickets == frozenset()
        assert exposure.undetermined_reasons == ()
        assert request_id in exposure.determined_request_ids
        assert exposure.tickets_by_request[request_id] == frozenset()

    def test_a_rejection_expects_no_exposure(self) -> None:
        """Phase B item B3: a definite broker rejection is determined,

        zero exposure -- mirrors the AMBIGUOUS_OUTCOME_RESOLVED
        {submitted=False} shape exactly, but reached via the new,
        honestly-named REJECTED event instead."""
        request_id = uuid4()
        history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(ExecutionEventType.REJECTED, payload={"retcode": 10_019}),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_position_tickets == frozenset()
        assert exposure.undetermined_reasons == ()
        assert request_id in exposure.determined_request_ids
        assert exposure.tickets_by_request[request_id] == frozenset()

    def test_a_resolution_of_submitted_expects_its_matching_tickets(self) -> None:
        request_id = uuid4()
        history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001, 900002]},
            ),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_position_tickets == frozenset({900001, 900002})
        assert exposure.undetermined_reasons == ()
        assert request_id in exposure.determined_request_ids
        assert exposure.tickets_by_request[request_id] == frozenset({900001, 900002})

    def test_a_resolution_with_a_malformed_payload_is_undetermined(self) -> None:
        request_id = uuid4()
        history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED, payload=None),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_position_tickets == frozenset()
        assert len(exposure.undetermined_reasons) == 1
        assert request_id not in exposure.determined_request_ids

    def test_an_integrity_ambiguity_is_undetermined_not_malformed(self) -> None:
        """Phase B item B4: >1 matching positions produces a deliberately

        `submitted`-less payload — distinct from a genuinely malformed
        one, with its own, honest reason text."""
        request_id = uuid4()
        history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={
                    "integrity_ambiguity": True,
                    "matching_position_count": 2,
                    "matching_tickets": [900001, 900002],
                },
            ),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_position_tickets == frozenset()
        assert request_id not in exposure.determined_request_ids
        assert request_id not in exposure.tickets_by_request
        assert len(exposure.undetermined_reasons) == 1
        assert "integrity ambiguity" in exposure.undetermined_reasons[0]
        assert "missing or malformed" not in exposure.undetermined_reasons[0]

    def test_a_non_market_entry_type_is_undetermined_for_pending_orders(self) -> None:
        request_id = uuid4()
        history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "LIMIT"}),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001]},
            ),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_pending_order_ids == frozenset()
        assert any("D-049" in reason for reason in exposure.undetermined_reasons)

    def test_a_reconciled_event_does_not_change_the_derived_exposure(self) -> None:
        request_id = uuid4()
        without_reconciled = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001]},
            ),
        ]
        with_reconciled = [
            *without_reconciled,
            event(ExecutionEventType.RECONCILED, payload={"book_status": "MATCHED"}),
        ]
        before = derive_expected_exposure([(request_id, without_reconciled)])
        after = derive_expected_exposure([(request_id, with_reconciled)])
        assert before.expected_position_tickets == after.expected_position_tickets
        assert before.tickets_by_request == after.tickets_by_request
        assert after.determined_request_ids == before.determined_request_ids

    def test_every_execution_event_type_has_a_declared_exposure_meaning(self) -> None:
        for member in ExecutionEventType:
            assert member in _EXPOSURE_BY_EVENT, f"{member} has no declared exposure meaning"


class TestExpectedStopLossDerivation:
    """Core critical path item 9: `DerivedExposure.expected_stop_loss_by_request`."""

    def test_a_resolution_of_submitted_expects_its_intended_stop_loss(self) -> None:
        request_id = uuid4()
        history = [
            event(
                ExecutionEventType.SUBMISSION_STARTED,
                payload={"entry_type": "MARKET", "stop_loss_price": "1.23456"},
            ),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001]},
            ),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_stop_loss_by_request[request_id] == Decimal("1.23456")

    def test_a_numeric_stop_loss_payload_is_read_via_str_not_float_precision(self) -> None:
        request_id = uuid4()
        history = [
            event(
                ExecutionEventType.SUBMISSION_STARTED,
                payload={"entry_type": "MARKET", "stop_loss_price": "1.10000"},
            ),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001]},
            ),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.expected_stop_loss_by_request[request_id] == Decimal("1.10000")

    def test_a_missing_stop_loss_in_the_submission_payload_is_absent_not_none(self) -> None:
        """Fail-closed: a request with no recoverable intended stop is

        simply absent from the mapping — `verify_protective_stops`
        treats that absence as `PROTECTIVE_STOP_MISSING`, not as "no
        stop was intended"."""
        request_id = uuid4()
        history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001]},
            ),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert request_id not in exposure.expected_stop_loss_by_request

    def test_a_missing_stop_loss_does_not_add_an_undetermined_reason(self) -> None:
        """Deliberately not folded into `undetermined_reasons` — that

        list feeds the whole-book MATCHED/MISMATCHED/UNKNOWN verdict,
        which item 9's own halt must stay decoupled from
        (`review/DEVIATIONS.md` D-051 gap 3)."""
        request_id = uuid4()
        history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001]},
            ),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert exposure.undetermined_reasons == ()

    def test_a_resolution_of_not_submitted_records_no_expected_stop_loss(self) -> None:
        request_id = uuid4()
        history = [
            event(
                ExecutionEventType.SUBMISSION_STARTED,
                payload={"entry_type": "MARKET", "stop_loss_price": "1.23456"},
            ),
            event(ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED, payload={"submitted": False}),
        ]
        exposure = derive_expected_exposure([(request_id, history)])
        assert request_id not in exposure.expected_stop_loss_by_request


class TestFlattenInteraction:
    def test_a_committed_but_unresolved_flatten_makes_its_targets_undetermined(self) -> None:
        request_id = uuid4()
        request_history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001]},
            ),
        ]
        flatten_request_id = uuid4()
        flatten_history = [
            flatten_event(
                FlattenEventType.FLATTEN_SUBMISSION_STARTED,
                payload={"instructions": [{"ticket": 900001}]},
            )
        ]
        exposure = derive_expected_exposure(
            [(request_id, request_history)],
            flatten_histories=[(flatten_request_id, flatten_history)],
        )
        assert exposure.expected_position_tickets == frozenset({900001})
        assert any(str(flatten_request_id) in reason for reason in exposure.undetermined_reasons)

    def test_a_resolved_flatten_removes_its_closed_tickets(self) -> None:
        request_id = uuid4()
        request_history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001, 900002]},
            ),
        ]
        flatten_history = [
            flatten_event(
                FlattenEventType.FLATTEN_SUBMISSION_STARTED,
                payload={"instructions": [{"ticket": 900001}]},
            ),
            flatten_event(
                FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
                payload={"closed_tickets": [900001], "still_open_tickets": []},
            ),
        ]
        exposure = derive_expected_exposure(
            [(request_id, request_history)], flatten_histories=[(uuid4(), flatten_history)]
        )
        assert exposure.expected_position_tickets == frozenset({900002})
        assert exposure.tickets_by_request[request_id] == frozenset({900002})

    def test_a_resolved_flatten_keeps_its_still_open_tickets_expected(self) -> None:
        request_id = uuid4()
        request_history = [
            event(ExecutionEventType.SUBMISSION_STARTED, payload={"entry_type": "MARKET"}),
            event(
                ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
                payload={"submitted": True, "matching_tickets": [900001]},
            ),
        ]
        flatten_history = [
            flatten_event(
                FlattenEventType.FLATTEN_SUBMISSION_STARTED,
                payload={"instructions": [{"ticket": 900001}]},
            ),
            flatten_event(
                FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
                payload={"closed_tickets": [], "still_open_tickets": [900001]},
            ),
        ]
        exposure = derive_expected_exposure(
            [(request_id, request_history)], flatten_histories=[(uuid4(), flatten_history)]
        )
        assert exposure.expected_position_tickets == frozenset({900001})
        assert exposure.undetermined_reasons == ()

    def test_a_flatten_targeting_a_ticket_the_platform_never_owned_changes_nothing(self) -> None:
        flatten_history = [
            flatten_event(
                FlattenEventType.FLATTEN_SUBMISSION_STARTED,
                payload={"instructions": [{"ticket": 555555}]},
            ),
            flatten_event(
                FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
                payload={"closed_tickets": [555555], "still_open_tickets": []},
            ),
        ]
        exposure = derive_expected_exposure([], flatten_histories=[(uuid4(), flatten_history)])
        assert exposure.expected_position_tickets == frozenset()
        assert exposure.undetermined_reasons == ()


class TestExpectedStateFromDurableExposure:
    def test_an_empty_history_is_exactly_flat(self) -> None:
        """The mechanical version of this item's central honesty claim:

        with no durable execution history, the derived expectation is
        bit-identical to `flat()`."""
        assert ExpectedState.from_durable_exposure(
            GUARD, DerivedExposure.empty()
        ) == ExpectedState.flat(GUARD)

    def test_a_derived_expectation_carries_its_tickets(self) -> None:
        exposure = DerivedExposure(expected_position_tickets=frozenset({900001}))
        expectation = ExpectedState.from_durable_exposure(GUARD, exposure)
        assert expectation.expected_position_tickets == frozenset({900001})

    def test_undetermined_reasons_survive_onto_the_expected_state(self) -> None:
        exposure = DerivedExposure(undetermined_reasons=("a reason",))
        expectation = ExpectedState.from_durable_exposure(GUARD, exposure)
        assert expectation.undetermined_reasons == ("a reason",)
