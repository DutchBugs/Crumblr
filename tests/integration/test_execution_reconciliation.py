"""Post-fill reconciliation, end to end, against real PostgreSQL and a fake

MT5 terminal — core critical path item 8, ADR-010. Mirrors
`test_execution_flatten.py`'s shape: this is the test that proves the
mechanism is real, not a no-op wrapper around `ExpectedState.flat()`, while
`close_all_positions`/`order_send` stay completely unreachable throughout.
"""

from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.application import execution
from crumblr.domain.enums import Environment, ExecutionEventType, FlattenEventType, ReasonCode
from crumblr.domain.hashing import mt5_magic_number
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.flatten import FlattenEventStore, FlattenRequestStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.risk.kill_switch import KillSwitch
from tests.conftest import FIXED_NOW
from tests.integration._execution_fixtures import (
    APPROVED_CANARY_ACCOUNT_REF,
    FakeMt5,
    fake_position,
    orchestrator,
    platform_config,
    spec,
)
from tests.integration.test_execution_orchestrator import sealed_capsule

pytestmark = pytest.mark.integration


def _fully_approved_config(the_spec: Any) -> Any:
    base_config = platform_config(expected_spec_version=the_spec.spec_version)
    version = base_config.config_version
    return base_config.model_copy(
        update={
            "risk": base_config.risk.model_copy(update={"approved_config_version": version}),
            "execution": base_config.execution.model_copy(
                update={
                    "submission_enabled": True,
                    "feedback_2_0_approved": True,
                    "approved_canary_account_ref": APPROVED_CANARY_ACCOUNT_REF,
                }
            ),
        }
    )


