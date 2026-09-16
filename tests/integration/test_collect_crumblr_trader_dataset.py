"""`scripts/collect_crumblr_trader_dataset.py` against real PostgreSQL.

Proves the new discovery/identity/exclusion-accounting logic Slice 2
adds on top of Slice 1's already-proven single-trade primitives
(`resolve_evidence`/`_resolve_isolated_close_window`, exercised
separately in `test_export_crumblr_trade_to_trainer.py` and not
re-proven here): walking a real `agent_decision_outcomes` row to the
right `execution_requests` claim via `derive_trade_intent_id`, refusing
on an identity mismatch, and correctly excluding a proposal that never
reached execution.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from scripts.collect_crumblr_trader_dataset import (
    IdentityMismatchError,
    collect,
    resolve_identity,
)
from sqlalchemy import Engine

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    DecisionContextBundle,
    TradeProposal,
    TradingAssignment,
)
from crumblr.agent_gateway.gateway import AgentGateway, derive_trade_intent_id
from crumblr.application.broker_state import BrokerStateObservation
from crumblr.domain.enums import (
    DataQuality,
    EntryType,
    Environment,
    ExecutionEventType,
    SessionState,
    Side,
)
from crumblr.domain.models import DecisionCapsule
from crumblr.persistence.agent_gateway import (
    PostgresAgentCredentialStore,
    PostgresAgentDecisionOutcomeStore,
    PostgresAgentIdentityStore,
    PostgresDecisionContextBundleStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.features import FeatureSnapshotStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore
from tests.conftest import (
    FIXED_NOW,
    make_broker_account_snapshot,
    make_broker_position_snapshot,
    make_instrument_spec,
    make_intent,
    make_risk_decision,
    make_supervisor_decision,
)

pytestmark = pytest.mark.integration

SECRET = "correct-horse-battery-staple"
STRATEGY_HASH = "test-strategy-artifact-hash-v1"


def identity(agent_id: UUID, **overrides: Any) -> AgentIdentity:
    fields: dict[str, Any] = {
        "agent_id": agent_id,
        "role": AgentRole.TRADER,
        "runtime_version": "trader-v1",
        "service_identity": f"spiffe://crumblr/agents/{agent_id}",
        "status": AgentStatus.ACTIVE,
        "registered_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return AgentIdentity.model_validate(fields)


def assignment(agent_id: UUID, assignment_id: UUID, **overrides: Any) -> TradingAssignment:
    fields: dict[str, Any] = {
        "assignment_id": assignment_id,
        "assignment_version": "assignment-v1",
        "allowed_agent_id": agent_id,
        "canonical_symbol": "EUR/USD",
        "timeframe": "M5",
        "strategy_artifact_id": uuid4(),
        "strategy_artifact_hash": STRATEGY_HASH,
        "valid_from_utc": FIXED_NOW - timedelta(days=1),
        "valid_until_utc": FIXED_NOW + timedelta(days=30),
        "max_proposals_per_hour": 60,
        "allowed_risk_fraction_min": Decimal("0.001"),
        "allowed_risk_fraction_max": Decimal("0.01"),
        "required_evidence_fields": (),
        "supervisor_policy_version": "supervisor-policy-v1",
        "environment": Environment.PAPER,
        "champion_shadow_status": ChampionShadowStatus.SHADOW,
    }
    fields.update(overrides)
    return TradingAssignment.model_validate(fields)


def proposal(
    *, agent_id: UUID, assignment_id: UUID, context_hash: str, **overrides: Any
) -> TradeProposal:
    fields: dict[str, Any] = {
        "proposal_id": uuid4(),
        "agent_id": agent_id,
        "assignment_id": assignment_id,
        "context_hash": context_hash,
        "strategy_artifact_hash": STRATEGY_HASH,
        "side": Side.BUY,
        "entry_type": EntryType.MARKET,
        "reference_price": Decimal("1.15348"),
        "stop_loss_price": Decimal("1.15148"),
        "take_profit_price": Decimal("1.15748"),
        "confidence": 0.8,
        "requested_risk_fraction": Decimal("0.005"),
        "reason_codes": ("test_reason",),
        "evidence_refs": (),
        "submitted_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
    }
    fields.update(overrides)
    return TradeProposal.model_validate(fields)


def build_gateway(engine: Engine) -> AgentGateway:
    return AgentGateway(
        identities=PostgresAgentIdentityStore(engine),
        credentials=PostgresAgentCredentialStore(engine),
        assignments=PostgresTradingAssignmentStore(engine),
        contexts=PostgresDecisionContextBundleStore(engine),
        outcomes=PostgresAgentDecisionOutcomeStore(engine),
        feature_evidence=FeatureSnapshotStore(engine),
    )


def with_context(gateway: AgentGateway, *, assignment_id: UUID) -> DecisionContextBundle:
    return gateway.publish_context(
        assignment_id=assignment_id,
        symbol="EUR/USD",
        market_snapshot_id=uuid4(),
        instrument_spec_version="spec-v1",
        portfolio_summary_hash="portfolio-abc",
        session_state=SessionState.OPEN,
        data_quality=DataQuality.GOOD,
        now=FIXED_NOW,
    )


def submit_accepted_proposal(
    engine: Engine, *, agent_id: UUID, assignment_id: UUID, **proposal_overrides: Any
) -> TradeProposal:
    """The one real, full-authorization-chain path to a genuine
    `TRADE_PROPOSAL` row in `agent_decision_outcomes` -- everything
    Slice 2's discovery step walks from."""
    gateway = build_gateway(engine)
    gateway.register_identity(identity(agent_id), credential_secret=SECRET)
    gateway.issue_assignment(assignment(agent_id, assignment_id))
    bundle = with_context(gateway, assignment_id=assignment_id)
    sent = proposal(
        agent_id=agent_id,
        assignment_id=assignment_id,
        context_hash=bundle.content_hash,
        **proposal_overrides,
    )
    result = gateway.submit_trade_proposal(
        agent_id=agent_id, credential_secret=SECRET, proposal=sent, now=FIXED_NOW
    )
    assert result.accepted is True, result.reason
    assert result.outcome_id == sent.proposal_id
    return sent


