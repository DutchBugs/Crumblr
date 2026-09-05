"""Generic wire-response envelope/adapter for a neutral-context external
Agent host (`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md` section 2
Phase A, Dev-2 item 1): "Move the reusable neutral external-Agent HTTP
response envelope/adapter into the Agent Gateway layer. PAPER_LITE must not
be the owner of the production wire contract."

This is the platform-owned counterpart of `AgentMarketContextV1`
(`market_context.py`): that module builds the *outbound* strategy-neutral
context; this module parses the *inbound* response any neutral-context
Agent host -- the real Static Agent fork once it speaks this contract
(feedback.1.28 section 13), PAPER_LITE's local toy Agent, or a future
independent third implementation (F-066 item 8's own regression proof) --
returns, into the same authoritative `TradeProposal`/`NoTradeDecision`
contracts Gateway itself accepts. It never invents or relaxes those
contracts; it only refuses a response that does not honestly satisfy them.

Transport itself (timeout/redirect/response-size bounds) is not duplicated
here -- reused from `static_agent_client.evaluate()`, the same zero-
dependency stdlib client `static_agent_transport.py`'s legacy fork proof
already uses.

**Now the production caller (Dev-3 Phase-A convergence, 2026-09-05).**
`application/paper_lite_agent.py`'s own copy of this exact envelope
(`PAPER_LITE_AGENT_SCHEMA_VERSION`/`HttpPaperLiteTradingAgent`) has been
retired -- `scripts/paper_lite.py` and `application/paper_lite_toy_agent.py`
both migrated onto this module instead, exactly the substitution the work
order named as a Dev-3 action ("Replace paper-only wire-envelope ownership
with Dev 2's generic Agent adapter"). The schema-version string below
(`"neutral-agent-response-1.0"`) is the one production wire value now --
nothing needed to keep speaking the old `"paper-lite-agent-1.0"`, since
F-064 means no HTTP transport was ever deployed under either name.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from crumblr.agent_gateway.contracts import NoTradeDecision, TradeProposal
from crumblr.agent_gateway.market_context import AgentMarketContextV1
from crumblr.agent_gateway.static_agent_client import StaticAgentClientConfig, evaluate

NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION = "neutral-agent-response-1.0"
"""The wire envelope: `{"schema_version", "decision_type", "decision"}`,
where `decision_type` is `"TRADE_PROPOSAL"` or `"NO_TRADE"` and `decision`
is the exact `model_dump(mode="json")` of the corresponding Crumblr
contract. Any Agent host implementing this contract returns exactly this
shape -- there is no separate "success" vs "decision" envelope."""


class NeutralAgentResponseError(ValueError):
    """The external Agent's response is unusable -- nothing may reach Gateway."""


class HttpNeutralAgentClient:
    """Strict HTTP adapter for a neutral-context external Agent host.

    The wire response carries an exact Crumblr `TradeProposal` or
    `NoTradeDecision`; it never carries a platform `TradeIntent` -- mapping
    an accepted proposal into one remains Gateway's own exclusive
    responsibility (`decision_path.py`).
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
            raise NeutralAgentResponseError(f"external Agent returned HTTP {status}")
        if response.get("schema_version") != NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION:
            raise NeutralAgentResponseError(
                f"unsupported Agent response schema: {response.get('schema_version')!r}"
            )
        raw_decision = response.get("decision")
        if not isinstance(raw_decision, dict):
            raise NeutralAgentResponseError("external Agent response has no decision object")
        try:
            if response.get("decision_type") == "TRADE_PROPOSAL":
                decision: TradeProposal | NoTradeDecision = TradeProposal.model_validate(
                    raw_decision
                )
            elif response.get("decision_type") == "NO_TRADE":
                decision = NoTradeDecision.model_validate(raw_decision)
            else:
                raise NeutralAgentResponseError(
                    f"unsupported decision_type: {response.get('decision_type')!r}"
                )
        except ValidationError as error:
            raise NeutralAgentResponseError(
                f"external Agent decision violates the Crumblr contract: {_error_summary(error)}"
            ) from error

        if decision.agent_id != self.agent_id:
            raise NeutralAgentResponseError("external Agent response claims another agent_id")
        if decision.assignment_id != context.provenance.assignment_id:
            raise NeutralAgentResponseError("external Agent response claims another assignment")
        if decision.context_hash != context.provenance.content_hash:
            raise NeutralAgentResponseError("external Agent response is bound to another context")
        return decision


def _error_summary(error: ValidationError) -> list[Any]:
    return error.errors(include_url=False, include_context=False)