class TestReconcileOnce:
    def test_no_committed_submission_never_reads_the_broker(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        fake = FakeMt5()

        orch = orchestrator(engine, config, fake, activation_watermark=None)
        outcomes = orch.reconcile_once()

        assert outcomes == ()
        assert fake.positions_get_calls == 0

    def test_a_fully_approved_config_reaches_reconciled_with_no_broker_position(
        self, engine: Engine
    ) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        first = orch.run_once()
        request_id = first[0].order_request_id
        assert first[0].event_type == ExecutionEventType.SUBMISSION_STARTED

        # Same pass: recovery resolves the ambiguity (submitted=False,
        # since the broker reports no matching position), and
        # reconcile_once() — running after the capsule loop — reconciles
        # it immediately (ADR-010 §2.6).
        second = orch.run_once()
        assert second[0].event_type == ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED

        events = ExecutionEventStore(engine).events_for(request_id)
        assert events[-1].event_type == ExecutionEventType.RECONCILED
        assert events[-1].payload is not None
        assert events[-1].payload["expected_position_tickets"] == []
        assert events[-1].payload["observed_open_tickets"] == []
        assert events[-1].payload["book_status"] == "MATCHED"

    def test_a_matching_broker_position_is_carried_into_the_expectation(
        self, engine: Engine
    ) -> None:
        """The test that proves this is not a no-op wrapper: a fake broker

        position carrying the request's computed magic makes the derived
        expectation contain that ticket, and the whole-book verdict is
        `MATCHED` — the same book compared against plain `flat()` would
        have been `MISMATCHED` (an "unexpected open position"), exactly
        the detection `flat()` is structurally incapable of avoiding.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        first = orch.run_once()
        request_id = first[0].order_request_id
        fake.open_positions = (fake_position(magic=mt5_magic_number(request_id), ticket=900042),)

        second = orch.run_once()
        assert second[0].event_type == ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED

        events = ExecutionEventStore(engine).events_for(request_id)
        reconciled = events[-1]
        assert reconciled.event_type == ExecutionEventType.RECONCILED
        assert reconciled.payload is not None
        assert reconciled.payload["expected_position_tickets"] == [900042]
        assert reconciled.payload["observed_open_tickets"] == [900042]
        # Against plain flat(), an observed ticket 900042 with no
        # expectation would report as "unexpected open position" —
        # MISMATCHED. Reconciled here as MATCHED precisely because the
        # expectation was derived from durable history, not from flat().
        assert reconciled.payload["book_status"] == "MATCHED"

    def test_a_second_pass_does_not_re_reconcile_an_already_reconciled_request(
        self, engine: Engine
    ) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        first = orch.run_once()
        request_id = first[0].order_request_id
        second = orch.run_once()
        assert second[0].event_type == ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED

        events_after_second = ExecutionEventStore(engine).events_for(request_id)
        assert events_after_second[-1].event_type == ExecutionEventType.RECONCILED

        positions_before_third = fake.positions_get_calls
        third = orch.run_once()
        assert third == ()
        assert fake.positions_get_calls == positions_before_third

        events_after_third = ExecutionEventStore(engine).events_for(request_id)
        assert len(events_after_third) == len(events_after_second)

    def test_a_resolved_flatten_removes_its_tickets_from_the_expectation(
        self, engine: Engine
    ) -> None:
        """Item 8 reading item 7's own durable history: a flatten that

        durably closed a ticket removes it from what a request is still
        expected to hold — D-050's own "Watch for" clause made
        mechanical.

        Durable request/event history is seeded directly through the
        stores (not driven through `run_once()` twice) so the request's
        `AMBIGUOUS_OUTCOME_RESOLVED` and the flatten's resolution both
        exist *before* `reconcile_once()` ever runs — otherwise the
        request would already have been reconciled in the same pass
        that resolved it (ADR-010 §2.6's own same-pass convergence),
        before the flatten closure could be seeded.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        capsule = sealed_capsule(engine, config)
        fake = FakeMt5()
        fake.open_positions = ()  # the broker now reports flat

        order_request_id = uuid4()
        assert capsule.trade_intent is not None
        ExecutionRequestStore(engine).claim(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,
            fingerprint="fp-1",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )
        request_events = ExecutionEventStore(engine)
        request_events.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.SUBMISSION_STARTED,
            occurred_at_utc=FIXED_NOW,
            payload={"entry_type": "MARKET"},
        )
        request_events.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
            occurred_at_utc=FIXED_NOW,
            payload={
                "magic_number": mt5_magic_number(order_request_id),
                "submitted": True,
                "matching_position_count": 1,
                "matching_tickets": [900042],
            },
        )

        # Seed a resolved flatten occurrence that durably closed 900042 —
        # directly through the stores, mirroring item 7's own real event
        # shapes, without needing to drive `flatten_once()` end to end.
        flatten_request_id = uuid4()
        FlattenRequestStore(engine).claim(
            flatten_request_id=flatten_request_id,
            environment=Environment.PAPER,
            canonical_symbol="EUR/USD",
            trading_day=FIXED_NOW.date(),
            session_close_utc=FIXED_NOW + timedelta(hours=5),
            flatten_deadline_utc=FIXED_NOW + timedelta(hours=4),
            fingerprint="fp-1",
            claimed_by="test-worker",
            now=FIXED_NOW,
        )
        flatten_events = FlattenEventStore(engine)
        flatten_events.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_SUBMISSION_STARTED,
            occurred_at_utc=FIXED_NOW,
            payload={"instructions": [{"ticket": 900042}]},
        )
        flatten_events.append(
            flatten_request_id=flatten_request_id,
            event_type=FlattenEventType.FLATTEN_OUTCOME_RESOLVED,
            occurred_at_utc=FIXED_NOW,
            payload={"closed_tickets": [900042], "still_open_tickets": []},
        )

        orch = orchestrator(engine, config, fake, activation_watermark=None)
        outcomes = orch.reconcile_once()

        events = ExecutionEventStore(engine).events_for(order_request_id)
        reconciled = events[-1]
        assert reconciled.event_type == ExecutionEventType.RECONCILED
        assert reconciled.payload is not None
        assert reconciled.payload["expected_position_tickets"] == []
        assert reconciled.payload["closed_tickets"] == [900042]
        assert len(outcomes) == 1


