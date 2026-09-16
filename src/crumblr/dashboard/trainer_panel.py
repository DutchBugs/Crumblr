"""Read-only Trainer/candidate-research state for the dashboard (FULL RUN 1).

This module never calls Trainer, never calls the Static Agent, and never
opens an MT5 connection — it only reads four JSON snapshot files, each
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

**Snapshot coherence (Dev 1 BLOCK, fixed here).** Three independent
findings from the first observability pass, all closed in this revision:

1. A readable-but-old `trainer_status.json` could render `REACHABLE`
   forever after the check-in process stopped running — the same "last
   thing the writer happened to say" trap `dashboard/state.py::_connectivity`
   already had to solve for the MT5 reader. `_campaign_panel` now takes an
   explicit `now`/`max_age` and downgrades a stale `REACHABLE` to `STALE`
   -- never silently kept.
2. `trainer_status.json`/`closed_trade_dataset_state.json`/
   `candidate_status.json` each carry their own `run_id`, but nothing
   compared them -- three panels from three different runs could be shown
   side by side as though they described one coherent FULL RUN.
   `_run_coherence` compares every non-null `run_id` across the three and
   sets `TrainerPanelState.run_coherence` to `INCOHERENT` (with the
   disagreeing ids named in `run_coherence_detail`) the moment two of them
   disagree -- `UNKNOWN` only when none are tagged at all, `COHERENT`
   otherwise. This is a visibility flag; it does not silently merge or
   hide the individual per-file values.
3. Static verification `PASS` was computed from six booleans in the
   verification record alone, with no check that record actually
   describes the candidate currently shown. `_verification_panel` now
   requires all three of the verification record's own immutable
   `lineage` identity fields -- `candidate_strategy_spec_hash`,
   `parent_strategy_key`, `parent_source_hash` (all already emitted by
   the Static Agent's `candidate_verifier.py`, no new envelope field
   needed) -- to equal the currently displayed candidate's own values
   before `PASS` is possible at all. The spec hash alone is not a
   sufficient identity: the same candidate spec can exist under a
   different frozen parent. A record for a different (or no longer
   current) candidate identity reads `STALE`, never `PASS`; a record that
   binds correctly but fails a proof reads `FAIL`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from crumblr.dashboard.reader_health import read_health_snapshot
from crumblr.domain.timeutils import UtcDatetime, utc_now

CANDIDATE_NOT_ACTIVE_BANNER = "CANDIDATE NOT ACTIVE — HUMAN PROMOTION REQUIRED"

DEFAULT_TRAINER_STATUS_MAX_AGE = timedelta(minutes=15)
"""How old `trainer_status.json`'s own `checked_at_utc` may be before a

`REACHABLE` reading is downgraded to `STALE` rather than trusted at face
value. Independent of `check_trainer_status.py`'s own run cadence (that
script is expected to be re-run every 5-15 minutes during a FULL RUN
observation window) -- this is the dashboard's own safety ceiling, the
same relationship `reconciliation.DEFAULT_MAX_SNAPSHOT_AGE` has to
`LiveReader`'s capture interval."""

TrainerReachability = Literal["REACHABLE", "UNREACHABLE", "STALE", "UNKNOWN"]
CampaignStatus = Literal["FOUND", "NOT_FOUND", "UNKNOWN"]
CandidateAvailability = Literal["AVAILABLE", "NONE", "UNKNOWN"]
VerificationResult = Literal["PASS", "FAIL", "STALE", "NOT_RUN"]
RunCoherence = Literal["COHERENT", "INCOHERENT", "UNKNOWN"]


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
    verified_candidate_strategy_spec_hash: str | None
    verified_parent_strategy_key: str | None
    verified_parent_source_hash: str | None
    """The full candidate identity the verification record itself names

    (`lineage.candidate_strategy_spec_hash`/`parent_strategy_key`/
    `parent_source_hash`) -- shown so a viewer can see directly why a
    `STALE` result does not match the candidate panel's own identity,
    rather than only being told it doesn't. All three are required to
    match: the same candidate spec hash can exist under a different
    frozen parent, so the spec hash alone is not a sufficient identity."""


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
    run_coherence: RunCoherence
    run_coherence_detail: str | None
    candidate_active: bool
    candidate_active_banner: str


