"""`dashboard/trainer_panel.py` -- read-only, file-based Trainer/candidate
research state for FULL RUN 1. No network, no database, no MT5: every
case here is driven by writing (or not writing) a plain JSON file.
"""

from __future__ import annotations

import json
from pathlib import Path

from crumblr.dashboard.trainer_panel import (
    CANDIDATE_NOT_ACTIVE_BANNER,
    build_trainer_panel,
)


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestAllFilesMissing:
    def test_every_panel_reads_as_unknown_not_an_error(self, tmp_path: Path) -> None:
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "trainer_status.json",
            dataset_status_path=tmp_path / "dataset_status.json",
            candidate_status_path=tmp_path / "candidate_status.json",
            verification_record_path=tmp_path / "verification_record.json",
        )
        assert panel.campaign.reachability == "UNKNOWN"
        assert panel.campaign.campaign_status == "UNKNOWN"
        assert panel.dataset.discovered_count is None
        assert panel.candidate.availability == "UNKNOWN"
        assert panel.verification.result == "NOT_RUN"

    def test_none_paths_are_also_unknown(self) -> None:
        panel = build_trainer_panel(
            trainer_status_path=None,
            dataset_status_path=None,
            candidate_status_path=None,
            verification_record_path=None,
        )
        assert panel.campaign.reachability == "UNKNOWN"
        assert panel.verification.result == "NOT_RUN"

    def test_candidate_never_reads_as_active(self, tmp_path: Path) -> None:
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "a.json",
            dataset_status_path=tmp_path / "b.json",
            candidate_status_path=tmp_path / "c.json",
            verification_record_path=tmp_path / "d.json",
        )
        assert panel.candidate_active is False
        assert panel.candidate_active_banner == CANDIDATE_NOT_ACTIVE_BANNER
        assert panel.candidate_active_banner == "CANDIDATE NOT ACTIVE — HUMAN PROMOTION REQUIRED"


class TestCampaignPanel:
    def test_reachable_and_found(self, tmp_path: Path) -> None:
        path = tmp_path / "trainer_status.json"
        _write(
            path,
            {
                "run_id": "FULLRUN1-1",
                "checked_at_utc": "2026-09-17T00:00:00+00:00",
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
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=path,
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
        )
        assert panel.campaign.reachability == "UNREACHABLE"
        assert panel.campaign.campaign_status == "NOT_FOUND"


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
        )
        assert panel.candidate.availability == "NONE"
        assert panel.candidate.http_status == 409


class TestVerificationPanel:
    def test_all_proofs_true_is_pass(self, tmp_path: Path) -> None:
        path = tmp_path / "verification_record.json"
        _write(
            path,
            {
                "candidate_hash": "abc123",
                "evaluation_outcome_kind": "TRADE_PROPOSAL",
                "base_code_hash_verified": True,
                "candidate_hash_recomputed_matches": True,
                "trainer_artifact_hash_verified": True,
                "candidate_strategy_spec_hash_verified": True,
                "parent_compatibility_verified": True,
                "unit_mapping_verified": True,
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=path,
        )
        assert panel.verification.result == "PASS"
        assert panel.verification.candidate_hash == "abc123"

    def test_any_false_proof_is_fail(self, tmp_path: Path) -> None:
        path = tmp_path / "verification_record.json"
        _write(
            path,
            {
                "base_code_hash_verified": True,
                "candidate_hash_recomputed_matches": True,
                "trainer_artifact_hash_verified": True,
                "candidate_strategy_spec_hash_verified": True,
                "parent_compatibility_verified": True,
                "unit_mapping_verified": False,
            },
        )
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=path,
        )
        assert panel.verification.result == "FAIL"

    def test_a_missing_proof_field_is_fail_not_pass(self, tmp_path: Path) -> None:
        """An older-format or partially-written record must never read as
        PASS merely because it happens not to contain a False value."""
        path = tmp_path / "verification_record.json"
        _write(path, {"base_code_hash_verified": True})
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=path,
        )
        assert panel.verification.result == "FAIL"

    def test_no_record_at_all_is_not_run_not_fail(self, tmp_path: Path) -> None:
        panel = build_trainer_panel(
            trainer_status_path=tmp_path / "missing.json",
            dataset_status_path=tmp_path / "missing.json",
            candidate_status_path=tmp_path / "missing.json",
            verification_record_path=tmp_path / "missing.json",
        )
        assert panel.verification.result == "NOT_RUN"
