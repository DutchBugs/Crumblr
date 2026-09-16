"""`scripts/collect_crumblr_trader_dataset.py::_write_snapshot` -- the
FULL RUN 1 dashboard-facing status file writer. No database, no network:
`_write_snapshot` only serializes an already-built `DatasetCollectionResult`.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from scripts.collect_crumblr_trader_dataset import _write_snapshot

from crumblr.trainer_bridge.dataset import DatasetCollectionResult, ExcludedOutcome, TraderIdentity

_IDENTITY = TraderIdentity(
    agent_id=UUID("760e93be-117c-48a3-b997-f258055ec29b"),
    assignment_id=UUID("f98c0396-dd13-4a99-b1e9-b83ea0f15ed7"),
    strategy_artifact_hash="81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5",
    canonical_symbol="EUR/USD",
    timeframe="M5",
)


class TestWriteSnapshot:
    def test_none_path_writes_nothing(self, tmp_path: Path) -> None:
        result = DatasetCollectionResult(
            identity=_IDENTITY, discovered_count=0, eligible=(), excluded=()
        )
        _write_snapshot(
            None,
            run_id="RUN-1",
            identity=_IDENTITY,
            result=result,
            campaign_id="CAM-FULLRUN1-1",
            posted=False,
            trainer_http_status=None,
        )
        assert list(tmp_path.iterdir()) == []

    def test_zero_eligible_snapshot_is_explicit(self, tmp_path: Path) -> None:
        outcome_id = uuid4()
        result = DatasetCollectionResult(
            identity=_IDENTITY,
            discovered_count=1,
            eligible=(),
            excluded=(ExcludedOutcome(outcome_id=outcome_id, reason="never reached execution"),),
        )
        path = tmp_path / "dataset_state.json"

        _write_snapshot(
            path,
            run_id="RUN-1",
            identity=_IDENTITY,
            result=result,
            campaign_id="CAM-FULLRUN1-1",
            posted=False,
            trainer_http_status=None,
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["run_id"] == "RUN-1"
        assert payload["campaign_id"] == "CAM-FULLRUN1-1"
        assert payload["discovered_count"] == 1
        assert payload["eligible_count"] == 0
        assert payload["excluded_count"] == 1
        assert payload["excluded_reasons"] == [
            {"outcome_id": str(outcome_id), "reason": "never reached execution"}
        ]
        assert payload["posted_to_trainer"] is False
        assert payload["trainer_http_status"] is None
        assert payload["identity"]["agent_id"] == str(_IDENTITY.agent_id)

    def test_posted_snapshot_carries_the_trainer_status(self, tmp_path: Path) -> None:
        result = DatasetCollectionResult(
            identity=_IDENTITY, discovered_count=0, eligible=(), excluded=()
        )
        path = tmp_path / "nested" / "dataset_state.json"

        _write_snapshot(
            path,
            run_id=None,
            identity=_IDENTITY,
            result=result,
            campaign_id="CAM-FULLRUN1-1",
            posted=True,
            trainer_http_status=200,
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["run_id"] is None
        assert payload["posted_to_trainer"] is True
        assert payload["trainer_http_status"] == 200
