from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

import crumblr.agent_gateway.neutral_agent_client as client_module
from crumblr.agent_gateway.contracts import NoTradeDecision, PolicyHints, TradeProposal
from crumblr.agent_gateway.market_context import (
    AgentMarketContextV1,
    build_agent_market_context_v1,
)
from crumblr.agent_gateway.neutral_agent_client import (
    NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION,
    HttpNeutralAgentClient,
    NeutralAgentResponseError,
)
from crumblr.agent_gateway.static_agent_client import StaticAgentClientConfig
from crumblr.domain.enums import (
    EntryType,
    KillSwitchState,
    ReconciliationStatus,
    SessionState,
    Side,
)
from tests.conftest import FIXED_NOW, make_instrument_spec, make_snapshot

AGENT_ID = UUID("8f8882ee-1cbc-4c31-a51b-f9f997848c00")
ASSIGNMENT_ID = UUID("3d6c4ce3-41c4-4e12-8dbb-54763581d1d9")


def context() -> AgentMarketContextV1:
    spec = make_instrument_spec()
    snapshot = make_snapshot(symbol_spec_version=spec.spec_version)
    return build_agent_market_context_v1(
        context_id=uuid4(),
        content_hash="context-hash",
        assignment_id=ASSIGNMENT_ID,
        strategy_artifact_id=uuid4(),
        strategy_artifact_hash="toy-artifact-v1",
        issued_at_utc=FIXED_NOW,
        expires_at_utc=FIXED_NOW + timedelta(minutes=5),
        snapshot=snapshot,
        spec=spec,
        session_state=SessionState.OPEN,
        safety_state=KillSwitchState.RUNNING,
        reconciliation_status=ReconciliationStatus.MATCHED,
        feature_snapshot_id=uuid4(),
        open_position_count=0,
        policy_hints=PolicyHints(session_blackout_active=False),
    )


def client() -> HttpNeutralAgentClient:
    return HttpNeutralAgentClient(
        agent_id=AGENT_ID,
        gateway_credential_secret="gateway-secret",
        client=StaticAgentClientConfig(
            base_url="http://127.0.0.1:8788",
            bearer_token="transport-secret",
        ),
    )


def no_trade(sent: AgentMarketContextV1) -> NoTradeDecision:
    return NoTradeDecision(
        decision_id=uuid4(),
        agent_id=AGENT_ID,
        assignment_id=ASSIGNMENT_ID,
        context_hash=sent.provenance.content_hash,
        reason_codes=("NEUTRAL_IDLE",),
        decided_at_utc=FIXED_NOW,
    )


def trade_proposal(sent: AgentMarketContextV1) -> TradeProposal:
    offset = Decimal("0.00200")
    reference = sent.market.ask
    return TradeProposal(
        proposal_id=uuid4(),
        agent_id=AGENT_ID,
        assignment_id=ASSIGNMENT_ID,
        context_hash=sent.provenance.content_hash,
        strategy_artifact_hash=sent.provenance.strategy_artifact_hash,
        side=Side.BUY,
        entry_type=EntryType.MARKET,
        reference_price=reference,
        stop_loss_price=reference - offset,
        take_profit_price=reference + (offset * 2),
        confidence=1.0,
        requested_risk_fraction=Decimal("0.01"),
        reason_codes=("NEUTRAL_BREAKOUT",),
        evidence_refs=(),
        submitted_at_utc=sent.provenance.issued_at_utc,
        expires_at_utc=sent.provenance.expires_at_utc,
    )


def envelope(decision: TradeProposal | NoTradeDecision) -> dict[str, Any]:
    return {
        "schema_version": NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION,
        "decision_type": ("TRADE_PROPOSAL" if isinstance(decision, TradeProposal) else "NO_TRADE"),
        "decision": decision.model_dump(mode="json"),
    }


class TestHttpNeutralAgentClient:
    def test_parses_an_exact_crumblr_no_trade_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = context()
        decision = no_trade(sent)
        monkeypatch.setattr(client_module, "evaluate", lambda *_: (200, envelope(decision)))

        assert client().decide(sent) == decision

    def test_parses_an_exact_crumblr_trade_proposal_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = context()
        decision = trade_proposal(sent)
        monkeypatch.setattr(client_module, "evaluate", lambda *_: (200, envelope(decision)))

        assert client().decide(sent) == decision

    @pytest.mark.parametrize(
        ("field", "value", "message"),
        [
            ("agent_id", uuid4(), "another agent_id"),
            ("assignment_id", uuid4(), "another assignment"),
            ("context_hash", "stale-context", "another context"),
        ],
    )
    def test_mismatched_binding_fails_before_gateway(
        self,
        monkeypatch: pytest.MonkeyPatch,
        field: str,
        value: object,
        message: str,
    ) -> None:
        sent = context()
        decision = no_trade(sent).model_copy(update={field: value})
        monkeypatch.setattr(client_module, "evaluate", lambda *_: (200, envelope(decision)))

        with pytest.raises(NeutralAgentResponseError, match=message):
            client().decide(sent)

    def test_non_success_status_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(client_module, "evaluate", lambda *_: (503, {"detail": "down"}))
        with pytest.raises(NeutralAgentResponseError, match="HTTP 503"):
            client().decide(context())

    def test_unsupported_schema_version_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            client_module,
            "evaluate",
            lambda *_: (200, {"schema_version": "some-other-1.0", "decision_type": "NO_TRADE"}),
        )
        with pytest.raises(NeutralAgentResponseError, match="unsupported Agent response schema"):
            client().decide(context())

    def test_missing_decision_object_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            client_module,
            "evaluate",
            lambda *_: (
                200,
                {
                    "schema_version": NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION,
                    "decision_type": "NO_TRADE",
                },
            ),
        )
        with pytest.raises(NeutralAgentResponseError, match="no decision object"):
            client().decide(context())

    def test_unsupported_decision_type_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sent = context()
        monkeypatch.setattr(
            client_module,
            "evaluate",
            lambda *_: (
                200,
                {
                    "schema_version": NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION,
                    "decision_type": "SOMETHING_ELSE",
                    "decision": no_trade(sent).model_dump(mode="json"),
                },
            ),
        )
        with pytest.raises(NeutralAgentResponseError, match="unsupported decision_type"):
            client().decide(sent)

    def test_a_decision_violating_the_crumblr_contract_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = context()
        malformed = no_trade(sent).model_dump(mode="json")
        del malformed["reason_codes"]
        monkeypatch.setattr(
            client_module,
            "evaluate",
            lambda *_: (
                200,
                {
                    "schema_version": NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION,
                    "decision_type": "NO_TRADE",
                    "decision": malformed,
                },
            ),
        )
        with pytest.raises(NeutralAgentResponseError, match="violates the Crumblr contract"):
            client().decide(sent)

    def test_empty_gateway_credential_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="credential must not be empty"):
            HttpNeutralAgentClient(
                agent_id=AGENT_ID,
                gateway_credential_secret="",
                client=StaticAgentClientConfig(base_url="http://127.0.0.1:8788", bearer_token=None),
            )
