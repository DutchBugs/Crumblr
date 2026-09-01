"""HTTP transport for the Agent Gateway (`agent_gateway/http.py`).

Uses the in-memory stores -- this is about the wire boundary (headers,
JSON, status codes, structural route exposure), not the Gateway's own
authorization logic, which `tests/unit/test_agent_gateway.py` already
covers exhaustively against the same `AgentGateway` class directly.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    DecisionContextBundle,
    TradingAssignment,
)
from crumblr.agent_gateway.gateway import AgentGateway
from crumblr.agent_gateway.http import create_app
from crumblr.agent_gateway.stores import (
    InMemoryAgentCredentialStore,
    InMemoryAgentDecisionOutcomeStore,
    InMemoryAgentIdentityStore,
    InMemoryDecisionContextBundleStore,
    InMemoryFeatureEvidenceStore,
    InMemoryTradingAssignmentStore,
)
from crumblr.domain.enums import DataQuality, Environment, SessionState
from tests.conftest import FIXED_NOW

AGENT_ID = uuid4()
ASSIGNMENT_ID = uuid4()
SECRET = "correct-horse-battery-staple"


def identity(**overrides: Any) -> AgentIdentity:
    fields: dict[str, Any] = {
        "agent_id": AGENT_ID,
        "role": AgentRole.TRADER,
        "runtime_version": "trader-v1",
        "service_identity": "spiffe://crumblr/agents/trader-v1",
        "status": AgentStatus.ACTIVE,
        "registered_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return AgentIdentity.model_validate(fields)


def assignment(**overrides: Any) -> TradingAssignment:
    fields: dict[str, Any] = {
        "assignment_id": ASSIGNMENT_ID,
        "assignment_version": "assignment-v1",
        "allowed_agent_id": AGENT_ID,
        "canonical_symbol": "EUR/USD",
        "timeframe": "M5",
        "strategy_artifact_id": uuid4(),
        "strategy_artifact_hash": "abc123",
        "valid_from_utc": FIXED_NOW - timedelta(days=1),
        "valid_until_utc": FIXED_NOW + timedelta(days=30),
        "max_proposals_per_hour": 6,
        "allowed_risk_fraction_min": Decimal("0.001"),
        "allowed_risk_fraction_max": Decimal("0.01"),
        "required_evidence_fields": (),
        "supervisor_policy_version": "supervisor-policy-v1",
        "environment": Environment.PAPER,
        "champion_shadow_status": ChampionShadowStatus.SHADOW,
    }
    fields.update(overrides)
    return TradingAssignment.model_validate(fields)


def proposal_json(*, context_hash: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "proposal_id": str(uuid4()),
        "agent_id": str(AGENT_ID),
        "assignment_id": str(ASSIGNMENT_ID),
        "context_hash": context_hash,
        "strategy_artifact_hash": "abc123",
        "side": "BUY",
        "entry_type": "MARKET",
        "reference_price": "1.08500",
        "stop_loss_price": "1.08000",
        "take_profit_price": "1.09000",
        "confidence": 0.8,
        "requested_risk_fraction": "0.005",
        "reason_codes": ["sweep_and_shift"],
        "evidence_refs": [],
        "submitted_at_utc": FIXED_NOW.isoformat(),
        "expires_at_utc": (FIXED_NOW + timedelta(minutes=5)).isoformat(),
    }
    fields.update(overrides)
    return fields


def no_trade_json(*, context_hash: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "decision_id": str(uuid4()),
        "agent_id": str(AGENT_ID),
        "assignment_id": str(ASSIGNMENT_ID),
        "context_hash": context_hash,
        "reason_codes": ["no_setup"],
        "decided_at_utc": FIXED_NOW.isoformat(),
    }
    fields.update(overrides)
    return fields


def _headers(*, agent_id: str = str(AGENT_ID), secret: str = SECRET) -> dict[str, str]:
    return {"X-Agent-Id": agent_id, "X-Agent-Credential": secret}


@pytest.fixture
def gateway() -> AgentGateway:
    return AgentGateway(
        identities=InMemoryAgentIdentityStore(),
        credentials=InMemoryAgentCredentialStore(),
        assignments=InMemoryTradingAssignmentStore(),
        contexts=InMemoryDecisionContextBundleStore(),
        outcomes=InMemoryAgentDecisionOutcomeStore(),
        feature_evidence=InMemoryFeatureEvidenceStore(),
    )


@pytest.fixture
def app(gateway: AgentGateway) -> FastAPI:
    return create_app(gateway=gateway, clock=lambda: FIXED_NOW)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _registered(gateway: AgentGateway, *, status: AgentStatus = AgentStatus.ACTIVE) -> None:
    gateway.register_identity(identity(status=status), credential_secret=SECRET)
    gateway.issue_assignment(assignment())


def _with_context(gateway: AgentGateway) -> DecisionContextBundle:
    """Publishes a context bundle the real way (review 1.26 §5's flow) --

    records `AgentContextEvidence` first, then issues a bundle citing it."""
    return gateway.publish_context(
        assignment_id=ASSIGNMENT_ID,
        symbol="EUR/USD",
        market_snapshot_id=uuid4(),
        instrument_spec_version="spec-v1",
        portfolio_summary_hash="portfolio-abc",
        session_state=SessionState.OPEN,
        data_quality=DataQuality.GOOD,
        now=FIXED_NOW,
    )


class TestSubmitProposal:
    def test_a_valid_accepted_proposal_returns_200(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        response = client.post(
            "/agent/proposals",
            json=proposal_json(context_hash=bundle.content_hash),
            headers=_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["outcome_type"] == "TRADE_PROPOSAL"
        assert body["reason"] is None

    def test_a_rejected_proposal_still_returns_200_with_accepted_false(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        """A refusal is a normal, fully-audited outcome, not a transport

        error -- see http.py's module docstring."""
        _registered(gateway)
        response = client.post(
            "/agent/proposals",
            json=proposal_json(context_hash="never-issued-hash"),
            headers=_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is False
        assert body["reason"] == "UNKNOWN_CONTEXT"

    def test_missing_auth_headers_are_rejected(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        response = client.post(
            "/agent/proposals", json=proposal_json(context_hash=bundle.content_hash)
        )
        assert response.status_code == 422

    def test_a_wrong_credential_returns_401(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        response = client.post(
            "/agent/proposals",
            json=proposal_json(context_hash=bundle.content_hash),
            headers=_headers(secret="wrong-secret"),
        )
        assert response.status_code == 401
        assert "wrong-secret" not in response.text
        assert SECRET not in response.text

    def test_an_unregistered_agent_returns_401(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        response = client.post(
            "/agent/proposals",
            json=proposal_json(context_hash="whatever"),
            headers=_headers(agent_id=str(uuid4())),
        )
        assert response.status_code == 401

    def test_a_non_uuid_agent_id_header_returns_400(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        _registered(gateway)
        response = client.post(
            "/agent/proposals",
            json=proposal_json(context_hash="whatever"),
            headers=_headers(agent_id="not-a-uuid"),
        )
        assert response.status_code == 400

    def test_impersonation_returns_403(self, gateway: AgentGateway, client: TestClient) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        response = client.post(
            "/agent/proposals",
            json=proposal_json(context_hash=bundle.content_hash, agent_id=str(uuid4())),
            headers=_headers(),
        )
        assert response.status_code == 403

    def test_malformed_json_body_returns_400(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        _registered(gateway)
        response = client.post(
            "/agent/proposals",
            content=b"{not valid json",
            headers={**_headers(), "Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "malformed_input"

    def test_a_proposal_missing_a_required_field_returns_400(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        payload = proposal_json(context_hash=bundle.content_hash)
        del payload["stop_loss_price"]
        response = client.post("/agent/proposals", json=payload, headers=_headers())
        assert response.status_code == 400
        assert response.json()["error"] == "malformed_input"

    def test_a_domain_validator_rejection_returns_400_not_500(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        """Regression coverage: a `@model_validator` rejection (not just a

        missing-field error) carries the raw exception in its `ctx`, which
        plain `json.dumps` cannot serialize -- this must still come back
        as a clean 400, never an unhandled 500."""
        _registered(gateway)
        bundle = _with_context(gateway)
        payload = proposal_json(
            context_hash=bundle.content_hash,
            side="BUY",
            reference_price="1.08500",
            stop_loss_price="1.09000",  # above reference on a BUY -- invalid
        )
        response = client.post("/agent/proposals", json=payload, headers=_headers())
        assert response.status_code == 400
        assert response.json()["error"] == "malformed_input"

    def test_an_identical_retry_replays_the_same_result(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        body = proposal_json(context_hash=bundle.content_hash)
        first = client.post("/agent/proposals", json=body, headers=_headers())
        second = client.post("/agent/proposals", json=body, headers=_headers())
        assert first.json() == second.json()

    def test_a_conflicting_retry_returns_409(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        body = proposal_json(context_hash=bundle.content_hash)
        client.post("/agent/proposals", json=body, headers=_headers())
        conflicting = dict(body)
        conflicting["confidence"] = 0.99
        response = client.post("/agent/proposals", json=conflicting, headers=_headers())
        assert response.status_code == 409


class TestSubmitNoTrade:
    def test_a_valid_no_trade_returns_200(self, gateway: AgentGateway, client: TestClient) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        response = client.post(
            "/agent/no-trade",
            json=no_trade_json(context_hash=bundle.content_hash),
            headers=_headers(),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["outcome_type"] == "NO_TRADE"

    def test_missing_auth_headers_are_rejected(
        self, gateway: AgentGateway, client: TestClient
    ) -> None:
        response = client.post("/agent/no-trade", json=no_trade_json(context_hash="whatever"))
        assert response.status_code == 422


class TestNoAdministrativeRouteExists:
    """Structural check, not just intent: `register_identity`/`issue_assignment`/

    `issue_context_bundle` must have no HTTP route at all -- mirrors
    `tests/integration/test_dashboard.py`'s structural "no mutation route"
    check for the same reason: a docstring promise is not a guarantee."""

    def test_only_the_two_agent_facing_routes_are_registered(self, app: FastAPI) -> None:
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        agent_paths = {path for path in paths if path.startswith("/agent")}
        assert agent_paths == {"/agent/proposals", "/agent/no-trade"}

    def test_docs_are_disabled(self, client: TestClient) -> None:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
