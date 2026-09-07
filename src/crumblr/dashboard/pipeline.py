"""Decision pipeline restaging (work order §16, Slice 5).

`agent_state.py::LastDecisionState` already gathers every fact this needs,
correctly bound to one concrete `outcome_id` (see that module's own
docstring for the identity discipline). This module adds no new evidence
reads, no new Risk/Policy computation, and no new identity correlation --
it only *presents* that same, already-correct evidence as 8 separate named
stages (MARKET -> CONTEXT -> AGENT -> GATEWAY -> CORE RISK -> PLATFORM
POLICY -> SUPERVISOR -> PAPER RESULT) instead of one collapsed
`platform_outcome` string, per the work order's own stage model.

Two PAPER_LITE audit-fact outcomes (`SESSION_BLOCKED`,
`PAPER_ORDER_CHECK_BLOCKED`) are pre-Core checks: PAPER_LITE's own session
policy / final order-check refuse *before* (or independently of) asking
Core Risk/Policy at all, and the audit fact itself does not record enough
to say precisely which stage in between ran -- `LastDecisionState.proposal`
is `None` for every audit-fact-derived outcome, not only these two, so this
module does not claim TRADE_PROPOSAL vs NO_TRADE for any of them; "RESPONSE
RECEIVED" is the same honest, non-overclaiming label already used for
`GATEWAY_REJECTED`. `RISK_BLOCKED` can come from *either* a real capsule
(`risk_verdict` set) or PAPER_LITE's own safety-halt audit fact
(`risk_verdict is None`) -- distinguished explicitly rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.dashboard.agent_state import AgentPanelState, LastDecisionState

NOT_APPLICABLE = "N/A"
NOT_REACHED = "NOT REACHED"
UNKNOWN = "UNKNOWN"

_RESPONSE_RECEIVED = "RESPONSE RECEIVED"

# Every stage value build_pipeline_view can ever emit, classified for the
# stage-verdict badge -- a *different* vocabulary from app.py::state_class's
# three sets (this module's own "REJECTED"/"BLOCK"/"ACCEPTED" etc. never
# appear there), so it gets its own explicit mapping rather than reusing
# state_class and hoping the fallback lands somewhere sane.
#
# RESPONSE_RECEIVED is deliberately NEUTRAL, not BAD: it is the same
# non-overclaiming "something happened, precise stage unknown" label used
# for several different pre-Core audit-fact shapes, several of which do end
# in a real block further downstream (already shown BAD at that later
# stage) -- coloring the Agent stage bad too would overclaim precision this
# label was chosen specifically to avoid. CLAIMED is WARN, not GOOD, per
# AWAITING_EVIDENCE's own "honestly ambiguous, not a guess" contract.
PIPELINE_GOOD_STAGES = frozenset(
    {"OBSERVED", "ISSUED", "ACCEPTED", "TRADE_PROPOSAL", "PASS", "APPROVE"}
)
PIPELINE_WARN_STAGES = frozenset({"AWAITING_OUTCOME", "SKIPPED_PAPER_MODE", "CLAIMED"})
PIPELINE_BAD_STAGES = frozenset({"REJECTED", "BLOCK", "PAPER_ORDER_CHECK_BLOCKED", UNKNOWN})


def pipeline_stage_class(value: str | None) -> str:
    """`good`/`warn`/`bad`/`neutral` for one pipeline stage's badge.

    Unmapped values fall back to `neutral`, the same conservative-but-not-
    alarming default `app.py::state_class` uses -- there is no stage value
    this module can emit that isn't one of the three sets above or an
    explicitly neutral one (`NO_TRADE`, `NOT REACHED`, `N/A`,
    `RESPONSE RECEIVED`), so the fallback only exists for a future outcome
    this module has not been taught yet.
    """
    upper = (value or "").upper()
    if upper in PIPELINE_GOOD_STAGES:
        return "good"
    if upper in PIPELINE_WARN_STAGES:
        return "warn"
    if upper in PIPELINE_BAD_STAGES:
        return "bad"
    return "neutral"


# outcome -> (agent, gateway, risk, policy, supervisor, paper), for every
# platform_outcome value that does not need extra branching beyond the
# outcome string itself (see build_pipeline_view for RISK_BLOCKED, which
# has two different real origins, and POLICY_BLOCKED/AWAITING_OUTCOME,
# which read the actual supervisor_skipped flag).
_FIXED_STAGE_TABLE: dict[str, tuple[str, str, str, str, str, str]] = {
    "NO_TRADE": (
        "NO_TRADE",
        "ACCEPTED",
        NOT_APPLICABLE,
        NOT_APPLICABLE,
        NOT_APPLICABLE,
        NOT_APPLICABLE,
    ),
    "GATEWAY_REJECTED": (
        _RESPONSE_RECEIVED,
        "REJECTED",
        NOT_REACHED,
        NOT_REACHED,
        NOT_REACHED,
        NOT_REACHED,
    ),
    "SESSION_BLOCKED": (
        _RESPONSE_RECEIVED,
        "ACCEPTED",
        NOT_REACHED,
        NOT_REACHED,
        NOT_REACHED,
        NOT_REACHED,
    ),
    "PAPER_ORDER_CHECK_BLOCKED": (
        _RESPONSE_RECEIVED,
        "ACCEPTED",
        NOT_REACHED,
        NOT_REACHED,
        NOT_REACHED,
        "PAPER_ORDER_CHECK_BLOCKED",
    ),
    "AWAITING_EVIDENCE": (
        "CLAIMED",
        "CLAIMED",
        NOT_APPLICABLE,
        NOT_APPLICABLE,
        NOT_APPLICABLE,
        NOT_APPLICABLE,
    ),
    # "DEGRADED" is handled as an early special case in build_pipeline_view
    # (below) -- it must also blank out market/context, which this table
    # alone cannot express since it only supplies the agent-onward fields.
}


@dataclass(frozen=True)
class PipelineView:
    """One row per work order §16 stage. Every field is a display string,
    not a verdict this module computed -- each is read straight off
    `LastDecisionState`'s already-authoritative fields."""

    market: str
    context: str
    agent: str
    gateway: str
    risk: str
    policy: str
    supervisor: str
    paper: str