def wire_full_eligible_trade(engine: Engine, *, intent_id: UUID, ticket: int) -> None:
    """The minimum durable chain Slice 1's `resolve_evidence` needs,
    reusing exactly its own expectations -- a sealed capsule, a claimed
    execution request under this exact `intent_id`, one `FILLED` event,
    and an isolated open->flat broker-state window."""
    InstrumentSpecStore(engine).record(
        make_instrument_spec(canonical_symbol="EUR/USD", captured_at_utc=FIXED_NOW)
    )
    intent = make_intent(
        intent_id=intent_id,
        symbol="EUR/USD",
        reference_price=Decimal("1.15348"),
        stop_loss_price=Decimal("1.15148"),
        take_profit_price=Decimal("1.15748"),
    )
    capsule = DecisionCapsule(
        capsule_id=uuid4(),
        occurred_at_utc=FIXED_NOW,
        correlation_id=uuid4(),
        canonical_symbol="EUR/USD",
        broker_symbol="EURUSD",
        market_snapshot_id=uuid4(),
        feature_set_version="features-v1",
        feature_values_hash="abc123",
        strategy_version=STRATEGY_HASH,
        model_version=None,
        trade_intent=intent,
        risk_config_version="cfg-v1",
        risk_decision=make_risk_decision(intent.intent_id),
        supervisor_decision=make_supervisor_decision(intent.intent_id),
        code_commit="deadbeef",
        environment=Environment.PAPER,
    )
    CapsuleStore(engine).seal(capsule)
    order_request_id = uuid4()
    ExecutionRequestStore(engine).claim(
        order_request_id=order_request_id,
        capsule_id=capsule.capsule_id,
        intent_id=intent.intent_id,
        fingerprint="fp-1",
        claimed_by="test",
        now=FIXED_NOW,
    )
    ExecutionEventStore(engine).append(
        order_request_id=order_request_id,
        event_type=ExecutionEventType.FILLED,
        occurred_at_utc=FIXED_NOW,
        payload={
            "executed_price": "1.15346",
            "executed_volume": "0.02",
            "mt5_order_ticket": ticket,
            "mt5_deal_ticket": ticket,
        },
    )

    last_open_at = FIXED_NOW
    post_close_at = FIXED_NOW + timedelta(minutes=1)
    open_account = make_broker_account_snapshot(
        observed_at_utc=last_open_at, balance=Decimal("9993.55"), equity=Decimal("9993.55")
    )
    BrokerStateStore(engine).record(
        BrokerStateObservation(
            account=open_account,
            positions=(
                make_broker_position_snapshot(
                    snapshot_id=open_account.snapshot_id,
                    ticket=ticket,
                    observed_at_utc=last_open_at,
                ),
            ),
            pending_orders=(),
        )
    )
    flat_account = make_broker_account_snapshot(
        observed_at_utc=post_close_at, balance=Decimal("9993.66"), equity=Decimal("9993.66")
    )
    BrokerStateStore(engine).record(
        BrokerStateObservation(account=flat_account, positions=(), pending_orders=())
    )


