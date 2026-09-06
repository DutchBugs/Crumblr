"""`dashboard.agent_state`: Agent panel, Agent health, and the Last Decision

precedence table — against real PostgreSQL, per this codebase's own
established practice of never mocking the database. These call the module's
functions directly rather than through the HTTP layer, since the precedence
table is genuinely non-trivial logic that deserves one fixture per row.

Review feedback (second pass) drove a real redesign here, not just a
signature change: `build_last_decision` is now anchored to one concrete
`outcome_id` per `assignment_id` (via `AgentDecisionOutcomeStore
.latest_outcome_id_for`), never "whichever capsule/fact is most recent in
time" — the precedence-table tests below use a small, explicit
`_FakeOutcomeStore` (a hand-written stand-in for the narrow
`_OutcomeStoreLike` Protocol `agent_state.py` itself defines, not a mock of
the persistence layer) to control exactly which `outcome_id`/settlement a
given test exercises. Separately, `TestTwoAssignmentsNeverCrossContaminate`
proves the real join is assignment-scoped end-to-end against real
PostgreSQL (`PostgresAgentDecisionOutcomeStore`, real claims, real capsules)
— that is the one guarantee worth proving against the real store, not a
fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import Engine

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    NoTradeDecision,
    TradingAssignment,
)
from crumblr.agent_gateway.events import AgentDecisionEventType
from crumblr.agent_gateway.stores import AgentDecisionEventRecord
from crumblr.dashboard.agent_state import (
    build_agent_health,
    build_agent_panel,
    build_last_decision,
)
from crumblr.dashboard.execution_panel import build_execution_gate_state
from crumblr.domain.enums import Environment, ReasonCode, RiskVerdict, SupervisorVerdict
from crumblr.domain.models import DecisionCapsule
from crumblr.persistence.agent_gateway import (
    PostgresAgentDecisionOutcomeStore,
    PostgresAgentIdentityStore,
    PostgresDecisionContextBundleStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.journal import CapsuleStore
from tests.conftest import FIXED_NOW, make_intent, make_risk_decision, make_supervisor_decision

pytestmark = pytest.mark.integration


def _assignment(**overrides: Any) -> TradingAssignment:
    fields: dict[str, Any] = {
        "assignment_id": uuid4(),
        "assignment_version": "assignment-v1",
        "allowed_agent_id": uuid4(),
        "canonical_symbol": "EUR/USD",
        "timeframe": "M5",
        "strategy_artifact_id": uuid4(),
        "strategy_artifact_hash": "artifact-hash-v1",
        "valid_from_utc": FIXED_NOW - timedelta(days=1),
        "valid_until_utc": FIXED_NOW + timedelta(days=30),
        "max_proposals_per_hour": 10,
        "allowed_risk_fraction_min": Decimal("0.001"),
        "allowed_risk_fraction_max": Decimal("0.01"),
        "required_evidence_fields": (),
        "supervisor_policy_version": "supervisor-policy-v1",
        "environment": Environment.PAPER,
        "champion_shadow_status": ChampionShadowStatus.SHADOW,
    }
    fields.update(overrides)
    return TradingAssignment(**fields)


def _identity(agent_id: Any, **overrides: Any) -> AgentIdentity:
    fields: dict[str, Any] = {
        "agent_id": agent_id,
        "role": AgentRole.TRADER,
        "runtime_version": "toy-agent-v1",
        "service_identity": "spiffe://crumblr/agents/toy",
        "status": AgentStatus.ACTIVE,
        "registered_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return AgentIdentity(**fields)


def _capsule_id_for(outcome_id: UUID) -> UUID:
    """Mirrors `agent_gateway/decision_path.py::_seal`'s own derivation —

    the exact identity `build_last_decision` looks up by."""
    return uuid5(NAMESPACE_URL, f"crumblr:agent-capsule:{outcome_id}")


def _capsule(*, outcome_id: UUID, **overrides: Any) -> DecisionCapsule:
    fields: dict[str, Any] = {
        "capsule_id": _capsule_id_for(outcome_id),
        "occurred_at_utc": FIXED_NOW,
        "correlation_id": uuid4(),
        "canonical_symbol": "EUR/USD",
        "broker_symbol": "EURUSD",
        "market_snapshot_id": uuid4(),
        "feature_set_version": "features-v1",
        "feature_values_hash": "abc123",
        "strategy_version": "1.0.0",
        "trade_intent": None,
        "risk_config_version": "cfg-v1",
        "code_commit": "deadbeef",
        "environment": Environment.PAPER,
    }
    fields.update(overrides)
    return DecisionCapsule(**fields)


def _entry(sequence: int, event_type: str, **payload: Any) -> dict[str, Any]:
    return {"sequence": sequence, "event_type": event_type, "payload": payload}


def _fact_entry(
    sequence: int, fact: str, outcome_id: UUID, detail: str | None = None
) -> dict[str, Any]:
    """A journal `AUDIT_FACT` entry correlated to one exact `outcome_id`,

    matching `application/paper_lite.py`'s own `record_audit_fact(...,
    correlation_id=gateway_result.outcome_id)` call shape."""
    return _entry(sequence, "AUDIT_FACT", fact=fact, correlation_id=str(outcome_id), detail=detail)


@dataclass
class _FakeOutcomeStore:
    """A hand-written stand-in for `_OutcomeStoreLike` — not a mock of the

    persistence layer, just a way to give `build_last_decision` a known,
    controlled `outcome_id`/settlement without needing a full Gateway
    claim/settle flow for every precedence-table row. The real store's own
    behaviour (assignment-scoping, real settlement rows) is proven
    separately in `TestTwoAssignmentsNeverCrossContaminate` against real
    PostgreSQL."""

    outcome_id: UUID | None
    settlement: AgentDecisionEventRecord | None = None

    def latest_outcome_id_for(self, assignment_id: UUID) -> UUID | None:
        del assignment_id
        return self.outcome_id

    def settlement_for(
        self, outcome_id: UUID, *, connection: Any = None
    ) -> AgentDecisionEventRecord | None:
        del outcome_id, connection
        return self.settlement


_ASSIGNMENT_ID = uuid4()


class TestAgentPanel:
    def test_no_assignment_id_configured_is_not_provisioned(self, engine: Engine) -> None:
        panel = build_agent_panel(
            assignment_store=PostgresTradingAssignmentStore(engine),
            identity_store=PostgresAgentIdentityStore(engine),
            context_bundle_store=PostgresDecisionContextBundleStore(engine),
            assignment_id=None,
            now=FIXED_NOW,
        )
        assert panel is None

    def test_an_unregistered_assignment_id_is_not_provisioned(self, engine: Engine) -> None:
        panel = build_agent_panel(
            assignment_store=PostgresTradingAssignmentStore(engine),
            identity_store=PostgresAgentIdentityStore(engine),
            context_bundle_store=PostgresDecisionContextBundleStore(engine),
            assignment_id=uuid4(),
            now=FIXED_NOW,
        )
        assert panel is None

    def test_a_currently_valid_assignment_reads_real_fields(self, engine: Engine) -> None:
        assignment_store = PostgresTradingAssignmentStore(engine)
        identity_store = PostgresAgentIdentityStore(engine)
        assignment = _assignment()
        assignment_store.register(assignment)
        identity_store.register(_identity(assignment.allowed_agent_id))

        panel = build_agent_panel(
            assignment_store=assignment_store,
            identity_store=identity_store,
            context_bundle_store=PostgresDecisionContextBundleStore(engine),
            assignment_id=assignment.assignment_id,
            now=FIXED_NOW,
        )

        assert panel is not None
        assert panel.assignment_status == "ACTIVE"
        assert panel.agent_id == assignment.allowed_agent_id
        assert panel.canonical_symbol == "EUR/USD"
        assert panel.timeframe == "M5"
        assert panel.strategy_artifact_id == assignment.strategy_artifact_id
        assert panel.strategy_artifact_hash == "artifact-hash-v1"
        assert panel.runtime_version == "toy-agent-v1"
        assert panel.agent_status == "ACTIVE"
        assert panel.latest_context_issued_at_utc is None
        assert panel.latest_context_hash is None

    def test_a_not_yet_valid_assignment_is_reported_as_such(self, engine: Engine) -> None:
        assignment_store = PostgresTradingAssignmentStore(engine)
        assignment = _assignment(
            valid_from_utc=FIXED_NOW + timedelta(days=1),
            valid_until_utc=FIXED_NOW + timedelta(days=30),
        )
        assignment_store.register(assignment)

        panel = build_agent_panel(
            assignment_store=assignment_store,
            identity_store=PostgresAgentIdentityStore(engine),
            context_bundle_store=PostgresDecisionContextBundleStore(engine),
            assignment_id=assignment.assignment_id,
            now=FIXED_NOW,
        )

        assert panel is not None
        assert panel.assignment_status == "NOT_YET_VALID"

    def test_an_expired_assignment_is_reported_as_such(self, engine: Engine) -> None:
        assignment_store = PostgresTradingAssignmentStore(engine)
        assignment = _assignment(
            valid_from_utc=FIXED_NOW - timedelta(days=30),
            valid_until_utc=FIXED_NOW - timedelta(days=1),
        )
        assignment_store.register(assignment)

        panel = build_agent_panel(
            assignment_store=assignment_store,
            identity_store=PostgresAgentIdentityStore(engine),
            context_bundle_store=PostgresDecisionContextBundleStore(engine),
            assignment_id=assignment.assignment_id,
            now=FIXED_NOW,
        )

        assert panel is not None
        assert panel.assignment_status == "EXPIRED"


def _panel_for(engine: Engine, *, agent_status: AgentStatus = AgentStatus.ACTIVE) -> Any:
    assignment_store = PostgresTradingAssignmentStore(engine)
    identity_store = PostgresAgentIdentityStore(engine)
    assignment = _assignment()
    assignment_store.register(assignment)
    identity_store.register(_identity(assignment.allowed_agent_id, status=agent_status))
    return build_agent_panel(
        assignment_store=assignment_store,
        identity_store=identity_store,
        context_bundle_store=PostgresDecisionContextBundleStore(engine),
        assignment_id=assignment.assignment_id,
        now=FIXED_NOW,
    )


class TestAgentHealth:
    def test_no_panel_is_not_provisioned(self) -> None:
        assert (
            build_agent_health(agent_panel=None, last_decision=None, now=FIXED_NOW, timeframe="M5")
            == "NOT PROVISIONED"
        )

    def test_expired_assignment_is_not_provisioned(self, engine: Engine) -> None:
        assignment_store = PostgresTradingAssignmentStore(engine)
        identity_store = PostgresAgentIdentityStore(engine)
        assignment = _assignment(
            valid_from_utc=FIXED_NOW - timedelta(days=30),
            valid_until_utc=FIXED_NOW - timedelta(days=1),
        )
        assignment_store.register(assignment)
        identity_store.register(_identity(assignment.allowed_agent_id))
        panel = build_agent_panel(
            assignment_store=assignment_store,
            identity_store=identity_store,
            context_bundle_store=PostgresDecisionContextBundleStore(engine),
            assignment_id=assignment.assignment_id,
            now=FIXED_NOW,
        )

        assert (
            build_agent_health(agent_panel=panel, last_decision=None, now=FIXED_NOW, timeframe="M5")
            == "NOT PROVISIONED"
        )

    def test_active_assignment_with_no_identity_record_is_unknown(self, engine: Engine) -> None:
        assignment_store = PostgresTradingAssignmentStore(engine)
        assignment = _assignment()
        assignment_store.register(assignment)
        # Deliberately no AgentIdentity registered for this agent_id.
        panel = build_agent_panel(
            assignment_store=assignment_store,
            identity_store=PostgresAgentIdentityStore(engine),
            context_bundle_store=PostgresDecisionContextBundleStore(engine),
            assignment_id=assignment.assignment_id,
            now=FIXED_NOW,
        )

        assert (
            build_agent_health(agent_panel=panel, last_decision=None, now=FIXED_NOW, timeframe="M5")
            == "UNKNOWN"
        )

    def test_suspended_agent_is_never_healthy(self, engine: Engine) -> None:
        panel = _panel_for(engine, agent_status=AgentStatus.SUSPENDED)

        result = build_agent_health(
            agent_panel=panel, last_decision=None, now=FIXED_NOW, timeframe="M5"
        )

        assert result != "HEALTHY"
        assert result == "NOT PROVISIONED"

    def test_retired_agent_is_never_healthy(self, engine: Engine) -> None:
        panel = _panel_for(engine, agent_status=AgentStatus.RETIRED)

        result = build_agent_health(
            agent_panel=panel, last_decision=None, now=FIXED_NOW, timeframe="M5"
        )

        assert result != "HEALTHY"
        assert result == "NOT PROVISIONED"

    def test_active_assignment_with_no_evidence_yet_is_waiting(self, engine: Engine) -> None:
        panel = _panel_for(engine)

        assert (
            build_agent_health(agent_panel=panel, last_decision=None, now=FIXED_NOW, timeframe="M5")
            == "WAITING"
        )

    def test_recent_evidence_is_healthy(self, engine: Engine) -> None:
        panel = _panel_for(engine)
        outcome_id = uuid4()
        capsule_store = CapsuleStore(engine)
        capsule_store.seal(_capsule(outcome_id=outcome_id, occurred_at_utc=FIXED_NOW))
        last_decision = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=capsule_store,
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=False,
        )

        assert (
            build_agent_health(
                agent_panel=panel, last_decision=last_decision, now=FIXED_NOW, timeframe="M5"
            )
            == "HEALTHY"
        )

    def test_stale_evidence_is_waiting(self, engine: Engine) -> None:
        panel = _panel_for(engine)
        outcome_id = uuid4()
        capsule_store = CapsuleStore(engine)
        capsule_store.seal(
            _capsule(outcome_id=outcome_id, occurred_at_utc=FIXED_NOW - timedelta(hours=6))
        )
        last_decision = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=capsule_store,
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=False,
        )

        assert (
            build_agent_health(
                agent_panel=panel, last_decision=last_decision, now=FIXED_NOW, timeframe="M5"
            )
            == "WAITING"
        )

    def test_a_degraded_last_decision_is_unknown_not_waiting(self, engine: Engine) -> None:
        panel = _panel_for(engine)
        last_decision = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=None),
            capsule_store=CapsuleStore(engine),
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=True,
        )

        assert (
            build_agent_health(
                agent_panel=panel, last_decision=last_decision, now=FIXED_NOW, timeframe="M5"
            )
            == "UNKNOWN"
        )


class TestJournalCorruptionFailsClosed:
    """A corrupt PAPER_LITE journal line must degrade the result visibly,

    never be silently skipped and then produce a confident outcome
    (review feedback, second pass)."""

    def test_corruption_reports_degraded_before_any_correlation_is_attempted(
        self, engine: Engine
    ) -> None:
        outcome_id = uuid4()
        capsule_store = CapsuleStore(engine)
        # Even with a perfectly good capsule and outcome available, a
        # corrupted journal read must still short-circuit to DEGRADED.
        capsule_store.seal(_capsule(outcome_id=outcome_id, trade_intent=None))

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=capsule_store,
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=True,
        )

        assert result is not None
        assert result.platform_outcome == "DEGRADED"


class TestLastDecisionPrecedenceTable:
    """One test per row of the durable-trace precedence `agent_state.py`'s

    own module docstring describes — each fixture reproduces exactly the
    combination of settlement/audit-fact/capsule state that row is for."""

    def test_no_assignment_id_is_none(self, engine: Engine) -> None:
        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=None),
            capsule_store=CapsuleStore(engine),
            assignment_id=None,
            journal_entries=(),
            journal_had_corruption=False,
        )
        assert result is None

    def test_no_outcome_ever_claimed_is_none(self, engine: Engine) -> None:
        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=None),
            capsule_store=CapsuleStore(engine),
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=False,
        )
        assert result is None

    def test_a_real_rejected_settlement_is_gateway_rejected_not_inferred(
        self, engine: Engine
    ) -> None:
        outcome_id = uuid4()
        settlement = AgentDecisionEventRecord(
            outcome_id=outcome_id,
            event_type=AgentDecisionEventType.REJECTED,
            occurred_at_utc=FIXED_NOW,
            reason_codes=("UNKNOWN_ASSIGNMENT",),
            detail="unknown assignment",
        )

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id, settlement=settlement),
            capsule_store=CapsuleStore(engine),
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "GATEWAY_REJECTED"
        assert result.platform_outcome_detail == "unknown assignment"

    def test_claimed_with_no_settlement_no_fact_no_capsule_is_awaiting_evidence(
        self, engine: Engine
    ) -> None:
        """This is the case the first version of this module mislabelled

        `GATEWAY_REJECTED` by inference. It must now say `AWAITING_EVIDENCE`,
        not assert a rejection that was never actually recorded."""
        outcome_id = uuid4()

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=CapsuleStore(engine),
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "AWAITING_EVIDENCE"
        assert result.platform_outcome != "GATEWAY_REJECTED"

    def test_a_no_trade_capsule_bound_to_this_outcome_id(self, engine: Engine) -> None:
        outcome_id = uuid4()
        capsule_store = CapsuleStore(engine)
        capsule_store.seal(_capsule(outcome_id=outcome_id, trade_intent=None))

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=capsule_store,
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "NO_TRADE"
        assert result.proposal is None

    def test_a_trade_proposal_capsule_with_risk_blocked(self, engine: Engine) -> None:
        outcome_id = uuid4()
        capsule_store = CapsuleStore(engine)
        intent = make_intent()
        risk = make_risk_decision(
            intent.intent_id,
            verdict=RiskVerdict.BLOCK,
            reason_codes=(ReasonCode.DAILY_LOSS_LIMIT,),
            approved_volume=None,
            stop_distance_points=None,
            risk_amount=None,
        )
        capsule_store.seal(_capsule(outcome_id=outcome_id, trade_intent=intent, risk_decision=risk))

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=capsule_store,
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "RISK_BLOCKED"
        assert result.proposal is not None
        assert result.proposal.side == intent.side.value

    def test_a_trade_proposal_capsule_with_policy_blocked(self, engine: Engine) -> None:
        outcome_id = uuid4()
        capsule_store = CapsuleStore(engine)
        intent = make_intent()
        risk = make_risk_decision(intent.intent_id, verdict=RiskVerdict.PASS)
        policy = make_supervisor_decision(
            intent.intent_id,
            verdict=SupervisorVerdict.VETO,
            reason_codes=(ReasonCode.DAILY_LOSS_LIMIT,),
        )
        capsule_store.seal(
            _capsule(
                outcome_id=outcome_id,
                trade_intent=intent,
                risk_decision=risk,
                supervisor_decision=policy,
            )
        )

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=capsule_store,
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "POLICY_BLOCKED"
        assert result.risk_verdict == "PASS"
        assert result.policy_verdict == "VETO"

    def test_a_trade_proposal_capsule_with_no_later_fact_awaits_outcome(
        self, engine: Engine
    ) -> None:
        outcome_id = uuid4()
        capsule_store = CapsuleStore(engine)
        intent = make_intent()
        risk = make_risk_decision(intent.intent_id, verdict=RiskVerdict.PASS)
        policy = make_supervisor_decision(intent.intent_id, verdict=SupervisorVerdict.APPROVE)
        capsule_store.seal(
            _capsule(
                outcome_id=outcome_id,
                trade_intent=intent,
                risk_decision=risk,
                supervisor_decision=policy,
            )
        )

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=capsule_store,
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=(),
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "AWAITING_OUTCOME"

    def test_awaiting_outcome_still_reports_supervisor_skipped_when_the_fact_exists(
        self, engine: Engine
    ) -> None:
        """Regression: `_from_capsule`'s caller must forward the

        `supervisor_skipped` flag computed from the journal — an earlier
        version of this code silently hardcoded it to `False` on this exact
        path, found by manually running the dashboard against seeded data."""
        outcome_id = uuid4()
        capsule_store = CapsuleStore(engine)
        intent = make_intent()
        risk = make_risk_decision(intent.intent_id, verdict=RiskVerdict.PASS)
        policy = make_supervisor_decision(intent.intent_id, verdict=SupervisorVerdict.APPROVE)
        capsule_store.seal(
            _capsule(
                outcome_id=outcome_id,
                occurred_at_utc=FIXED_NOW,
                trade_intent=intent,
                risk_decision=risk,
                supervisor_decision=policy,
            )
        )
        entries = (_fact_entry(0, "SUPERVISOR_SKIPPED_PAPER_MODE", outcome_id, "skipped"),)

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=capsule_store,
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=entries,
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "AWAITING_OUTCOME"
        assert result.supervisor_skipped is True

    def test_a_safety_halted_audit_fact_bound_to_this_outcome_id_is_risk_blocked(
        self, engine: Engine
    ) -> None:
        outcome_id = uuid4()
        entries = (_fact_entry(0, "PAPER_LITE_SAFETY_HALTED", outcome_id, "DAILY_LOSS_LIMIT"),)

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=CapsuleStore(engine),
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=entries,
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "RISK_BLOCKED"
        assert result.platform_outcome_detail == "DAILY_LOSS_LIMIT"

    def test_a_fact_bound_to_a_different_outcome_id_is_ignored(self, engine: Engine) -> None:
        """The exact cross-assignment/cross-outcome leak review feedback

        named: a fact correlated to some *other* outcome_id must never be
        picked up just because it is "the only one present"."""
        outcome_id = uuid4()
        other_outcome_id = uuid4()
        entries = (_fact_entry(0, "PAPER_LITE_SAFETY_HALTED", other_outcome_id, "irrelevant"),)

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=CapsuleStore(engine),
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=entries,
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "AWAITING_EVIDENCE"

    def test_a_session_blocked_audit_fact(self, engine: Engine) -> None:
        outcome_id = uuid4()
        entries = (_fact_entry(0, "PAPER_LITE_SESSION_BLOCKED", outcome_id, "CLOSED"),)

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=CapsuleStore(engine),
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=entries,
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "SESSION_BLOCKED"
        assert result.platform_outcome_detail == "CLOSED"

    def test_an_order_check_blocked_audit_fact(self, engine: Engine) -> None:
        outcome_id = uuid4()
        entries = (
            _fact_entry(0, "SUPERVISOR_SKIPPED_PAPER_MODE", outcome_id, "skipped"),
            _fact_entry(1, "PAPER_LITE_ORDER_CHECK_BLOCKED", outcome_id, "invalid stops"),
        )

        result = build_last_decision(
            outcome_store=_FakeOutcomeStore(outcome_id=outcome_id),
            capsule_store=CapsuleStore(engine),
            assignment_id=_ASSIGNMENT_ID,
            journal_entries=entries,
            journal_had_corruption=False,
        )

        assert result is not None
        assert result.platform_outcome == "PAPER_ORDER_CHECK_BLOCKED"
        assert result.platform_outcome_detail == "invalid stops"
        assert result.supervisor_skipped is True


class TestExecutionGateState:
    def test_every_gate_closed_by_default_is_disabled(self) -> None:
        from crumblr.config import ExecutionConfig

        config = ExecutionConfig.model_validate(
            {
                "max_spread_points": 30,
                "max_market_data_age_ms": 5000,
                "order_timeout_ms": 5000,
                "max_slippage_points": 20,
            }
        )

        state = build_execution_gate_state(execution_config=config, live_trading_acknowledged=False)

        assert state.disabled is True
        assert set(state.closed_gates) == {
            "submission_enabled",
            "feedback_2_0_approved",
            "flatten_submission_enabled",
            "live_trading_acknowledged",
        }

    def test_all_four_gates_open_is_not_disabled(self) -> None:
        from crumblr.config import ExecutionConfig

        config = ExecutionConfig.model_validate(
            {
                "max_spread_points": 30,
                "max_market_data_age_ms": 5000,
                "order_timeout_ms": 5000,
                "max_slippage_points": 20,
                "submission_enabled": True,
                "feedback_2_0_approved": True,
                "flatten_submission_enabled": True,
            }
        )

        state = build_execution_gate_state(execution_config=config, live_trading_acknowledged=True)

        assert state.disabled is False
        assert state.closed_gates == ()

    def test_one_closed_gate_is_still_disabled_and_named(self) -> None:
        from crumblr.config import ExecutionConfig

        config = ExecutionConfig.model_validate(
            {
                "max_spread_points": 30,
                "max_market_data_age_ms": 5000,
                "order_timeout_ms": 5000,
                "max_slippage_points": 20,
                "submission_enabled": True,
                "feedback_2_0_approved": True,
                "flatten_submission_enabled": True,
            }
        )

        state = build_execution_gate_state(execution_config=config, live_trading_acknowledged=False)

        assert state.disabled is True
        assert state.closed_gates == ("live_trading_acknowledged",)


class TestTwoAssignmentsNeverCrossContaminate:
    """The exact regression review feedback asked for: with two real,

    independently-claimed PAPER assignments in the same database, the
    dashboard must never show one assignment's decision under the other's
    Agent panel. Uses the real `PostgresAgentDecisionOutcomeStore` end to
    end — this is the guarantee worth proving against the real store."""

    def _claim_no_trade(
        self,
        outcome_store: PostgresAgentDecisionOutcomeStore,
        *,
        assignment_id: UUID,
        agent_id: UUID,
    ) -> UUID:
        decision = NoTradeDecision(
            decision_id=uuid4(),
            agent_id=agent_id,
            assignment_id=assignment_id,
            context_hash="context-hash",
            reason_codes=("no_setup",),
            decided_at_utc=FIXED_NOW,
        )
        with outcome_store.transaction() as connection:
            outcome_store.lock_assignment(assignment_id, connection=connection)
            outcome_store.claim_no_trade(decision, now=FIXED_NOW, connection=connection)
        # `OutcomeClaimResult` carries only `claimed: bool` -- the outcome_id
        # is the decision's own id, exactly how `claim_no_trade`'s real
        # implementation derives it (persistence/agent_gateway.py).
        return decision.decision_id

    def test_each_assignment_only_ever_sees_its_own_outcome(self, engine: Engine) -> None:
        outcome_store = PostgresAgentDecisionOutcomeStore(engine)
        capsule_store = CapsuleStore(engine)

        assignment_a = uuid4()
        agent_a = uuid4()
        assignment_b = uuid4()
        agent_b = uuid4()

        outcome_a = self._claim_no_trade(
            outcome_store, assignment_id=assignment_a, agent_id=agent_a
        )
        capsule_store.seal(_capsule(outcome_id=outcome_a, trade_intent=None))

        # assignment_b claims *after* assignment_a, so a naive "most recent
        # in time" implementation would incorrectly attribute outcome_b to
        # both assignments.
        outcome_b = self._claim_no_trade(
            outcome_store, assignment_id=assignment_b, agent_id=agent_b
        )
        intent = make_intent()
        risk = make_risk_decision(
            intent.intent_id,
            verdict=RiskVerdict.BLOCK,
            reason_codes=(ReasonCode.DAILY_LOSS_LIMIT,),
            approved_volume=None,
            stop_distance_points=None,
            risk_amount=None,
        )
        capsule_store.seal(_capsule(outcome_id=outcome_b, trade_intent=intent, risk_decision=risk))

        result_a = build_last_decision(
            outcome_store=outcome_store,
            capsule_store=capsule_store,
            assignment_id=assignment_a,
            journal_entries=(),
            journal_had_corruption=False,
        )
        result_b = build_last_decision(
            outcome_store=outcome_store,
            capsule_store=capsule_store,
            assignment_id=assignment_b,
            journal_entries=(),
            journal_had_corruption=False,
        )

        assert result_a is not None
        assert result_a.platform_outcome == "NO_TRADE"
        assert result_b is not None
        assert result_b.platform_outcome == "RISK_BLOCKED"
        assert outcome_store.latest_outcome_id_for(assignment_a) == outcome_a
        assert outcome_store.latest_outcome_id_for(assignment_b) == outcome_b
        assert outcome_store.latest_outcome_id_for(assignment_a) != outcome_b