def _no_evidence_view() -> PipelineView:
    return PipelineView(
        market=UNKNOWN,
        context=UNKNOWN,
        agent=UNKNOWN,
        gateway=UNKNOWN,
        risk=NOT_APPLICABLE,
        policy=NOT_APPLICABLE,
        supervisor=NOT_APPLICABLE,
        paper=NOT_APPLICABLE,
    )


def build_pipeline_view(
    *, agent_panel: AgentPanelState | None, last_decision: LastDecisionState | None
) -> PipelineView:
    """`agent_panel is None` means no assignment is provisioned at all --
    every stage reads UNKNOWN/N/A, the same "NOT PROVISIONED" honesty the
    Agent health card already applies. `last_decision is None` means the
    assignment exists but has never claimed an outcome yet -- MARKET/
    CONTEXT can still say what they know from the assignment's own context
    history; AGENT onward is UNKNOWN because nothing has been claimed."""
    if agent_panel is None:
        return _no_evidence_view()

    if last_decision is not None and last_decision.platform_outcome == "DEGRADED":
        # A corrupted PAPER_LITE journal line means nothing downstream of
        # it can be trusted either -- including whatever market/context
        # facts this function would otherwise report from agent_panel.
        # Matches agent_state.py's own rule: "an incomplete read must not
        # produce a confident answer, even one that happens to not need
        # the missing line."
        return _no_evidence_view()

    market = "OBSERVED" if agent_panel.latest_context_issued_at_utc is not None else UNKNOWN
    context = "ISSUED" if agent_panel.latest_context_hash is not None else UNKNOWN

    if last_decision is None:
        return PipelineView(
            market=market,
            context=context,
            agent=UNKNOWN,
            gateway=UNKNOWN,
            risk=NOT_APPLICABLE,
            policy=NOT_APPLICABLE,
            supervisor=NOT_APPLICABLE,
            paper=NOT_APPLICABLE,
        )

    outcome = last_decision.platform_outcome

    if outcome == "RISK_BLOCKED":
        if last_decision.risk_verdict is None:
            # PAPER_LITE's own safety-halt audit fact -- Core Risk never
            # ran at all, the same shape as SESSION_BLOCKED.
            agent, gateway, risk, policy, supervisor, paper = (
                _RESPONSE_RECEIVED,
                "ACCEPTED",
                NOT_REACHED,
                NOT_REACHED,
                NOT_REACHED,
                NOT_REACHED,
            )
        else:
            agent, gateway, risk, policy, supervisor, paper = (
                "TRADE_PROPOSAL",
                "ACCEPTED",
                "BLOCK",
                NOT_REACHED,
                NOT_REACHED,
                NOT_REACHED,
            )
        return PipelineView(
            market=market,
            context=context,
            agent=agent,
            gateway=gateway,
            risk=risk,
            policy=policy,
            supervisor=supervisor,
            paper=paper,
        )

    if outcome == "POLICY_BLOCKED":
        return PipelineView(
            market=market,
            context=context,
            agent="TRADE_PROPOSAL",
            gateway="ACCEPTED",
            risk="PASS",
            policy="BLOCK",
            supervisor=NOT_REACHED,
            paper=NOT_REACHED,
        )

    if outcome == "AWAITING_OUTCOME":
        supervisor = "SKIPPED_PAPER_MODE" if last_decision.supervisor_skipped else NOT_REACHED
        return PipelineView(
            market=market,
            context=context,
            agent="TRADE_PROPOSAL",
            gateway="ACCEPTED",
            risk="PASS",
            policy="APPROVE",
            supervisor=supervisor,
            paper="AWAITING_OUTCOME",
        )

    fixed = _FIXED_STAGE_TABLE.get(outcome)
    if fixed is None:
        # An outcome string this module does not recognize yet -- fail
        # closed to UNKNOWN rather than guess a shape for it.
        return PipelineView(
            market=market,
            context=context,
            agent=UNKNOWN,
            gateway=UNKNOWN,
            risk=NOT_APPLICABLE,
            policy=NOT_APPLICABLE,
            supervisor=NOT_APPLICABLE,
            paper=NOT_APPLICABLE,
        )
    agent, gateway, risk, policy, supervisor, paper = fixed
    return PipelineView(
        market=market,
        context=context,
        agent=agent,
        gateway=gateway,
        risk=risk,
        policy=policy,
        supervisor=supervisor,
        paper=paper,
    )
