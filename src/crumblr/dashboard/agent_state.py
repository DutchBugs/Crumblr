"""Read-only Agent/PAPER_LITE state for the dashboard — no decision logic.

Every function here only reads already-persisted facts and reduces them to a
display shape; none of them evaluate a proposal, call the Gateway, or touch
Risk/Policy. Two genuine gaps in what is durably persisted shape this module:

- `AgentMarketContextV1` and `PaperLiteOutcomeType` (see
  `application/paper_lite.py`) are only ever in-process values — neither is
  stored as its own row. What *is* durable, keyed consistently by the
  Gateway's own `outcome_id`, is: `AgentDecisionOutcomeStore`'s settlement
  event, PAPER_LITE's own append-only audit journal (`paper_lite_journal
  .py`), and the sealed `DecisionCapsule` (`persistence/journal.py
  ::CapsuleStore`) for any outcome that reached Core Risk. `build_last_decision`
  reconstructs "what actually happened most recently" by reading those three
  in the same precedence PAPER_LITE's own code produces them in — it does
  not re-run or duplicate that logic, only reads its traces.
- Some terminal states (`GATEWAY_REJECTED`, `SESSION_BLOCKED`, a kill-switch
  `RISK_BLOCKED`) leave no capsule at all. When the latest decision window
  has neither a matching journal fact nor a capsule sealed after it, this
  module infers `GATEWAY_REJECTED` (the one path that leaves no local trace
  by design) and marks the result `inferred=True` rather than presenting a
  guess as a certainty.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID

from crumblr.domain.enums import Environment, RiskVerdict, SupervisorVerdict
from crumblr.domain.models import DecisionCapsule
from crumblr.domain.timeutils import UtcDatetime
from crumblr.market_data.pipeline import interval_for
from crumblr.persistence.journal import CapsuleStore

AssignmentStatus = Literal["ACTIVE", "EXPIRED", "NOT_YET_VALID"]
AgentHealthState = Literal["HEALTHY", "WAITING", "NOT PROVISIONED", "UNKNOWN"]

_SAFETY_HALTED_FACT = "PAPER_LITE_SAFETY_HALTED"
_SESSION_BLOCKED_FACT = "PAPER_LITE_SESSION_BLOCKED"
_ORDER_CHECK_BLOCKED_FACT = "PAPER_LITE_ORDER_CHECK_BLOCKED"
_SUPERVISOR_SKIPPED_FACT = "SUPERVISOR_SKIPPED_PAPER_MODE"
_WINDOW_CLAIMED_FACT = "PAPER_LITE_DECISION_WINDOW_CLAIMED"

_NO_EVIDENCE_FOR_WINDOW_DETAIL = "no risk/policy evidence recorded for the latest decision window"

_AUDIT_FACT_OUTCOME = {
    _SAFETY_HALTED_FACT: "RISK_BLOCKED",
    _SESSION_BLOCKED_FACT: "SESSION_BLOCKED",
    _ORDER_CHECK_BLOCKED_FACT: "PAPER_ORDER_CHECK_BLOCKED",
}


class _AssignmentStoreLike(Protocol):
    def current(self, assignment_id: UUID) -> Any: ...


class _IdentityStoreLike(Protocol):
    def current(self, agent_id: UUID) -> Any: ...


class _ContextBundleStoreLike(Protocol):
    def latest_for(self, assignment_id: UUID) -> Any: ...


@dataclass(frozen=True)
class AgentPanelState:
    """Everything the Agent/StrategyArtifact panel renders — all optional

    fields are `None` because the underlying record genuinely does not
    exist, never because a lookup failed silently."""

    agent_id: UUID
    assignment_id: UUID
    assignment_status: AssignmentStatus
    valid_from_utc: UtcDatetime
    valid_until_utc: UtcDatetime
    canonical_symbol: str
    timeframe: str
    strategy_artifact_id: UUID
    strategy_artifact_hash: str
    runtime_version: str | None
    agent_status: str | None
    latest_context_issued_at_utc: UtcDatetime | None
    latest_context_hash: str | None


@dataclass(frozen=True)
class TradeProposalSummary:
    side: str
    entry_type: str
    reference_price: str
    stop_loss_price: str | None
    take_profit_price: str | None
    requested_risk_fraction: str | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class LastDecisionState:
    """The most recent PAPER_LITE cycle this module could find evidence for.

    `platform_outcome` is one of `NO_TRADE`, `GATEWAY_REJECTED`,
    `SESSION_BLOCKED`, `RISK_BLOCKED`, `POLICY_BLOCKED`,
    `PAPER_ORDER_CHECK_BLOCKED`, `PAPER_FILLED`, `AWAITING_OUTCOME` — the
    last one is a genuine, honestly-labelled ambiguity (Core Risk passed and
    Platform Policy approved, but no later journal fact says what happened
    next — a crash between approval and order-check, most likely)."""

    occurred_at_utc: UtcDatetime | None
    platform_outcome: str
    platform_outcome_detail: str | None
    inferred: bool
    proposal: TradeProposalSummary | None
    risk_verdict: str | None
    risk_reason_codes: tuple[str, ...]
    policy_verdict: str | None
    supervisor_skipped: bool


def build_agent_panel(
    *,
    assignment_store: _AssignmentStoreLike,
    identity_store: _IdentityStoreLike,
    context_bundle_store: _ContextBundleStoreLike,
    assignment_id: UUID | None,
    now: UtcDatetime,
) -> AgentPanelState | None:
    """`None` means "not provisioned" — no `assignment_id` was configured, or

    none was found for the one configured. Never raises on a missing record.
    """
    if assignment_id is None:
        return None
    assignment = assignment_store.current(assignment_id)
    if assignment is None:
        return None

    if now < assignment.valid_from_utc:
        status: AssignmentStatus = "NOT_YET_VALID"
    elif now > assignment.valid_until_utc:
        status = "EXPIRED"
    else:
        status = "ACTIVE"

    identity = identity_store.current(assignment.allowed_agent_id)
    bundle = context_bundle_store.latest_for(assignment_id)

    return AgentPanelState(
        agent_id=assignment.allowed_agent_id,
        assignment_id=assignment.assignment_id,
        assignment_status=status,
        valid_from_utc=assignment.valid_from_utc,
        valid_until_utc=assignment.valid_until_utc,
        canonical_symbol=assignment.canonical_symbol,
        timeframe=assignment.timeframe,
        strategy_artifact_id=assignment.strategy_artifact_id,
        strategy_artifact_hash=assignment.strategy_artifact_hash,
        runtime_version=identity.runtime_version if identity is not None else None,
        agent_status=identity.status.value if identity is not None else None,
        latest_context_issued_at_utc=(bundle.issued_at_utc if bundle is not None else None),
        latest_context_hash=(bundle.content_hash if bundle is not None else None),
    )


def _proposal_summary(capsule: DecisionCapsule) -> TradeProposalSummary | None:
    intent = capsule.trade_intent
    if intent is None:
        return None
    return TradeProposalSummary(
        side=intent.side.value,
        entry_type=intent.entry_type.value,
        reference_price=str(intent.reference_price),
        stop_loss_price=(
            str(intent.stop_loss_price) if intent.stop_loss_price is not None else None
        ),
        take_profit_price=(
            str(intent.take_profit_price) if intent.take_profit_price is not None else None
        ),
        requested_risk_fraction=(
            str(intent.requested_risk_fraction)
            if intent.requested_risk_fraction is not None
            else None
        ),
        reason_codes=tuple(intent.reason_codes),
    )


def _from_capsule(capsule: DecisionCapsule, *, supervisor_skipped: bool) -> LastDecisionState:
    risk = capsule.risk_decision
    policy = capsule.supervisor_decision
    risk_verdict = risk.verdict.value if risk is not None else None
    policy_verdict = policy.verdict.value if policy is not None else None

    if capsule.trade_intent is None:
        outcome = "NO_TRADE"
    elif risk is None or risk.verdict is not RiskVerdict.PASS:
        outcome = "RISK_BLOCKED"
    elif policy is None or policy.verdict is not SupervisorVerdict.APPROVE:
        outcome = "POLICY_BLOCKED"
    else:
        # Risk PASS + Policy APPROVE with no later journal fact to say what
        # happened at order-check/fill time -- a real, not fabricated, gap.
        outcome = "AWAITING_OUTCOME"

    return LastDecisionState(
        occurred_at_utc=capsule.occurred_at_utc,
        platform_outcome=outcome,
        platform_outcome_detail=None,
        inferred=False,
        proposal=_proposal_summary(capsule),
        risk_verdict=risk_verdict,
        risk_reason_codes=(tuple(code.value for code in risk.reason_codes) if risk else ()),
        policy_verdict=policy_verdict,
        supervisor_skipped=supervisor_skipped,
    )


def build_last_decision(
    *,
    capsule_store: CapsuleStore,
    journal_entries: tuple[dict[str, Any], ...],
) -> LastDecisionState | None:
    """`None` means no PAPER_LITE evidence exists anywhere yet ("NO EVIDENCE")."""
    window_claims = [
        entry
        for entry in journal_entries
        if entry.get("event_type") == "AUDIT_FACT"
        and entry.get("payload", {}).get("fact") == _WINDOW_CLAIMED_FACT
    ]
    latest_claim = max(window_claims, key=lambda entry: entry["sequence"], default=None)
    latest_claim_sequence = latest_claim["sequence"] if latest_claim is not None else -1

    after_claim = [entry for entry in journal_entries if entry["sequence"] > latest_claim_sequence]

    supervisor_skipped = any(
        entry.get("event_type") == "AUDIT_FACT"
        and entry.get("payload", {}).get("fact") == _SUPERVISOR_SKIPPED_FACT
        for entry in after_claim
    )

    fill_entry = next(
        (entry for entry in after_claim if entry.get("event_type") == "PAPER_ORDER_ACCEPTED"),
        None,
    )
    if fill_entry is not None:
        return LastDecisionState(
            occurred_at_utc=None,
            platform_outcome="PAPER_FILLED",
            platform_outcome_detail=None,
            inferred=False,
            proposal=None,
            risk_verdict=None,
            risk_reason_codes=(),
            policy_verdict=None,
            supervisor_skipped=supervisor_skipped,
        )

    fact_entry = next(
        (
            entry
            for entry in after_claim
            if entry.get("event_type") == "AUDIT_FACT"
            and entry.get("payload", {}).get("fact") in _AUDIT_FACT_OUTCOME
        ),
        None,
    )
    if fact_entry is not None:
        fact = fact_entry["payload"]["fact"]
        return LastDecisionState(
            occurred_at_utc=None,
            platform_outcome=_AUDIT_FACT_OUTCOME[fact],
            platform_outcome_detail=fact_entry["payload"].get("detail"),
            inferred=False,
            proposal=None,
            risk_verdict=None,
            risk_reason_codes=(),
            policy_verdict=None,
            supervisor_skipped=supervisor_skipped,
        )

    capsules = capsule_store.read_all(environment=Environment.PAPER)
    latest_capsule = capsules[-1] if capsules else None

    if latest_capsule is None:
        if latest_claim is None:
            return None
        return LastDecisionState(
            occurred_at_utc=_parse_claim_bar_time(latest_claim),
            platform_outcome="GATEWAY_REJECTED",
            platform_outcome_detail=_NO_EVIDENCE_FOR_WINDOW_DETAIL,
            inferred=True,
            proposal=None,
            risk_verdict=None,
            risk_reason_codes=(),
            policy_verdict=None,
            supervisor_skipped=False,
        )

    if latest_claim is not None:
        window_bar_time = _parse_claim_bar_time(latest_claim)
        if window_bar_time is not None and latest_capsule.occurred_at_utc < window_bar_time:
            # The most recent capsule predates the most recent decision
            # window -- that window produced no capsule and no fact, which
            # is exactly what a Gateway rejection looks like from here.
            return LastDecisionState(
                occurred_at_utc=window_bar_time,
                platform_outcome="GATEWAY_REJECTED",
                platform_outcome_detail=(_NO_EVIDENCE_FOR_WINDOW_DETAIL),
                inferred=True,
                proposal=None,
                risk_verdict=None,
                risk_reason_codes=(),
                policy_verdict=None,
                supervisor_skipped=False,
            )

    return _from_capsule(latest_capsule, supervisor_skipped=supervisor_skipped)


def _parse_claim_bar_time(claim_entry: dict[str, Any]) -> UtcDatetime | None:
    detail = claim_entry.get("payload", {}).get("detail")
    if not detail:
        return None
    try:
        return datetime.fromisoformat(detail)
    except ValueError:
        return None


def build_agent_health(
    *,
    agent_panel: AgentPanelState | None,
    last_decision: LastDecisionState | None,
    now: UtcDatetime,
    timeframe: str,
) -> AgentHealthState:
    if agent_panel is None or agent_panel.assignment_status != "ACTIVE":
        return "NOT PROVISIONED"
    if agent_panel.runtime_version is None:
        # The assignment names an agent_id no AgentIdentity record exists
        # for -- a real inconsistency, not merely "no evidence yet".
        return "UNKNOWN"
    if last_decision is None or last_decision.occurred_at_utc is None:
        return "WAITING"
    staleness_threshold = interval_for(timeframe) * 3
    if now - last_decision.occurred_at_utc > staleness_threshold:
        return "WAITING"
    return "HEALTHY"