def _parse_utc(raw: str | None) -> datetime | None:
    """Fails closed on anything that is not a genuinely timezone-aware

    timestamp -- a syntactically valid but naive `datetime.fromisoformat()`
    result is rejected here too, not merely left for a later `now -
    parsed` subtraction to raise `TypeError` (a naive/aware subtraction is
    always an error, never a comparison Python can perform). `now` is
    always timezone-aware, so returning a naive `parsed` here would crash
    `_is_stale` instead of reporting an honest stale reading.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed


def _is_stale(checked_at_utc: str | None, *, now: UtcDatetime, max_age: timedelta) -> bool:
    """Fails closed: a missing or unparsable timestamp counts as stale --

    the same "an incomplete read must not produce a confident answer" rule
    `dashboard/state.py::_heartbeat_expired` already applies to the MT5
    reader's own liveness evidence.
    """
    parsed = _parse_utc(checked_at_utc)
    if parsed is None:
        return True
    return (now - parsed) > max_age


def _campaign_panel(
    snapshot: dict[str, Any] | None, *, now: UtcDatetime, max_age: timedelta
) -> TrainerCampaignPanelState:
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
    checked_at_utc = snapshot.get("checked_at_utc")
    reachability_block = snapshot.get("reachability") or {}
    campaign_block = snapshot.get("campaign") or {}
    reachable = reachability_block.get("reachable")
    reachability: TrainerReachability = (
        "REACHABLE" if reachable is True else "UNREACHABLE" if reachable is False else "UNKNOWN"
    )
    # A stale prior success must not remain REACHABLE -- only the confident
    # "success" reading is downgraded; UNREACHABLE/UNKNOWN are already
    # non-confident and gain nothing from being further re-labeled STALE.
    if reachability == "REACHABLE" and _is_stale(checked_at_utc, now=now, max_age=max_age):
        reachability = "STALE"
    found = campaign_block.get("found")
    campaign_status: CampaignStatus = (
        "FOUND" if found is True else "NOT_FOUND" if found is False else "UNKNOWN"
    )
    campaign_body = campaign_block.get("campaign") or {}
    return TrainerCampaignPanelState(
        reachability=reachability,
        checked_at_utc=checked_at_utc,
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


def _verification_panel(
    snapshot: dict[str, Any] | None,
    *,
    current_candidate_strategy_spec_hash: str | None,
    current_parent_strategy_key: str | None,
    current_parent_source_hash: str | None,
) -> StaticVerificationPanelState:
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
            verified_candidate_strategy_spec_hash=None,
            verified_parent_strategy_key=None,
            verified_parent_source_hash=None,
        )
    checks = (
        snapshot.get("base_code_hash_verified"),
        snapshot.get("candidate_hash_recomputed_matches"),
        snapshot.get("trainer_artifact_hash_verified"),
        snapshot.get("candidate_strategy_spec_hash_verified"),
        snapshot.get("parent_compatibility_verified"),
        snapshot.get("unit_mapping_verified"),
    )
    all_proofs_true = all(check is True for check in checks)

    # The verification record's own lineage names exactly which candidate
    # it verified (candidate_verifier.py already emits this -- no new
    # envelope field needed). PASS requires ALL THREE immutable identity
    # fields to equal the currently displayed candidate's own: the same
    # candidate_strategy_spec_hash can exist under a different frozen
    # parent (parent_strategy_key/parent_source_hash), so the spec hash
    # alone is not a sufficient identity match. A record whose proofs all
    # passed for a *different* (or no longer current) candidate identity
    # must never read as PASS for the one shown now.
    lineage = snapshot.get("lineage") or {}
    verified_hash = lineage.get("candidate_strategy_spec_hash")
    verified_parent_strategy_key = lineage.get("parent_strategy_key")
    verified_parent_source_hash = lineage.get("parent_source_hash")
    binds_to_current_candidate = (
        verified_hash is not None
        and current_candidate_strategy_spec_hash is not None
        and verified_hash == current_candidate_strategy_spec_hash
        and verified_parent_strategy_key is not None
        and current_parent_strategy_key is not None
        and verified_parent_strategy_key == current_parent_strategy_key
        and verified_parent_source_hash is not None
        and current_parent_source_hash is not None
        and verified_parent_source_hash == current_parent_source_hash
    )

    result: VerificationResult
    if not binds_to_current_candidate:
        result = "STALE"
    elif all_proofs_true:
        result = "PASS"
    else:
        result = "FAIL"

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
        verified_candidate_strategy_spec_hash=verified_hash,
        verified_parent_strategy_key=verified_parent_strategy_key,
        verified_parent_source_hash=verified_parent_source_hash,
    )


def _run_coherence(run_ids: tuple[str | None, ...]) -> tuple[RunCoherence, str | None]:
    """`UNKNOWN` when no file is tagged with a `run_id` at all (nothing to

    compare -- not itself a sign of a problem, most often simply "no
    status script has been run yet with `--run-id` supplied"). `COHERENT`
    when every tagged file agrees. `INCOHERENT` the moment two tagged
    files disagree -- fail-closed and visible, never silently combined
    into one story.
    """
    tagged = sorted({run_id for run_id in run_ids if run_id is not None})
    if not tagged:
        return "UNKNOWN", None
    if len(tagged) == 1:
        return "COHERENT", None
    return "INCOHERENT", f"disagreeing run_id values: {', '.join(tagged)}"


def build_trainer_panel(
    *,
    trainer_status_path: Path | None,
    dataset_status_path: Path | None,
    candidate_status_path: Path | None,
    verification_record_path: Path | None,
    now: UtcDatetime | None = None,
    trainer_status_max_age: timedelta = DEFAULT_TRAINER_STATUS_MAX_AGE,
) -> TrainerPanelState:
    """Read all four snapshot files (any/all may be `None` or missing) and

    reduce them to one display shape. Every path is independently read: a
    missing verification record does not affect the campaign/dataset/
    candidate panels, and vice versa. `run_coherence`/`run_coherence_detail`
    and the verification `STALE` binding are the only places this function
    combines information across files -- both are visibility checks, never
    a silent merge.
    """
    resolved_now = now if now is not None else utc_now()
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

    campaign = _campaign_panel(campaign_snapshot, now=resolved_now, max_age=trainer_status_max_age)
    dataset = _dataset_panel(dataset_snapshot)
    candidate = _candidate_panel(candidate_snapshot)
    verification = _verification_panel(
        verification_snapshot,
        current_candidate_strategy_spec_hash=candidate.candidate_strategy_spec_hash,
        current_parent_strategy_key=candidate.parent_strategy_key,
        current_parent_source_hash=candidate.parent_source_hash,
    )
    run_coherence, run_coherence_detail = _run_coherence(
        (campaign.run_id, dataset.run_id, candidate.run_id)
    )

    return TrainerPanelState(
        campaign=campaign,
        dataset=dataset,
        candidate=candidate,
        verification=verification,
        run_coherence=run_coherence,
        run_coherence_detail=run_coherence_detail,
        candidate_active=False,
        candidate_active_banner=CANDIDATE_NOT_ACTIVE_BANNER,
    )
