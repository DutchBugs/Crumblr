"""Provision a PAPER_LITE test/toy Agent identity and immutable assignment."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from crumblr.agent_gateway.contracts import (
    AgentIdentity,
    AgentRole,
    AgentStatus,
    ChampionShadowStatus,
    TradingAssignment,
)
from crumblr.agent_gateway.gateway import AgentGateway
from crumblr.agent_gateway.stores import (
    InMemoryDecisionContextBundleStore,
    InMemoryFeatureEvidenceStore,
)
from crumblr.application.paper_lite import (
    PaperLiteConfigurationError,
    require_paper_lite_database_url,
)
from crumblr.domain.enums import Environment
from crumblr.domain.timeutils import UtcDatetime
from crumblr.persistence.agent_gateway import (
    PostgresAgentCredentialStore,
    PostgresAgentDecisionOutcomeStore,
    PostgresAgentIdentityStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.engine import create_db_engine, database_url

GATEWAY_CREDENTIAL_ENV = "CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", type=UUID, required=True)
    parser.add_argument("--assignment-id", type=UUID, required=True)
    parser.add_argument("--strategy-artifact-id", type=UUID, required=True)
    parser.add_argument("--strategy-artifact-hash", required=True)
    parser.add_argument("--valid-from", type=_parse_time, required=True)
    parser.add_argument("--valid-until", type=_parse_time, required=True)
    parser.add_argument("--symbol", default="EUR/USD")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--service-identity", default="paper-lite-local-toy-agent")
    parser.add_argument("--runtime-version", default="paper-lite-toy-v1")
    parser.add_argument("--max-proposals-per-hour", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    credential = os.getenv(GATEWAY_CREDENTIAL_ENV)
    if not credential:
        raise SystemExit(
            f"set {GATEWAY_CREDENTIAL_ENV}; the credential is never accepted in a CLI argument"
        )
    try:
        configured_database_url = require_paper_lite_database_url(database_url())
    except PaperLiteConfigurationError as error:
        raise SystemExit(str(error)) from error
    engine = create_db_engine(configured_database_url)
    try:
        identities = PostgresAgentIdentityStore(engine)
        credentials = PostgresAgentCredentialStore(engine)
        assignments = PostgresTradingAssignmentStore(engine)
        # Context stores are never called by these two administrative methods;
        # in-memory instances keep this setup utility from constructing unrelated
        # publication machinery merely to reuse Gateway's authority checks.
        gateway = AgentGateway(
            identities=identities,
            credentials=credentials,
            assignments=assignments,
            contexts=InMemoryDecisionContextBundleStore(),
            outcomes=PostgresAgentDecisionOutcomeStore(engine),
            feature_evidence=InMemoryFeatureEvidenceStore(),
        )
        gateway.register_identity(
            AgentIdentity(
                agent_id=args.agent_id,
                role=AgentRole.TRADER,
                runtime_version=args.runtime_version,
                service_identity=args.service_identity,
                status=AgentStatus.ACTIVE,
                registered_at_utc=args.valid_from,
            ),
            credential_secret=credential,
        )
        gateway.issue_assignment(
            TradingAssignment(
                assignment_id=args.assignment_id,
                assignment_version="paper-lite-v1",
                allowed_agent_id=args.agent_id,
                canonical_symbol=args.symbol,
                timeframe=args.timeframe,
                strategy_artifact_id=args.strategy_artifact_id,
                strategy_artifact_hash=args.strategy_artifact_hash,
                valid_from_utc=args.valid_from,
                valid_until_utc=args.valid_until,
                max_proposals_per_hour=args.max_proposals_per_hour,
                allowed_risk_fraction_min=Decimal("0.0001"),
                allowed_risk_fraction_max=Decimal("0.02"),
                required_evidence_fields=(),
                supervisor_policy_version="paper-lite-external-supervisor-skipped-v1",
                environment=Environment.PAPER,
                champion_shadow_status=ChampionShadowStatus.SHADOW,
            )
        )
    finally:
        engine.dispose()


def _parse_time(value: str) -> UtcDatetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must be timezone-aware ISO-8601")
    return parsed


if __name__ == "__main__":
    main()
