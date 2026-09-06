"""Read-only Agent/PAPER_LITE state for the dashboard — no decision logic.

Every function here only reads already-persisted facts and reduces them to a
display shape; none of them evaluate a proposal, call the Gateway, or touch
Risk/Policy.

**Identity discipline (review feedback, second pass):** the first version of
`build_last_decision` found "the most recent capsule/journal fact anywhere"
and presented it as though it were this assignment's decision — with two or
more PAPER assignments active, that could show one assignment's outcome next
to another's Agent panel. Every lookup here is now anchored to a single,
concrete `outcome_id`, obtained from `AgentDecisionOutcomeStore
.latest_outcome_id_for(assignment_id)` — the one store that actually records
which assignment claimed which outcome. The PAPER_LITE journal and
`CapsuleStore` are only ever consulted *for that exact `outcome_id`*
(`correlation_id` on a journal audit fact, or the deterministic
`capsule_id = uuid5(NAMESPACE_URL, f"crumblr:agent-capsule:{outcome_id}")`
derivation `decision_path.py` itself uses) — never by "whichever is most
recent in time," which is exactly the cross-assignment leak this replaces.

**No more inferring `GATEWAY_REJECTED` from absence.** The first version
treated "a decision window was claimed but nothing else was ever recorded"
as good evidence of a Gateway rejection. It is not: it is equally consistent
with a crash between claim and settlement, or a settlement that has not
propagated yet. `GATEWAY_REJECTED` is now reported *only* when
`AgentDecisionOutcomeStore.settlement_for(outcome_id)` returns a real
`REJECTED` event — a durable fact, not a guess. Genuine "claimed, nothing
further known" now reports `AWAITING_EVIDENCE`, and a corrupt PAPER_LITE
journal line (surfaced by `paper_lite_journal.read_journal_entries`'s
`had_corruption` flag) reports `DEGRADED` immediately, before any
correlation is attempted — an incomplete read must not produce a confident
answer.

**Named, deliberate gap: `PAPER_FILLED` is not currently reachable through
this precedence.** `persistence.paper_lite.py`'s `PAPER_ORDER_ACCEPTED`
journal entry (the durable trace of a real fill) carries no `outcome_id`/
`correlation_id` in its payload — confirmed by reading `DurablePaperBroker
.submit()` directly. Every other audit fact this module reads
(`PAPER_LITE_SAFETY_HALTED`/`_SESSION_BLOCKED`/`_ORDER_CHECK_BLOCKED`/
`SUPERVISOR_SKIPPED_PAPER_MODE`) is written with `correlation_id=
gateway_result.outcome_id`, so those bind cleanly; a fill does not. Rather
than guess a fill from timing (exactly the kind of un-anchored inference
this rewrite removes), a capsule with Risk `PASS` + Policy `APPROVE` and no
further outcome_id-bound fact reports `AWAITING_OUTCOME` — correct even for
a genuinely filled order, until `application/paper_lite.py` (Dev-2/Dev-3
owned, out of this branch's scope) adds a correlation id to that payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.agent_gateway.events import AgentDecisionEventType
from crumblr.domain.enums import RiskVerdict, SupervisorVerdict
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

_AUDIT_FACT_OUTCOME = {
    _SAFETY_HALTED_FACT: "RISK_BLOCKED",
    _SESSION_BLOCKED_FACT: "SESSION_BLOCKED",
    _ORDER_CHECK_BLOCKED_FACT: "PAPER_ORDER_CHECK_BLOCKED",
}

_DEGRADED_DETAIL = "a PAPER_LITE journal line could not be read; evidence may be incomplete"
_AWAITING_EVIDENCE_DETAIL = "the outcome was claimed but no further evidence has been recorded yet"


class _AssignmentStoreLike(Protocol):
    def current(self, assignment_id: UUID) -> Any: ...


class _IdentityStoreLike(Protocol):
    def current(self, agent_id: UUID) -> Any: ...


class _ContextBundleStoreLike(Protocol):
    def latest_for(self, assignment_id: UUID) -> Any: ...


class _OutcomeStoreLike(Protocol):
    def latest_outcome_id_for(self, assignment_id: UUID) -> UUID | None: ...
    def settlement_for(self, outcome_id: UUID, *, connection: Any = None) -> Any: ...


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
    """The decision this exact assignment's most recently claimed outcome

    resolves to. `platform_outcome` is one of `NO_TRADE`, `GATEWAY_REJECTED`,
    `SESSION_BLOCKED`, `RISK_BLOCKED`, `POLICY_BLOCKED`,
    `PAPER_ORDER_CHECK_BLOCKED`, `AWAITING_OUTCOME`, `AWAITING_EVIDENCE`,
    `DEGRADED` — see the module docstring for what each of the last three
    means and why `PAPER_FILLED` is not currently reachable here."""

    occurred_at_utc: UtcDatetime | None
    platform_outcome: str
    platform_outcome_detail: str | None
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
        # Risk PASS + Policy APPROVE with no outcome_id-bound fact to say
        # what happened at order-check/fill time -- see the module
        # docstring's "named, deliberate gap" note on PAPER_FILLED.
        outcome = "AWAITING_OUTCOME"

    return LastDecisionState(
        occurred_at_utc=capsule.occurred_at_utc,
        platform_outcome=outcome,
        platform_outcome_detail=None,
        proposal=_proposal_summary(capsule),
        risk_verdict=risk_verdict,
        risk_reason_codes=(tuple(code.value for code in risk.reason_codes) if risk else ()),
        policy_verdict=policy_verdict,
        supervisor_skipped=supervisor_skipped,
    )


def build_last_decision(
    *,
    outcome_store: _OutcomeStoreLike,
    capsule_store: CapsuleStore,
    assignment_id: UUID | None,
    journal_entries: tuple[dict[str, Any], ...],
    journal_had_corruption: bool,
) -> LastDecisionState | None:
    """`None` means no evidence exists anywhere yet for this assignment

    ("NO EVIDENCE"). Every other return is anchored to one concrete
    `outcome_id` this exact `assignment_id` actually claimed — see the
    module docstring for the full identity-binding discipline.
    """
    if journal_had_corruption:
        # Fail closed before attempting any correlation: an incomplete read
        # of the journal must not produce a confident answer, even one that
        # happens to not need the missing line.
        return LastDecisionState(
            occurred_at_utc=None,
            platform_outcome="DEGRADED",
            platform_outcome_detail=_DEGRADED_DETAIL,
            proposal=None,
            risk_verdict=None,
            risk_reason_codes=(),
            policy_verdict=None,
            supervisor_skipped=False,
        )

    if assignment_id is None:
        return None
    outcome_id = outcome_store.latest_outcome_id_for(assignment_id)
    if outcome_id is None:
        return None

    settlement = outcome_store.settlement_for(outcome_id)
    if settlement is not None and settlement.event_type is AgentDecisionEventType.REJECTED:
        return LastDecisionState(
            occurred_at_utc=settlement.occurred_at_utc,
            platform_outcome="GATEWAY_REJECTED",
            platform_outcome_detail=settlement.detail,
            proposal=None,
            risk_verdict=None,
            risk_reason_codes=tuple(settlement.reason_codes),
            policy_verdict=None,
            supervisor_skipped=False,
        )

    outcome_id_str = str(outcome_id)
    matching = tuple(
        entry
        for entry in journal_entries
        if entry.get("payload", {}).get("correlation_id") == outcome_id_str
    )
    supervisor_skipped = any(
        entry.get("event_type") == "AUDIT_FACT"
        and entry["payload"].get("fact") == _SUPERVISOR_SKIPPED_FACT
        for entry in matching
    )
    fact_entry = next(
        (
            entry
            for entry in matching
            if entry.get("event_type") == "AUDIT_FACT"
            and entry["payload"].get("fact") in _AUDIT_FACT_OUTCOME
        ),
        None,
    )
    if fact_entry is not None:
        fact = fact_entry["payload"]["fact"]
        return LastDecisionState(
            occurred_at_utc=None,
            platform_outcome=_AUDIT_FACT_OUTCOME[fact],
            platform_outcome_detail=fact_entry["payload"].get("detail"),
            proposal=None,
            risk_verdict=None,
            risk_reason_codes=(),
            policy_verdict=None,
            supervisor_skipped=supervisor_skipped,
        )

    capsule_id = uuid5(NAMESPACE_URL, f"crumblr:agent-capsule:{outcome_id}")
    capsule = capsule_store.get(capsule_id)
    if capsule is not None:
        return _from_capsule(capsule, supervisor_skipped=supervisor_skipped)

    return LastDecisionState(
        occurred_at_utc=(settlement.occurred_at_utc if settlement is not None else None),
        platform_outcome="AWAITING_EVIDENCE",
        platform_outcome_detail=_AWAITING_EVIDENCE_DETAIL,
        proposal=None,
        risk_verdict=None,
        risk_reason_codes=(),
        policy_verdict=None,
        supervisor_skipped=supervisor_skipped,
    )


_NEVER_HEALTHY_OUTCOMES = frozenset({"DEGRADED", "GATEWAY_REJECTED"})
"""Recent evidence of either of these must never read as `HEALTHY` — a

