"""FEEDBACK.2.0 DEMO EXECUTION — the one owner-authorized canary path,

end to end against real PostgreSQL and a fake MT5 terminal — never the
real one. Mirrors `test_execution_orchestrator.py`'s own
`test_a_fully_approved_config_reaches_submission_started` scaffold
(the most permissive config this platform can construct), one step
further: a real `EntrySubmissionSink` plus a real `CanaryPermitStore`
now sit behind `SUBMISSION_GATE_PASSED`, so the whole chain --

    SubmissionGate PASS -> capsule-identity check -> permit scope
    validation -> atomic (consume permit + SUBMISSION_STARTED) ->
    order_send -> normalize -> durable FILLED/REJECTED

-- is exercised for real, on a fake terminal, with no real order ever
reaching a real broker.

`CanaryEntrySubmissionConfig.capsule_id` (Dev 1 review BLOCK fix) binds
one canary run to exactly one sealed `DecisionCapsule` -- every class
below constructs the config only *after* sealing the capsule it targets,
mirroring the required real-driver sequence (seal the capsule, read back
its real id, only then construct the canary configuration).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.application.execution import CanaryEntrySubmissionConfig
from crumblr.config import PlatformConfig
from crumblr.domain.enums import ExecutionEventType, OrderState, ReasonCode
from crumblr.persistence.canary_permit import CanaryPermitStore
from crumblr.persistence.execution import ExecutionEventStore
from tests.conftest import FIXED_NOW, make_intent
from tests.integration._execution_fixtures import (
    APPROVED_CANARY_ACCOUNT_REF,
    FakeEntrySubmissionSink,
    FakeMt5,
    canary_permit,
    orchestrator,
    platform_config,
)
from tests.integration.test_execution_orchestrator import sealed_capsule

pytestmark = pytest.mark.integration

_AGENT_ID = uuid4()
_ASSIGNMENT_ID = uuid4()
_STRATEGY_ARTIFACT_HASH = "a" * 64


def _fully_approved_config(engine: Engine) -> tuple[PlatformConfig, Any]:
    from tests.integration._execution_fixtures import spec

    the_spec = spec()
    from crumblr.persistence.instrument_specs import InstrumentSpecStore

    InstrumentSpecStore(engine).record(the_spec)
    base_config = platform_config(expected_spec_version=the_spec.spec_version)
    version = base_config.config_version
    config = base_config.model_copy(
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
    return config, the_spec


def _sealed_agent_capsule(engine: Engine, config: PlatformConfig, **overrides: Any) -> Any:
    intent = overrides.pop("trade_intent", None) or make_intent(
        created_at_utc=FIXED_NOW,
        expires_at_utc=FIXED_NOW + timedelta(minutes=10),
        reference_price="1.08500",
        stop_loss_price="1.08000",
        take_profit_price="1.09000",
        requested_risk_fraction=overrides.pop("requested_risk_fraction", Decimal("0.005")),
    )
    return sealed_capsule(engine, config, trade_intent=intent, **overrides)


class TestEntrySubmissionAdapterNoneLeavesExistingBehaviourUnchanged:
    def test_no_adapter_still_stops_at_submission_started(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.SUBMISSION_STARTED
        assert fake.order_send_calls == 0
        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        assert [e.event_type for e in events][-1] == ExecutionEventType.SUBMISSION_STARTED


class TestStructuralProtocolNeverNamesTheConcreteGateway:
    def test_execution_module_source_never_names_the_demo_gateway(self) -> None:
        """Same mechanical proof as

        `test_demo_order_send_gateway.py::TestNotWiredIntoTheOrchestrator`,
        re-asserted here because this slice is exactly the one that could
        have been tempted to import the concrete class directly instead of
        going through `EntrySubmissionSink`.
        """
        import inspect

        from crumblr.application import execution

        source = inspect.getsource(execution)
        assert "DemoOrderSendMt5Gateway" not in source

    def test_entry_submission_sink_is_a_narrow_structural_protocol(self) -> None:
        from crumblr.application.execution import EntrySubmissionSink

        assert getattr(EntrySubmissionSink, "_is_protocol", False) is True
        members = [name for name in vars(EntrySubmissionSink) if not name.startswith("_")]
        assert members == ["order_send"]


class TestPermitScopeMismatchesBlockBeforeSubmissionStarted:
    def _run(
        self, engine: Engine, config: PlatformConfig, permit: Any, **canary_config_kwargs: Any
    ) -> tuple[Any, FakeMt5, FakeEntrySubmissionSink]:
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink()
        store = CanaryPermitStore(engine)
        store.issue(permit)
        canary_config = CanaryEntrySubmissionConfig(
            permit_id=permit.permit_id, capsule_id=capsule.capsule_id, **canary_config_kwargs
        )

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=canary_config,
        )
        outcomes = orch.run_once()
        assert len(outcomes) == 1
        assert outcomes[0].capsule_id == capsule.capsule_id
        return outcomes[0], fake, sink

    def test_account_mismatch_blocks(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit(approved_account_ref="not-the-real-account-ref")
        outcome, fake, sink = self._run(engine, config, permit)
        assert outcome.event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert ReasonCode.CANARY_PERMIT_ACCOUNT_MISMATCH in outcome.reason_codes
        assert fake.order_send_calls == 0
        assert sink.order_send_calls == 0

    def test_server_mismatch_blocks(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit(expected_server="Some-Other-Server")
        outcome, _, sink = self._run(engine, config, permit)
        assert outcome.event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert ReasonCode.CANARY_PERMIT_SERVER_MISMATCH in outcome.reason_codes
        assert sink.order_send_calls == 0

    def test_agent_mismatch_blocks(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit(
            agent_id=uuid4(),
            assignment_id=_ASSIGNMENT_ID,
            strategy_artifact_hash=_STRATEGY_ARTIFACT_HASH,
        )
        outcome, _, sink = self._run(
            engine,
            config,
            permit,
            agent_id=_AGENT_ID,
            assignment_id=_ASSIGNMENT_ID,
            strategy_artifact_hash=_STRATEGY_ARTIFACT_HASH,
        )
        assert outcome.event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert ReasonCode.CANARY_PERMIT_AGENT_MISMATCH in outcome.reason_codes
        assert sink.order_send_calls == 0

    def test_assignment_mismatch_blocks(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit(
            agent_id=_AGENT_ID,
            assignment_id=uuid4(),
            strategy_artifact_hash=_STRATEGY_ARTIFACT_HASH,
        )
        outcome, _, sink = self._run(
            engine,
            config,
            permit,
            agent_id=_AGENT_ID,
            assignment_id=_ASSIGNMENT_ID,
            strategy_artifact_hash=_STRATEGY_ARTIFACT_HASH,
        )
        assert outcome.event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert ReasonCode.CANARY_PERMIT_ASSIGNMENT_MISMATCH in outcome.reason_codes
        assert sink.order_send_calls == 0

    def test_strategy_artifact_hash_mismatch_blocks(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit(
            agent_id=_AGENT_ID,
            assignment_id=_ASSIGNMENT_ID,
            strategy_artifact_hash="b" * 64,
        )
        outcome, _, sink = self._run(
            engine,
            config,
            permit,
            agent_id=_AGENT_ID,
            assignment_id=_ASSIGNMENT_ID,
            strategy_artifact_hash=_STRATEGY_ARTIFACT_HASH,
        )
        assert outcome.event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert ReasonCode.CANARY_PERMIT_STRATEGY_ARTIFACT_MISMATCH in outcome.reason_codes
        assert sink.order_send_calls == 0

    def test_risk_fraction_exceeded_blocks(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit(max_requested_risk_fraction="0.001")
        capsule = _sealed_agent_capsule(engine, config, requested_risk_fraction=Decimal("0.005"))
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink()
        store = CanaryPermitStore(engine)
        store.issue(permit)
        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=capsule.capsule_id
            ),
        )
        outcomes = orch.run_once()
        assert len(outcomes) == 1
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert ReasonCode.CANARY_PERMIT_RISK_FRACTION_EXCEEDED in outcomes[0].reason_codes
        assert sink.order_send_calls == 0


class TestPermitNotFoundExpiredOrAlreadyConsumedBlockWithoutSend:
    def test_permit_not_found_blocks(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink()
        store = CanaryPermitStore(engine)
        # Deliberately never issued.
        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=uuid4(), capsule_id=capsule.capsule_id
            ),
        )
        outcomes = orch.run_once()
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert outcomes[0].reason_codes == (ReasonCode.CANARY_PERMIT_NOT_FOUND,)
        assert sink.order_send_calls == 0

    def test_expired_permit_blocks(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit(valid_until_utc=FIXED_NOW - timedelta(seconds=1))
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink()
        store = CanaryPermitStore(engine)
        store.issue(permit)
        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=capsule.capsule_id
            ),
        )
        outcomes = orch.run_once()
        assert outcomes[0].event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert ReasonCode.CANARY_PERMIT_EXPIRED in outcomes[0].reason_codes
        assert sink.order_send_calls == 0

    def test_already_consumed_permit_blocks(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit()
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink()
        store = CanaryPermitStore(engine)
        store.issue(permit)
        # A different order_request_id already consumed it.
        store.consume(permit.permit_id, order_request_id=uuid4(), now=FIXED_NOW)

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=capsule.capsule_id
            ),
        )
        outcomes = orch.run_once()
        assert outcomes[0].event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert outcomes[0].reason_codes == (ReasonCode.CANARY_PERMIT_ALREADY_CONSUMED,)
        assert sink.order_send_calls == 0


class TestWrongCapsuleCannotConsumePermitOrSend:
    """Dev 1 review BLOCK fix, finding 1: bind execution to the exact

    `DecisionCapsule` this canary run was configured for."""

    def test_wrong_capsule_id_blocks_before_permit_read(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit()
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink()
        store = CanaryPermitStore(engine)
        store.issue(permit)
        # The configured capsule_id names a capsule that was never sealed.
        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=uuid4()
            ),
        )
        outcomes = orch.run_once()

        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert outcomes[0].reason_codes == (ReasonCode.CANARY_PERMIT_CAPSULE_MISMATCH,)
        # No permit consumption was even attempted.
        assert store.consumption_for(permit.permit_id) is None
        # No SUBMISSION_STARTED was ever appended for this request.
        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        assert ExecutionEventType.SUBMISSION_STARTED not in [e.event_type for e in events]
        # No broker call of any kind.
        assert sink.order_send_calls == 0
        assert fake.order_send_calls == 0

    def test_two_capsules_same_scope_only_the_configured_one_can_send(self, engine: Engine) -> None:
        """Two capsules, same environment + canonical symbol, same

        account/risk bounds/EUR/USD MARKET shape -- only the one exact
        `capsule_id` the canary was configured for may consume the permit
        or reach `order_send`; the other is processed (visible in
        `run_once()`'s own outcome list) but refused before either.
        """
        config, _ = _fully_approved_config(engine)
        permit = canary_permit()
        store = CanaryPermitStore(engine)
        store.issue(permit)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink(state=OrderState.FILLED)

        target_capsule = _sealed_agent_capsule(
            engine, config, requested_risk_fraction=Decimal("0.005")
        )
        other_capsule = _sealed_agent_capsule(
            engine, config, requested_risk_fraction=Decimal("0.004")
        )
        assert target_capsule.capsule_id != other_capsule.capsule_id

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=target_capsule.capsule_id
            ),
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 2
        by_capsule = {outcome.capsule_id: outcome for outcome in outcomes}

        target_outcome = by_capsule[target_capsule.capsule_id]
        other_outcome = by_capsule[other_capsule.capsule_id]

        assert target_outcome.event_type == ExecutionEventType.FILLED
        assert other_outcome.event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert other_outcome.reason_codes == (ReasonCode.CANARY_PERMIT_CAPSULE_MISMATCH,)

        # Exactly one real order_send, for the target capsule only.
        assert sink.order_send_calls == 1
        assert fake.order_send_calls == 0

        # The permit ends consumed by the target's own order request only.
        consumption = store.consumption_for(permit.permit_id)
        assert consumption is not None
        assert consumption.order_request_id == target_outcome.order_request_id
        assert consumption.order_request_id != other_outcome.order_request_id

        # The non-target request never reached SUBMISSION_STARTED at all.
        other_events = ExecutionEventStore(engine).events_for(other_outcome.order_request_id)
        assert ExecutionEventType.SUBMISSION_STARTED not in [e.event_type for e in other_events]
        assert other_events[-1].event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED

        # The target request did reach SUBMISSION_STARTED en route to FILLED.
        target_events = ExecutionEventStore(engine).events_for(target_outcome.order_request_id)
        assert ExecutionEventType.SUBMISSION_STARTED in [e.event_type for e in target_events]


class TestPermitConsumptionAndSubmissionStartedAreOneTransaction:
    def test_a_conflicting_prior_submission_started_event_rolls_back_the_permit_too(
        self, engine: Engine
    ) -> None:
        """Exercises `CanaryPermitStore.transaction()` +

        `.consume()`/`ExecutionEventStore.append()` directly — the exact
        primitive `ExecutionOrchestrator._attempt_real_entry_submission`
        relies on — rather than through the full capsule/orchestrator
        machinery: forcing this exact conflict through `run_once()` would
        need a real pre-existing `SUBMISSION_STARTED` event for the same
        `order_request_id`, which only a full prior successful run can
        create, at which point the *second* run would hit ambiguous-
        submission recovery (item 6) instead of this code path at all.
        Testing the primitive directly is the precise, not merely
        convenient, way to prove this specific property.

        Forces the append half of the atomic block to fail
        (`ExecutionEventConflictError` — a `SUBMISSION_STARTED` already
        recorded under this `order_request_id` with different content)
        and proves the permit consumption inside the same `with` block
        rolled back too: the permit remains consumable afterward.
        """
        from crumblr.persistence.execution import (
            ExecutionEventConflictError,
            ExecutionRequestStore,
        )

        config, _ = _fully_approved_config(engine)
        capsule = _sealed_agent_capsule(engine, config)
        permit = canary_permit()
        store = CanaryPermitStore(engine)
        store.issue(permit)
        requests = ExecutionRequestStore(engine)
        events = ExecutionEventStore(engine)
        order_request_id = uuid4()
        assert capsule.trade_intent is not None
        requests.claim(
            order_request_id=order_request_id,
            capsule_id=capsule.capsule_id,
            intent_id=capsule.trade_intent.intent_id,
            fingerprint="test-fingerprint",
            claimed_by="test",
            now=FIXED_NOW,
        )
        # A real SUBMISSION_STARTED already exists with different content.
        events.append(
            order_request_id=order_request_id,
            event_type=ExecutionEventType.SUBMISSION_STARTED,
            occurred_at_utc=FIXED_NOW,
            payload={"content": "A"},
        )

        with pytest.raises(ExecutionEventConflictError), store.transaction() as connection:
            consume_result = store.consume(
                permit.permit_id,
                order_request_id=order_request_id,
                now=FIXED_NOW,
                connection=connection,
            )
            assert consume_result.outcome.value == "CONSUMED"
            events.append(
                order_request_id=order_request_id,
                event_type=ExecutionEventType.SUBMISSION_STARTED,
                occurred_at_utc=FIXED_NOW,
                payload={"content": "B"},
                connection=connection,
            )

        assert store.consumption_for(permit.permit_id) is None


class TestExactlyOneOrderSendAfterSuccessfulAtomicCommitment:
    def test_order_send_is_called_exactly_once(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit()
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink(state=OrderState.FILLED)
        store = CanaryPermitStore(engine)
        store.issue(permit)

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=capsule.capsule_id
            ),
        )
        outcomes = orch.run_once()

        assert outcomes[0].capsule_id == capsule.capsule_id
        assert sink.order_send_calls == 1
        assert fake.order_send_calls == 0  # the real terminal is never touched
        assert store.consumption_for(permit.permit_id) is not None
        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        event_types = [e.event_type for e in events]
        assert ExecutionEventType.SUBMISSION_STARTED in event_types
        assert event_types[-1] == ExecutionEventType.FILLED


class TestBrokerExceptionLeavesRecoverableSubmissionStarted:
    def test_mt5_call_failed_leaves_submission_started_as_the_last_event(
        self, engine: Engine
    ) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit()
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink(raise_transport_error=True)
        store = CanaryPermitStore(engine)
        store.issue(permit)

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=capsule.capsule_id
            ),
        )
        outcomes = orch.run_once()

        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.SUBMISSION_STARTED
        assert sink.order_send_calls == 1
        # The durable commitment survives the transport failure -- this is
        # exactly what makes the existing item-6 ambiguous-recovery
        # mechanism able to find and resolve it on a later pass.
        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        assert [e.event_type for e in events][-1] == ExecutionEventType.SUBMISSION_STARTED
        assert store.consumption_for(permit.permit_id) is not None


class TestSuccessfulResponseNormalizesAndPersistsCorrectly:
    def test_filled_response_persists_a_filled_event_with_broker_fields(
        self, engine: Engine
    ) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit()
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink(state=OrderState.FILLED)
        store = CanaryPermitStore(engine)
        store.issue(permit)

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=capsule.capsule_id
            ),
        )
        outcomes = orch.run_once()

        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.FILLED
        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        filled_event = events[-1]
        assert filled_event.event_type == ExecutionEventType.FILLED
        assert filled_event.payload is not None
        assert filled_event.payload["state"] == "FILLED"
        assert filled_event.payload["mt5_order_ticket"] == 555001

    def test_rejected_response_persists_a_rejected_event(self, engine: Engine) -> None:
        config, _ = _fully_approved_config(engine)
        permit = canary_permit()
        capsule = _sealed_agent_capsule(engine, config)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink(state=OrderState.REJECTED)
        store = CanaryPermitStore(engine)
        store.issue(permit)

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=capsule.capsule_id
            ),
        )
        outcomes = orch.run_once()

        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.REJECTED
        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        # Not necessarily the last event: a REJECTED request has zero
        # exposure (`_Exposure.DETERMINED`), so `reconcile_once()` -- run
        # at the end of this same `run_once()` pass -- can immediately
        # append a further `RECONCILED` once the (empty) book matches.
        rejected_event = next(e for e in events if e.event_type == ExecutionEventType.REJECTED)
        assert rejected_event.payload is not None
        assert rejected_event.payload["state"] == "REJECTED"
        assert rejected_event.payload["mt5_order_ticket"] is None


class TestSecondUseOfTheSamePermitCannotSend:
    def test_a_second_capsule_cannot_reuse_an_already_consumed_permit(self, engine: Engine) -> None:
        """A *new* canary configuration (fresh `capsule_id`) reusing the

        same already-consumed `permit_id` still cannot send -- proving
        `ALREADY_CONSUMED` is a property of the permit itself, not merely
        a side effect of the capsule-identity check above. Mirrors the
        real driver's own per-cycle reconstruction of
        `CanaryEntrySubmissionConfig` from a fresh `capsule_id`
        (Dev 1 review BLOCK fix).
        """
        config, _ = _fully_approved_config(engine)
        permit = canary_permit()
        store = CanaryPermitStore(engine)
        store.issue(permit)
        fake = FakeMt5()
        sink = FakeEntrySubmissionSink(state=OrderState.FILLED)

        first_capsule = _sealed_agent_capsule(engine, config)
        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=first_capsule.capsule_id
            ),
        )
        first_outcomes = orch.run_once()
        assert first_outcomes[0].capsule_id == first_capsule.capsule_id
        assert first_outcomes[0].event_type == ExecutionEventType.FILLED
        assert sink.order_send_calls == 1

        # A second, distinct capsule (different intent -> different
        # order_request_id) -- with a *fresh* canary configuration
        # correctly bound to this new capsule's own id -- tries to reuse
        # the exact same permit_id.
        second_capsule = _sealed_agent_capsule(
            engine, config, requested_risk_fraction=Decimal("0.004")
        )
        second_orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            entry_submission_adapter=sink,
            canary_permit_store=store,
            canary_config=CanaryEntrySubmissionConfig(
                permit_id=permit.permit_id, capsule_id=second_capsule.capsule_id
            ),
        )
        second_outcomes = second_orch.run_once()
        matching = [o for o in second_outcomes if o.capsule_id == second_capsule.capsule_id]
        assert len(matching) == 1
        assert matching[0].event_type == ExecutionEventType.CANARY_PERMIT_BLOCKED
        assert matching[0].reason_codes == (ReasonCode.CANARY_PERMIT_ALREADY_CONSUMED,)
        # order_send was never reached a second time.
        assert sink.order_send_calls == 1
