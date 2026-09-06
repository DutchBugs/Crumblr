"""Immutable research-plane contracts from ADR-005 Step D.

These are evidence and proposal contracts. They deliberately cannot represent
a TradingAssignment, TradeIntent, approval, Risk decision, or execution request.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from crumblr.domain.models import Contract, Symbol, VersionTag
from crumblr.domain.timeutils import UtcDatetime

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmpty = Annotated[str, Field(min_length=1, max_length=500)]
JsonObject = dict[str, object]
ResearchVerdict = Literal["REJECTED", "INCONCLUSIVE", "RESEARCH_PROMISING"]


class StrategyArtifact(Contract):
    """Immutable strategy specification produced outside Crumblr Core."""

    schema_version: Literal["1.0"] = "1.0"
    artifact_id: UUID
    strategy_id: NonEmpty
    semantic_version: VersionTag
    artifact_hash: Sha256
    producer_agent_id: UUID
    parent_artifact_hash: Sha256 | None = None
    hypothesis: NonEmpty
    canonical_symbol: Symbol
    timeframe: Annotated[str, Field(min_length=1, max_length=16)]
    expected_holding_period: NonEmpty
    required_features: tuple[NonEmpty, ...]
    data_sources: tuple[NonEmpty, ...]
    rules: JsonObject
    supported_market_capabilities: tuple[NonEmpty, ...] = ()
    execution_assumptions: JsonObject
    known_failure_modes: tuple[NonEmpty, ...] = ()
    forbidden_regimes: tuple[NonEmpty, ...] = ()
    evaluation_criteria: JsonObject
    created_at_utc: UtcDatetime


class BacktestRequest(Contract):
    """Frozen, reproducible request. A hash identifies every test input."""

    schema_version: Literal["1.0"] = "1.0"
    request_id: UUID
    request_hash: Sha256
    strategy_artifact_hash: Sha256
    dataset_hash: Sha256
    code_version: VersionTag
    cost_model_hash: Sha256
    random_seed: int = Field(ge=0)
    max_experiments: int = Field(gt=0, le=10_000)
    max_runtime_minutes: int = Field(gt=0, le=43_200)
    requested_by_agent_id: UUID
    requested_at_utc: UtcDatetime


class BacktestReport(Contract):
    """Immutable backtest evidence with provenance, never a promotion."""

    schema_version: Literal["1.0"] = "1.0"
    report_id: UUID
    report_hash: Sha256
    request_hash: Sha256
    strategy_artifact_hash: Sha256
    dataset_hash: Sha256
    code_version: VersionTag
    cost_model_hash: Sha256
    random_seed: int = Field(ge=0)
    status: Literal["COMPLETED", "FAILED", "INCONCLUSIVE"]
    metrics: JsonObject
    decision_summary: JsonObject
    regime_breakdown: JsonObject
    out_of_sample: JsonObject
    sealed_holdout: JsonObject
    completed_at_utc: UtcDatetime


class EvaluationRecord(Contract):
    """Typed assessment of one report against frozen criteria."""

    schema_version: Literal["1.0"] = "1.0"
    evaluation_id: UUID
    evaluation_hash: Sha256
    report_hash: Sha256
    evaluator_agent_id: UUID
    verdict: ResearchVerdict
    reasons: tuple[NonEmpty, ...]
    criteria_results: JsonObject
    evaluated_at_utc: UtcDatetime


class TrainingFinding(Contract):
    """A learned research fact; never an executable command."""

    schema_version: Literal["1.0"] = "1.0"
    finding_id: UUID
    finding_hash: Sha256
    campaign_id: NonEmpty
    strategy_artifact_hash: Sha256
    report_hash: Sha256
    finding_type: Literal[
        "STRATEGY_QUALITY",
        "EXECUTION_QUALITY",
        "POLICY_EFFECT",
        "DATA_QUALITY",
    ]
    summary: NonEmpty
    evidence: JsonObject
    limitations: tuple[NonEmpty, ...] = ()
    produced_by_agent_id: UUID
    created_at_utc: UtcDatetime


class StrategyChangeProposal(Contract):
    """Unapproved candidate output from Training.

    The explicit false/required fields prevent this contract from becoming a
    back door into assignment or live-strategy mutation.
    """

    schema_version: Literal["1.0"] = "1.0"
    proposal_id: UUID
    proposal_hash: Sha256
    parent_strategy_artifact_hash: Sha256
    candidate_strategy_artifact: StrategyArtifact
    finding_hashes: tuple[Sha256, ...]
    exact_change: JsonObject
    expected_effect: NonEmpty
    research_status: ResearchVerdict
    human_approval_required: Literal[True] = True
    automatic_promotion: Literal[False] = False
    execution_authority: Literal[False] = False
    created_at_utc: UtcDatetime

    @model_validator(mode="after")
    def _candidate_must_descend_from_parent(self) -> StrategyChangeProposal:
        if self.candidate_strategy_artifact.parent_artifact_hash != (
            self.parent_strategy_artifact_hash
        ):
            raise ValueError("candidate artifact must name the proposal parent")
        return self