`DEGRADED` read means the evidence itself cannot be trusted, and a recent
`GATEWAY_REJECTED` means the Agent's own proposal/no-trade claim was
rejected before Risk/Policy ever saw it (stale context, unknown
assignment, rate limit, malformed evidence) — a real integration problem
with the Agent side specifically, unlike `RISK_BLOCKED`/`POLICY_BLOCKED`/
etc., which mean the platform's own downstream gates correctly evaluated
and vetoed a proposal — evidence the pipeline *is* working, not that it
isn't. Review feedback (third pass): the first version of this function
only checked recency, so a recent rejection could still read `HEALTHY`."""


def build_agent_health(
    *,
    agent_panel: AgentPanelState | None,
    last_decision: LastDecisionState | None,
    now: UtcDatetime,
    timeframe: str,
) -> AgentHealthState:
    if agent_panel is None or agent_panel.assignment_status != "ACTIVE":
        return "NOT PROVISIONED"
    if agent_panel.agent_status is None:
        # The assignment names an agent_id no AgentIdentity record exists
        # for -- a real inconsistency, not merely "no evidence yet".
        return "UNKNOWN"
    if agent_panel.agent_status != "ACTIVE":
        # SUSPENDED / RETIRED -- a known, deliberate non-active state, but
        # still reported as UNKNOWN rather than a bespoke label (review
        # feedback, third pass: "NOT PROVISIONED" reads as "nothing is
        # configured," which is false here — an assignment *is* active,
        # the agent itself is the one deliberately disabled). Never green
        # either way; the Agent panel's own `agent_status` field carries
        # the precise SUSPENDED/RETIRED distinction for anyone who needs it.
        return "UNKNOWN"
    if last_decision is not None and last_decision.platform_outcome in _NEVER_HEALTHY_OUTCOMES:
        return "UNKNOWN"
    if last_decision is None or last_decision.occurred_at_utc is None:
        return "WAITING"
    staleness_threshold = interval_for(timeframe) * 3
    if now - last_decision.occurred_at_utc > staleness_threshold:
        return "WAITING"
    return "HEALTHY"
