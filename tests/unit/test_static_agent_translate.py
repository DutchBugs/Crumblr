"""agent_gateway/static_agent_translate.py -- TraderDecision 1.0 NO_TRADE
response -> platform NoTradeDecision -> AgentGateway.

`REAL_FORK_RESPONSE` below is not a hand-built fixture -- it is the exact
JSON body this session captured from a real, running, unmodified
`crumblr_strategy_agent.cli serve` process (the raw `core.autocrlf=false`
clone of `DutchBugs/crumblr-static-agent-host`), for the payload
`static_agent_transport.build_unhealthy_market_context()` built with
`decision_window_id="window-http-roundtrip-1"`. Recorded in
`review/AGENT_STATUS.md`. Using the genuine response, not a hand-written
approximation, is the strongest available proof this module actually
translates what the fork actually sends.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    DecisionContextBundle,
    TradingAssignment,
)
from crumblr.agent_gateway.errors import ImpersonationError
from crumblr.agent_gateway.gateway import AgentGateway
from crumblr.agent_gateway.static_agent_translate import (
    StaticAgentResponseRejectedError,
    submit_static_agent_no_trade,
    translate_no_trade_response,
)
from crumblr.agent_gateway.stores import (
    InMemoryAgentCredentialStore,
    InMemoryAgentDecisionOutcomeStore,
    InMemoryAgentIdentityStore,
    InMemoryDecisionContextBundleStore,
    InMemoryFeatureEvidenceStore,
    InMemoryTradingAssignmentStore,
)
from crumblr.domain.enums import DataQuality, Environment, SessionState

NOW = datetime(2026, 9, 1, 12, 30, 0, tzinfo=UTC)
AGENT_ID = uuid4()
ASSIGNMENT_ID = uuid4()
SECRET = "correct-horse-battery-staple"

SENT_CONTEXT: dict[str, Any] = {
    "schema_version": "1.0",
    "decision_window_id": "window-http-roundtrip-1",
    "decision_time_utc": "2026-09-01T12:30:00+00:00",
    "mode": "LIVE_SHADOW",
    "data_origin": "LIVE_FORWARD",
    "strategy": {
        "strategy_id": "ICT_SB_EURUSD_PIVOT2",
        "version": "5.0",
        "source_hash": "eb6e762a95d35ada8f25734440c9ee3008dcbbfe5ced8e3a3d3cda3e6293cda7",
        "profile": "EURUSD_PIVOT2_CORE_V5",
        "config_id": "EURUSD_V5_DEFAULTS",
    },
    "market": {
        "canonical_symbol": "EURUSD",
        "broker_symbol": "EURUSD",
        "timeframe": "M5",
        "market_data_health": "UNKNOWN",
        "last_completed_bar_close_time_utc": "2026-09-01T12:25:00+00:00",
    },
    "instrument_spec": {
        "broker_symbol": "EURUSD",
        "digits": 5,
        "point": "0.00001",
        "tick_size": "0.00001",
        "observed_at_utc": "2026-09-01T12:29:00+00:00",
    },
    "features": {
        "schema_version": "1.0",
        "producer": "CRUMBLR_FROZEN_STRATEGY_CORE",
        "available_at_utc": "2026-09-01T12:25:00+00:00",
        "source_bar_ids": ["EURUSD:M5:2026-09-01T12:25:00+00:00"],
        "observation": {
            "event_type": "NO_TRADE",
            "reason_codes": ["NOT_EVALUATED_MARKET_DATA_UNHEALTHY"],
            "uses_only_confirmed_data": True,
        },
    },
    "input_identity": "input_bfbc660445efcdc21738eaa2e079ab2fb0d9261b556a9200ced6415ad1e9a12d",
}
"""The exact payload sent for the captured real response below (reconstructed
field for field from this session's terminal output, not regenerated --
`input_identity` is the load-bearing field `translate_no_trade_response`
actually checks, and it must match `REAL_FORK_RESPONSE`'s own exactly)."""

REAL_FORK_RESPONSE: dict[str, Any] = {
    "audit": {
        "crumblr_must_persist_append_only": True,
        "data_origin": "LIVE_FORWARD",
        "feature_snapshot": {
            "available_at_utc": "2026-09-01T12:25:00+00:00",
            "observation": {
                "event_type": "NO_TRADE",
                "reason_codes": ["NOT_EVALUATED_MARKET_DATA_UNHEALTHY"],
                "uses_only_confirmed_data": True,
            },
            "producer": "CRUMBLR_FROZEN_STRATEGY_CORE",
            "schema_version": "1.0",
            "source_bar_ids": ["EURUSD:M5:2026-09-01T12:25:00+00:00"],
        },
        "research_only": False,
        "source_bar_ids": ["EURUSD:M5:2026-09-01T12:25:00+00:00"],
        "uses_only_confirmed_data": True,
    },
    "confidence": None,
    "decision_id": "decision_bc5671ef755299ba8141c91ee9a3a18331d3c04777fa9ddc7dbdcb0d4523f826",
    "decision_time_utc": "2026-09-01T12:30:00Z",
    "decision_type": "NO_TRADE",
    "decision_window_id": "window-http-roundtrip-1",
    "eligible_for_intent_risk": False,
    "executable": False,
    "execution_authority": False,
    "input_identity": "input_bfbc660445efcdc21738eaa2e079ab2fb0d9261b556a9200ced6415ad1e9a12d",
    "next_component": "STOP",
    "reason_code_source": "CRUMBLR_INTEGRATION",
    "reason_codes": ["MARKET_DATA_STALE"],
    "schema_version": "1.0",
    "status": "COMPLETED_NO_TRADE",
    "strategy": {
        "config_id": "EURUSD_V5_DEFAULTS",
        "profile": "EURUSD_PIVOT2_CORE_V5",
        "source_hash": "eb6e762a95d35ada8f25734440c9ee3008dcbbfe5ced8e3a3d3cda3e6293cda7",
        "strategy_id": "ICT_SB_EURUSD_PIVOT2",
        "version": "5.0",
    },
    "trade_intent": None,
    "trader": {
        "dynamic_reasoning_used": False,
        "historical_impact_data_used": False,
        "news_used": False,
        "trader_id": "CRUMBLR_STATIC_TRADER",
        "type": "STATIC",
        "version": "0.2.0",
    },
}


def identity(**overrides: Any) -> AgentIdentity:
    fields: dict[str, Any] = {
        "agent_id": AGENT_ID,
        "role": AgentRole.TRADER,
        "runtime_version": "static-trader-v1",
        "service_identity": "spiffe://crumblr/agents/static-trader",
        "status": AgentStatus.ACTIVE,
        "registered_at_utc": NOW,
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
        "strategy_artifact_hash": (
            "eb6e762a95d35ada8f25734440c9ee3008dcbbfe5ced8e3a3d3cda3e6293cda7"
        ),
        "valid_from_utc": NOW - timedelta(days=1),
        "valid_until_utc": NOW + timedelta(days=30),
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


def _registered(gateway: AgentGateway) -> None:
    gateway.register_identity(identity(), credential_secret=SECRET)
    gateway.issue_assignment(assignment())


def _with_context(gateway: AgentGateway, **overrides: Any) -> DecisionContextBundle:
    fields: dict[str, Any] = {
        "assignment_id": ASSIGNMENT_ID,
        "symbol": "EUR/USD",
        "market_snapshot_id": uuid4(),
        "instrument_spec_version": "spec-v1",
        "portfolio_summary_hash": "portfolio-abc",
        "session_state": SessionState.OPEN,
        "data_quality": DataQuality.GOOD,
        "now": NOW,
    }
    fields.update(overrides)
    return gateway.publish_context(**fields)


class TestTranslateRealForkResponse:
    """The strongest available proof: the actual JSON a real, unmodified
    fork process returned, not a hand-built approximation."""

    def test_translates_the_captured_response(self) -> None:
        decision = translate_no_trade_response(
            REAL_FORK_RESPONSE,
            sent_context=SENT_CONTEXT,
            agent_id=AGENT_ID,
            assignment_id=ASSIGNMENT_ID,
            context_hash="ctx-hash-abc",
        )
        assert decision.agent_id == AGENT_ID
        assert decision.assignment_id == ASSIGNMENT_ID
        assert decision.context_hash == "ctx-hash-abc"
        assert decision.reason_codes == ("MARKET_DATA_STALE",)
        assert decision.decided_at_utc == NOW

    def test_the_decision_id_is_deterministic_from_the_forks_own_decision_id(self) -> None:
        first = translate_no_trade_response(
            REAL_FORK_RESPONSE,
            sent_context=SENT_CONTEXT,
            agent_id=AGENT_ID,
            assignment_id=ASSIGNMENT_ID,
            context_hash="ctx-hash-abc",
        )
        second = translate_no_trade_response(
            dict(REAL_FORK_RESPONSE),
            sent_context=SENT_CONTEXT,
            agent_id=AGENT_ID,
            assignment_id=ASSIGNMENT_ID,
            context_hash="ctx-hash-abc",
        )
        assert first.decision_id == second.decision_id

    def test_repeated_translation_is_fully_retry_safe_including_the_fingerprint(self) -> None:
        """Code-review finding: `decided_at_utc` used to be a caller-
        supplied wall-clock parameter, so translating the identical
        response twice (e.g. a retry after a network blip) produced the
        same `decision_id` but a *different* `decision_fingerprint` --
        `AgentDecisionOutcomeStore._claim` treats that as a genuine
        conflict (`DecisionConflictError`), not a safe retry. Now derived
        from the response's own `decision_time_utc`, so repeated
        translation is identical end to end, not merely same-`decision_id`."""
        first = translate_no_trade_response(
            REAL_FORK_RESPONSE,
            sent_context=SENT_CONTEXT,
            agent_id=AGENT_ID,
            assignment_id=ASSIGNMENT_ID,
            context_hash="ctx-hash-abc",
        )
        second = translate_no_trade_response(
            dict(REAL_FORK_RESPONSE),
            sent_context=SENT_CONTEXT,
            agent_id=AGENT_ID,
            assignment_id=ASSIGNMENT_ID,
            context_hash="ctx-hash-abc",
        )
        assert first == second
        assert first.decision_fingerprint == second.decision_fingerprint

    def test_end_to_end_submission_through_the_real_gateway(self, gateway: AgentGateway) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        sent_context = {**SENT_CONTEXT}
        response = {**REAL_FORK_RESPONSE}

        result = submit_static_agent_no_trade(
            gateway,
            response,
            sent_context=sent_context,
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            assignment_id=ASSIGNMENT_ID,
            context_hash=bundle.content_hash,
            now=NOW,
        )
        assert result.accepted is True


class TestRefusals:
    def _mutate(self, **overrides: Any) -> dict[str, Any]:
        return {**REAL_FORK_RESPONSE, **overrides}

    def test_refuses_an_unsupported_schema_version(self) -> None:
        with pytest.raises(StaticAgentResponseRejectedError, match="schema_version"):
            translate_no_trade_response(
                self._mutate(schema_version="2.0"),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_trade_intent_decision_type(self) -> None:
        with pytest.raises(StaticAgentResponseRejectedError, match="NO_TRADE"):
            translate_no_trade_response(
                self._mutate(decision_type="TRADE_INTENT"),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_response_that_claims_executable(self) -> None:
        with pytest.raises(StaticAgentResponseRejectedError, match="execution authority"):
            translate_no_trade_response(
                self._mutate(executable=True),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_response_that_claims_execution_authority(self) -> None:
        with pytest.raises(StaticAgentResponseRejectedError, match="execution authority"):
            translate_no_trade_response(
                self._mutate(execution_authority=True),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_no_trade_response_carrying_a_trade_intent(self) -> None:
        with pytest.raises(StaticAgentResponseRejectedError, match="trade_intent"):
            translate_no_trade_response(
                self._mutate(trade_intent={"setup": "unexpected"}),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_mismatched_input_identity(self) -> None:
        """The response must answer *this* request -- a stale or
        mismatched response is refused, never accepted on trust."""
        with pytest.raises(StaticAgentResponseRejectedError, match="input_identity"):
            translate_no_trade_response(
                self._mutate(input_identity="input_" + "0" * 64),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_strategy_identity_that_does_not_match_the_known_package(self) -> None:
        tampered_strategy = {**REAL_FORK_RESPONSE["strategy"], "version": "6.0"}
        with pytest.raises(StaticAgentResponseRejectedError, match="strategy identity"):
            translate_no_trade_response(
                self._mutate(strategy=tampered_strategy),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_empty_reason_codes(self) -> None:
        with pytest.raises(StaticAgentResponseRejectedError, match="reason_codes"):
            translate_no_trade_response(
                self._mutate(reason_codes=[]),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_missing_decision_id(self) -> None:
        mutated = self._mutate()
        del mutated["decision_id"]
        with pytest.raises(StaticAgentResponseRejectedError, match="decision_id"):
            translate_no_trade_response(
                mutated,
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_missing_decision_time_utc(self) -> None:
        mutated = self._mutate()
        del mutated["decision_time_utc"]
        with pytest.raises(StaticAgentResponseRejectedError, match="decision_time_utc"):
            translate_no_trade_response(
                mutated,
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_malformed_decision_time_utc(self) -> None:
        with pytest.raises(StaticAgentResponseRejectedError, match="decision_time_utc"):
            translate_no_trade_response(
                self._mutate(decision_time_utc="not-a-timestamp"),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )

    def test_refuses_a_naive_decision_time_utc(self) -> None:
        with pytest.raises(StaticAgentResponseRejectedError, match="decision_time_utc"):
            translate_no_trade_response(
                self._mutate(decision_time_utc="2026-09-01T12:30:00"),
                sent_context=SENT_CONTEXT,
                agent_id=AGENT_ID,
                assignment_id=ASSIGNMENT_ID,
                context_hash="ctx",
            )


class TestGatewaySemanticsStillApply:
    """`submit_static_agent_no_trade` must not weaken anything the Gateway
    itself already enforces -- translation happens first, but every
    Gateway-level check still runs on the constructed decision. (A
    same-parameter-sourced `agent_id` means this specific bridge function
    cannot itself construct an impersonating decision -- that guard is
    exercised directly against the Gateway in `test_agent_gateway.py`.)"""

    def test_an_unknown_assignment_is_still_a_normal_rejection_not_an_exception(
        self, gateway: AgentGateway
    ) -> None:
        _registered(gateway)
        bundle = _with_context(gateway)
        result = submit_static_agent_no_trade(
            gateway,
            REAL_FORK_RESPONSE,
            sent_context=SENT_CONTEXT,
            agent_id=AGENT_ID,
            credential_secret=SECRET,
            assignment_id=uuid4(),  # never issued
            context_hash=bundle.content_hash,
            now=NOW,
        )
        assert result.accepted is False

    def test_a_decision_translated_for_a_different_agent_is_still_refused_as_impersonation(
        self, gateway: AgentGateway
    ) -> None:
        """`translate_no_trade_response` and `gateway.submit_no_trade` can
        be called with different `agent_id`s directly (unlike
        `submit_static_agent_no_trade`'s single, shared parameter) --
        proves translation does not bypass the Gateway's own guard."""
        _registered(gateway)
        bundle = _with_context(gateway)
        other_agent = uuid4()
        decision = translate_no_trade_response(
            REAL_FORK_RESPONSE,
            sent_context=SENT_CONTEXT,
            agent_id=other_agent,
            assignment_id=ASSIGNMENT_ID,
            context_hash=bundle.content_hash,
        )
        with pytest.raises(ImpersonationError):
            gateway.submit_no_trade(
                agent_id=AGENT_ID, credential_secret=SECRET, decision=decision, now=NOW
            )
