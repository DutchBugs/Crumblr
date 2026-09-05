"""Local-only deterministic toy Agent for PAPER_LITE integration evidence.

It intentionally owns a made-up strategy-local reason vocabulary. Passing the
same neutral context through this Agent proves Crumblr does not need a global
reason-code whitelist or strategy implementation (F-066 item 8). It is not the
genuine Static Agent and must never be presented as that acceptance result.
"""

from __future__ import annotations

import secrets
from decimal import Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import FastAPI, Header, HTTPException

from crumblr.agent_gateway.contracts import NoTradeDecision, TradeProposal
from crumblr.agent_gateway.market_context import AgentMarketContextV1
from crumblr.agent_gateway.neutral_agent_client import NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION
from crumblr.domain.enums import EntryType, Side


class ToyAgentMode(StrEnum):
    NO_TRADE = "NO_TRADE"
    BUY = "BUY"
    SELL = "SELL"


def create_toy_agent_app(
    *,
    agent_id: UUID,
    mode: ToyAgentMode,
    requested_risk_fraction: Decimal,
    bearer_token: str,
) -> FastAPI:
    """Create a localhost-oriented test Agent with bearer authentication."""

    if not bearer_token:
        raise ValueError("the toy Agent bearer token must not be empty")
    app = FastAPI(
        title="Crumblr PAPER_LITE toy Agent",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.post("/v1/trader/evaluate")
    def decide(
        context: AgentMarketContextV1,
        authorization: str | None = Header(default=None),
    ) -> dict[str, object]:
        expected = f"Bearer {bearer_token}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="authentication failed")

        decision_id = uuid5(
            NAMESPACE_URL,
            f"crumblr:paper-lite-toy:{agent_id}:{context.provenance.content_hash}:{mode.value}",
        )
        if mode is ToyAgentMode.NO_TRADE or (
            context.policy_hints is not None and context.policy_hints.session_blackout_active
        ):
            decision: TradeProposal | NoTradeDecision = NoTradeDecision(
                decision_id=decision_id,
                agent_id=agent_id,
                assignment_id=context.provenance.assignment_id,
                context_hash=context.provenance.content_hash,
                reason_codes=("TOY_ORBITAL_RANGE_IDLE",),
                decided_at_utc=context.provenance.issued_at_utc,
            )
            decision_type = "NO_TRADE"
        else:
            side = Side.BUY if mode is ToyAgentMode.BUY else Side.SELL
            reference = context.market.ask if side is Side.BUY else context.market.bid
            offset = Decimal("0.00200")
            decision = TradeProposal(
                proposal_id=decision_id,
                agent_id=agent_id,
                assignment_id=context.provenance.assignment_id,
                context_hash=context.provenance.content_hash,
                strategy_artifact_hash=context.provenance.strategy_artifact_hash,
                side=side,
                entry_type=EntryType.MARKET,
                reference_price=reference,
                stop_loss_price=reference - offset if side is Side.BUY else reference + offset,
                take_profit_price=(
                    reference + (offset * 2) if side is Side.BUY else reference - (offset * 2)
                ),
                confidence=1.0,
                requested_risk_fraction=requested_risk_fraction,
                reason_codes=("TOY_ORBITAL_BREAKOUT_CONFIRMED",),
                evidence_refs=(),
                submitted_at_utc=context.provenance.issued_at_utc,
                expires_at_utc=context.provenance.expires_at_utc,
            )
            decision_type = "TRADE_PROPOSAL"
        return {
            "schema_version": NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION,
            "decision_type": decision_type,
            "decision": decision.model_dump(mode="json"),
        }

    return app
