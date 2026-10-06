"""TECHNICAL VERIFICATION ONLY -- Gateway -> Risk -> Policy -> Supervisor.

Deterministic, non-live. Every input here is a hand-built fixture driven through
the *real*, unmodified `AgentGateway.submit_trade_proposal`, `evaluate_agent_
trade_intent` (real Core Risk, real strategy-neutral Policy Gate) and the
`ReferenceSupervisor`. Nothing here touches the live Reader, the live Static
Agent, MT5, a database or any network. It must NOT be counted as live
acceptance: live acceptance still requires a genuine TRADE_PROPOSAL produced
by the real Static Agent from real market data.

Why this exists: the 2026-10-05 operational acceptance pass proved the live path
through Reader -> Agent -> decision -> Gateway -> capsule -> Dashboard, but the
market never produced a proposal, so Risk/Policy/Supervisor were never entered
live. The pieces were unit-tested separately; this module proves, in one labelled
place and from the real Gateway output, each required case:

  1. a valid TRADE_PROPOSAL is accepted and reaches Risk PASS, Policy APPROVE and
     an external-Supervisor APPROVE, sealing a capsule, with no execution;
  2. a malformed proposal is rejected by the Gateway and never reaches Risk;
  3. real Risk rejections (not hand-set verdicts) stop before Policy/Supervisor;
  4. real Policy rejections stop before the external Supervisor;
  5. the external Supervisor can approve, veto, or be silent -- silence is UNKNOWN,
     never approval -- and never overrides Risk or Policy.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    ExternalSupervisorVerdict,
    TradeProposal,
    TradingAssignment,
)
from crumblr.agent_gateway.errors import AuthenticationError, UnknownAgentError
from crumblr.agent_gateway.gateway import AgentDecisionOutcomeResult, AgentGateway
from crumblr.agent_gateway.reference_supervisor import (
    LOW_CONFIDENCE,
    ReferenceSupervisor,
    ReferenceSupervisorConfig,
)
from crumblr.agent_gateway.stores import (
    InMemoryAgentCredentialStore,
    InMemoryAgentDecisionOutcomeStore,
    InMemoryAgentIdentityStore,
    InMemoryDecisionContextBundleStore,
    InMemoryFeatureEvidenceStore,
    InMemoryTradingAssignmentStore,
)
from crumblr.domain.enums import (
    DataQuality,
    EntryType,
    Environment,
    IncidentStatus,
    ReasonCode,
    ReconciliationStatus,
    RiskVerdict,
    SessionState,
    Side,
    SupervisorVerdict,
)
from crumblr.domain.models import InstrumentSpec, MarketSnapshot
from tests.conftest import FIXED_NOW, make_instrument_spec, make_snapshot
from tests.unit.test_agent_decision_path import (
    CountingSupervisor,
    Fixture,
    NeverReturnsSupervisor,
    untrusted_position,
)

ARTIFACT_HASH = "81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5"
CREDENTIAL = "fixture-credential-not-a-real-secret"


class Chain:
    """One real Gateway + one onboarded agent/assignment, fixture inputs only."""

    def __init__(
        self, *, spec: InstrumentSpec | None = None, snapshot: MarketSnapshot | None = None
    ) -> None:
        self.spec = spec or make_instrument_spec()
        self.snapshot = snapshot or make_snapshot(symbol_spec_version=self.spec.spec_version)
        self.gateway = AgentGateway(
            identities=InMemoryAgentIdentityStore(),
            credentials=InMemoryAgentCredentialStore(),
            assignments=InMemoryTradingAssignmentStore(),
            contexts=InMemoryDecisionContextBundleStore(),
            outcomes=InMemoryAgentDecisionOutcomeStore(),
            feature_evidence=InMemoryFeatureEvidenceStore(),
        )
        self.agent_id = uuid4()
        self.assignment_id = uuid4()
        self.gateway.register_identity(
            AgentIdentity(
                agent_id=self.agent_id,
                role=AgentRole.TRADER,
                runtime_version="technical-verification-fixture",
                service_identity="spiffe://crumblr/agents/technical-verification",
                status=AgentStatus.ACTIVE,
                registered_at_utc=FIXED_NOW,
            ),
            credential_secret=CREDENTIAL,
        )
        self.gateway.issue_assignment(
            TradingAssignment(
                assignment_id=self.assignment_id,
                assignment_version="assignment-v1",
                allowed_agent_id=self.agent_id,
                canonical_symbol="EUR/USD",
                timeframe="M5",
                strategy_artifact_id=uuid4(),
                strategy_artifact_hash=ARTIFACT_HASH,
                valid_from_utc=FIXED_NOW - timedelta(days=1),
                valid_until_utc=FIXED_NOW + timedelta(days=30),
                max_proposals_per_hour=10,
                allowed_risk_fraction_min=Decimal("0.001"),
                allowed_risk_fraction_max=Decimal("0.01"),
                required_evidence_fields=(),
                supervisor_policy_version="supervisor-policy-v1",
                environment=Environment.PAPER,
                champion_shadow_status=ChampionShadowStatus.SHADOW,
            )
        )
        self.bundle = self.gateway.publish_context(
            assignment_id=self.assignment_id,
            symbol="EUR/USD",
            market_snapshot_id=self.snapshot.snapshot_id,
            instrument_spec_version=self.spec.spec_version,
            portfolio_summary_hash="portfolio-technical-verification",
            session_state=SessionState.OPEN,
            data_quality=DataQuality.GOOD,
            now=FIXED_NOW,
        )

    def proposal(self, **overrides: Any) -> TradeProposal:
        fields: dict[str, Any] = {
            "proposal_id": uuid4(),
            "agent_id": self.agent_id,
            "assignment_id": self.assignment_id,
            "context_hash": self.bundle.content_hash,
            "strategy_artifact_hash": ARTIFACT_HASH,
            "side": Side.BUY,
            "entry_type": EntryType.MARKET,
            "reference_price": Decimal("1.08500"),
            "stop_loss_price": Decimal("1.08000"),
            "take_profit_price": Decimal("1.09000"),
            "confidence": 0.8,
            "requested_risk_fraction": Decimal("0.005"),
            "reason_codes": ("fixture_setup_alpha", "fixture_confirm_beta"),
            "evidence_refs": (),
            "submitted_at_utc": FIXED_NOW,
            "expires_at_utc": FIXED_NOW + timedelta(minutes=5),
        }
        fields.update(overrides)
        return TradeProposal.model_validate(fields)

    def submit(self, proposal: TradeProposal) -> AgentDecisionOutcomeResult:
        return self.gateway.submit_trade_proposal(
            agent_id=self.agent_id, credential_secret=CREDENTIAL, proposal=proposal, now=FIXED_NOW
        )

    def accepted(
        self, **proposal_overrides: Any
    ) -> tuple[TradeProposal, AgentDecisionOutcomeResult]:
        proposal = self.proposal(**proposal_overrides)
        result = self.submit(proposal)
        assert result.accepted, f"fixture proposal unexpectedly rejected: {result.reason}"
        assert result.trade_intent is not None
        return proposal, result

    def fixture(self, **kwargs: Any) -> Fixture:
        return Fixture(spec=self.spec, snapshot=self.snapshot, **kwargs)


def reference_supervisor(*, min_confidence: float = 0.0) -> ReferenceSupervisor:
    return ReferenceSupervisor(
        ReferenceSupervisorConfig(supervisor_agent_id=uuid4(), min_confidence=min_confidence)
    )


class TestValidProposalReachesEveryStage:
    def test_a_valid_proposal_is_accepted_and_passes_risk_policy_and_supervisor(self) -> None:
        chain = Chain()
        proposal, gateway_result = chain.accepted()
        intent = gateway_result.trade_intent
        assert intent is not None

        result, recorder = chain.fixture().evaluate(
            intent,
            strategy_version=intent.strategy_version,
            proposal=proposal,
            external_supervisor=reference_supervisor(),
        )

        # Gateway -> real TradeIntent (bound to the proposal's own content)
        assert intent.side is Side.BUY
        assert intent.reference_price == Decimal("1.08500")
        assert intent.stop_loss_price == Decimal("1.08000")
        assert intent.take_profit_price == Decimal("1.09000")
        # Core Risk
        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS
        # strategy-neutral Policy Gate
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.APPROVE
        # external Supervisor, bound to this exact proposal and intent
        assert result.external_supervisor_outcome is not None
        assert result.external_supervisor_outcome.verdict is ExternalSupervisorVerdict.APPROVE
        review = result.external_supervisor_outcome.review
        assert review is not None
        assert review.proposal_id == proposal.proposal_id
        assert review.trade_intent_id == intent.intent_id
        assert review.risk_decision_id == result.risk_decision.decision_id
        assert review.policy_gate_decision_id == result.supervisor_decision.decision_id
        # capsule sealed with the whole chain, and no execution anywhere
        assert recorder.sealed == [result.capsule]
        assert result.capsule.trade_intent == intent
        assert result.capsule.risk_decision == result.risk_decision
        assert result.capsule.supervisor_decision == result.supervisor_decision
        assert result.capsule.execution_result is None
        assert not hasattr(result, "approved_order")


class TestMalformedProposalIsRejectedByTheGateway:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"stop_loss_price": None},
            {"take_profit_price": None},
            {"side": "SIDEWAYS"},
            {"confidence": 7.0},
            {"requested_risk_fraction": Decimal("-0.001")},
            {"requested_risk_fraction": "NaN"},
            {"reference_price": Decimal("0")},
        ],
    )
    def test_a_structurally_invalid_proposal_cannot_even_be_constructed(
        self, overrides: dict[str, Any]
    ) -> None:
        chain = Chain()
        with pytest.raises(ValidationError):
            chain.proposal(**overrides)

    @pytest.mark.parametrize(
        ("overrides", "why"),
        [
            ({"reason_codes": ()}, "no reason codes"),
            (
                {"requested_risk_fraction": Decimal("0.05")},
                "risk fraction above the assignment band",
            ),
            (
                {"requested_risk_fraction": Decimal("0.0001")},
                "risk fraction below the assignment band",
            ),
            ({"strategy_artifact_hash": "0" * 64}, "wrong StrategyArtifact hash"),
            ({"context_hash": "not-an-issued-context"}, "unissued context hash"),
            (
                {
                    "submitted_at_utc": FIXED_NOW - timedelta(minutes=10),
                    "expires_at_utc": FIXED_NOW - timedelta(seconds=1),
                },
                "already expired",
            ),
        ],
    )
    def test_a_well_formed_but_inadmissible_proposal_is_rejected_with_no_trade_intent(
        self, overrides: dict[str, Any], why: str
    ) -> None:
        chain = Chain()
        result = chain.submit(chain.proposal(**overrides))

        assert result.accepted is False, why
        assert result.trade_intent is None, why
        assert result.reason, why  # a stated, auditable reason

    def test_an_unknown_agent_or_wrong_credential_is_refused_before_anything_else(self) -> None:
        chain = Chain()
        proposal = chain.proposal()

        with pytest.raises(AuthenticationError):
            chain.gateway.submit_trade_proposal(
                agent_id=chain.agent_id, credential_secret="wrong", proposal=proposal, now=FIXED_NOW
            )
        with pytest.raises(UnknownAgentError):
            chain.gateway.submit_trade_proposal(
                agent_id=uuid4(), credential_secret=CREDENTIAL, proposal=proposal, now=FIXED_NOW
            )


class TestRealRiskRejectionStopsTheChain:
    """Proposals the Gateway ACCEPTS (they are well-formed and inside the

    assignment) that the real Core Risk engine then blocks -- not hand-set
    verdicts. Policy and the external Supervisor must never be reached."""

    def _risk_blocked(self, chain: Chain, **kwargs: Any) -> Any:
        _, gateway_result = chain.accepted(**kwargs.pop("proposal", {}))
        intent = gateway_result.trade_intent
        assert intent is not None
        supervisor = CountingSupervisor(reference_supervisor())
        result, recorder = kwargs.pop("fixture", chain.fixture()).evaluate(
            intent,
            strategy_version=intent.strategy_version,
            proposal=chain.proposal(),
            external_supervisor=supervisor,
        )
        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.BLOCK
        assert result.supervisor_decision is None  # Policy Gate never reached
        assert result.external_supervisor_outcome is None
        assert supervisor.call_count == 0  # external Supervisor never asked
        assert not any(source == "supervisor" for _, _, _, source in recorder.events)
        assert recorder.sealed == [result.capsule]  # still a complete, auditable capsule
        assert result.capsule.risk_decision == result.risk_decision
        assert result.capsule.execution_result is None
        return result

    def test_a_stop_that_is_too_tight_is_blocked_by_risk(self) -> None:
        chain = Chain()
        result = self._risk_blocked(
            chain,
            proposal={
                "stop_loss_price": Decimal("1.08470")
            },  # 30 points; config floor is 50 -> INVALID_STOP
        )
        assert ReasonCode.INVALID_STOP in result.risk_decision.reason_codes

    def test_a_spread_wider_than_the_limit_is_blocked_by_risk(self) -> None:
        spec = make_instrument_spec()
        wide = make_snapshot(
            symbol_spec_version=spec.spec_version,
            bid=Decimal("1.08500"),
            ask=Decimal("1.08560"),
            spread_points=60,  # config max_spread_points is 25
        )
        chain = Chain(spec=spec, snapshot=wide)
        result = self._risk_blocked(chain)
        assert ReasonCode.SPREAD_TOO_WIDE in result.risk_decision.reason_codes

    def test_a_halted_kill_switch_blocks_risk_with_system_halted(self) -> None:
        from crumblr.risk.kill_switch import KillSwitch

        kill_switch = KillSwitch()
        kill_switch.trip(
            reason_codes=(ReasonCode.MAX_DRAWDOWN,),
            tripped_by="technical-verification",
            occurred_at_utc=FIXED_NOW,
        )
        chain = Chain()
        result = self._risk_blocked(chain, fixture=chain.fixture(kill_switch=kill_switch))
        assert ReasonCode.SYSTEM_HALTED in result.risk_decision.reason_codes

    def test_untrusted_open_risk_blocks_risk_with_open_risk_unknown(self) -> None:
        chain = Chain()
        result = self._risk_blocked(
            chain, fixture=chain.fixture(open_positions=(untrusted_position(),))
        )
        assert ReasonCode.OPEN_RISK_UNKNOWN in result.risk_decision.reason_codes


class TestRealPolicyRejectionStopsBeforeTheExternalSupervisor:
    """Risk PASSES, then the strategy-neutral Policy Gate refuses. The

    external Supervisor must not be asked and must never be a way around it."""

    def _policy_refused(self, **fixture_kwargs: Any) -> Any:
        chain = Chain()
        proposal, gateway_result = chain.accepted()
        intent = gateway_result.trade_intent
        assert intent is not None
        supervisor = CountingSupervisor(reference_supervisor())
        result, recorder = chain.fixture(**fixture_kwargs).evaluate(
            intent,
            strategy_version=intent.strategy_version,
            proposal=proposal,
            external_supervisor=supervisor,
        )
        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS  # Risk was not the blocker
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is not SupervisorVerdict.APPROVE
        assert supervisor.call_count == 0
        assert result.external_supervisor_outcome is None
        assert recorder.sealed == [result.capsule]
        assert result.capsule.execution_result is None
        return result

    def test_unknown_reconciliation_halts_at_the_policy_gate(self) -> None:
        result = self._policy_refused(reconciliation_status=ReconciliationStatus.UNKNOWN)
        assert result.supervisor_decision.verdict is SupervisorVerdict.HALT

    def test_a_reconciliation_mismatch_is_refused_at_the_policy_gate(self) -> None:
        result = self._policy_refused(reconciliation_status=ReconciliationStatus.MISMATCHED)
        assert ReasonCode.RECONCILIATION_MISMATCH in result.supervisor_decision.reason_codes

    def test_an_active_incident_is_refused_at_the_policy_gate(self) -> None:
        result = self._policy_refused(incident_status=IncidentStatus.ACTIVE)
        assert ReasonCode.ACTIVE_INCIDENT in result.supervisor_decision.reason_codes

    def test_an_unknown_incident_state_is_refused_not_assumed_clear(self) -> None:
        result = self._policy_refused(incident_status=IncidentStatus.UNKNOWN)
        assert ReasonCode.INCIDENT_STATE_UNKNOWN in result.supervisor_decision.reason_codes


class TestExternalSupervisorAcceptBlockAndSilence:
    def _evaluate(self, supervisor: Any, **proposal_overrides: Any) -> Any:
        chain = Chain()
        proposal, gateway_result = chain.accepted(**proposal_overrides)
        intent = gateway_result.trade_intent
        assert intent is not None
        result, _ = chain.fixture().evaluate(
            intent,
            strategy_version=intent.strategy_version,
            proposal=proposal,
            external_supervisor=supervisor,
        )
        # Core Risk and the Policy Gate are decided first and are unaffected by
        # whatever the external Supervisor says.
        assert result.risk_decision is not None
        assert result.risk_decision.verdict is RiskVerdict.PASS
        assert result.supervisor_decision is not None
        assert result.supervisor_decision.verdict is SupervisorVerdict.APPROVE
        assert result.external_supervisor_outcome is not None
        return result

    def test_supervisor_approves_a_confident_well_formed_proposal(self) -> None:
        result = self._evaluate(reference_supervisor(min_confidence=0.5), confidence=0.9)
        assert result.external_supervisor_outcome.verdict is ExternalSupervisorVerdict.APPROVE

    def test_supervisor_blocks_a_low_confidence_proposal_without_overriding_risk_or_policy(
        self,
    ) -> None:
        result = self._evaluate(reference_supervisor(min_confidence=0.9), confidence=0.2)
        outcome = result.external_supervisor_outcome
        assert outcome.verdict is ExternalSupervisorVerdict.VETO
        assert LOW_CONFIDENCE in outcome.reason_codes

    def test_a_supervisor_that_never_answers_is_unknown_never_approval(self) -> None:
        result = self._evaluate(NeverReturnsSupervisor())
        assert result.external_supervisor_outcome.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert result.external_supervisor_record is not None
        assert result.external_supervisor_record.verdict is ExternalSupervisorVerdict.UNKNOWN
        assert result.external_supervisor_record.review_id is None
