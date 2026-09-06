"""`dashboard.agent_state`: Agent panel, Agent health, and the Last Decision

precedence table — against real PostgreSQL, per this codebase's own
established practice of never mocking the database. These call the module's
functions directly rather than through the HTTP layer, since the precedence
table (finding 4 of the dashboard-refresh plan) is genuinely non-trivial
logic that deserves one fixture per row, not just an end-to-end smoke test.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    TradingAssignment,
)
from crumblr.dashboard.agent_state import (
    build_agent_health,
    build_agent_panel,
    build_last_decision,
)
from crumblr.domain.enums import Environment, ReasonCode, RiskVerdict, SupervisorVerdict
from crumblr.domain.models import DecisionCapsule
from crumblr.persistence.agent_gateway import (
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


def _capsule(**overrides: Any) -> DecisionCapsule:
    fields: dict[str, Any] = {
        "capsule_id": uuid4(),
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

    def test_active_assignment_with_no_evidence_yet_is_waiting(self, engine: Engine) -> None:
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

        assert (
            build_agent_health(agent_panel=panel, last_decision=None, now=FIXED_NOW, timeframe="M5")
            == "WAITING"
        )

    def test_recent_evidence_is_healthy(self, engine: Engine) -> None:
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
        capsule_store = CapsuleStore(engine)
        capsule_store.seal(_capsule(occurred_at_utc=FIXED_NOW))
        last_decision = build_last_decision(capsule_store=capsule_store, journal_entries=())

        assert (
            build_agent_health(
                agent_panel=panel, last_decision=last_decision, now=FIXED_NOW, timeframe="M5"
            )
            == "HEALTHY"
        )

    def test_stale_evidence_is_waiting(self, engine: Engine) -> None:
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
        capsule_store = CapsuleStore(engine)
        capsule_store.seal(_capsule(occurred_at_utc=FIXED_NOW - timedelta(hours=6)))
        last_decision = build_last_decision(capsule_store=capsule_store, journal_entries=())

        assert (
            build_agent_health(
                agent_panel=panel, last_decision=last_decision, now=FIXED_NOW, timeframe="M5"
            )
            == "WAITING"
        )


class TestLastDecisionPrecedenceTable:
    """One test per row of the durable-trace table in `agent_state.py`'s

    own module docstring — each fixture reproduces exactly the combination
    of settlement/audit-fact/capsule state that row describes."""

    def test_no_evidence_anywhere_is_none(self, engine: Engine) -> None:
        result = build_last_decision(capsule_store=CapsuleStore(engine), journal_entries=())
        assert result is None

    def test_a_no_trade_capsule(self, engine: Engine) -> None:
        capsule_store = CapsuleStore(engine)
        capsule_store.seal(_capsule(trade_intent=None))

        result = build_last_decision(capsule_store=capsule_store, journal_entries=())

        assert result is not None
        assert result.platform_outcome == "NO_TRADE"
        assert result.proposal is None
        assert result.inferred is False

    def test_a_trade_proposal_capsule_with_risk_blocked(self, engine: Engine) -> None:
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
        capsule_store.seal(_capsule(trade_intent=intent, risk_decision=risk))

        result = build_last_decision(capsule_store=capsule_store, journal_entries=())

        assert result is not None
        assert result.platform_outcome == "RISK_BLOCKED"
        assert result.proposal is not None
        assert result.proposal.side == intent.side.value

    def test_a_trade_proposal_capsule_with_policy_blocked(self, engine: Engine) -> None:
        capsule_store = CapsuleStore(engine)
        intent = make_intent()
        risk = make_risk_decision(intent.intent_id, verdict=RiskVerdict.PASS)
        policy = make_supervisor_decision(
            intent.intent_id,
            verdict=SupervisorVerdict.VETO,
            reason_codes=(ReasonCode.DAILY_LOSS_LIMIT,),
        )
        capsule_store.seal(
            _capsule(trade_intent=intent, risk_decision=risk, supervisor_decision=policy)
        )

        result = build_last_decision(capsule_store=capsule_store, journal_entries=())

        assert result is not None
        assert result.platform_outcome == "POLICY_BLOCKED"
        assert result.risk_verdict == "PASS"
        assert result.policy_verdict == "VETO"

    def test_a_trade_proposal_capsule_with_no_later_fact_awaits_outcome(
        self, engine: Engine
    ) -> None:
        capsule_store = CapsuleStore(engine)
        intent = make_intent()
        risk = make_risk_decision(intent.intent_id, verdict=RiskVerdict.PASS)
        policy = make_supervisor_decision(intent.intent_id, verdict=SupervisorVerdict.APPROVE)
        capsule_store.seal(
            _capsule(trade_intent=intent, risk_decision=risk, supervisor_decision=policy)
        )

        result = build_last_decision(capsule_store=capsule_store, journal_entries=())

        assert result is not None
        assert result.platform_outcome == "AWAITING_OUTCOME"
        assert result.inferred is False

    def test_awaiting_outcome_still_reports_supervisor_skipped_when_the_fact_exists(
        self, engine: Engine
    ) -> None:
        """Regression: `_from_capsule`'s caller must forward the

        `supervisor_skipped` flag computed from the journal — an earlier
        version of this code silently hardcoded it to `False` on this exact
        path, found by manually running the dashboard against seeded data
        (the SUPERVISOR_SKIPPED_PAPER_MODE audit fact was in the journal but
        never made it into the API response)."""
        capsule_store = CapsuleStore(engine)
        intent = make_intent()
        risk = make_risk_decision(intent.intent_id, verdict=RiskVerdict.PASS)
        policy = make_supervisor_decision(intent.intent_id, verdict=SupervisorVerdict.APPROVE)
        capsule_store.seal(
            _capsule(
                occurred_at_utc=FIXED_NOW,
                trade_intent=intent,
                risk_decision=risk,
                supervisor_decision=policy,
            )
        )
        entries = (
            _entry(
                0,
                "AUDIT_FACT",
                fact="PAPER_LITE_DECISION_WINDOW_CLAIMED",
                detail=FIXED_NOW.isoformat(),
            ),
            _entry(1, "AUDIT_FACT", fact="SUPERVISOR_SKIPPED_PAPER_MODE", detail="skipped"),
        )

        result = build_last_decision(capsule_store=capsule_store, journal_entries=entries)

        assert result is not None
        assert result.platform_outcome == "AWAITING_OUTCOME"
        assert result.supervisor_skipped is True

    def test_a_safety_halted_audit_fact_is_risk_blocked(self, engine: Engine) -> None:
        entries = (
            _entry(
                0,
                "AUDIT_FACT",
                fact="PAPER_LITE_DECISION_WINDOW_CLAIMED",
                detail="2026-08-17T12:00:00+00:00",
            ),
            _entry(1, "AUDIT_FACT", fact="PAPER_LITE_SAFETY_HALTED", detail="DAILY_LOSS_LIMIT"),
        )

        result = build_last_decision(capsule_store=CapsuleStore(engine), journal_entries=entries)

        assert result is not None
        assert result.platform_outcome == "RISK_BLOCKED"
        assert result.platform_outcome_detail == "DAILY_LOSS_LIMIT"
        assert result.inferred is False

    def test_a_session_blocked_audit_fact(self, engine: Engine) -> None:
        entries = (
            _entry(
                0,
                "AUDIT_FACT",
                fact="PAPER_LITE_DECISION_WINDOW_CLAIMED",
                detail="2026-08-17T12:00:00+00:00",
            ),
            _entry(1, "AUDIT_FACT", fact="PAPER_LITE_SESSION_BLOCKED", detail="CLOSED"),
        )

        result = build_last_decision(capsule_store=CapsuleStore(engine), journal_entries=entries)

        assert result is not None
        assert result.platform_outcome == "SESSION_BLOCKED"
        assert result.platform_outcome_detail == "CLOSED"

    def test_an_order_check_blocked_audit_fact(self, engine: Engine) -> None:
        entries = (
            _entry(
                0,
                "AUDIT_FACT",
                fact="PAPER_LITE_DECISION_WINDOW_CLAIMED",
                detail="2026-08-17T12:00:00+00:00",
            ),
            _entry(1, "AUDIT_FACT", fact="SUPERVISOR_SKIPPED_PAPER_MODE", detail="skipped"),
            _entry(2, "AUDIT_FACT", fact="PAPER_LITE_ORDER_CHECK_BLOCKED", detail="invalid stops"),
        )

        result = build_last_decision(capsule_store=CapsuleStore(engine), journal_entries=entries)

        assert result is not None
        assert result.platform_outcome == "PAPER_ORDER_CHECK_BLOCKED"
        assert result.platform_outcome_detail == "invalid stops"
        assert result.supervisor_skipped is True

    def test_a_paper_order_accepted_entry_is_filled(self, engine: Engine) -> None:
        entries = (
            _entry(
                0,
                "AUDIT_FACT",
                fact="PAPER_LITE_DECISION_WINDOW_CLAIMED",
                detail="2026-08-17T12:00:00+00:00",
            ),
            _entry(1, "AUDIT_FACT", fact="SUPERVISOR_SKIPPED_PAPER_MODE", detail="skipped"),
            _entry(2, "PAPER_ORDER_ACCEPTED"),
        )

        result = build_last_decision(capsule_store=CapsuleStore(engine), journal_entries=entries)

        assert result is not None
        assert result.platform_outcome == "PAPER_FILLED"
        assert result.supervisor_skipped is True

    def test_a_window_claim_with_nothing_after_and_no_capsule_infers_gateway_rejected(
        self, engine: Engine
    ) -> None:
        entries = (
            _entry(
                0,
                "AUDIT_FACT",
                fact="PAPER_LITE_DECISION_WINDOW_CLAIMED",
                detail="2026-08-17T12:00:00+00:00",
            ),
        )

        result = build_last_decision(capsule_store=CapsuleStore(engine), journal_entries=entries)

        assert result is not None
        assert result.platform_outcome == "GATEWAY_REJECTED"
        assert result.inferred is True

    def test_a_window_claim_with_only_a_stale_capsule_infers_gateway_rejected(
        self, engine: Engine
    ) -> None:
        capsule_store = CapsuleStore(engine)
        # This capsule belongs to an *older* decision window than the claim below.
        capsule_store.seal(_capsule(occurred_at_utc=FIXED_NOW - timedelta(hours=1)))
        entries = (
            _entry(
                0,
                "AUDIT_FACT",
                fact="PAPER_LITE_DECISION_WINDOW_CLAIMED",
                detail=FIXED_NOW.isoformat(),
            ),
        )

        result = build_last_decision(capsule_store=capsule_store, journal_entries=entries)

        assert result is not None
        assert result.platform_outcome == "GATEWAY_REJECTED"
        assert result.inferred is True

    def test_a_window_claim_with_a_fresh_capsule_uses_the_capsule(self, engine: Engine) -> None:
        capsule_store = CapsuleStore(engine)
        capsule_store.seal(_capsule(occurred_at_utc=FIXED_NOW, trade_intent=None))
        entries = (
            _entry(
                0,
                "AUDIT_FACT",
                fact="PAPER_LITE_DECISION_WINDOW_CLAIMED",
                detail=(FIXED_NOW - timedelta(seconds=1)).isoformat(),
            ),
        )

        result = build_last_decision(capsule_store=capsule_store, journal_entries=entries)

        assert result is not None
        assert result.platform_outcome == "NO_TRADE"
        assert result.inferred is False
