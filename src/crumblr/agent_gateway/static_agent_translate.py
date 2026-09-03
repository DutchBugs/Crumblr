"""Translate a Static Agent `TraderDecision 1.0` response into a Crumblr
`NoTradeDecision`, then submit it through the existing `AgentGateway`
(feedback.1.27 section 5.2 / section 6 item D).

**Scope, deliberately narrow: `decision_type == "NO_TRADE"` only.**
`feedback.1.27.md` section 5.2 is explicit: "Crumblr must never accept
[the fork's nested `trade_intent` object] as the platform `TradeIntent`...
agent `decision_type=TRADE_INTENT` -> treat nested geometry/reason fields
as UNTRUSTED PROPOSAL MATERIAL -> construct Crumblr `TradeProposal`."
Building that translation is deliberately out of scope until AG-015/F-066's
strategy-neutral context/vocabulary question is resolved on the fork side
(`review/AGENT_FEEDBACK.md`) -- today, the only response this bridge can
honestly produce is `NO_TRADE` (the unhealthy-market smoke path,
`static_agent_transport.py`), so that is the only translation this module
builds. `translate_no_trade_response()` refuses anything else rather than
guessing at a `TradeProposal` shape nobody has designed yet.

**Never trusted blindly.** The response is bound back to the exact
context Crumblr sent, not merely well-formed: `input_identity` must match
what this session computed and sent (proves the decision answers *this*
request, not a stale or mismatched one), the echoed `strategy` block must
match the known frozen-package identity (`static_agent_transport
.STATIC_AGENT_STRATEGY_IDENTITY`) exactly, and `executable`/
`execution_authority` must both be `false` -- refusing outright, never
constructing a decision, if any of these disagree.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from crumblr.agent_gateway.contracts import NoTradeDecision
from crumblr.agent_gateway.gateway import AgentDecisionOutcomeResult, AgentGateway
from crumblr.agent_gateway.static_agent_transport import STATIC_AGENT_STRATEGY_IDENTITY
from crumblr.domain.timeutils import UtcDatetime


class StaticAgentResponseRejectedError(ValueError):
    """The Static Agent's response could not be honestly translated into a
    `NoTradeDecision` -- malformed, claims execution authority, answers a
    different request than the one sent, or is not `NO_TRADE` at all.
    Never a reason to construct a decision anyway; the caller gets no
    `NoTradeDecision` and nothing is submitted to the Gateway."""


def translate_no_trade_response(
    response: dict[str, Any],
    *,
    sent_context: dict[str, Any],
    agent_id: UUID,
    assignment_id: UUID,
    context_hash: str,
) -> NoTradeDecision:
    """Build a platform `NoTradeDecision` from a genuine `TraderDecision
    1.0` NO_TRADE response.

    `sent_context` is the exact `dict` payload this bridge sent as the
    request (`static_agent_transport.build_unhealthy_market_context()`'s
    return value) -- used only to verify the response actually answers it,
    never copied into the constructed decision.

    `decided_at_utc` is deliberately *not* a parameter: it is derived from
    the response's own `decision_time_utc`, never from the caller's
    wall-clock "now". Self-review finding: `NoTradeDecision
    .decision_fingerprint` (`contracts.py`) hashes `decided_at_utc`, so a
    caller-supplied wall-clock value would make translating the identical
    response twice (a retry after e.g. a network blip) produce the same
    deterministic `decision_id` but a *different* fingerprint -- exactly
    the "same id, different content" shape `AgentDecisionOutcomeStore
    ._claim` treats as a fail-closed conflict, `DecisionConflictError`,
    turning a safe idempotent retry into a hard failure. The same class of
    bug AG-016 fixed for `append_event`'s `occurred_at_utc`, here for
    `NoTradeDecision` via a different path.
    """
    if response.get("schema_version") != "1.0":
        raise StaticAgentResponseRejectedError(
            f"unsupported response schema_version: {response.get('schema_version')!r}"
        )
    if response.get("decision_type") != "NO_TRADE":
        raise StaticAgentResponseRejectedError(
            "translate_no_trade_response() only accepts decision_type=NO_TRADE -- "
            f"got {response.get('decision_type')!r} (TRADE_INTENT translation is not built yet)"
        )
    if response.get("executable") is not False or response.get("execution_authority") is not False:
        raise StaticAgentResponseRejectedError(
            "refusing a response that does not explicitly disclaim execution authority"
        )
    if response.get("trade_intent") is not None:
        raise StaticAgentResponseRejectedError("a NO_TRADE response must not carry a trade_intent")
    if response.get("input_identity") != sent_context.get("input_identity"):
        raise StaticAgentResponseRejectedError(
            "response input_identity does not match the context this bridge sent -- "
            "refusing a decision that does not answer this request"
        )
    if response.get("strategy") != STATIC_AGENT_STRATEGY_IDENTITY:
        raise StaticAgentResponseRejectedError(
            "response strategy identity does not match the known frozen package"
        )

    reason_codes_raw = response.get("reason_codes")
    if not isinstance(reason_codes_raw, list) or not reason_codes_raw:
        raise StaticAgentResponseRejectedError(
            "NO_TRADE response must carry non-empty reason_codes"
        )
    if not all(isinstance(code, str) and code for code in reason_codes_raw):
        raise StaticAgentResponseRejectedError("reason_codes must be non-empty strings")

    fork_decision_id = response.get("decision_id")
    if not isinstance(fork_decision_id, str) or not fork_decision_id:
        raise StaticAgentResponseRejectedError("response is missing decision_id")

    decided_at_utc = _parse_decision_time(response.get("decision_time_utc"))

    try:
        return NoTradeDecision(
            decision_id=uuid5(NAMESPACE_URL, f"crumblr:static-agent-decision:{fork_decision_id}"),
            agent_id=agent_id,
            assignment_id=assignment_id,
            context_hash=context_hash,
            reason_codes=tuple(reason_codes_raw),
            decided_at_utc=decided_at_utc,
        )
    except ValidationError as error:
        # `NoTradeDecision.reason_codes` carries its own structural bounds
        # (count/length/safe-character -- `agent_gateway/contracts.py
        # ::ReasonCodes`) beyond the coarse `isinstance` checks above.
        # Self-review finding: a response that passed those coarse checks
        # but violated one of the contract's own bounds (too many codes, a
        # too-long one, a non-ASCII/control character) must still resolve
        # to this module's own documented `StaticAgentResponseRejectedError`
        # -- never a raw `pydantic.ValidationError` a caller that only
        # catches this module's own error type would not expect.
        raise StaticAgentResponseRejectedError(
            f"response reason_codes violate the platform contract's own bounds: {error}"
        ) from error


def _parse_decision_time(raw: Any) -> UtcDatetime:
    if not isinstance(raw, str) or not raw:
        raise StaticAgentResponseRejectedError("response is missing decision_time_utc")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise StaticAgentResponseRejectedError(
            f"response decision_time_utc is not a valid timestamp: {raw!r}"
        ) from error
    if parsed.tzinfo is None:
        raise StaticAgentResponseRejectedError(
            f"response decision_time_utc is not timezone-aware: {raw!r}"
        )
    return parsed


def submit_static_agent_no_trade(
    gateway: AgentGateway,
    response: dict[str, Any],
    *,
    sent_context: dict[str, Any],
    agent_id: UUID,
    credential_secret: str,
    assignment_id: UUID,
    context_hash: str,
    now: UtcDatetime,
) -> AgentDecisionOutcomeResult:
    """Translate and submit in one call -- the end of the bridge for the
    NO_TRADE case: a genuine Static Agent HTTP response becomes an
    authenticated, audited Crumblr outcome. Raises
    `StaticAgentResponseRejectedError` before ever calling the Gateway if
    the response cannot be honestly translated; every `AgentGatewayError`
    the Gateway itself can raise (unknown/inactive agent, wrong
    credential, impersonation, a genuine idempotency conflict) still
    propagates unchanged. `now` is the Gateway's own claim timestamp
    (`AgentGateway.submit_no_trade`'s existing `now` parameter) -- unlike
    `decided_at_utc`, this one is not part of `decision_fingerprint` and a
    fresh wall-clock value on every call/retry is correct, the same
    reasoning `TradeProposal`/`NoTradeDecision` submission already relies
    on elsewhere in the Gateway.
    """
    decision = translate_no_trade_response(
        response,
        sent_context=sent_context,
        agent_id=agent_id,
        assignment_id=assignment_id,
        context_hash=context_hash,
    )
    return gateway.submit_no_trade(
        agent_id=agent_id, credential_secret=credential_secret, decision=decision, now=now
    )
