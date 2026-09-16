"""`dashboard/trainer_panel.py` -- read-only, file-based Trainer/candidate
research state for FULL RUN 1. No network, no database, no MT5: every
case here is driven by writing (or not writing) a plain JSON file.

Snapshot-coherence fix (Dev 1 BLOCK): staleness, run_id agreement across
the three status files, and verification-to-candidate hash binding are
all exercised here with an explicit, deterministic `now` -- never the
real wall clock, so these tests never depend on when they happen to run.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from crumblr.dashboard.trainer_panel import (
    CANDIDATE_NOT_ACTIVE_BANNER,
    DEFAULT_TRAINER_STATUS_MAX_AGE,
    build_trainer_panel,
)

_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


_ALL_PROOFS_TRUE = {
    "base_code_hash_verified": True,
    "candidate_hash_recomputed_matches": True,
    "trainer_artifact_hash_verified": True,
    "candidate_strategy_spec_hash_verified": True,
    "parent_compatibility_verified": True,
    "unit_mapping_verified": True,
}


class TestAllFilesMissing:
    def test_every_panel_reads_as_unknown_not_an_error(self, tmp_path: Path) -> None:
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "trainer_status.json",
            dataset_status_path=tmp_path / "dataset_status.json",
            candidate_status_path=tmp_path / "candidate_status.json",
            verification_record_path=tmp_path / "verification_record.json",
            now=_NOW,
        )
        assert panel.campaign.reachability == "UNKNOWN"
        assert panel.campaign.campaign_status == "UNKNOWN"
        assert panel.dataset.discovered_count is None
        assert panel.candidate.availability == "UNKNOWN"
        assert panel.verification.result == "NOT_RUN"
        assert panel.run_coherence == "UNKNOWN"
        assert panel.run_coherence_detail is None

    def test_none_paths_are_also_unknown(self) -> None:
        panel = build_trainer_panel(
            trainer_status_path=None,
            dataset_status_path=None,
            candidate_status_path=None,
            verification_record_path=None,
            now=_NOW,
        )
        assert panel.campaign.reachability == "UNKNOWN"
        assert panel.verification.result == "NOT_RUN"

    def test_candidate_never_reads_as_active(self, tmp_path: Path) -> None:
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "a.json",
            dataset_status_path=tmp_path / "b.json",
            candidate_status_path=tmp_path / "c.json",
            verification_record_path=tmp_path / "d.json",
            now=_NOW,
        )
        assert panel.candidate_active is False
        assert panel.candidate_active_banner == CANDIDATE_NOT_ACTIVE_BANNER
        assert panel.candidate_active_banner == "CANDIDATE NOT ACTIVE — HUMAN PROMOTION REQUIRED"

    def test_defaults_to_the_real_clock_when_now_is_omitted(self, tmp_path: Path) -> None:
        """`now` is optional in production (the dashboard always wants the

        real clock) -- only tests pin it."""
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
        )
        assert panel.campaign.reachability == "UNKNOWN"


class TestCampaignPanel:
    def test_reachable_and_found(self, tmp_path: Path) -> None:
        path = tmp_path / "trainer_status.json"
        _write(
            path,
            {
                "run_id": "FULLRUN1-1",
                "checked_at_utc": "2026-09-17T11:55:00+00:00",
                "trainer_base_url": "http://127.0.0.1:8766",
                "campaign_id": "CAM-FULLRUN1-1",
                "reachability": {"reachable": True},
                "campaign": {
                    "found": True,
                    "campaign": {"mode": "MODE_2", "strategy_key": "ict-sb-eurusd-pivot2@v1"},
                },
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=path,
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.campaign.reachability == "REACHABLE"
        assert panel.campaign.campaign_status == "FOUND"
        assert panel.campaign.campaign_mode == "MODE_2"
        assert panel.campaign.run_id == "FULLRUN1-1"

    def test_unreachable_trainer_and_missing_campaign(self, tmp_path: Path) -> None:
        path = tmp_path / "trainer_status.json"
        _write(
            path,
            {
                "reachability": {"reachable": False},
                "campaign": {"found": False},
                "campaign_id": "CAM-FULLRUN1-1",
                "checked_at_utc": "2026-09-17T11:59:00+00:00",
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=path,
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.campaign.reachability == "UNREACHABLE"
        assert panel.campaign.campaign_status == "NOT_FOUND"

    def test_a_stale_success_becomes_stale_not_reachable(self, tmp_path: Path) -> None:
        """The exact regression this fix closes: a `reachable: true` snapshot

        whose own `checked_at_utc` is older than `DEFAULT_TRAINER_STATUS_MAX_AGE`
        must not keep reading REACHABLE just because the JSON itself is
        still perfectly readable."""
        path = tmp_path / "trainer_status.json"
        stale_at = _NOW - DEFAULT_TRAINER_STATUS_MAX_AGE - timedelta(minutes=1)
        _write(
            path,
            {
                "reachability": {"reachable": True},
                "campaign": {"found": True, "campaign": {"mode": "MODE_2"}},
                "checked_at_utc": stale_at.isoformat(),
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=path,
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.campaign.reachability == "STALE"

    def test_a_fresh_success_stays_reachable(self, tmp_path: Path) -> None:
        path = tmp_path / "trainer_status.json"
        fresh_at = _NOW - timedelta(minutes=1)
        _write(
            path,
            {
                "reachability": {"reachable": True},
                "campaign": {"found": True, "campaign": {"mode": "MODE_2"}},
                "checked_at_utc": fresh_at.isoformat(),
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=path,
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.campaign.reachability == "REACHABLE"

    def test_a_missing_checked_at_utc_fails_closed_to_stale(self, tmp_path: Path) -> None:
        path = tmp_path / "trainer_status.json"
        _write(path, {"reachability": {"reachable": True}, "campaign": {"found": True}})
        panel = build_trainer_panel(
            trainer_status_path=path,
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.campaign.reachability == "STALE"

    def test_a_stale_unreachable_stays_unreachable_not_relabeled(self, tmp_path: Path) -> None:
        """Staleness only ever downgrades the confident REACHABLE claim --

        an already-conservative UNREACHABLE gains nothing from also being
        called STALE."""
        path = tmp_path / "trainer_status.json"
        stale_at = _NOW - DEFAULT_TRAINER_STATUS_MAX_AGE - timedelta(minutes=1)
        _write(
            path,
            {
                "reachability": {"reachable": False},
                "campaign": {},
                "checked_at_utc": stale_at.isoformat(),
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=path,
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.campaign.reachability == "UNREACHABLE"


class TestDatasetPanel:
    def test_zero_eligible_is_shown_plainly(self, tmp_path: Path) -> None:
        path = tmp_path / "dataset_status.json"
        _write(
            path,
            {
                "discovered_count": 3,
                "eligible_count": 0,
                "excluded_count": 3,
                "excluded_reasons": [{"outcome_id": "abc", "reason": "never reached execution"}],
                "posted_to_trainer": False,
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=path,
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.dataset.discovered_count == 3
        assert panel.dataset.eligible_count == 0
        assert panel.dataset.posted_to_trainer is False
        assert panel.dataset.excluded_reasons == ("abc: never reached execution",)


class TestCandidatePanel:
    def test_available_candidate_carries_provenance(self, tmp_path: Path) -> None:
        path = tmp_path / "candidate_status.json"
        _write(
            path,
            {
                "available": True,
                "http_status": 200,
                "candidate_strategy_spec_hash": "spec-hash-abc",
                "parent_strategy_key": "ict-sb-eurusd-pivot2@v1",
                "research_status": "RESEARCH_PROMISING",
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=path,
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.candidate.availability == "AVAILABLE"
        assert panel.candidate.candidate_strategy_spec_hash == "spec-hash-abc"
        assert panel.candidate.research_status == "RESEARCH_PROMISING"

    def test_no_candidate_yet_is_none_not_unknown(self, tmp_path: Path) -> None:
        path = tmp_path / "candidate_status.json"
        _write(
            path,
            {
                "available": False,
                "http_status": 409,
                "detail": {"error": "ConflictError", "message": "no candidate yet"},
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=path,
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.candidate.availability == "NONE"
        assert panel.candidate.http_status == 409


class TestVerificationPanel:
    def test_no_record_at_all_is_not_run_not_fail(self, tmp_path: Path) -> None:
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.verification.result == "NOT_RUN"

    def test_matching_current_run_and_current_candidate_is_pass(self, tmp_path: Path) -> None:
        """Required regression: hashes agree and every proof is true ->

        PASS."""
        candidate_path = tmp_path / "candidate_status.json"
        _write(candidate_path, {"available": True, "candidate_strategy_spec_hash": "hash-CURRENT"})
        verification_path = tmp_path / "verification_record.json"
        _write(
            verification_path,
            {
                "candidate_hash": "joint-hash-abc",
                "evaluation_outcome_kind": "TRADE_PROPOSAL",
                "lineage": {"candidate_strategy_spec_hash": "hash-CURRENT"},
                **_ALL_PROOFS_TRUE,
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=candidate_path,
            verification_record_path=verification_path,
            now=_NOW,
        )
        assert panel.verification.result == "PASS"
        assert panel.verification.candidate_hash == "joint-hash-abc"
        assert panel.verification.verified_candidate_strategy_spec_hash == "hash-CURRENT"

    def test_a_record_for_a_different_candidate_is_stale_not_pass(self, tmp_path: Path) -> None:
        """Required regression: an old verification record whose six proofs

        are all true, but whose lineage names a *different* candidate than
        the one currently shown, must never read as PASS for the one shown
        now."""
        candidate_path = tmp_path / "candidate_status.json"
        _write(candidate_path, {"available": True, "candidate_strategy_spec_hash": "hash-CURRENT"})
        verification_path = tmp_path / "verification_record.json"
        _write(
            verification_path,
            {"lineage": {"candidate_strategy_spec_hash": "hash-OLD-DIFFERENT"}, **_ALL_PROOFS_TRUE},
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=candidate_path,
            verification_record_path=verification_path,
            now=_NOW,
        )
        assert panel.verification.result == "STALE"
        assert panel.verification.verified_candidate_strategy_spec_hash == "hash-OLD-DIFFERENT"

    def test_no_current_candidate_to_bind_to_is_stale_not_pass(self, tmp_path: Path) -> None:
        """A verification record with all-true proofs but no currently

        AVAILABLE candidate to compare against must not be trusted as
        describing "the candidate shown now" -- there is no candidate
        shown now."""
        verification_path = tmp_path / "verification_record.json"
        _write(
            verification_path,
            {"lineage": {"candidate_strategy_spec_hash": "hash-SOMETHING"}, **_ALL_PROOFS_TRUE},
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",  # no candidate at all
            verification_record_path=verification_path,
            now=_NOW,
        )
        assert panel.verification.result == "STALE"

    def test_matching_candidate_but_a_false_proof_is_fail_not_stale(self, tmp_path: Path) -> None:
        candidate_path = tmp_path / "candidate_status.json"
        _write(candidate_path, {"available": True, "candidate_strategy_spec_hash": "hash-CURRENT"})
        verification_path = tmp_path / "verification_record.json"
        proofs = {**_ALL_PROOFS_TRUE, "unit_mapping_verified": False}
        _write(
            verification_path,
            {"lineage": {"candidate_strategy_spec_hash": "hash-CURRENT"}, **proofs},
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=candidate_path,
            verification_record_path=verification_path,
            now=_NOW,
        )
        assert panel.verification.result == "FAIL"

    def test_matching_candidate_but_a_missing_proof_field_is_fail_not_pass(
        self, tmp_path: Path
    ) -> None:
        """An older-format or partially-written record must never read as

        PASS merely because it happens not to contain an explicit False
        value."""
        candidate_path = tmp_path / "candidate_status.json"
        _write(candidate_path, {"available": True, "candidate_strategy_spec_hash": "hash-CURRENT"})
        verification_path = tmp_path / "verification_record.json"
        _write(
            verification_path,
            {
                "lineage": {"candidate_strategy_spec_hash": "hash-CURRENT"},
                "base_code_hash_verified": True,
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=candidate_path,
            verification_record_path=verification_path,
            now=_NOW,
        )
        assert panel.verification.result == "FAIL"


class TestRunCoherence:
    def test_no_run_id_anywhere_is_unknown(self, tmp_path: Path) -> None:
        trainer_path = tmp_path / "trainer_status.json"
        dataset_path = tmp_path / "dataset_status.json"
        _write(trainer_path, {"reachability": {}, "campaign": {}})
        _write(dataset_path, {"discovered_count": 0})
        panel = build_trainer_panel(
            trainer_status_path=trainer_path,
            dataset_status_path=dataset_path,
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.run_coherence == "UNKNOWN"
        assert panel.run_coherence_detail is None

    def test_matching_run_ids_are_coherent(self, tmp_path: Path) -> None:
        trainer_path = tmp_path / "trainer_status.json"
        dataset_path = tmp_path / "dataset_status.json"
        candidate_path = tmp_path / "candidate_status.json"
        _write(trainer_path, {"run_id": "FULLRUN1-SAME", "reachability": {}, "campaign": {}})
        _write(dataset_path, {"run_id": "FULLRUN1-SAME", "discovered_count": 0})
        _write(candidate_path, {"run_id": "FULLRUN1-SAME", "available": False})
        panel = build_trainer_panel(
            trainer_status_path=trainer_path,
            dataset_status_path=dataset_path,
            candidate_status_path=candidate_path,
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.run_coherence == "COHERENT"
        assert panel.run_coherence_detail is None

    def test_a_partially_tagged_agreement_is_still_coherent(self, tmp_path: Path) -> None:
        """Only the *non-null* run_ids must agree -- a file that has never

        been run with `--run-id` at all (None) is not itself a mismatch."""
        trainer_path = tmp_path / "trainer_status.json"
        candidate_path = tmp_path / "candidate_status.json"
        _write(trainer_path, {"run_id": "FULLRUN1-SAME", "reachability": {}, "campaign": {}})
        _write(candidate_path, {"run_id": "FULLRUN1-SAME", "available": False})
        panel = build_trainer_panel(
            trainer_status_path=trainer_path,
            dataset_status_path=tmp_path / "missing.json",  # never tagged
            candidate_status_path=candidate_path,
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.run_coherence == "COHERENT"

    def test_mismatched_run_ids_are_incoherent_and_visible(self, tmp_path: Path) -> None:
        """Required regression: disagreeing run_ids must be reported as

        incoherent, fail-closed, with the disagreeing values named -- never
        silently combined into one story."""
        trainer_path = tmp_path / "trainer_status.json"
        dataset_path = tmp_path / "dataset_status.json"
        candidate_path = tmp_path / "candidate_status.json"
        _write(trainer_path, {"run_id": "FULLRUN1-A", "reachability": {}, "campaign": {}})
        _write(dataset_path, {"run_id": "FULLRUN1-B", "discovered_count": 0})
        _write(candidate_path, {"run_id": "FULLRUN1-A", "available": False})
        panel = build_trainer_panel(
            trainer_status_path=trainer_path,
            dataset_status_path=dataset_path,
            candidate_status_path=candidate_path,
            verification_record_path=tmp_path / "missing.json",
            now=_NOW,
        )
        assert panel.run_coherence == "INCOHERENT"
        assert panel.run_coherence_detail is not None
        assert "FULLRUN1-A" in panel.run_coherence_detail
        assert "FULLRUN1-B" in panel.run_coherence_detail
