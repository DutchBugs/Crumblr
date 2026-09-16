"""`scripts/fetch_trainer_candidate.py::_write_status` -- the FULL RUN 1
dashboard-facing candidate-availability status writer. No network, no
Crumblr database: pure serialization of an already-fetched response.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.fetch_trainer_candidate import _write_status

_CANDIDATE = {
    "schema_version": 1,
    "contract": "crumblr-trainer-candidate-artifact",
    "artifact_state": "UNAPPROVED_CANDIDATE",
    "strategy_identity": {
        "parent_strategy_key": "ict-sb-eurusd-pivot2@v1",
        "parent_source_hash": "real-source-hash",
        "candidate_strategy_spec_hash": "spec-hash-abc",
    },
    "research_lineage": {"experiment_id": "EXP-1"},
    "evaluation": {"research_status": "RESEARCH_PROMISING"},
}


class TestWriteStatus:
    def test_none_path_writes_nothing(self, tmp_path: Path) -> None:
        _write_status(
            None,
            run_id="RUN-1",
            campaign_id="CAM-FULLRUN1-1",
            http_status=200,
            available=True,
            detail=None,
            candidate=_CANDIDATE,
        )
        assert list(tmp_path.iterdir()) == []

    def test_available_candidate_carries_provenance(self, tmp_path: Path) -> None:
        path = tmp_path / "candidate_status.json"
        _write_status(
            path,
            run_id="RUN-1",
            campaign_id="CAM-FULLRUN1-1",
            http_status=200,
            available=True,
            detail=None,
            candidate=_CANDIDATE,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["available"] is True
        assert payload["candidate_strategy_spec_hash"] == "spec-hash-abc"
        assert payload["parent_strategy_key"] == "ict-sb-eurusd-pivot2@v1"
        assert payload["research_status"] == "RESEARCH_PROMISING"
        assert payload["detail"] is None

    def test_unavailable_409_carries_the_conflict_detail(self, tmp_path: Path) -> None:
        path = tmp_path / "candidate_status.json"
        conflict = {"error": "ConflictError", "message": "no RESEARCH_PROMISING candidate"}
        _write_status(
            path,
            run_id="RUN-1",
            campaign_id="CAM-FULLRUN1-1",
            http_status=409,
            available=False,
            detail=conflict,
            candidate=None,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["available"] is False
        assert payload["http_status"] == 409
        assert payload["detail"] == conflict
        assert payload["candidate_strategy_spec_hash"] is None

    def test_unreachable_trainer_has_no_http_status(self, tmp_path: Path) -> None:
        path = tmp_path / "candidate_status.json"
        _write_status(
            path,
            run_id=None,
            campaign_id="CAM-FULLRUN1-1",
            http_status=None,
            available=False,
            detail="connection refused",
            candidate=None,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["http_status"] is None
        assert payload["run_id"] is None
        assert payload["available"] is False
