"""`dashboard.pipeline`: work order §16's 8-stage decision pipeline view.

Every test here checks a *presentation* claim only -- this module adds no
new evidence-gathering, so there is nothing to test about whether Risk or
Policy verdicts are correct, only whether the already-correct
`LastDecisionState`/`AgentPanelState` facts are restaged into the right
stage labels without ever overclaiming more precision than the evidence
actually supports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from crumblr.dashboard.agent_state import AgentPanelState, LastDecisionState
from crumblr.dashboard.pipeline import (
    NOT_APPLICABLE,
    NOT_REACHED,
    UNKNOWN,
    build_pipeline_view,
    pipeline_stage_class,
)

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _agent_panel(**overrides: object) -> AgentPanelState:
    fields: dict[str, object] = {
        "agent_id": uuid4(),
        "assignment_id": uuid4(),
        "assignment_status": "ACTIVE",
        "valid_from_utc": NOW,
        "valid_until_utc": NOW,
        "canonical_symbol": "EUR/USD",
        "timeframe": "M5",
        "strategy_artifact_id": uuid4(),
        "strategy_artifact_hash": "hash-v1",
        "runtime_version": "toy-v1",
        "agent_status": "ACTIVE",
        "latest_context_issued_at_utc": NOW,
        "latest_context_hash": "context-hash-abc",
    }
    fields.update(overrides)
    return AgentPanelState(**fields)  # type: ignore[arg-type]


def _last_decision(**overrides: object) -> LastDecisionState:
    fields: dict[str, object] = {
        "occurred_at_utc": NOW,
        "platform_outcome": "NO_TRADE",
        "platform_outcome_detail": None,
        "proposal": None,
        "risk_verdict": None,
        "risk_reason_codes": (),
        "policy_verdict": None,
        "supervisor_skipped": False,
    }
    fields.update(overrides)
    return LastDecisionState(**fields)  # type: ignore[arg-type]


def test_no_assignment_provisioned_is_unknown_everywhere() -> None:
    view = build_pipeline_view(agent_panel=None, last_decision=None)

    assert view.market == UNKNOWN
    assert view.context == UNKNOWN
    assert view.agent == UNKNOWN
    assert view.gateway == UNKNOWN
    assert view.risk == NOT_APPLICABLE
    assert view.policy == NOT_APPLICABLE
    assert view.supervisor == NOT_APPLICABLE
    assert view.paper == NOT_APPLICABLE


def test_an_active_assignment_with_no_claimed_outcome_yet_is_waiting() -> None:
    view = build_pipeline_view(agent_panel=_agent_panel(), last_decision=None)

    assert view.market == "OBSERVED"
    assert view.context == "ISSUED"
    assert view.agent == UNKNOWN
    assert view.gateway == UNKNOWN
    assert view.risk == NOT_APPLICABLE


def test_no_trade_is_a_clean_terminal_row_not_reached_downstream() -> None:
    view = build_pipeline_view(
        agent_panel=_agent_panel(), last_decision=_last_decision(platform_outcome="NO_TRADE")
    )

    assert view.market == "OBSERVED"
    assert view.context == "ISSUED"
    assert view.agent == "NO_TRADE"
    assert view.gateway == "ACCEPTED"
    assert view.risk == NOT_APPLICABLE
    assert view.policy == NOT_APPLICABLE
    assert view.supervisor == NOT_APPLICABLE
    assert view.paper == NOT_APPLICABLE


def test_gateway_rejected_stops_every_later_stage() -> None:
    view = build_pipeline_view(
        agent_panel=_agent_panel(),
        last_decision=_last_decision(platform_outcome="GATEWAY_REJECTED"),
    )

    assert view.agent == "RESPONSE RECEIVED"
    assert view.gateway == "REJECTED"
    assert view.risk == NOT_REACHED
    assert view.policy == NOT_REACHED
    assert view.supervisor == NOT_REACHED
    assert view.paper == NOT_REACHED


def test_awaiting_evidence_is_honestly_ambiguous_not_a_guess() -> None:
    view = build_pipeline_view(
        agent_panel=_agent_panel(),
        last_decision=_last_decision(platform_outcome="AWAITING_EVIDENCE"),
    )

    assert view.agent == "CLAIMED"
    assert view.gateway == "CLAIMED"
    assert view.risk == NOT_APPLICABLE


def test_degraded_journal_reads_as_unknown_everywhere_not_a_confident_answer() -> None:
    view = build_pipeline_view(
        agent_panel=_agent_panel(), last_decision=_last_decision(platform_outcome="DEGRADED")
    )

    assert view.market == UNKNOWN
    assert view.context == UNKNOWN
    assert view.agent == UNKNOWN
    assert view.gateway == UNKNOWN


def test_session_blocked_is_a_paper_lite_pre_core_audit_fact_never_reaching_risk() -> None:
    view = build_pipeline_view(
        agent_panel=_agent_panel(), last_decision=_last_decision(platform_outcome="SESSION_BLOCKED")
    )

    assert view.agent == "RESPONSE RECEIVED"
    assert view.gateway == "ACCEPTED"
    assert view.risk == NOT_REACHED
    assert view.policy == NOT_REACHED
    assert view.supervisor == NOT_REACHED
    assert view.paper == NOT_REACHED


def test_paper_order_check_blocked_places_the_block_at_the_paper_stage() -> None:
    view = build_pipeline_view(
        agent_panel=_agent_panel(),
        last_decision=_last_decision(platform_outcome="PAPER_ORDER_CHECK_BLOCKED"),
    )

    assert view.risk == NOT_REACHED
    assert view.policy == NOT_REACHED
    assert view.supervisor == NOT_REACHED
    assert view.paper == "PAPER_ORDER_CHECK_BLOCKED"


class TestRiskBlockedHasTwoDistinctOrigins:
    """RISK_BLOCKED can come from a real capsule (Core Risk genuinely
    evaluated and blocked) or from PAPER_LITE's own safety-halt audit fact
    (Core Risk never ran) -- `risk_verdict` is the only signal telling
    them apart, and this module must never conflate the two."""

    def test_a_real_capsule_verdict_shows_risk_as_reached_and_blocking(self) -> None:
        view = build_pipeline_view(
            agent_panel=_agent_panel(),
            last_decision=_last_decision(platform_outcome="RISK_BLOCKED", risk_verdict="BLOCK"),
        )

        assert view.agent == "TRADE_PROPOSAL"
        assert view.gateway == "ACCEPTED"
        assert view.risk == "BLOCK"
        assert view.policy == NOT_REACHED

    def test_a_safety_halt_audit_fact_with_no_verdict_never_claims_risk_was_reached(self) -> None:
        view = build_pipeline_view(
            agent_panel=_agent_panel(),
            last_decision=_last_decision(platform_outcome="RISK_BLOCKED", risk_verdict=None),
        )

        assert view.agent == "RESPONSE RECEIVED"
        assert view.risk == NOT_REACHED, "Core Risk never actually ran for a pre-Core safety halt"


def test_policy_blocked_shows_risk_pass_and_policy_block() -> None:
    view = build_pipeline_view(
        agent_panel=_agent_panel(),
        last_decision=_last_decision(platform_outcome="POLICY_BLOCKED", risk_verdict="PASS"),
    )

    assert view.agent == "TRADE_PROPOSAL"
    assert view.gateway == "ACCEPTED"
    assert view.risk == "PASS"
    assert view.policy == "BLOCK"
    assert view.supervisor == NOT_REACHED
    assert view.paper == NOT_REACHED


class TestAwaitingOutcomeCarriesTheSupervisorSkipFlag:
    def test_supervisor_skipped_is_shown_explicitly(self) -> None:
        view = build_pipeline_view(
            agent_panel=_agent_panel(),
            last_decision=_last_decision(
                platform_outcome="AWAITING_OUTCOME",
                risk_verdict="PASS",
                policy_verdict="APPROVE",
                supervisor_skipped=True,
            ),
        )

        assert view.risk == "PASS"
        assert view.policy == "APPROVE"
        assert view.supervisor == "SKIPPED_PAPER_MODE"
        assert view.paper == "AWAITING_OUTCOME"

    def test_supervisor_not_skipped_and_not_otherwise_evaluated_is_not_reached(self) -> None:
        view = build_pipeline_view(
            agent_panel=_agent_panel(),
            last_decision=_last_decision(
                platform_outcome="AWAITING_OUTCOME",
                risk_verdict="PASS",
                policy_verdict="APPROVE",
                supervisor_skipped=False,
            ),
        )

        assert view.supervisor == NOT_REACHED


def test_an_unrecognized_future_outcome_string_fails_closed_to_unknown() -> None:
    view = build_pipeline_view(
        agent_panel=_agent_panel(),
        last_decision=_last_decision(platform_outcome="SOME_FUTURE_OUTCOME_NOBODY_HAS_MAPPED_YET"),
    )

    assert view.agent == UNKNOWN
    assert view.gateway == UNKNOWN
    assert view.risk == NOT_APPLICABLE


def test_no_context_ever_issued_reads_context_as_unknown_not_a_guess() -> None:
    view = build_pipeline_view(
        agent_panel=_agent_panel(latest_context_issued_at_utc=None, latest_context_hash=None),
        last_decision=None,
    )

    assert view.market == UNKNOWN
    assert view.context == UNKNOWN


class TestPipelineStageClass:
    """`pipeline_stage_class` must classify every value `build_pipeline_view`
    can ever emit -- a distinct vocabulary from `app.py::state_class`'s
    three sets, so this checks the module's own explicit mapping directly
    rather than assuming it lines up with the unrelated one."""

    def test_good_stages(self) -> None:
        for value in ("OBSERVED", "ISSUED", "ACCEPTED", "TRADE_PROPOSAL", "PASS", "APPROVE"):
            assert pipeline_stage_class(value) == "good", value

    def test_warn_stages(self) -> None:
        for value in ("AWAITING_OUTCOME", "SKIPPED_PAPER_MODE", "CLAIMED"):
            assert pipeline_stage_class(value) == "warn", value

    def test_bad_stages(self) -> None:
        for value in ("REJECTED", "BLOCK", "PAPER_ORDER_CHECK_BLOCKED", "UNKNOWN"):
            assert pipeline_stage_class(value) == "bad", value

    def test_neutral_stages_include_response_received_despite_downstream_blocks(self) -> None:
        # RESPONSE RECEIVED must never read as bad just because several of
        # the outcomes that produce it happen to end in a real block
        # further downstream -- that block is already shown bad at its own
        # stage; this label only claims "something happened".
        for value in ("NO_TRADE", NOT_REACHED, NOT_APPLICABLE, "RESPONSE RECEIVED"):
            assert pipeline_stage_class(value) == "neutral", value

    def test_an_unrecognized_future_value_falls_back_to_neutral_not_bad(self) -> None:
        assert (
            pipeline_stage_class("SOME_FUTURE_STAGE_VALUE_NOBODY_HAS_CLASSIFIED_YET") == "neutral"
        )

    def test_case_insensitive_and_none_safe(self) -> None:
        assert pipeline_stage_class("accepted") == "good"
        assert pipeline_stage_class(None) == "neutral"
