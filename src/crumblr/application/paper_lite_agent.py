"""External Agent adapters for PAPER_LITE's neutral Crumblr contract."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from crumblr.agent_gateway.contracts import NoTradeDecision, TradeProposal
from crumblr.agent_gateway.market_context import AgentMarketContextV1
from crumblr.agent_gateway.static_agent_client import StaticAgentClientConfig, evaluate

PAPER_LITE_AGENT_SCHEMA_VERSION = "paper-lite-agent-1.0"


class PaperLiteAgentResponseError(ValueError):
    """The external Agent response is unusable and nothing may reach Gateway."""


class HttpPaperLiteTradingAgent:
    """Strict HTTP adapter implementing ``PaperLiteTradingAgent``.

    Transport uses the already-proven no-redirect, timeout-bounded and
    response-size-bounded client. The wire response carries an exact Crumblr
    ``TradeProposal`` or ``NoTradeDecision``; it never carries a platform
    ``TradeIntent``.
    """

    __slots__ = ("_agent_id", "_client", "_credential_secret")

    def __init__(
        self,
        *,
        agent_id: UUID,
        gateway_credential_secret: str,
        client: StaticAgentClientConfig,
    ) -> None:
        if not gateway_credential_secret:
            raise ValueError("Gateway Agent credential must not be empty")
        self._agent_id = agent_id
        self._credential_secret = gateway_credential_secret
        self._client = client

    @property
    def agent_id(self) -> UUID:
        return self._agent_id

    @property
    def credential_secret(self) -> str:
        return self._credential_secret

    def decide(self, context: AgentMarketContextV1) -> TradeProposal | NoTradeDecision:
        status, response = evaluate(self._client, context.model_dump(mode="json"))
        if status != 200:
            raise PaperLiteAgentResponseError(f"external Agent returned HTTP {status}")
        if response.get("schema_version") != PAPER_LITE_AGENT_SCHEMA_VERSION:
            raise PaperLiteAgentResponseError(
                f"unsupported Agent response schema: {response.get('schema_version')!r}"
            )
        raw_decision = response.get("decision")
        if not isinstance(raw_decision, dict):
            raise PaperLiteAgentResponseError("external Agent response has no decision object")
        try:
            if response.get("decision_type") == "TRADE_PROPOSAL":
                decision: TradeProposal | NoTradeDecision = TradeProposal.model_validate(
                    raw_decision
                )
            elif response.get("decision_type") == "NO_TRADE":
                decision = NoTradeDecision.model_validate(raw_decision)
            else:
                raise PaperLiteAgentResponseError(
                    f"unsupported decision_type: {response.get('decision_type')!r}"
                )
        except ValidationError as error:
            raise PaperLiteAgentResponseError(
                f"external Agent decision violates the Crumblr contract: {_error_summary(error)}"
            ) from error

        if decision.agent_id != self.agent_id:
            raise PaperLiteAgentResponseError("external Agent response claims another agent_id")
        if decision.assignment_id != context.provenance.assignment_id:
            raise PaperLiteAgentResponseError("external Agent response claims another assignment")
        if decision.context_hash != context.provenance.content_hash:
            raise PaperLiteAgentResponseError("external Agent response is bound to another context")
        return decision


def _error_summary(error: ValidationError) -> list[Any]:
    return error.errors(include_url=False, include_context=False)
