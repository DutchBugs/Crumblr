"""First-draft external-agent contracts (ADR-005, Step A).

Field shapes come from `review/EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md` §5,
revised per the owner's six mandatory tweaks recorded in ADR-005. Every
contract here subclasses `crumblr.domain.models.Contract` directly, reusing
its immutability, extra-field rejection and Decimal/UTC guarantees rather
than inventing a second set — the same guarantees the guide's own §6
specifically praises.

**Nothing outside this package imports from it.** There is no Agent
Gateway, no auth, no mapping from a `TradeProposal` to a platform-owned
`TradeIntent` — that is Step B. `TradeIntent`, `DecisionCapsule` and
`ExecutionOrchestrator` remain the sole authoritative internal contracts;
nothing here replaces or feeds them yet.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import Field, computed_field, model_validator

from crumblr.domain.enums import DataQuality, EntryType, Environment, SessionState, Side
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import DIRECTIONAL_SIDES, Contract, Symbol, VersionTag
from crumblr.domain.money import Price, RiskFraction
from crumblr.domain.timeutils import UtcDatetime


class AgentRole(StrEnum):
    TRADER = "TRADER"
    SUPERVISOR = "SUPERVISOR"
    STRATEGY = "STRATEGY"
    BACKTEST = "BACKTEST"
    TRAINING = "TRAINING"


class AgentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class AgentIdentity(Contract):
    """An external agent's identity (guide §5). A display name is not an

    identity — `service_identity` is the thing an Agent Gateway would
    actually authenticate (a public key fingerprint, an mTLS/SPIFFE-style
    id), never a human-readable label alone.
    """

    agent_id: UUID
    role: AgentRole
    runtime_version: VersionTag
    model_provider: str | None = None
    model_version: VersionTag | None = None
    service_identity: Annotated[str, Field(min_length=1, max_length=256)]
    status: AgentStatus
    capability_claims: tuple[str, ...] = ()
    registered_at_utc: UtcDatetime


class ChampionShadowStatus(StrEnum):
    CHAMPION = "CHAMPION"
    SHADOW = "SHADOW"


class TradingAssignment(Contract):
    """A concrete, versioned opdracht (guide §5) naming what one agent may

    propose: market, timeframe, strategy artifact, validity window, rate
    limit and the requested-risk band it may ask within. Crumblr's Risk
    Engine and Policy Gate remain fully authoritative over what is
    actually permitted — an assignment bounds what an agent may *request*,
    never what gets approved.
    """

    assignment_id: UUID
    assignment_version: VersionTag
    allowed_agent_id: UUID
    canonical_symbol: Symbol
    timeframe: Annotated[str, Field(min_length=1, max_length=16)]
    strategy_artifact_id: UUID
    strategy_artifact_hash: str
    valid_from_utc: UtcDatetime
    valid_until_utc: UtcDatetime
    max_proposals_per_hour: int = Field(gt=0)
    allowed_risk_fraction_min: RiskFraction
    allowed_risk_fraction_max: RiskFraction
    required_evidence_fields: tuple[str, ...]
    supervisor_policy_version: VersionTag
    """Required, not optional — owner direction O-007 / guide §2.7: an

    external Supervisor Agent is required for the agent-driven MVP, even
    though it is never the safety foundation."""
    environment: Environment
    champion_shadow_status: ChampionShadowStatus

    @model_validator(mode="after")
    def _check_validity_window(self) -> Self:
        if self.valid_until_utc <= self.valid_from_utc:
            raise ValueError("valid_until_utc must be after valid_from_utc")
        return self

    @model_validator(mode="after")
    def _check_risk_fraction_range(self) -> Self:
        if self.allowed_risk_fraction_min > self.allowed_risk_fraction_max:
            raise ValueError("allowed_risk_fraction_min must not exceed allowed_risk_fraction_max")
        return self


class PolicyHints(Contract):
    """Typed, closed replacement for an open `dict[str, Any]` payload

    (owner tweak 4) — nothing crosses the agent boundary as an
    unstructured blob. A conservative first-draft field list; extend it
    deliberately as real needs appear, never by widening this back into
    `Any`.
    """

    max_intents_per_hour_hint: int | None = Field(default=None, gt=0)
    min_stop_distance_points_hint: int | None = Field(default=None, gt=0)
    session_blackout_active: bool = False
    notes: Annotated[str, Field(max_length=500)] | None = None


class DecisionContextBundle(Contract):
    """Immutable references to what an agent was allowed to see (guide

    §5): a market snapshot, the current instrument spec, a portfolio
    summary, session/data-quality state, optional policy hints and a
    point-in-time news reference. Carries no broker credentials or
    mutation rights. `news_snapshot_id` is a reference into an
    already-ingested, content-addressed news store — never a live URL or
    fetch instruction (owner tweak 6; see `review/THREAT_MODEL_AGENT_GATEWAY.md`).
    """

    context_id: UUID
    assignment_id: UUID
    market_snapshot_id: UUID
    instrument_spec_version: str
    portfolio_summary_hash: str
    session_state: SessionState
    data_quality: DataQuality
    policy_hints: PolicyHints | None = None
    news_snapshot_id: UUID | None = None
    issued_at_utc: UtcDatetime
    expires_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _check_expiry(self) -> Self:
        if self.expires_at_utc <= self.issued_at_utc:
            raise ValueError("expires_at_utc must be after issued_at_utc")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        """Complete-content fingerprint, excluding `context_id` — mirrors

        `InstrumentSpec.spec_version`/`TradeIntent.decision_hash`'s own
        pattern, so a bundle's freshness claim cannot be forged by
        supplying a hash the actual content does not match.
        """
        return fingerprint(
            {
                "assignment_id": self.assignment_id,
                "market_snapshot_id": self.market_snapshot_id,
                "instrument_spec_version": self.instrument_spec_version,
                "portfolio_summary_hash": self.portfolio_summary_hash,
                "session_state": self.session_state,
                "data_quality": self.data_quality,
                "policy_hints": (
                    self.policy_hints.model_dump(mode="json")
                    if self.policy_hints is not None
                    else None
                ),
                "news_snapshot_id": self.news_snapshot_id,
                "issued_at_utc": self.issued_at_utc,
                "expires_at_utc": self.expires_at_utc,
            }
        )


class TradeProposal(Contract):
    """A directional proposal from an external Trading Agent (guide §5).

    Always directional (BUY/SELL) — `NO_TRADE` is a `NoTradeDecision`, not
    a `TradeProposal` with an empty side (owner tweak 1). Always carries
    both SL and TP (guide §2.5/§2.B) — a proposal missing either is
    rejected here, at construction, before it can reach an Agent Gateway
    at all.
    """

    proposal_id: UUID
    agent_id: UUID
    assignment_id: UUID
    context_hash: str
    """Must equal the `DecisionContextBundle.content_hash` it was formed

    against — binds the proposal to one specific, already-issued,
    immutable context."""
    strategy_artifact_hash: str
    side: Side
    entry_type: EntryType
    reference_price: Price
    stop_loss_price: Price
    take_profit_price: Price
    confidence: float = Field(ge=0.0, le=1.0)
    requested_risk_fraction: RiskFraction
    """A request only (owner tweak 5) — Crumblr's Risk Engine and Policy

    Gate remain fully authoritative over permitted risk and final sizing,
    exactly as `TradeIntent.requested_risk_fraction` already documents for
    the internal path. An agent cannot ask for a lot size, only for "up to
    this fraction of equity," and even that can be refused or reduced
    downstream."""
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[UUID, ...]
    """References into an already-ingested, content-addressed evidence

    store — never a fetch instruction (owner tweak 6, same reasoning as
    `DecisionContextBundle.news_snapshot_id`)."""
    submitted_at_utc: UtcDatetime
    expires_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _check_directional(self) -> Self:
        if self.side not in DIRECTIONAL_SIDES:
            raise ValueError(
                f"a TradeProposal must be directional (BUY or SELL), got {self.side}; "
                "NO_TRADE is a NoTradeDecision, not a TradeProposal"
            )
        return self

    @model_validator(mode="after")
    def _check_stop_and_target_direction(self) -> Self:
        """Mirrors `TradeIntent._check_stop_direction` — a stop on the

        wrong side of entry is an accelerator, not a brake."""
        if self.side is Side.BUY:
            if self.stop_loss_price >= self.reference_price:
                raise ValueError("BUY stop_loss_price must be below reference_price")
            if self.take_profit_price <= self.reference_price:
                raise ValueError("BUY take_profit_price must be above reference_price")
        elif self.side is Side.SELL:
            if self.stop_loss_price <= self.reference_price:
                raise ValueError("SELL stop_loss_price must be above reference_price")
            if self.take_profit_price >= self.reference_price:
                raise ValueError("SELL take_profit_price must be below reference_price")
        return self

    @model_validator(mode="after")
    def _check_lifetime(self) -> Self:
        if self.expires_at_utc <= self.submitted_at_utc:
            raise ValueError("expires_at_utc must be after submitted_at_utc")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def proposal_fingerprint(self) -> str:
        """Complete-content fingerprint, excluding `proposal_id` — the

        canonical identity a future Agent Gateway's idempotent claim logic
        keys on (owner tweak 3), the same pattern
        `ExecutionRequestStore.claim()` already proves out for internal
        execution requests (`persistence/execution.py`): same
        `proposal_id` + matching `proposal_fingerprint` is a safe retry;
        same `proposal_id` + a different fingerprint is a fail-closed
        conflict, never a silent overwrite.
        """
        return fingerprint(
            {
                "agent_id": self.agent_id,
                "assignment_id": self.assignment_id,
                "context_hash": self.context_hash,
                "strategy_artifact_hash": self.strategy_artifact_hash,
                "side": self.side,
                "entry_type": self.entry_type,
                "reference_price": self.reference_price,
                "stop_loss_price": self.stop_loss_price,
                "take_profit_price": self.take_profit_price,
                "confidence": repr(self.confidence),
                "requested_risk_fraction": self.requested_risk_fraction,
                "reason_codes": list(self.reason_codes),
                "evidence_refs": list(self.evidence_refs),
                "submitted_at_utc": self.submitted_at_utc,
                "expires_at_utc": self.expires_at_utc,
            }
        )


class NoTradeDecision(Contract):
    """A durable, explicit agent decision (owner tweak 1) — never inferred

    from the absence of a `TradeProposal`. Structurally distinct from "no
    response arrived before the window closed," which is a future Agent
    Gateway's observation about the agent, not a decision the agent made,
    and must never be recorded or read as equivalent to this contract.
    """

    decision_id: UUID
    agent_id: UUID
    assignment_id: UUID
    context_hash: str
    reason_codes: tuple[str, ...]
    decided_at_utc: UtcDatetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def decision_fingerprint(self) -> str:
        """Complete-content fingerprint, excluding `decision_id` — the same

        idempotent-claim pattern `TradeProposal.proposal_fingerprint` gives
        the Gateway for BUY/SELL proposals (Step B), applied to NO_TRADE so
        a retried identical decision claims safely and a retried decision
        with different content fails closed, rather than NO_TRADE getting a
        weaker idempotency guarantee than a directional proposal.
        """
        return fingerprint(
            {
                "agent_id": self.agent_id,
                "assignment_id": self.assignment_id,
                "context_hash": self.context_hash,
                "reason_codes": list(self.reason_codes),
                "decided_at_utc": self.decided_at_utc,
            }
        )


class ProposalWithdrawal(Contract):
    """A request to withdraw an earlier `TradeProposal` (owner tweak 6).

    Valid only strictly before `ExecutionEventType.SUBMISSION_STARTED`
    (`crumblr.domain.enums`, the exact marker Phase-4 already reserves for
    M5) — a future Agent Gateway enforces that timing rule; this contract
    only records the attempt. Every attempt is durably audited whether
    honoured or refused as too late, never silently dropped — `honoured`
    distinguishes the two outcomes rather than only ever storing successes.
    """

    withdrawal_id: UUID
    proposal_id: UUID
    proposal_fingerprint: str
    agent_id: UUID
    requested_at_utc: UtcDatetime
    reason: Annotated[str, Field(max_length=500)]
    honoured: bool


class ExternalSupervisorVerdict(StrEnum):
    """Deliberately a separate enum from the internal `SupervisorVerdict`

    (`APPROVE`/`VETO`/`HALT`) — an external Supervisor's signal is
    veto-only and never authoritative on its own, a different authority
    than the internal deterministic Policy Gate's verdict.
    """

    APPROVE = "APPROVE"
    VETO = "VETO"
    UNKNOWN = "UNKNOWN"
    """Timeout, error, or an invalid response — never approval (guide

    §2.7, owner direction O-007)."""


class SupervisorReview(Contract):
    """An external Supervisor Agent's review of one proposal (guide §5).

    Cannot change side, price, SL, TP or risk. Binds not only the
    `TradeProposal` it reviewed but the exact platform-owned `TradeIntent`
    the Agent Gateway derived from it, plus (once available) the relevant
    internal Risk and Policy Gate decisions (owner tweak 2) — so a review
    is provably about one specific, fully-identified internal decision
    chain, not merely "some proposal with this id."
    """

    review_id: UUID
    proposal_id: UUID
    proposal_fingerprint: str
    trade_intent_id: UUID
    trade_intent_decision_hash: str
    risk_decision_id: UUID | None = None
    policy_gate_decision_id: UUID | None = None
    """References to the relevant intent-time `RiskDecision` and the

    deterministic Policy Gate's own decision (`evaluator.pretrade`'s
    `SupervisorDecision`). Optional because a review may be requested
    before either has run; once populated they complete the audit chain
    from the external review back to the exact internal decisions it was
    reviewing."""
    supervisor_agent_id: UUID
    supervisor_runtime_version: VersionTag
    policy_version: VersionTag
    verdict: ExternalSupervisorVerdict
    reason_codes: tuple[str, ...]
    evidence_claims: tuple[UUID, ...]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    calibration_notes: Annotated[str, Field(max_length=2000)] | None = None
    reviewed_at_utc: UtcDatetime
    expires_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _check_expiry(self) -> Self:
        if self.expires_at_utc <= self.reviewed_at_utc:
            raise ValueError("expires_at_utc must be after reviewed_at_utc")
        return self
