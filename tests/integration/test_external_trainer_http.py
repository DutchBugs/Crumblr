"""Real HTTP proof between Crumblr's Step-D client and Crumblr Trainer."""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest

from crumblr.research.contracts import BacktestRequest, StrategyArtifact
from crumblr.research.registry import JsonlResearchArtifactRegistry
from crumblr.research.trainer_client import CrumblrTrainerClient, HttpTrainerTransport

trainer_api = pytest.importorskip("crumblr_trainer.api")
trainer_config = pytest.importorskip("crumblr_trainer.config")
trainer_service = pytest.importorskip("crumblr_trainer.service")

AGENT_ID = UUID("11111111-1111-4111-8111-111111111111")


class _Browser:
    def health(self) -> dict[str, object]:
        return {"ok": True, "engine": "integration-stub"}


def _strategy_zip() -> bytes:
    specification = {
        "name": "Crumblr Integration Strategy",
        "version": "1.0",
        "instrument": "EURUSD",
        "timeframe": "M5",
        "strategy_config": {"TakeProfitR": 2.0},
        "local_strategy": {
            "engine": "crumblr-rules-v1",
            "entry": {
                "signal": "momentum",
                "side": "both",
                "lookback": 20,
                "threshold_atr": 0.25,
            },
            "exits": {
                "stop_atr": 1.0,
                "take_profits": [{"r": 2.0, "fraction": 1.0}],
                "max_hold_bars": 48,
            },
            "costs": {"point_size": 0.00001, "spread_points": 8},
        },
    }
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("Strategy.mq5", '#property version "1.0"\n')
        archive.writestr("trainer-strategy.json", json.dumps(specification))
    return output.getvalue()


def _candles() -> bytes:
    rows: list[str] = []
    price = 1.1
    for index in range(700):
        change = 0.00014 if index % 3 else -0.00010
        opening, closing = price, price + change
        rows.append(
            f"2024.01.{1 + index // 144:02d},{index % 24:02d}:{index % 60:02d},"
            f"{opening:.5f},{max(opening, closing) + 0.00004:.5f},"
            f"{min(opening, closing) - 0.00004:.5f},{closing:.5f},1"
        )
        price = closing
    return ("\n".join(rows) + "\n").encode()


def test_real_http_campaign_reaches_completion(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory() as trainer_home:
        service = trainer_service.TrainerService(
            trainer_config.TrainerConfig(home=Path(trainer_home)), browser=_Browser()
        )
        server = trainer_api.TrainerAPIServer(
            ("127.0.0.1", 0), service, api_key="integration-secret"
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            package = _strategy_zip()
            candles = _candles()
            artifact_hash = hashlib.sha256(package).hexdigest()
            dataset_hash = hashlib.sha256(candles).hexdigest()
            artifact = StrategyArtifact(
                artifact_id=UUID("22222222-2222-4222-8222-222222222222"),
                strategy_id="integration-strategy",
                semantic_version="1.0",
                artifact_hash=artifact_hash,
                producer_agent_id=AGENT_ID,
                hypothesis="Run one bounded external Trainer experiment.",
                canonical_symbol="EURUSD",
                timeframe="M5",
                expected_holding_period="intraday",
                required_features=("OHLC bars",),
                data_sources=("frozen test candles",),
                rules={"engine": "crumblr-rules-v1"},
                execution_assumptions={"research_only": True},
                evaluation_criteria={"max_experiments": 1},
                created_at_utc="2026-09-06T12:00:00Z",
            )
            request = BacktestRequest(
                request_id=UUID("33333333-3333-4333-8333-333333333333"),
                request_hash="c" * 64,
                strategy_artifact_hash=artifact_hash,
                dataset_hash=dataset_hash,
                code_version="trainer-1.0",
                cost_model_hash="d" * 64,
                random_seed=42,
                max_experiments=1,
                max_runtime_minutes=5,
                requested_by_agent_id=AGENT_ID,
                requested_at_utc="2026-09-06T12:00:00Z",
            )
            client = CrumblrTrainerClient(
                transport=HttpTrainerTransport(
                    base_url=f"http://127.0.0.1:{server.server_address[1]}",
                    api_key="integration-secret",
                ),
                registry=JsonlResearchArtifactRegistry(tmp_path / "research.jsonl"),
                trainer_agent_id=AGENT_ID,
            )
            binding = client.start_existing_strategy(
                artifact=artifact,
                strategy_zip=package,
                candle_csv=candles,
                dataset_hash=dataset_hash,
                request=request,
            )
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                progress = client.progress(binding)
                if progress.get("automation", {}).get("state") == "COMPLETED":
                    break
                time.sleep(0.05)
            assert progress["automation"]["state"] == "COMPLETED"
            assert progress["experiments_used"] == 1
            assert progress["development_dataset"]["request_hash"] == request.request_hash
            assert progress["development_dataset"]["strategy_artifact_hash"] == artifact_hash
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