def _seed_determined_request(
    engine: Engine,
    config: Any,
    *,
    ticket: int = 900042,
    stop_loss_price: str | None = "1.08300",
) -> Any:
    """Seed a durable, determined, submitted request directly through the

    stores — same pattern as
    `TestReconcileOnce::test_a_resolved_flatten_removes_its_tickets_from_the_expectation`
    — so `reconcile_once()` can be exercised in isolation without driving
    `run_once()` through the full capsule/submission machinery.
    """
    capsule = sealed_capsule(engine, config)
    order_request_id = uuid4()
    assert capsule.trade_intent is not None
    ExecutionRequestStore(engine).claim(
        order_request_id=order_request_id,
        capsule_id=capsule.capsule_id,
        intent_id=capsule.trade_intent.intent_id,
        fingerprint="fp-1",
        claimed_by="test-worker",
        now=FIXED_NOW,
    )
    request_events = ExecutionEventStore(engine)
    submission_payload: dict[str, Any] = {"entry_type": "MARKET"}
    if stop_loss_price is not None:
        submission_payload["stop_loss_price"] = stop_loss_price
    request_events.append(
        order_request_id=order_request_id,
        event_type=ExecutionEventType.SUBMISSION_STARTED,
        occurred_at_utc=FIXED_NOW,
        payload=submission_payload,
    )
    request_events.append(
        order_request_id=order_request_id,
        event_type=ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
        occurred_at_utc=FIXED_NOW,
        payload={
            "magic_number": mt5_magic_number(order_request_id),
            "submitted": True,
            "matching_position_count": 1,
            "matching_tickets": [ticket],
        },
    )
    return order_request_id


