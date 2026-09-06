"""Step-D research plane: immutable artifacts and Trainer boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from crumblr.research.contracts import BacktestRequest, StrategyArtifact
from crumblr.research.registry import ArtifactConflictError, JsonlResearchArtifactRegistry
from crumblr.research.trainer_client import (
    CrumblrTrainerClient,
    TrainerIntegrationError,
    TrainerTransport,
)

AGENT_ID = UUID("11111111-1111-4111-8111-111111111111")
ARTIFACT_HASH = "a" * 64
DATASET_HASH = "b" * 64
REQUEST_HASH = "c" * 64
COST_HASH = "d" * 64
CANDIDATE_HASH = "e" * 64


def artifact() -> StrategyArtifact:
    return StrategyArtifact(
        artifact_id=UUID("22222222-2222-4222-8222-222222222222"),
        strategy_id="silver-bullet",
        semantic_version="5.0",
        artifact_hash=ARTIFACT_HASH,
        producer_agent_id=AGENT_ID,
        hypothesis="Optimize the existing frozen strategy.",
        canonical_symbol="US30",
        timeframe="M5",
        expected_holding_period="intraday",
        required_features=("OHLC bars",),
        data_sources=("Crumblr frozen candles",),
        rules={"engine": "crumblr-rules-v1"},
        execution_assumptions={"costs_included": True},
        evaluation_criteria={"minimum_trades": 20},
        created_at_utc=datetime(2026, 9, 6, tzinfo=UTC),
    )


def backtest_request() -> BacktestRequest:
    return BacktestRequest(
        request_id=UUID("33333333-3333-4333-8333-333333333333"),
        request_hash=REQUEST_HASH,
        strategy_artifact_hash=ARTIFACT_HASH,
        dataset_hash=DATASET_HASH,
        code_version="trainer-1.0",
        cost_model_hash=COST_HASH,
        random_seed=42,
        max_experiments=20,
        max_runtime_minutes=30,
        requested_by_agent_id=AGENT_ID,
        requested_at_utc=datetime(2026, 9, 6, tzinfo=UTC),
    )


class FakeTrainerTransport(TrainerTransport):
    def __init__(self, *, tamper: str | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        self.tamper = tamper

    def request(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        self.calls.append((method, path, payload))
        if path == "/api/v1/integration/crumblr-contract":
            return {
                "contract": "crumblr-research-plane-v1",
                "execution_capability": False,
            }
        if path == "/api/v1/strategies/import":
            return {"strategy_key": "silver-bullet@5.0"}
        if path == "/api/v1/campaigns":
            return {"campaign_id": "CAM-STEP-D"}
        if path.endswith("/historical-candles"):
            return {"campaign": {"campaign_id": "CAM-STEP-D"}}
        if path.endswith("/agent/run"):
            return {"background": True}
        if path == "/api/v1/overview":
            return {
                "campaigns": [
                    {
                        "campaign_id": "CAM-STEP-D",
                        "status": "COMPLETED",
                        "experiments_used": 20,
                    }
                ]
            }
        if path.endswith("/candidate-artifact"):
            lineage: dict[str, object] = {
                "campaign_id": "CAM-STEP-D",
                "dataset_hash": DATASET_HASH,
                "crumblr_request_hash": REQUEST_HASH,
                "crumblr_strategy_artifact_hash": ARTIFACT_HASH,
                "random_seed": 42,
                "cost_model_hash": COST_HASH,
            }
            if self.tamper == "dataset":
                lineage["dataset_hash"] = "f" * 64
            governance: dict[str, object] = {
                "human_approval_required": True,
                "execution_authority": False,
            }
            if self.tamper == "execution":
                governance["execution_authority"] = True
            return {
                "artifact_state": "UNAPPROVED_CANDIDATE",
                "strategy_identity": {
                    "candidate_strategy_spec": {
                        "engine": "crumblr-rules-v1",
                        "parameters": {"TakeProfitR": 1.75},
                    },
                    "candidate_strategy_spec_hash": CANDIDATE_HASH,
                },
                "research_lineage": lineage,
                "evaluation": {
                    "research_status": "RESEARCH_PROMISING",
                    "metrics": {"net_r": 8.5, "max_drawdown_r": 3.0},
                    "out_of_sample": {"status": "PASS"},
                    "sealed_holdout": {"status": "PASS"},
                },
                "governance": governance,
            }
        raise AssertionError(f"unexpected Trainer call: {method} {path}")


def client(tmp_path: Path, transport: TrainerTransport) -> CrumblrTrainerClient:
    return CrumblrTrainerClient(
        transport=transport,
        registry=JsonlResearchArtifactRegistry(tmp_path / "research.jsonl"),
        trainer_agent_id=AGENT_ID,
    )


def test_registry_is_durable_idempotent_and_immutable(tmp_path: Path) -> None:
    path = tmp_path / "research.jsonl"
    registry = JsonlResearchArtifactRegistry(path)
    original = artifact()
    assert registry.register(
        kind="StrategyArtifact", identity=original.artifact_hash, artifact=original
    )
    assert not registry.register(
        kind="StrategyArtifact", identity=original.artifact_hash, artifact=original
    )
    restored = JsonlResearchArtifactRegistry(path)
    assert (
        restored.get(kind="StrategyArtifact", identity=ARTIFACT_HASH, contract=StrategyArtifact)
        == original
    )
    changed = original.model_copy(update={"hypothesis": "different content"})
    with pytest.raises(ArtifactConflictError):
        restored.register(kind="StrategyArtifact", identity=changed.artifact_hash, artifact=changed)


def test_full_crumblr_trainer_flow_yields_unapproved_proposal(tmp_path: Path) -> None:
    transport = FakeTrainerTransport()
    integration = client(tmp_path, transport)
    binding = integration.start_existing_strategy(
        artifact=artifact(),
        strategy_zip=b"PK safe strategy package",
        candle_csv=b"time,open,high,low,close\n",
        dataset_hash=DATASET_HASH,
        request=backtest_request(),
    )
    assert integration.progress(binding)["status"] == "COMPLETED"
    resumed = integration.resume(binding, additional_experiments=10, additional_runtime_minutes=15)
    assert resumed["background"] is True
    outcome = integration.collect_candidate(binding=binding, parent=artifact())

    assert outcome.proposal.research_status == "RESEARCH_PROMISING"
    assert outcome.proposal.human_approval_required is True
    assert outcome.proposal.automatic_promotion is False
    assert outcome.proposal.execution_authority is False
    assert outcome.proposal.candidate_strategy_artifact.parent_artifact_hash == ARTIFACT_HASH
    reloaded = JsonlResearchArtifactRegistry(tmp_path / "research.jsonl")
    assert reloaded.count() == 7
    assert any(path.endswith("/agent/run") for _, path, _ in transport.calls)


@pytest.mark.parametrize("tamper", ["dataset", "execution"])
def test_candidate_tampering_fails_closed(tmp_path: Path, tamper: str) -> None:
    transport = FakeTrainerTransport(tamper=tamper)
    integration = client(tmp_path, transport)
    binding = integration.start_existing_strategy(
        artifact=artifact(),
        strategy_zip=b"PK safe strategy package",
        candle_csv=b"time,open,high,low,close\n",
        dataset_hash=DATASET_HASH,
        request=backtest_request(),
    )
    with pytest.raises(TrainerIntegrationError):
        integration.collect_candidate(binding=binding, parent=artifact())
