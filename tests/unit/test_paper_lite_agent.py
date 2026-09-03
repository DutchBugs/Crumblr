from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

import crumblr.application.paper_lite_agent as agent_module
from crumblr.agent_gateway.contracts import NoTradeDecision, PolicyHints, TradeProposal
from crumblr.agent_gateway.market_context import (
    AgentMarketContextV1,
    build_agent_market_context_v1,
)
from crumblr.agent_gateway.static_agent_client import StaticAgentClientConfig
from crumblr.application.paper_lite_agent import (
    PAPER_LITE_AGENT_SCHEMA_VERSION,
    HttpPaperLiteTradingAgent,
    PaperLiteAgentResponseError,
)
from crumblr.application.paper_lite_toy_agent import ToyAgentMode, create_toy_agent_app
from crumblr.domain.enums import KillSwitchState, ReconciliationStatus, SessionState
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


def client() -> HttpPaperLiteTradingAgent:
    return HttpPaperLiteTradingAgent(
        agent_id=AGENT_ID,
        gateway_credential_secret="gateway-secret",
        client=StaticAgentClientConfig(
            base_url="http://127.0.0.1:8788",
            bearer_token="transport-secret",
        ),
    )


def envelope(decision: TradeProposal | NoTradeDecision) -> dict[str, Any]:
    return {
        "schema_version": PAPER_LITE_AGENT_SCHEMA_VERSION,
        "decision_type": ("TRADE_PROPOSAL" if isinstance(decision, TradeProposal) else "NO_TRADE"),
        "decision": decision.model_dump(mode="json"),
    }


class TestToyAgent:
    def test_requires_bearer_authentication(self) -> None:
        app = create_toy_agent_app(
            agent_id=AGENT_ID,
            mode=ToyAgentMode.NO_TRADE,
            requested_risk_fraction=Decimal("0.01"),
            bearer_token="secret",
        )
        response = TestClient(app).post(
            "/v1/trader/evaluate", json=context().model_dump(mode="json")
        )
        assert response.status_code == 401

    @pytest.mark.parametrize("mode", [ToyAgentMode.BUY, ToyAgentMode.SELL])
    def test_directional_agent_owns_opaque_strategy_vocabulary(self, mode: ToyAgentMode) -> None:
        app = create_toy_agent_app(
            agent_id=AGENT_ID,
            mode=mode,
            requested_risk_fraction=Decimal("0.01"),
            bearer_token="secret",
        )
        response = TestClient(app).post(
            "/v1/trader/evaluate",
            json=context().model_dump(mode="json"),
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 200
        body = response.json()
        proposal = TradeProposal.model_validate(body["decision"])
        assert proposal.reason_codes == ("TOY_ORBITAL_BREAKOUT_CONFIRMED",)
        assert proposal.context_hash == "context-hash"

    def test_session_blackout_returns_no_trade_even_in_directional_mode(self) -> None:
        app = create_toy_agent_app(
            agent_id=AGENT_ID,
            mode=ToyAgentMode.BUY,
            requested_risk_fraction=Decimal("0.01"),
            bearer_token="secret",
        )
        blocked = context().model_copy(
            update={"policy_hints": PolicyHints(session_blackout_active=True)}
        )
        response = TestClient(app).post(
            "/v1/trader/evaluate",
            json=blocked.model_dump(mode="json"),
            headers={"Authorization": "Bearer secret"},
        )
        assert response.status_code == 200
        assert response.json()["decision_type"] == "NO_TRADE"


class TestHttpPaperLiteTradingAgent:
    def test_parses_an_exact_crumblr_no_trade_contract(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent = context()
        decision = NoTradeDecision(
            decision_id=uuid4(),
            agent_id=AGENT_ID,
            assignment_id=ASSIGNMENT_ID,
            context_hash=sent.provenance.content_hash,
            reason_codes=("TOY_IDLE",),
            decided_at_utc=FIXED_NOW,
        )
        monkeypatch.setattr(agent_module, "evaluate", lambda *_: (200, envelope(decision)))

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
        decision = NoTradeDecision(
            decision_id=uuid4(),
            agent_id=AGENT_ID,
            assignment_id=ASSIGNMENT_ID,
            context_hash=sent.provenance.content_hash,
            reason_codes=("TOY_IDLE",),
            decided_at_utc=FIXED_NOW,
        ).model_copy(update={field: value})
        monkeypatch.setattr(agent_module, "evaluate", lambda *_: (200, envelope(decision)))

        with pytest.raises(PaperLiteAgentResponseError, match=message):
            client().decide(sent)

    def test_non_success_status_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(agent_module, "evaluate", lambda *_: (503, {"detail": "down"}))
        with pytest.raises(PaperLiteAgentResponseError, match="HTTP 503"):
            client().decide(context())