class TestVerifyProtectiveStopsIntegration:
    """Core critical path item 9, end to end: `reconcile_once()` calling

    `verify_protective_stops` against a real, durably-seeded request and
    a fake broker position — mirrors `TestReconcileOnce`'s own shape.
    """

    def test_a_matching_broker_side_stop_trips_nothing(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        order_request_id = _seed_determined_request(engine, config, stop_loss_price="1.08300")
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042, sl=1.08300),)
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine, config, fake, activation_watermark=None, kill_switch=kill_switch
        )
        orch.reconcile_once()

        assert not kill_switch.is_halted
        events = ExecutionEventStore(engine).events_for(order_request_id)
        reconciled = events[-1]
        assert reconciled.event_type == ExecutionEventType.RECONCILED
        assert reconciled.payload is not None
        assert reconciled.payload["protective_stop_issues"] == []

    def test_a_missing_broker_side_stop_trips_the_kill_switch(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        order_request_id = _seed_determined_request(engine, config, stop_loss_price="1.08300")
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042, sl=0.0),)
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine, config, fake, activation_watermark=None, kill_switch=kill_switch
        )
        orch.reconcile_once()

        assert kill_switch.is_halted
        assert ReasonCode.PROTECTIVE_STOP_MISSING in kill_switch.active_reasons

        events = ExecutionEventStore(engine).events_for(order_request_id)
        reconciled = events[-1]
        assert reconciled.event_type == ExecutionEventType.RECONCILED
        assert reconciled.payload is not None
        issues = reconciled.payload["protective_stop_issues"]
        assert len(issues) == 1
        assert issues[0]["ticket"] == 900042
        assert issues[0]["reason"] == "PROTECTIVE_STOP_MISSING"
        assert issues[0]["observed"] is None

    def test_a_mismatched_broker_side_stop_trips_the_kill_switch(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        order_request_id = _seed_determined_request(engine, config, stop_loss_price="1.08300")
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042, sl=1.07000),)
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine, config, fake, activation_watermark=None, kill_switch=kill_switch
        )
        orch.reconcile_once()

        assert kill_switch.is_halted
        assert ReasonCode.PROTECTIVE_STOP_MISMATCH in kill_switch.active_reasons

        events = ExecutionEventStore(engine).events_for(order_request_id)
        reconciled = events[-1]
        assert reconciled.event_type == ExecutionEventType.RECONCILED
        assert reconciled.payload is not None
        issues = reconciled.payload["protective_stop_issues"]
        assert len(issues) == 1
        assert issues[0]["reason"] == "PROTECTIVE_STOP_MISMATCH"
        assert issues[0]["expected"] == "1.08300"

    def test_an_undeterminable_expected_stop_trips_as_missing(self, engine: Engine) -> None:
        """`ApprovedOrder.stop_loss_price` is required, so a determined,

        submitted request with no recoverable intended stop should be
        unreachable in practice — but the durable record is still read
        defensively, and its absence still fails closed."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        order_request_id = _seed_determined_request(engine, config, stop_loss_price=None)
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042, sl=1.08300),)
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine, config, fake, activation_watermark=None, kill_switch=kill_switch
        )
        orch.reconcile_once()

        assert kill_switch.is_halted
        assert ReasonCode.PROTECTIVE_STOP_MISSING in kill_switch.active_reasons
        events = ExecutionEventStore(engine).events_for(order_request_id)
        assert events[-1].event_type == ExecutionEventType.RECONCILED

    def test_a_second_pass_is_a_no_op_trip_once_already_halted(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        _seed_determined_request(engine, config, stop_loss_price="1.08300")
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042, sl=0.0),)
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine, config, fake, activation_watermark=None, kill_switch=kill_switch
        )
        orch.reconcile_once()
        assert kill_switch.is_halted
        first_reasons = kill_switch.active_reasons

        # Already-reconciled: a second pass has nothing left to determine
        # and does not touch the broker or the kill switch again.
        orch.reconcile_once()
        assert kill_switch.is_halted
        assert kill_switch.active_reasons == first_reasons

    def test_order_send_and_close_all_positions_stay_unreachable(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        _seed_determined_request(engine, config, stop_loss_price="1.08300")
        fake = FakeMt5()
        fake.open_positions = (fake_position(ticket=900042, sl=0.0),)

        orch = orchestrator(engine, config, fake, activation_watermark=None)
        orch.reconcile_once()

        assert fake.order_send_calls == 0
        assert fake.close_all_positions_calls == 0


class TestStillInert:
    def test_protective_stop_verification_never_fires_via_the_ordinary_run_once_path(
        self, engine: Engine
    ) -> None:
        """Core critical path item 9 is structurally inert today for the

        same reason item 8 is: `verify_protective_stops` only ever sees a
        non-empty `attributed` set for a request whose durable history
        reached `AMBIGUOUS_OUTCOME_RESOLVED{submitted=True}` — which
        nothing can write while `order_send` stays an unconditional raise
        (`OrderCheckMt5Gateway.order_send`). Driving the real
        `run_once()`/`reconcile_once()` path end to end (not the manual
        `_seed_determined_request` seeding the tests above use) proves
        this directly: the kill switch never trips from this producer.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        sealed_capsule(engine, config)
        fake = FakeMt5()
        kill_switch = KillSwitch()

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            kill_switch=kill_switch,
        )
        orch.run_once()
        orch.run_once()

        assert not kill_switch.is_halted

    def test_order_send_and_close_all_positions_are_never_called(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = _fully_approved_config(the_spec)
        sealed_capsule(engine, config)
        fake = FakeMt5()
        fake.open_positions = ()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        orch.run_once()
        orch.run_once()
        orch.reconcile_once()

        assert fake.order_send_calls == 0
        assert fake.close_all_positions_calls == 0

    def test_no_broker_fact_event_is_ever_emitted(self) -> None:
        """Source-level guard, directly protecting ADR-010's central

        naming decision: `SUBMITTED`/`BROKER_ACK`/`FILLED`/`CLOSED` each
        assert a broker fact no code path here can produce."""
        source = inspect.getsource(execution)
        for name in ("SUBMITTED", "BROKER_ACK", "FILLED", "CLOSED"):
            assert f"ExecutionEventType.{name}" not in source
