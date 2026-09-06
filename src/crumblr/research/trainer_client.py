"""Fail-closed Crumblr client for the external Crumblr Trainer service."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.research.contracts import (
    BacktestReport,
    BacktestRequest,
    EvaluationRecord,
    ResearchVerdict,
    StrategyArtifact,
    StrategyChangeProposal,
    TrainingFinding,
)
from crumblr.research.hashing import research_fingerprint
from crumblr.research.registry import JsonlResearchArtifactRegistry

_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class TrainerIntegrationError(RuntimeError):
    """Trainer transport or contract failed closed."""


class TrainerTransport(Protocol):
    def request(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]: ...


class HttpTrainerTransport:
    """Bounded authenticated JSON transport; no Crumblr secrets are sent."""

    def __init__(self, *, base_url: str, api_key: str, timeout_seconds: float = 20) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("remote Trainer URL must use HTTPS")
        if not api_key:
            raise ValueError("Trainer API key must not be empty")
        self._base_url = base_url.rstrip("/") + "/"
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def request(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            urljoin(self._base_url, path.lstrip("/")),
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with build_opener().open(request, timeout=self._timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise TrainerIntegrationError("Trainer request failed") from error
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise TrainerIntegrationError("Trainer response exceeded the size limit")
        try:
            parsed = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as error:
            raise TrainerIntegrationError("Trainer returned invalid JSON") from error
        if not isinstance(parsed, dict):
            raise TrainerIntegrationError("Trainer response must be a JSON object")
        return parsed


@dataclass(frozen=True, slots=True)
class TrainerCampaignBinding:
    campaign_id: str
    strategy_key: str
    strategy_artifact_hash: str
    dataset_hash: str
    request_hash: str
    code_version: str
    cost_model_hash: str
    random_seed: int


@dataclass(frozen=True, slots=True)
class TrainerResearchOutcome:
    report: BacktestReport
    evaluation: EvaluationRecord
    finding: TrainingFinding
    proposal: StrategyChangeProposal


class CrumblrTrainerClient:
    """Orchestrates research only and records every returned artifact."""

    def __init__(
        self,
        *,
        transport: TrainerTransport,
        registry: JsonlResearchArtifactRegistry,
        trainer_agent_id: UUID,
    ) -> None:
        self._transport = transport
        self._registry = registry
        self._trainer_agent_id = trainer_agent_id

    def capabilities(self) -> dict[str, object]:
        result = self._transport.request("GET", "/api/v1/integration/crumblr-contract")
        if result.get("contract") != "crumblr-research-plane-v1":
            raise TrainerIntegrationError("Trainer has an incompatible research contract")
        if result.get("execution_capability") is not False:
            raise TrainerIntegrationError("Trainer must declare zero execution capability")
        return result

    def start_existing_strategy(
        self,
        *,
        artifact: StrategyArtifact,
        strategy_zip: bytes,
        candle_csv: bytes,
        dataset_hash: str,
        request: BacktestRequest,
    ) -> TrainerCampaignBinding:
        self.capabilities()
        if request.strategy_artifact_hash != artifact.artifact_hash:
            raise TrainerIntegrationError("request and strategy artifact hashes differ")
        if request.dataset_hash != dataset_hash:
            raise TrainerIntegrationError("request and dataset hashes differ")
        self._registry.register(
            kind="StrategyArtifact", identity=artifact.artifact_hash, artifact=artifact
        )
        self._registry.register(
            kind="BacktestRequest", identity=request.request_hash, artifact=request
        )
        imported = self._transport.request(
            "POST",
            "/api/v1/strategies/import",
            {
                "files": [
                    {
                        "name": "strategy.zip",
                        "content_base64": base64.b64encode(strategy_zip).decode("ascii"),
                    }
                ]
            },
        )
        strategy_key = _required_string(imported, "strategy_key")
        campaign = self._transport.request(
            "POST",
            "/api/v1/campaigns",
            {
                "strategy_key": strategy_key,
                "mode": "MODE_1",
                "objective": artifact.hypothesis,
                "allowed_changes": [
                    "TP",
                    "exits",
                    "SL",
                    "break-even",
                    "entries",
                    "filters",
                    "risk",
                ],
                "forbidden_changes": [
                    "live trading",
                    "broker credentials",
                    "automatic promotion",
                ],
                "max_experiments": request.max_experiments,
                "max_variants": request.max_experiments,
                "max_runtime_minutes": request.max_runtime_minutes,
                "development_dataset": {
                    "source": "crumblr_research_plane_v1",
                    "dataset_hash": dataset_hash,
                    "request_hash": request.request_hash,
                    "strategy_artifact_hash": artifact.artifact_hash,
                    "random_seed": request.random_seed,
                    "cost_model_hash": request.cost_model_hash,
                },
            },
        )
        campaign_id = _required_string(campaign, "campaign_id")
        self._transport.request(
            "POST",
            f"/api/v1/campaigns/{campaign_id}/historical-candles",
            {
                "file": {
                    "name": "historical-candles.csv",
                    "content_base64": base64.b64encode(candle_csv).decode("ascii"),
                }
            },
        )
        self._transport.request("POST", f"/api/v1/campaigns/{campaign_id}/agent/run", {})
        return TrainerCampaignBinding(
            campaign_id=campaign_id,
            strategy_key=strategy_key,
            strategy_artifact_hash=artifact.artifact_hash,
            dataset_hash=dataset_hash,
            request_hash=request.request_hash,
            code_version=str(request.code_version),
            cost_model_hash=request.cost_model_hash,
            random_seed=request.random_seed,
        )

    def progress(self, binding: TrainerCampaignBinding) -> dict[str, object]:
        overview = self._transport.request("GET", "/api/v1/overview")
        campaigns = overview.get("campaigns")
        if not isinstance(campaigns, list):
            raise TrainerIntegrationError("Trainer overview has no campaigns")
        for campaign in campaigns:
            if isinstance(campaign, dict) and campaign.get("campaign_id") == binding.campaign_id:
                result = dict(campaign)
                activities = overview.get("agent_activity")
                if isinstance(activities, list):
                    result["agent_activity"] = next(
                        (
                            item
                            for item in activities
                            if isinstance(item, dict)
                            and item.get("campaign_id") == binding.campaign_id
                        ),
                        None,
                    )
                return result
        raise TrainerIntegrationError("bound Trainer campaign is missing")

    def resume(
        self,
        binding: TrainerCampaignBinding,
        *,
        additional_experiments: int,
        additional_runtime_minutes: int,
    ) -> dict[str, object]:
        if not 1 <= additional_experiments <= 10_000:
            raise ValueError("additional_experiments must be between 1 and 10000")
        if not 1 <= additional_runtime_minutes <= 43_200:
            raise ValueError("additional_runtime_minutes must be between 1 and 43200")
        return self._transport.request(
            "POST",
            f"/api/v1/campaigns/{binding.campaign_id}/agent/run",
            {
                "additional_experiments": additional_experiments,
                "additional_runtime_minutes": additional_runtime_minutes,
            },
        )

    def collect_candidate(
        self,
        *,
        binding: TrainerCampaignBinding,
        parent: StrategyArtifact,
    ) -> TrainerResearchOutcome:
        raw = self._transport.request(
            "GET", f"/api/v1/campaigns/{binding.campaign_id}/candidate-artifact"
        )
        return self._translate_candidate(raw=raw, binding=binding, parent=parent)

    def _translate_candidate(
        self,
        *,
        raw: dict[str, object],
        binding: TrainerCampaignBinding,
        parent: StrategyArtifact,
    ) -> TrainerResearchOutcome:
        if raw.get("artifact_state") != "UNAPPROVED_CANDIDATE":
            raise TrainerIntegrationError("Trainer candidate is not unapproved")
        governance = _required_dict(raw, "governance")
        if governance.get("human_approval_required") is not True:
            raise TrainerIntegrationError("Trainer candidate bypasses human approval")
        if governance.get("execution_authority") is not False:
            raise TrainerIntegrationError("Trainer candidate claims execution authority")
        lineage = _required_dict(raw, "research_lineage")
        if lineage.get("campaign_id") != binding.campaign_id:
            raise TrainerIntegrationError("Trainer candidate belongs to another campaign")
        if lineage.get("dataset_hash") != binding.dataset_hash:
            raise TrainerIntegrationError("Trainer candidate dataset hash does not match")
        if lineage.get("crumblr_request_hash") != binding.request_hash:
            raise TrainerIntegrationError("Trainer candidate request hash does not match")
        if lineage.get("crumblr_strategy_artifact_hash") != parent.artifact_hash:
            raise TrainerIntegrationError("Trainer candidate parent artifact does not match")
        identity = _required_dict(raw, "strategy_identity")
        rules = _required_dict(identity, "candidate_strategy_spec")
        candidate_hash = _required_string(identity, "candidate_strategy_spec_hash")
        evaluation_data = _required_dict(raw, "evaluation")
        now = datetime.now(UTC)
        report_payload = {
            "request_hash": binding.request_hash,
            "candidate_hash": candidate_hash,
            "evaluation": evaluation_data,
            "lineage": lineage,
        }
        report_hash = research_fingerprint(report_payload)
        report = BacktestReport(
            report_id=_stable_uuid("report", report_hash),
            report_hash=report_hash,
            request_hash=binding.request_hash,
            strategy_artifact_hash=parent.artifact_hash,
            dataset_hash=binding.dataset_hash,
            code_version=binding.code_version,
            cost_model_hash=binding.cost_model_hash,
            random_seed=binding.random_seed,
            status="COMPLETED",
            metrics=_required_dict(evaluation_data, "metrics"),
            decision_summary={"research_status": evaluation_data.get("research_status")},
            regime_breakdown={},
            out_of_sample=_optional_dict(evaluation_data.get("out_of_sample")),
            sealed_holdout=_optional_dict(evaluation_data.get("sealed_holdout")),
            completed_at_utc=now,
        )
        raw_verdict = str(evaluation_data.get("research_status", "INCONCLUSIVE"))
        verdict = cast(ResearchVerdict, raw_verdict)
        if verdict not in {"REJECTED", "INCONCLUSIVE", "RESEARCH_PROMISING"}:
            raise TrainerIntegrationError("Trainer returned an unknown research status")
        evaluation_hash = research_fingerprint({"report_hash": report_hash, "verdict": verdict})
        evaluation = EvaluationRecord(
            evaluation_id=_stable_uuid("evaluation", evaluation_hash),
            evaluation_hash=evaluation_hash,
            report_hash=report_hash,
            evaluator_agent_id=self._trainer_agent_id,
            verdict=verdict,
            reasons=("Trainer V1 frozen evaluation criteria applied.",),
            criteria_results=evaluation_data,
            evaluated_at_utc=now,
        )
        finding_hash = research_fingerprint(
            {"campaign_id": binding.campaign_id, "report_hash": report_hash, "verdict": verdict}
        )
        finding = TrainingFinding(
            finding_id=_stable_uuid("finding", finding_hash),
            finding_hash=finding_hash,
            campaign_id=binding.campaign_id,
            strategy_artifact_hash=parent.artifact_hash,
            report_hash=report_hash,
            finding_type="STRATEGY_QUALITY",
            summary=f"Trainer candidate evaluated as {verdict}.",
            evidence=evaluation_data,
            limitations=("Research result; no live-performance claim.",),
            produced_by_agent_id=self._trainer_agent_id,
            created_at_utc=now,
        )
        candidate = StrategyArtifact(
            artifact_id=_stable_uuid("strategy", candidate_hash),
            strategy_id=parent.strategy_id,
            semantic_version=f"candidate-{candidate_hash[:12]}",
            artifact_hash=candidate_hash,
            producer_agent_id=self._trainer_agent_id,
            parent_artifact_hash=parent.artifact_hash,
            hypothesis=f"Trainer candidate from campaign {binding.campaign_id}",
            canonical_symbol=parent.canonical_symbol,
            timeframe=parent.timeframe,
            expected_holding_period=parent.expected_holding_period,
            required_features=parent.required_features,
            data_sources=parent.data_sources,
            rules=rules,
            supported_market_capabilities=parent.supported_market_capabilities,
            execution_assumptions=parent.execution_assumptions,
            known_failure_modes=parent.known_failure_modes,
            forbidden_regimes=parent.forbidden_regimes,
            evaluation_criteria=parent.evaluation_criteria,
            created_at_utc=now,
        )
        proposal_hash = research_fingerprint(
            {
                "parent": parent.artifact_hash,
                "candidate": candidate_hash,
                "finding": finding_hash,
                "status": verdict,
            }
        )
        proposal = StrategyChangeProposal(
            proposal_id=_stable_uuid("proposal", proposal_hash),
            proposal_hash=proposal_hash,
            parent_strategy_artifact_hash=parent.artifact_hash,
            candidate_strategy_artifact=candidate,
            finding_hashes=(finding_hash,),
            exact_change=rules,
            expected_effect="Improve frozen evaluation metrics without changing Core safety.",
            research_status=verdict,
            created_at_utc=now,
        )
        for kind, identity_value, artifact in (
            ("BacktestReport", report.report_hash, report),
            ("EvaluationRecord", evaluation.evaluation_hash, evaluation),
            ("TrainingFinding", finding.finding_hash, finding),
            ("StrategyArtifact", candidate.artifact_hash, candidate),
            ("StrategyChangeProposal", proposal.proposal_hash, proposal),
        ):
            self._registry.register(kind=kind, identity=identity_value, artifact=artifact)
        return TrainerResearchOutcome(report, evaluation, finding, proposal)


def _required_dict(container: dict[str, object], key: str) -> dict[str, object]:
    value = container.get(key)
    if not isinstance(value, dict):
        raise TrainerIntegrationError(f"Trainer response has no {key} object")
    return value


def _optional_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _required_string(container: dict[str, object], key: str) -> str:
    value = container.get(key)
    if not isinstance(value, str) or not value:
        raise TrainerIntegrationError(f"Trainer response has no {key}")
    return value


def _stable_uuid(kind: str, identity: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"crumblr:research:{kind}:{identity}")
