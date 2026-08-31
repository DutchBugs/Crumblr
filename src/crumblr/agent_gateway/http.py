"""HTTP transport for the Agent Gateway (ADR-005 Step B/§9's eventual "external

Trader against genuine shadow context" proof).

Every prior Step B proof called `AgentGateway` directly from a test — the
same process, the same interpreter, no wire in between. That proves the
Gateway's own logic, but not that a *genuinely separate* process (unable
to import Crumblr's internals, holding only a credential) can reach it
safely. This module is that boundary: two agent-facing routes only,
`POST /agent/proposals` and `POST /agent/no-trade`. Administrative
operations (`register_identity`, `issue_assignment`, `issue_context_bundle`)
get no route at all — `gateway.py`'s own module docstring already calls
these "never reachable by an agent," and that stays true structurally
here, not only by convention: nothing in this file constructs a request
handler for them.

Kept under `agent_gateway/`, not `src/crumblr/api/` — `build.md`'s
architecture diagram already earmarks `api/` for Core's own Control API
(HALT reset, operator controls), a different purpose and a different
authority boundary than this shadow-mode, non-authoritative ingestion
surface.

Authentication is the same interim shared-secret mechanism `auth.py`/AG-001
already establish, carried as two headers (`X-Agent-Id`, `X-Agent-Credential`)
rather than a `Bearer` token, so the two values stay visibly distinct in
transit — never the final mTLS/SPIFFE-shaped mechanism `service_identity`
is named for.

Every response is a well-formed JSON body, even for a rejection —
`AgentDecisionOutcomeResult(accepted=False, ...)` is a normal, fully-audited
outcome (see `gateway.py`), not a transport-level error, so it comes back
as `200 OK` with `"accepted": false` in the body. Only a call that never
reached evaluation at all (bad auth, malformed JSON, a structural content
conflict) uses a non-2xx status, and the response body never includes
exception internals or a traceback — matching `AuthenticationError`'s own
"don't help an attacker enumerate valid agent_ids" discipline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from crumblr.agent_gateway.contracts import NoTradeDecision, TradeProposal
from crumblr.agent_gateway.errors import (
    AgentGatewayError,
    AgentNotActiveError,
    AssignmentConflictError,
    AuthenticationError,
    ContextConflictError,
    DecisionConflictError,
    ImpersonationError,
    UnknownAgentError,
)
from crumblr.agent_gateway.gateway import AgentDecisionOutcomeResult, AgentGateway
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.observability.logging import get_logger

_log = get_logger("agent_gateway_http")

_AUTH_FAILURE_TYPES = (UnknownAgentError, AuthenticationError, AgentNotActiveError)
"""Deliberately one HTTP outcome for all three -- see `errors.py`'s own

docstring on `AuthenticationError`: never let the response distinguish
"unknown agent" from "wrong secret" from "suspended", since that
distinction is exactly what would help an attacker enumerate agents."""

_CONFLICT_TYPES = (DecisionConflictError, AssignmentConflictError, ContextConflictError)


def _outcome_to_json(result: AgentDecisionOutcomeResult) -> dict[str, Any]:
    return {
        "outcome_id": str(result.outcome_id),
        "outcome_type": result.outcome_type.value,
        "accepted": result.accepted,
        "reason": result.reason.value if result.reason is not None else None,
    }


class _AgentCredentials:
    __slots__ = ("agent_id", "secret")

    def __init__(self, agent_id: UUID, secret: str) -> None:
        self.agent_id = agent_id
        self.secret = secret


def _agent_credentials(
    x_agent_id: Annotated[str, Header()],
    x_agent_credential: Annotated[str, Header()],
) -> _AgentCredentials:
    try:
        agent_id = UUID(x_agent_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Agent-Id must be a UUID") from None
    return _AgentCredentials(agent_id=agent_id, secret=x_agent_credential)


def create_app(*, gateway: AgentGateway, clock: Callable[[], UtcDatetime] = utc_now) -> FastAPI:
    """Build the agent-facing HTTP app against one already-constructed

    `AgentGateway`. The caller owns the Gateway's own store lifecycle (the
    same convention `dashboard/app.py::create_app` uses for its engine) --
    this only routes HTTP requests to it.
    """
    app = FastAPI(
        title="Crumblr Agent Gateway — shadow",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def _handle(
        credentials: _AgentCredentials,
        contract_cls: type[TradeProposal] | type[NoTradeDecision],
        payload: Any,
    ) -> JSONResponse:
        try:
            content = contract_cls.model_validate(payload)
        except ValidationError as error:
            # `include_context=False` matters, not just `include_url=False`:
            # a custom `@model_validator`'s `ctx` can carry the raw
            # exception object (e.g. the ValueError from
            # TradeProposal._check_stop_and_target_direction), which plain
            # `json.dumps` (JSONResponse's own encoder) cannot serialize --
            # caught by this module's own test suite raising a 500 instead
            # of the intended 400.
            return JSONResponse(
                {
                    "error": "malformed_input",
                    "detail": error.errors(include_url=False, include_context=False),
                },
                status_code=400,
            )

        try:
            if isinstance(content, TradeProposal):
                result = gateway.submit_trade_proposal(
                    agent_id=credentials.agent_id,
                    credential_secret=credentials.secret,
                    proposal=content,
                    now=clock(),
                )
            else:
                result = gateway.submit_no_trade(
                    agent_id=credentials.agent_id,
                    credential_secret=credentials.secret,
                    decision=content,
                    now=clock(),
                )
        except _AUTH_FAILURE_TYPES:
            return JSONResponse({"error": "authentication_failed"}, status_code=401)
        except ImpersonationError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        except _CONFLICT_TYPES as error:
            _log.warning("agent_gateway_http.conflict", error_type=type(error).__name__)
            return JSONResponse({"error": "conflict"}, status_code=409)
        except AgentGatewayError as error:
            # Fail closed on any AgentGatewayError this handler does not
            # explicitly know about yet, rather than letting it surface as
            # an unhandled 500 with a traceback -- defense in depth for a
            # new exception type added later without updating this map.
            _log.error("agent_gateway_http.unmapped_gateway_error", error_type=type(error).__name__)
            return JSONResponse({"error": "internal_error"}, status_code=500)

        return JSONResponse(_outcome_to_json(result), status_code=200)

    @app.post("/agent/proposals")
    async def submit_proposal(
        request: Request, credentials: Annotated[_AgentCredentials, Depends(_agent_credentials)]
    ) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "malformed_input", "detail": "invalid JSON"}, status_code=400
            )
        return _handle(credentials, TradeProposal, payload)

    @app.post("/agent/no-trade")
    async def submit_no_trade(
        request: Request, credentials: Annotated[_AgentCredentials, Depends(_agent_credentials)]
    ) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse(
                {"error": "malformed_input", "detail": "invalid JSON"}, status_code=400
            )
        return _handle(credentials, NoTradeDecision, payload)

    return app
