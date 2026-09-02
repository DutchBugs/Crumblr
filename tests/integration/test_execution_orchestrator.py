"""The Execution Service, end to end, against real PostgreSQL and a fake

MT5 terminal — never the real one. This is the test that proves the
non-sending guarantee holds for the whole assembled chain, not just for
each piece in isolation: a sealed, approved capsule reaches a persisted
`ORDER_CHECKED`/`ORDER_CHECK_REJECTED` event, and `order_send` is never
called.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.config import ExecutionConfig, MarketConfig, PlatformConfig
from crumblr.domain.enums import Environment, ExecutionEventType, ReasonCode
from crumblr.domain.hashing import mt5_magic_number
from crumblr.domain.models import DecisionCapsule
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestConflictError
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore
from crumblr.persistence.risk_session import PostgresRiskSessionStore
from crumblr.risk.session import RiskSessionState
from crumblr.trading_agent.sessions import trading_day
from tests.conftest import FIXED_NOW, make_intent, make_risk_decision, make_supervisor_decision
from tests.integration._execution_fixtures import (
    BROKER_SYMBOL,
    STRATEGY_VERSION,
    FakeMt5,
    fake_position,
    orchestrator,
    order_check_result,
    platform_config,
    spec,
)

pytestmark = pytest.mark.integration


def sealed_capsule(engine: Engine, config: PlatformConfig, **overrides: Any) -> DecisionCapsule:
    intent = overrides.pop("trade_intent", None) or make_intent(
        created_at_utc=FIXED_NOW,
        expires_at_utc=FIXED_NOW + timedelta(minutes=10),
        reference_price="1.08500",
        stop_loss_price="1.08000",
        take_profit_price="1.09000",
        requested_risk_fraction="0.005",
    )
    fields: dict[str, Any] = {
        "capsule_id": uuid4(),
        "occurred_at_utc": FIXED_NOW,
        "correlation_id": uuid4(),
        "canonical_symbol": "EUR/USD",
        "broker_symbol": BROKER_SYMBOL,
        "market_snapshot_id": uuid4(),
        "feature_set_version": "features-v1",
        "feature_values_hash": "abc123",
        "strategy_version": STRATEGY_VERSION,
        "model_version": None,
        "trade_intent": intent,
        "risk_config_version": config.config_version,
        "risk_decision": make_risk_decision(
            intent.intent_id,
            risk_config_version=config.config_version,
            approved_volume="0.05",
            account_equity="10000",
            stop_distance_points=500,
            risk_amount="50",
        ),
        "supervisor_decision": make_supervisor_decision(intent.intent_id),
        "code_commit": "deadbeef",
        "environment": Environment.PAPER,
    }
    fields.update(overrides)
    capsule = DecisionCapsule(**fields)
    CapsuleStore(engine).seal(capsule)
    return capsule


class TestEndToEnd:
    def test_a_clean_eligible_capsule_reaches_order_checked(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        capsule = sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].capsule_id == capsule.capsule_id
        # `order_check` is real and reached (below); the *outcome* reported
        # for the run reflects the true final state, one step further —
        # the submission gate (Dev-1 core critical path item 2). Every
        # shipped/test config leaves three of nine legs unapproved, so a
        # clean capsule correctly, honestly ends BLOCKED here, never
        # PASSED, until an owner explicitly approves submission.
        assert outcomes[0].event_type == ExecutionEventType.SUBMISSION_GATE_BLOCKED
        assert set(outcomes[0].reason_codes) == {
            ReasonCode.RISK_POLICY_NOT_APPROVED,
            ReasonCode.EXECUTION_NOT_EXPLICITLY_ENABLED,
            ReasonCode.FEEDBACK_2_0_NOT_APPROVED,
        }
        assert fake.order_send_calls == 0
        assert fake.order_check_requests  # the real order_check call happened

        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        event_types = [event.event_type for event in events]
        assert event_types == [
            ExecutionEventType.REQUEST_CLAIMED,
            ExecutionEventType.FINAL_RISK_PASSED,
            ExecutionEventType.ORDER_CHECKED,
            ExecutionEventType.SUBMISSION_GATE_BLOCKED,
        ]

        # Review 1.22 F-057: the durable link ADR-001 requires. The
        # FINAL_RISK_PASSED event carries the complete serialized FINAL
        # RiskDecision plus a fingerprint binding it to the exact order it
        # authorized — never by mutating the sealed DecisionCapsule.
        final_risk_event = events[1]
        assert final_risk_event.payload is not None
        assert final_risk_event.payload["final_risk_decision"]["verdict"] == "PASS"
        assert final_risk_event.payload["order_fingerprint"]

    def test_a_fully_approved_config_reaches_submission_started(self, engine: Engine) -> None:
        """The gate genuinely can open, and opening it now durably commits

        the platform to attempting one broker submission — core critical
        path item 3 (review 1.26 §6 / review 1.27 §8), one step further
        than `SUBMISSION_GATE_PASSED` alone. A test-only config with all
        three approval fields set (F-062 makes this achievable at all —
        see `tests/unit/test_config.py::test_approving_this_exact_version_does_not_change_it`).
        Never a shipped default; every real config leaves this BLOCKED, as
        `test_a_clean_eligible_capsule_reaches_order_checked` proves above.

        The hard assertion this test exists for: even from the most
        permissive config this platform can construct, `order_send` is
        still never called — `_start_submission` appends an event and
        stops, it does not call the adapter.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        base_config = platform_config(expected_spec_version=the_spec.spec_version)
        version = base_config.config_version
        config = base_config.model_copy(
            update={
                "risk": base_config.risk.model_copy(update={"approved_config_version": version}),
                "execution": base_config.execution.model_copy(
                    update={"submission_enabled": True, "feedback_2_0_approved": True}
                ),
            }
        )
        capsule = sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.SUBMISSION_STARTED
        assert outcomes[0].reason_codes == ()
        assert fake.order_send_calls == 0

        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        event_types = [event.event_type for event in events]
        assert event_types == [
            ExecutionEventType.REQUEST_CLAIMED,
            ExecutionEventType.FINAL_RISK_PASSED,
            ExecutionEventType.ORDER_CHECKED,
            ExecutionEventType.SUBMISSION_GATE_PASSED,
            ExecutionEventType.SUBMISSION_STARTED,
        ]

        gate_event = next(
            e for e in events if e.event_type == ExecutionEventType.SUBMISSION_GATE_PASSED
        )
        assert gate_event.payload is not None
        assert gate_event.payload["submission_enabled"] is True
        assert gate_event.payload["feedback_2_0_approved"] is True
        assert gate_event.payload["approved_risk_config_version"] == version
        assert gate_event.payload["risk_config_version"] == version

        submission_event = next(
            e for e in events if e.event_type == ExecutionEventType.SUBMISSION_STARTED
        )
        assert submission_event.payload is not None
        assert submission_event.payload["order_request_id"] == str(outcomes[0].order_request_id)
        assert submission_event.payload["broker_symbol"] == BROKER_SYMBOL
        assert submission_event.payload["side"] == "BUY"
        assert Decimal(submission_event.payload["volume"]) > 0
        # Core critical path item 5: the durable commitment record itself
        # carries the MT5 magic a future order_send would use — proves
        # the computed field genuinely flows through the real event, not
        # only in isolation.
        assert submission_event.payload["magic_number"] == mt5_magic_number(
            outcomes[0].order_request_id
        )

    def test_a_stalled_submission_is_recovered_not_reprocessed(self, engine: Engine) -> None:
        """Core critical path item 6: a request stuck at `SUBMISSION_STARTED`

        (the one state a crash between commitment and a real broker
        response could leave behind) is recovered on the next
        `run_once()` pass, not silently ignored forever — but recovery
        never resubmits, and never re-reads the broker once resolved.

        Core critical path item 8: the same pass that recovers the
        ambiguity also reconciles it — `reconcile_once()` runs after the
        capsule loop, so a request resolved by `_recover_ambiguous_
        submission` earlier in this same pass is already `DETERMINED`
        by the time reconciliation looks at it (`RECONCILED` follows
        `AMBIGUOUS_OUTCOME_RESOLVED` in the same `run_once()` call, not a
        pass later) — see ADR-010 §2.6.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        base_config = platform_config(expected_spec_version=the_spec.spec_version)
        version = base_config.config_version
        config = base_config.model_copy(
            update={
                "risk": base_config.risk.model_copy(update={"approved_config_version": version}),
                "execution": base_config.execution.model_copy(
                    update={"submission_enabled": True, "feedback_2_0_approved": True}
                ),
            }
        )
        capsule = sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        first = orch.run_once()
        assert len(first) == 1
        assert first[0].event_type == ExecutionEventType.SUBMISSION_STARTED
        request_id = first[0].order_request_id
        # The normal pipeline itself already reads positions once, as
        # part of the regular portfolio-state observation — the baseline
        # to measure recovery's own read against, not zero.
        positions_read_before_recovery = fake.positions_get_calls

        second = orch.run_once()
        assert len(second) == 1
        assert second[0].capsule_id == capsule.capsule_id
        assert second[0].event_type == ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED
        assert fake.order_send_calls == 0
        # +2, not +1: `_recover_ambiguous_submission`'s own magic search
        # (item 6), plus `reconcile_once()`'s own broker observation
        # (item 8) — both run in this same pass, since reconciliation
        # runs after the capsule loop and this request is now
        # DETERMINED (submitted=False -> empty exposure) by the time it
        # gets there.
        assert fake.positions_get_calls == positions_read_before_recovery + 2

        events = ExecutionEventStore(engine).events_for(request_id)
        assert [event.event_type for event in events][-3:] == [
            ExecutionEventType.SUBMISSION_STARTED,
            ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
            ExecutionEventType.RECONCILED,
        ]
        recovery_event = events[-2]
        assert recovery_event.payload is not None
        assert recovery_event.payload["magic_number"] == mt5_magic_number(request_id)
        assert recovery_event.payload["submitted"] is False
        assert recovery_event.payload["matching_position_count"] == 0
        assert recovery_event.payload["matching_tickets"] == []

        reconciled_event = events[-1]
        assert reconciled_event.payload is not None
        assert reconciled_event.payload["expected_position_tickets"] == []
        assert reconciled_event.payload["observed_open_tickets"] == []
        assert reconciled_event.payload["closed_tickets"] == []
        assert reconciled_event.payload["book_status"] == "MATCHED"

        # A third pass must not re-run recovery, re-run reconciliation, or
        # re-read the broker for either — the request is already fully
        # resolved (`_recover_ambiguous_submission` sees `RECONCILED`,
        # not `SUBMISSION_STARTED`, as the last event and returns
        # immediately; `reconcile_once()` finds no pending candidate and
        # does the same).
        positions_read_after_recovery = fake.positions_get_calls
        third = orch.run_once()
        assert third == ()
        assert fake.positions_get_calls == positions_read_after_recovery

    def test_a_matching_broker_position_resolves_as_submitted(self, engine: Engine) -> None:
        """The positive case: a broker position genuinely carrying the

        computed magic number makes recovery conclude `submitted=True` —
        proving the mechanism correctly detects this too, even though
        nothing in this codebase can produce that case for real yet
        (`order_send` stays unreachable regardless of this test)."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        base_config = platform_config(expected_spec_version=the_spec.spec_version)
        version = base_config.config_version
        config = base_config.model_copy(
            update={
                "risk": base_config.risk.model_copy(update={"approved_config_version": version}),
                "execution": base_config.execution.model_copy(
                    update={"submission_enabled": True, "feedback_2_0_approved": True}
                ),
            }
        )
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        first = orch.run_once()
        request_id = first[0].order_request_id
        fake.open_positions = (fake_position(magic=mt5_magic_number(request_id), ticket=900042),)

        second = orch.run_once()
        assert len(second) == 1
        assert second[0].event_type == ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED
        assert fake.order_send_calls == 0

        events = ExecutionEventStore(engine).events_for(request_id)
        # Core critical path item 8: since the exposure is now DETERMINED
        # with a ticket that is genuinely observed open, `reconcile_once()`
        # (running after the capsule loop, same pass) reconciles it
        # immediately too — see ADR-010 §2.6.
        recovery_event = events[-2]
        assert recovery_event.payload is not None
        assert recovery_event.payload["submitted"] is True
        assert recovery_event.payload["matching_position_count"] == 1
        assert recovery_event.payload["matching_tickets"] == [900042]

        reconciled_event = events[-1]
        assert reconciled_event.event_type == ExecutionEventType.RECONCILED
        assert reconciled_event.payload is not None
        assert reconciled_event.payload["expected_position_tickets"] == [900042]
        assert reconciled_event.payload["observed_open_tickets"] == [900042]
        assert reconciled_event.payload["book_status"] == "MATCHED"

    def test_a_broker_rejected_order_never_reaches_the_submission_gate(
        self, engine: Engine
    ) -> None:
        """`ORDER_CHECK_REJECTED` short-circuits — the gate is never

        evaluated when the broker itself refused the order, regardless of
        what it would have decided."""
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        base_config = platform_config(expected_spec_version=the_spec.spec_version)
        version = base_config.config_version
        config = base_config.model_copy(
            update={
                "risk": base_config.risk.model_copy(update={"approved_config_version": version}),
                "execution": base_config.execution.model_copy(
                    update={"submission_enabled": True, "feedback_2_0_approved": True}
                ),
            }
        )
        sealed_capsule(engine, config)
        fake = FakeMt5()

        def rejecting_order_check(request: dict[str, Any]) -> Any:
            fake.order_check_requests.append(request)
            return order_check_result(retcode=10019, comment="No money")

        fake.order_check = rejecting_order_check  # type: ignore[method-assign]

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].event_type == ExecutionEventType.ORDER_CHECK_REJECTED
        assert fake.order_send_calls == 0

        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        event_types = {event.event_type for event in events}
        assert ExecutionEventType.SUBMISSION_GATE_PASSED not in event_types
        assert ExecutionEventType.SUBMISSION_GATE_BLOCKED not in event_types

    def test_the_approved_order_is_linked_to_both_risk_decisions(self, engine: Engine) -> None:
        """F-057: `ApprovedOrder` names the intent-time decision it descends

        from *and* the FINAL Risk decision that actually authorized
        submission — not the intent-time one standing in for both.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        capsule = sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        outcomes = orch.run_once()
        assert len(outcomes) == 1

        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        final_risk_event = next(
            e for e in events if e.event_type == ExecutionEventType.FINAL_RISK_PASSED
        )
        assert final_risk_event.payload is not None
        final_risk_decision_id = final_risk_event.payload["final_risk_decision"]["decision_id"]

        order_check_event = next(
            e for e in events if e.event_type == ExecutionEventType.ORDER_CHECKED
        )
        assert order_check_event.payload is not None
        assert order_check_event.payload["order_request_id"] == str(outcomes[0].order_request_id)

        # The two RiskDecisions are genuinely different records: the
        # intent-time one lives on the capsule; FINAL Risk's is a fresh
        # decision_id derived from an "execution-pass"/"execution-*"
        # discriminator (risk/policies.py::_decision_id), never the same id
        # as the intent-time decision.
        assert capsule.risk_decision is not None
        assert final_risk_decision_id != str(capsule.risk_decision.decision_id)

    def test_two_capsules_sharing_an_intent_hash_but_different_approval_content_fail_closed(
        self, engine: Engine
    ) -> None:
        """F-059: `intent.decision_hash` alone does not identify an approved

        execution request — the intent-time `RiskDecision` content is part
        of what was actually approved. Two capsules built from the *same*
        `TradeIntent` (so the same `decision_hash`, hence the same derived
        `order_request_id`) but a different approved volume must conflict,
        not silently read as "already claimed, harmless retry."
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        shared_intent = make_intent(
            created_at_utc=FIXED_NOW,
            expires_at_utc=FIXED_NOW + timedelta(minutes=10),
            reference_price="1.08500",
            stop_loss_price="1.08000",
            take_profit_price="1.09000",
            requested_risk_fraction="0.005",
        )
        sealed_capsule(
            engine,
            config,
            trade_intent=shared_intent,
            risk_decision=make_risk_decision(
                shared_intent.intent_id,
                risk_config_version=config.config_version,
                approved_volume="0.05",
                account_equity="10000",
                stop_distance_points=500,
                risk_amount="50",
            ),
        )
        sealed_capsule(
            engine,
            config,
            trade_intent=shared_intent,
            risk_decision=make_risk_decision(
                shared_intent.intent_id,
                risk_config_version=config.config_version,
                approved_volume="0.10",  # different — the same intent was approved differently
                account_equity="20000",
                stop_distance_points=500,
                risk_amount="100",
            ),
        )
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )

        with pytest.raises(ExecutionRequestConflictError):
            orch.run_once()

        assert fake.order_send_calls == 0

    def test_two_capsules_differing_only_in_uncalibrated_checks_fail_closed(
        self, engine: Engine
    ) -> None:
        """F-059 (review 1.23 §4): the earlier fix hand-selected fields off

        `SupervisorDecision` and omitted `uncalibrated_checks` — a field
        that "explicitly changes what a Supervisor approval means". Two
        capsules identical except for that one previously-omitted field
        must now conflict too, not just a change to `approved_volume`.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        shared_intent = make_intent(
            created_at_utc=FIXED_NOW,
            expires_at_utc=FIXED_NOW + timedelta(minutes=10),
            reference_price="1.08500",
            stop_loss_price="1.08000",
            take_profit_price="1.09000",
            requested_risk_fraction="0.005",
        )
        shared_risk_decision = make_risk_decision(
            shared_intent.intent_id,
            risk_config_version=config.config_version,
            approved_volume="0.05",
            account_equity="10000",
            stop_distance_points=500,
            risk_amount="50",
        )
        sealed_capsule(
            engine,
            config,
            trade_intent=shared_intent,
            risk_decision=shared_risk_decision,
            supervisor_decision=make_supervisor_decision(
                shared_intent.intent_id, uncalibrated_checks=()
            ),
        )
        sealed_capsule(
            engine,
            config,
            trade_intent=shared_intent,
            risk_decision=shared_risk_decision,
            supervisor_decision=make_supervisor_decision(
                shared_intent.intent_id, uncalibrated_checks=("signal_frequency_anomaly",)
            ),
        )
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )

        with pytest.raises(ExecutionRequestConflictError):
            orch.run_once()

        assert fake.order_send_calls == 0

    def test_session_recovery_uses_final_now_not_the_earlier_now(self, engine: Engine) -> None:
        """F-058's remaining gap (review 1.23 §3): `recover_session()`'s

        `market_day` must reflect `final_now` (taken immediately before
        it), not the earlier `run_once()`-level `now`.

        A pre-existing risk-session record is seeded for the trading day
        `final_now` belongs to — a day *ahead* of the one the stale, early
        `now` would resolve to. `recover_session()` halts when the
        record's `trading_day` is ahead of the `market_day` it is given.
        With the fix, `market_day=trading_day(final_now)` matches the
        seeded record exactly, so no halt happens and the capsule reaches
        `ORDER_CHECKED`. Before the fix, `market_day=trading_day(now)`
        would have made the seeded record look like it was from the
        future relative to the stale `now`, and `recover_session` would
        have halted instead.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        # A generous max market-data age: this test's `late` clock value is
        # 10 hours past `FIXED_NOW`, and the fake tick's own timestamp
        # stays fixed at `FIXED_NOW` — FINAL Risk (correctly, per the
        # already-fixed part of F-058) judges that staleness against
        # `final_now` too. Widened here purely to isolate *this* test's
        # target — the session-day sequencing fix — from that other,
        # already-proven-correct behaviour.
        config = platform_config(expected_spec_version=the_spec.spec_version).model_copy(
            update={
                "execution": ExecutionConfig.model_validate(
                    {
                        "max_spread_points": 25,
                        "max_market_data_age_ms": 50_000_000,
                        "order_timeout_ms": 5000,
                        "max_slippage_points": 20,
                    }
                )
            }
        )
        # A long expiry: this test's `late` clock value is 10 hours past
        # `FIXED_NOW`, well beyond `sealed_capsule()`'s default 10-minute
        # intent expiry — long enough here that intent expiry never
        # interferes with isolating the session-recovery sequencing fix.
        long_lived_intent = make_intent(
            created_at_utc=FIXED_NOW,
            expires_at_utc=FIXED_NOW + timedelta(hours=24),
            reference_price="1.08500",
            stop_loss_price="1.08000",
            take_profit_price="1.09000",
            requested_risk_fraction="0.005",
        )
        sealed_capsule(engine, config, trade_intent=long_lived_intent)
        fake = FakeMt5()

        early = FIXED_NOW
        late = FIXED_NOW + timedelta(hours=10)  # crosses the 17:00 America/New_York rollover
        call_count = {"n": 0}

        def progressing_clock() -> datetime:
            call_count["n"] += 1
            return early if call_count["n"] == 1 else late

        PostgresRiskSessionStore(engine).save(
            RiskSessionState(
                trading_day=trading_day(late),
                session_start_equity=Decimal("10000"),
                current_equity=Decimal("10000"),
                peak_equity=Decimal("10000"),
                realized_pnl=Decimal("0"),
                max_drawdown_fraction=Decimal("0"),
                max_session_loss_fraction=Decimal("0"),
                open_risk_fraction=Decimal("0"),
                open_position_count=0,
                recorded_at_utc=early,
            )
        )

        orch = orchestrator(
            engine,
            config,
            fake,
            activation_watermark=FIXED_NOW - timedelta(seconds=1),
            clock=progressing_clock,
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        # The sequencing fix under test reaches ORDER_CHECKED (the durable
        # event log still has that row); the outcome itself goes one step
        # further to the submission gate, correctly BLOCKED here since this
        # test's config carries no governance approvals — unrelated to what
        # this test verifies, see `test_a_clean_eligible_capsule_reaches_order_checked`.
        assert outcomes[0].event_type == ExecutionEventType.SUBMISSION_GATE_BLOCKED
        events = ExecutionEventStore(engine).events_for(outcomes[0].order_request_id)
        assert ExecutionEventType.ORDER_CHECKED in [event.event_type for event in events]

    def test_order_send_is_never_called_even_when_everything_passes(self, engine: Engine) -> None:
        """The hard assertion: not "the test passed", but that the one method

        which would place a real order was never invoked.
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        ).run_once()

        assert fake.order_send_calls == 0

    def test_a_capsule_sealed_before_the_watermark_is_ineligible_and_touches_no_broker(
        self, engine: Engine
    ) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        capsule = sealed_capsule(engine, config)
        fake = FakeMt5()

        # Watermark strictly after the capsule was sealed.
        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW + timedelta(hours=1)
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].capsule_id == capsule.capsule_id
        assert outcomes[0].event_type == ExecutionEventType.INELIGIBLE
        assert not fake.order_check_requests
        assert fake.order_send_calls == 0

    def test_no_watermark_ever_set_means_nothing_is_ever_eligible(self, engine: Engine) -> None:
        """The shipped-config default: `activation_watermark=None`. Every

        capsule, however clean, is refused — "building the execution path
        != enabling the execution path."
        """
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(engine, config, fake, activation_watermark=None)
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].event_type == ExecutionEventType.INELIGIBLE
        assert fake.order_send_calls == 0

    def test_a_second_run_once_does_not_reprocess_an_already_claimed_capsule(
        self, engine: Engine
    ) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        config = platform_config(expected_spec_version=the_spec.spec_version)
        sealed_capsule(engine, config)
        fake = FakeMt5()
        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )

        first = orch.run_once()
        second = orch.run_once()

        assert len(first) == 1
        assert second == ()
        assert len(fake.order_check_requests) == 1

    def test_an_unpinned_instrument_spec_blocks_on_reconciliation(self, engine: Engine) -> None:
        the_spec = spec()
        InstrumentSpecStore(engine).record(the_spec)
        # No expected_spec_version pinned -> reconciliation must read UNKNOWN.
        config = platform_config(expected_spec_version=the_spec.spec_version).model_copy(
            update={
                "markets": (
                    MarketConfig(
                        canonical_symbol="EUR/USD", enabled=True, expected_spec_version=None
                    ),
                )
            }
        )
        sealed_capsule(engine, config)
        fake = FakeMt5()

        orch = orchestrator(
            engine, config, fake, activation_watermark=FIXED_NOW - timedelta(seconds=1)
        )
        outcomes = orch.run_once()

        assert len(outcomes) == 1
        assert outcomes[0].event_type == ExecutionEventType.RECONCILIATION_BLOCKED
        assert not fake.order_check_requests
        assert fake.order_send_calls == 0
