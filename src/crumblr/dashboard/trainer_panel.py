"""Read-only Trainer/candidate-research state for the dashboard (FULL RUN 1).

This module never calls Trainer, never calls the Static Agent, and never
opens an MT5 connection — it only reads three JSON snapshot files, each
written by its own separate, explicitly-run, read-only status process:

- `scripts/check_trainer_status.py` -> Trainer reachability + campaign state
- `scripts/collect_crumblr_trader_dataset.py --json` -> closed-trade dataset
  discovered/eligible/excluded counts
- `scripts/fetch_trainer_candidate.py --status-json` -> candidate
  availability/provenance
- crumblr-static-agent-host's `scripts/verify_trainer_candidate.py --out`
  (pointed directly at a path under this repo's `var/`, since both repos
  run on the same host) -> Static candidate verification PASS/FAIL

Any file that is missing, unreadable, or stale simply renders as
`UNKNOWN`/`NOT YET CHECKED` — never a fabricated PASS, never a crash. The
`candidate_active`/`candidate_active_banner` fields are structural
constants: this module has no path by which a candidate could ever read
as active, because Trainer candidate activation is not something this
codebase does anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from crumblr.dashboard.reader_health import read_health_snapshot

CANDIDATE_NOT_ACTIVE_BANNER = "CANDIDATE NOT ACTIVE — HUMAN PROMOTION REQUIRED"

TrainerReachability = Literal["REACHABLE", "UNREACHABLE", "UNKNOWN"]
CampaignStatus = Literal["FOUND", "NOT_FOUND", "UNKNOWN"]
CandidateAvailability = Literal["AVAILABLE", "NONE", "UNKNOWN"]
VerificationResult = Literal["PASS", "FAIL", "NOT_RUN"]


@dataclass(frozen=True)
class TrainerCampaignPanelState:
    reachability: TrainerReachability
    checked_at_utc: str | None
    trainer_base_url: str | None
    campaign_id: str | None
    campaign_status: CampaignStatus
    campaign_mode: str | None
    campaign_strategy_key: str | None
    run_id: str | None


@dataclass(frozen=True)
class ClosedTradeDatasetPanelState:
    checked_at_utc: str | None
    campaign_id: str | None
    discovered_count: int | None
    eligible_count: int | None
    excluded_count: int | None
    excluded_reasons: tuple[str, ...]
    posted_to_trainer: bool | None
    trainer_http_status: int | None
    run_id: str | None


@dataclass(frozen=True)
class TrainerCandidatePanelState:
    checked_at_utc: str | None
    campaign_id: str | None
    availability: CandidateAvailability
    http_status: int | None
    detail: str | None
    candidate_strategy_spec_hash: str | None
    parent_strategy_key: str | None
    parent_source_hash: str | None
    research_status: str | None
    run_id: str | None


@dataclass(frozen=True)
class StaticVerificationPanelState:
    result: VerificationResult
    candidate_hash: str | None
    evaluation_outcome_kind: str | None
    base_code_hash_verified: bool | None
    candidate_hash_recomputed_matches: bool | None
    trainer_artifact_hash_verified: bool | None
    candidate_strategy_spec_hash_verified: bool | None
    parent_compatibility_verified: bool | None
    unit_mapping_verified: bool | None


@dataclass(frozen=True)
class TrainerPanelState:
    """Everything the "Trainer & Candidate Research" card renders.

    `candidate_active` is always `False` and `candidate_active_banner` is
    always `CANDIDATE_NOT_ACTIVE_BANNER` — not derived from any file this
    module reads, because no file this module reads could ever make a
    candidate active. Shown unconditionally, independent of every other
    field's state.
    """

    campaign: TrainerCampaignPanelState
    dataset: ClosedTradeDatasetPanelState
    candidate: TrainerCandidatePanelState
    verification: StaticVerificationPanelState
    candidate_active: bool
    candidate_active_banner: str


def _campaign_panel(snapshot: dict[str, Any] | None) -> TrainerCampaignPanelState:
    if snapshot is None:
        return TrainerCampaignPanelState(
            reachability="UNKNOWN",
            checked_at_utc=None,
            trainer_base_url=None,
            campaign_id=None,
            campaign_status="UNKNOWN",
            campaign_mode=None,
            campaign_strategy_key=None,
            run_id=None,
        )
    reachability_block = snapshot.get("reachability") or {}
    campaign_block = snapshot.get("campaign") or {}
    reachable = reachability_block.get("reachable")
    reachability: TrainerReachability = (
        "REACHABLE" if reachable is True else "UNREACHABLE" if reachable is False else "UNKNOWN"
    )
    found = campaign_block.get("found")
    campaign_status: CampaignStatus = (
        "FOUND" if found is True else "NOT_FOUND" if found is False else "UNKNOWN"
    )
    campaign_body = campaign_block.get("campaign") or {}
    return TrainerCampaignPanelState(
        reachability=reachability,
        checked_at_utc=snapshot.get("checked_at_utc"),
        trainer_base_url=snapshot.get("trainer_base_url"),
        campaign_id=snapshot.get("campaign_id"),
        campaign_status=campaign_status,
        campaign_mode=campaign_body.get("mode"),
        campaign_strategy_key=campaign_body.get("strategy_key"),
        run_id=snapshot.get("run_id"),
    )


def _dataset_panel(snapshot: dict[str, Any] | None) -> ClosedTradeDatasetPanelState:
    if snapshot is None:
        return ClosedTradeDatasetPanelState(
            checked_at_utc=None,
            campaign_id=None,
            discovered_count=None,
            eligible_count=None,
            excluded_count=None,
            excluded_reasons=(),
            posted_to_trainer=None,
            trainer_http_status=None,
            run_id=None,
        )
    reasons = tuple(
        f"{item.get('outcome_id')}: {item.get('reason')}"
        for item in snapshot.get("excluded_reasons") or []
    )
    return ClosedTradeDatasetPanelState(
        checked_at_utc=snapshot.get("checked_at_utc"),
        campaign_id=snapshot.get("campaign_id"),
        discovered_count=snapshot.get("discovered_count"),
        eligible_count=snapshot.get("eligible_count"),
        excluded_count=snapshot.get("excluded_count"),
        excluded_reasons=reasons,
        posted_to_trainer=snapshot.get("posted_to_trainer"),
        trainer_http_status=snapshot.get("trainer_http_status"),
        run_id=snapshot.get("run_id"),
    )


def _candidate_panel(snapshot: dict[str, Any] | None) -> TrainerCandidatePanelState:
    if snapshot is None:
        return TrainerCandidatePanelState(
            checked_at_utc=None,
            campaign_id=None,
            availability="UNKNOWN",
            http_status=None,
            detail=None,
            candidate_strategy_spec_hash=None,
            parent_strategy_key=None,
            parent_source_hash=None,
            research_status=None,
            run_id=None,
        )
    available = snapshot.get("available")
    availability: CandidateAvailability = (
        "AVAILABLE" if available is True else "NONE" if available is False else "UNKNOWN"
    )
    detail = snapshot.get("detail")
    return TrainerCandidatePanelState(
        checked_at_utc=snapshot.get("checked_at_utc"),
        campaign_id=snapshot.get("campaign_id"),
        availability=availability,
        http_status=snapshot.get("http_status"),
        detail=str(detail) if detail is not None else None,
        candidate_strategy_spec_hash=snapshot.get("candidate_strategy_spec_hash"),
        parent_strategy_key=snapshot.get("parent_strategy_key"),
        parent_source_hash=snapshot.get("parent_source_hash"),
        research_status=snapshot.get("research_status"),
        run_id=snapshot.get("run_id"),
    )


def _verification_panel(snapshot: dict[str, Any] | None) -> StaticVerificationPanelState:
    if snapshot is None:
        return StaticVerificationPanelState(
            result="NOT_RUN",
            candidate_hash=None,
            evaluation_outcome_kind=None,
            base_code_hash_verified=None,
            candidate_hash_recomputed_matches=None,
            trainer_artifact_hash_verified=None,
            candidate_strategy_spec_hash_verified=None,
            parent_compatibility_verified=None,
            unit_mapping_verified=None,
        )
    checks = (
        snapshot.get("base_code_hash_verified"),
        snapshot.get("candidate_hash_recomputed_matches"),
        snapshot.get("trainer_artifact_hash_verified"),
        snapshot.get("candidate_strategy_spec_hash_verified"),
        snapshot.get("parent_compatibility_verified"),
        snapshot.get("unit_mapping_verified"),
    )
    # PASS requires every recorded proof to be explicitly True -- a missing
    # or False value (an older-format record, a partial write) must never
    # read as PASS. NOT_RUN is reserved for "no record at all" above.
    result: VerificationResult = "PASS" if all(check is True for check in checks) else "FAIL"
    return StaticVerificationPanelState(
        result=result,
        candidate_hash=snapshot.get("candidate_hash"),
        evaluation_outcome_kind=snapshot.get("evaluation_outcome_kind"),
        base_code_hash_verified=snapshot.get("base_code_hash_verified"),
        candidate_hash_recomputed_matches=snapshot.get("candidate_hash_recomputed_matches"),
        trainer_artifact_hash_verified=snapshot.get("trainer_artifact_hash_verified"),
        candidate_strategy_spec_hash_verified=snapshot.get("candidate_strategy_spec_hash_verified"),
        parent_compatibility_verified=snapshot.get("parent_compatibility_verified"),
        unit_mapping_verified=snapshot.get("unit_mapping_verified"),
    )


def build_trainer_panel(
    *,
    trainer_status_path: Path | None,
    dataset_status_path: Path | None,
    candidate_status_path: Path | None,
    verification_record_path: Path | None,
) -> TrainerPanelState:
    """Read all four snapshot files (any/all may be `None` or missing) and
    reduce them to one display shape. Every path is independent: a missing
    verification record does not affect the campaign/dataset/candidate
    panels, and vice versa."""
    campaign_snapshot = (
        read_health_snapshot(trainer_status_path) if trainer_status_path is not None else None
    )
    dataset_snapshot = (
        read_health_snapshot(dataset_status_path) if dataset_status_path is not None else None
    )
    candidate_snapshot = (
        read_health_snapshot(candidate_status_path) if candidate_status_path is not None else None
    )
    verification_snapshot = (
        read_health_snapshot(verification_record_path)
        if verification_record_path is not None
        else None
    )
    return TrainerPanelState(
        campaign=_campaign_panel(campaign_snapshot),
        dataset=_dataset_panel(dataset_snapshot),
        candidate=_candidate_panel(candidate_snapshot),
        verification=_verification_panel(verification_snapshot),
        candidate_active=False,
        candidate_active_banner=CANDIDATE_NOT_ACTIVE_BANNER,
    )
