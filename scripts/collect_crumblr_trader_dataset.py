"""TRAINER/TRADER/CRUMBLR V1 -- SLICE 2: one Trader identity's closed-trade
dataset snapshot.

    uv run python scripts/collect_crumblr_trader_dataset.py \\
        --agent-id 760e93be-117c-48a3-b997-f258055ec29b \\
        --assignment-id f98c0396-dd13-4a99-b1e9-b83ea0f15ed7 \\
        --trainer-base-url http://127.0.0.1:8766 \\
        --campaign-id CAM-ICT-DATASET-1

Read-only against Crumblr, one outbound HTTP POST at most. Builds ONE
immutable Trainer MODE_2 `RESULT_CONTRACT` dataset snapshot from every
eligible completed trade belonging to exactly one explicit identity
(agent_id + assignment_id, cross-checked against the registered
`TradingAssignment`'s own `strategy_artifact_hash`/`canonical_symbol`/
`timeframe`). Never mixes identities: a different agent_id or
assignment_id is always a separate run, a separate campaign, a separate
dataset.

Discovery walks `agent_decision_outcomes` (the durable Agent Gateway
audit trail) for every `TRADE_PROPOSAL` this exact identity was ever
claimed against, derives each one's `TradeIntent.intent_id`
(`agent_gateway.gateway.derive_trade_intent_id` -- the same formula the
Gateway itself uses, not a re-guess), and resolves each candidate through
`scripts/export_crumblr_trade_to_trainer.py::resolve_evidence` --
Slice 1's own single-trade primitive, completely unchanged, isolated-
close-window proof included. A trade that primitive cannot resolve is
EXCLUDED with the exact reason, never estimated, never silently dropped,
and never salvaged by weakening the isolation check.

If zero trades are eligible, this is reported as a plain, explicit fact
-- no POST is attempted, and canary-fixture data is never substituted for
a Trader identity that has not produced a real closed trade yet.

`transaction_costs_included` is always `False` and is not a CLI flag --
it is a dataset truth claim about every included trade, not an operator
assertion this tool should let anyone override on the command line.
It stays hard-coded closed until Crumblr has durable evidence proving all
applicable broker costs (commission/swap/spread) for every trade a
dataset snapshot includes, not merely that a balance delta netted them
out for one specific trade.

Not built here (deliberately out of scope for this slice):
StrategyMaterializer, candidate promotion, automatic TradingAssignment
changes, Trainer-to-Trader activation, automatic campaign creation or
rotation, LIVE.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine

from crumblr.agent_gateway.gateway import derive_trade_intent_id
from crumblr.persistence.agent_gateway import (
    PostgresAgentDecisionOutcomeStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, create_db_engine, database_url
from crumblr.persistence.execution import ExecutionRequestStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.trainer_bridge.dataset import (
    DatasetCollectionResult,
    ExcludedOutcome,
    TraderIdentity,
    build_dataset_result,
)
from crumblr.trainer_bridge.evidence import ClosedTradeEvidence, derive_return_r
from crumblr.trainer_bridge.trainer_client import (
    TrainerClientConfig,
    TrainerTransportError,
    post_agent_data,
)

# `scripts` is only importable as a package when the repo root is on
# `sys.path` -- true under pytest (rootdir), not guaranteed for a plain
# `python scripts/collect_crumblr_trader_dataset.py` invocation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.export_crumblr_trade_to_trainer import (
    EvidenceIncompleteError,
    resolve_evidence,
)


class IdentityMismatchError(RuntimeError):
    """The named `--agent-id`/`--assignment-id` do not cohere -- a setup
    error, refused entirely rather than silently collecting under a
    different identity than the one actually named."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--agent-id", type=UUID, required=True)
    parser.add_argument("--assignment-id", type=UUID, required=True)
    parser.add_argument("--trainer-base-url", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument(
        "--trainer-agent-id",
        default=None,
        help="opaque agent_id string sent to Trainer's own contract; defaults to the "
        "Crumblr --agent-id itself",
    )
    parser.add_argument("--trainer-api-key", default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve and print the dataset, do not POST",
    )
    return parser.parse_args()


def resolve_identity(engine: Engine, *, agent_id: UUID, assignment_id: UUID) -> TraderIdentity:
    assignment = PostgresTradingAssignmentStore(engine).current(assignment_id)
    if assignment is None:
        raise IdentityMismatchError(f"no TradingAssignment registered for {assignment_id}")
    if assignment.allowed_agent_id != agent_id:
        raise IdentityMismatchError(
            f"assignment {assignment_id} is registered for agent "
            f"{assignment.allowed_agent_id}, not the named agent {agent_id}"
        )
    return TraderIdentity(
        agent_id=agent_id,
        assignment_id=assignment_id,
        strategy_artifact_hash=assignment.strategy_artifact_hash,
        canonical_symbol=assignment.canonical_symbol,
        timeframe=assignment.timeframe,
    )


def collect(
    engine: Engine, identity: TraderIdentity
) -> tuple[DatasetCollectionResult, dict[UUID, Decimal]]:
    outcome_ids = PostgresAgentDecisionOutcomeStore(engine).trade_proposal_outcome_ids_for(
        agent_id=identity.agent_id, assignment_id=identity.assignment_id
    )
    requests_store = ExecutionRequestStore(engine)
    specs = InstrumentSpecStore(engine)

    eligible: list[ClosedTradeEvidence] = []
    excluded: list[ExcludedOutcome] = []
    returns_r: dict[UUID, Decimal] = {}

    for outcome_id in outcome_ids:
        intent_id = derive_trade_intent_id(outcome_id)
        order_request_id = requests_store.order_request_id_for_intent(intent_id)
        if order_request_id is None:
            excluded.append(
                ExcludedOutcome(
                    outcome_id=outcome_id,
                    reason="no execution_requests row for this proposal's intent -- it never "
                    "reached FINAL Risk/execution (refused earlier in the chain, or a NO_TRADE "
                    "misclassified upstream)",
                )
            )
            continue

        try:
            evidence = resolve_evidence(engine, order_request_id)
        except EvidenceIncompleteError as error:
            excluded.append(ExcludedOutcome(outcome_id=outcome_id, reason=str(error)))
            continue

        if evidence.strategy_version != identity.strategy_artifact_hash:
            excluded.append(
                ExcludedOutcome(
                    outcome_id=outcome_id,
                    reason=f"capsule strategy_version {evidence.strategy_version!r} does not "
                    f"match the declared assignment's strategy_artifact_hash "
                    f"{identity.strategy_artifact_hash!r}",
                )
            )
            continue
        if evidence.canonical_symbol != identity.canonical_symbol:
            excluded.append(
                ExcludedOutcome(
                    outcome_id=outcome_id,
                    reason=f"capsule canonical_symbol {evidence.canonical_symbol!r} does not "
                    f"match the declared assignment's canonical_symbol "
                    f"{identity.canonical_symbol!r}",
                )
            )
            continue

        spec = specs.at_or_before(
            canonical_symbol=evidence.canonical_symbol, at=evidence.fill_occurred_at_utc
        )
        if spec is None:
            excluded.append(
                ExcludedOutcome(
                    outcome_id=outcome_id,
                    reason=f"no instrument_specs row for {evidence.canonical_symbol} at or "
                    f"before {evidence.fill_occurred_at_utc}",
                )
            )
            continue

        eligible.append(evidence)
        returns_r[evidence.order_request_id] = derive_return_r(evidence, spec)

    result = DatasetCollectionResult(
        identity=identity,
        discovered_count=len(outcome_ids),
        eligible=tuple(eligible),
        excluded=tuple(excluded),
    )
    return result, returns_r


def main() -> int:
    args = parse_args()

    try:
        url = database_url()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        print(f"set {DATABASE_URL_ENV_VAR} to Crumblr's own database", file=sys.stderr)
        return 2

    engine = create_db_engine(url)
    try:
        identity = resolve_identity(
            engine, agent_id=args.agent_id, assignment_id=args.assignment_id
        )
    except IdentityMismatchError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2

    result, returns_r = collect(engine, identity)
    engine.dispose()

    print("=== identity ===")
    print(f"  agent_id:                {identity.agent_id}")
    print(f"  assignment_id:           {identity.assignment_id}")
    print(f"  strategy_artifact_hash:  {identity.strategy_artifact_hash}")
    print(f"  canonical_symbol:        {identity.canonical_symbol}")
    print(f"  timeframe:               {identity.timeframe}")
    print()
    print("=== counts ===")
    print(f"  discovered: {result.discovered_count}")
    print(f"  eligible:   {len(result.eligible)}")
    print(f"  excluded:   {len(result.excluded)}")
    if result.excluded:
        print("\n  exclusion reasons:")
        for item in result.excluded:
            print(f"    - {item.outcome_id}: {item.reason}")

    if not result.eligible:
        print(
            "\nNo eligible closed trades for this identity yet -- reporting the fact "
            "plainly, not posting to Trainer, not substituting any other identity's data."
        )
        return 0

    # Hard-coded, not an operator flag: `transaction_costs_included` is a
    # dataset truth claim, not an assertion this CLI should let anyone
    # make on the command line. It stays `False` until Crumblr has durable
    # evidence proving all applicable costs for every included trade --
    # see the module docstring.
    dataset = build_dataset_result(result, returns_r, transaction_costs_included=False)
    print("\n=== normalized dataset (Trainer RESULT_CONTRACT.md shape) ===")
    print(json.dumps(dataset, indent=2))

    if args.dry_run:
        print("\n--dry-run: not posting to Trainer")
        return 0

    config = TrainerClientConfig(base_url=args.trainer_base_url, api_key=args.trainer_api_key)
    trainer_agent_id = args.trainer_agent_id or str(args.agent_id)
    try:
        status, body = post_agent_data(
            config,
            campaign_id=args.campaign_id,
            agent_id=trainer_agent_id,
            result=dataset,
            source_reference=json.dumps(
                {
                    "agent_id": str(identity.agent_id),
                    "assignment_id": str(identity.assignment_id),
                    "strategy_artifact_hash": identity.strategy_artifact_hash,
                    "canonical_symbol": identity.canonical_symbol,
                    "timeframe": identity.timeframe,
                    "discovered_count": result.discovered_count,
                    "eligible_count": len(result.eligible),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    except TrainerTransportError as error:
        print(f"BLOCKED: Trainer call failed: {error}", file=sys.stderr)
        return 2

    print(f"\n=== Trainer response (HTTP {status}) ===")
    print(json.dumps(body, indent=2))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