class TestResolveIdentity:
    def test_an_unregistered_assignment_is_refused(self, engine: Engine) -> None:
        with pytest.raises(IdentityMismatchError):
            resolve_identity(engine, agent_id=uuid4(), assignment_id=uuid4())

    def test_an_assignment_registered_for_a_different_agent_is_refused(
        self, engine: Engine
    ) -> None:
        agent_id = uuid4()
        assignment_id = uuid4()
        other_agent_id = uuid4()
        PostgresTradingAssignmentStore(engine).register(assignment(agent_id, assignment_id))

        with pytest.raises(IdentityMismatchError):
            resolve_identity(engine, agent_id=other_agent_id, assignment_id=assignment_id)

    def test_a_coherent_identity_resolves_from_the_registered_assignment(
        self, engine: Engine
    ) -> None:
        agent_id = uuid4()
        assignment_id = uuid4()
        PostgresTradingAssignmentStore(engine).register(assignment(agent_id, assignment_id))

        result = resolve_identity(engine, agent_id=agent_id, assignment_id=assignment_id)

        assert result.agent_id == agent_id
        assert result.assignment_id == assignment_id
        assert result.strategy_artifact_hash == STRATEGY_HASH
        assert result.canonical_symbol == "EUR/USD"
        assert result.timeframe == "M5"


class TestCollect:
    def test_no_trade_proposal_outcomes_yet_is_zero_discovered_zero_eligible(
        self, engine: Engine
    ) -> None:
        agent_id = uuid4()
        assignment_id = uuid4()
        PostgresTradingAssignmentStore(engine).register(assignment(agent_id, assignment_id))
        identity_obj = resolve_identity(engine, agent_id=agent_id, assignment_id=assignment_id)

        result, returns_r = collect(engine, identity_obj)

        assert result.discovered_count == 0
        assert result.eligible == ()
        assert result.excluded == ()
        assert returns_r == {}

    def test_a_proposal_that_never_reached_execution_is_excluded_not_dropped(
        self, engine: Engine
    ) -> None:
        agent_id = uuid4()
        assignment_id = uuid4()
        submit_accepted_proposal(engine, agent_id=agent_id, assignment_id=assignment_id)
        identity_obj = resolve_identity(engine, agent_id=agent_id, assignment_id=assignment_id)

        result, returns_r = collect(engine, identity_obj)

        assert result.discovered_count == 1
        assert result.eligible == ()
        assert len(result.excluded) == 1
        assert "no execution_requests row" in result.excluded[0].reason
        assert returns_r == {}

    def test_a_fully_wired_closed_trade_is_eligible(self, engine: Engine) -> None:
        agent_id = uuid4()
        assignment_id = uuid4()
        sent = submit_accepted_proposal(engine, agent_id=agent_id, assignment_id=assignment_id)
        intent_id = derive_trade_intent_id(sent.proposal_id)
        wire_full_eligible_trade(engine, intent_id=intent_id, ticket=555001)
        identity_obj = resolve_identity(engine, agent_id=agent_id, assignment_id=assignment_id)

        result, returns_r = collect(engine, identity_obj)

        assert result.discovered_count == 1
        assert len(result.eligible) == 1
        assert result.excluded == ()
        assert result.eligible[0].intent_id == intent_id
        assert len(returns_r) == 1

    def test_two_identities_never_mix_even_with_the_same_agent(self, engine: Engine) -> None:
        """Same `agent_id`, two different `assignment_id`s -- each
        collection must only ever see its own assignment's outcomes."""
        agent_id = uuid4()
        assignment_a = uuid4()
        assignment_b = uuid4()
        submit_accepted_proposal(engine, agent_id=agent_id, assignment_id=assignment_a)
        submit_accepted_proposal(engine, agent_id=agent_id, assignment_id=assignment_b)

        identity_a = resolve_identity(engine, agent_id=agent_id, assignment_id=assignment_a)
        identity_b = resolve_identity(engine, agent_id=agent_id, assignment_id=assignment_b)

        result_a, _ = collect(engine, identity_a)
        result_b, _ = collect(engine, identity_b)

        assert result_a.discovered_count == 1
        assert result_b.discovered_count == 1
