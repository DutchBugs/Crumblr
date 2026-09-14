"""FEEDBACK.2.0 DEMO EXECUTION — the small Agent live-decision + real

preflight/canary-submission driver.

    uv run python scripts/agent_canary_execution.py \\
        --agent-id 760e93be-117c-48a3-b997-f258055ec29b \\
        --assignment-id f98c0396-dd13-4a99-b1e9-b83ea0f15ed7 \\
        --agent-url http://127.0.0.1:8765 \\
        --code-commit <git rev-parse HEAD> \\
        --enable-external-supervisor \\
        --canary-permit-id <uuid printed by issue_canary_permit.py> \\
        --once

Reuses the *existing* Agent/Gateway/RunRecorder/CapsuleStore chain --
nothing here is a new decision engine:

    HttpNeutralAgentClient.decide()      -- the real Static Agent, over HTTP
    AgentGateway.submit_trade_proposal() -- accept/reject against the
                                             provisioned TradingAssignment
    agent_gateway.decision_path
        .evaluate_agent_trade_intent()   -- Core Risk -> strategy-neutral
                                             Policy -> (optional) external
                                             Supervisor -> seals a real
                                             DecisionCapsule via RunRecorder
                                             (JournalRecorder -> CapsuleStore)
    ExecutionOrchestrator.run_once()     -- the same real preflight chain
                                             every other script in this
                                             codebase already exercises:
                                             claim -> eligibility -> gate
                                             -> fresh broker read ->
                                             reconciliation -> FINAL Risk
                                             -> order_check -> SubmissionGate

**Without `--canary-permit-id`, this is preflight-only** -- the exact same
non-sending guarantee every other script in this codebase already proves
(`order_send` unreachable). `--canary-permit-id` is the one flag that
changes that: it constructs a real `EntrySubmissionSink` (the reviewed
`DemoOrderSendMt5Gateway`, imported only here, never in
`application/execution.py` itself) plus a real `CanaryPermitStore` and
`CanaryEntrySubmissionConfig` fixing this run's own `--agent-id`/
`--assignment-id`/the loaded assignment's `strategy_artifact_hash`, and
passes all three into `ExecutionOrchestrator` -- which then fails closed
on every one of `SubmissionGate`'s ten conditions *and* the permit's own
exact-scope check before ever calling `order_send` for real.

One MT5 connection for the whole process, shared between the decision-
time portfolio read (`capture_broker_state`, the same coherent-observation
discipline `ExecutionOrchestrator` itself uses) and the execution-time
adapter -- never two independent connections racing each other.

`config/agent_canary_demo.yaml` is read and applied explicitly here
(never by `crumblr.config.load_config`, which only ever merges
`base.yaml` + `<environment>.yaml`) -- only when `--apply-canary-config`
is passed, so a plain preflight-only run never silently reads it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID

import yaml

from crumblr.agent_gateway.contracts import PolicyHints, TradeProposal, TradingAssignment
from crumblr.agent_gateway.decision_path import (
    ExternalSupervisorProvider,
    PortfolioSnapshot,
    evaluate_agent_trade_intent,
)
from crumblr.agent_gateway.evidence import build_agent_context_evidence
from crumblr.agent_gateway.gateway import AgentGateway
from crumblr.agent_gateway.market_context import build_agent_market_context_v1
from crumblr.agent_gateway.neutral_agent_client import HttpNeutralAgentClient
from crumblr.agent_gateway.reference_supervisor import (
    ReferenceSupervisor,
    ReferenceSupervisorConfig,
)
from crumblr.agent_gateway.static_agent_client import StaticAgentClientConfig
from crumblr.application.bootstrap import DurableRuntime, build_durable_runtime
from crumblr.application.broker_state import capture_broker_state
from crumblr.application.execution import (
    CanaryEntrySubmissionConfig,
    ExecutionOrchestrator,
)
from crumblr.application.reconciliation import ExpectedState, reconcile
from crumblr.application.recording import JournalRecorder
from crumblr.config import PlatformConfig, load_config
from crumblr.domain.enums import Environment, IncidentStatus, SessionState
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import InstrumentSpec, MarketSnapshot
from crumblr.domain.money import price_to_points
from crumblr.domain.timeutils import utc_now
from crumblr.market_data.synthetic import snapshot_id_for
from crumblr.mt5_gateway.client import MissingCredentialsError, Mt5Client, read_credentials
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.persistence.agent_gateway import (
    PostgresAgentCredentialStore,
    PostgresAgentDecisionOutcomeStore,
    PostgresAgentIdentityStore,
    PostgresDecisionContextBundleStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.canary_permit import CanaryPermitStore
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, DEFAULT_TEST_URL
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.features import FeatureSnapshotStore
from crumblr.persistence.flatten import FlattenEventStore, FlattenRequestStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore
from crumblr.persistence.market_data import MarketDataStore
from crumblr.trading_agent.sessions import is_market_open

REPO_ROOT = Path(__file__).resolve().parent.parent
GATEWAY_CREDENTIAL_ENV = "CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL"
AGENT_TOKEN_ENV = "CRUMBLR_PAPER_LITE_AGENT_TOKEN"
"""Same shared bearer contract `paper_lite.py` already uses for the

existing Stage C/ICT Agent -- also this script's own `--gateway-
credential-env`/`--agent-token-env` defaults, so omitting both flags is
byte-for-byte the prior, unparameterized behaviour. A run against a
*different* registered AgentIdentity (Dev 1 review BLOCK fix -- the
deterministic canary fixture has its own separately-provisioned
credential/token, never this pair) must pass its own var names via those
two flags; nothing here ever aliases one identity's secret onto
another's env-var name."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", type=UUID, required=True)
    parser.add_argument("--assignment-id", type=UUID, required=True)
    parser.add_argument("--agent-url", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--symbol", default="EUR/USD")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--environment", default=Environment.PAPER.value)
    parser.add_argument(
        "--safety-state-file",
        type=Path,
        default=REPO_ROOT / "var" / "agent_paper_post_incident.safety.json",
        help="the durable safety-latch file this run's kill switch checks against the "
        "Postgres safety_state_events journal for agreement -- defaults to the exact "
        "post-incident latch path every other Stage C process on this baseline uses "
        "(config/agent_paper_post_incident.yaml's own safety_latch_path); "
        "var/safety_state.json is a different, stale, pre-incident evidence-run latch "
        "and must never be used here",
    )
    parser.add_argument(
        "--enable-external-supervisor",
        action="store_true",
        help="gate the sealed capsule on a real external-Supervisor review (the "
        "deterministic ReferenceSupervisor), not only Core Risk/Policy",
    )
    parser.add_argument("--external-supervisor-min-confidence", type=float, default=0.5)
    parser.add_argument(
        "--apply-canary-config",
        action="store_true",
        help="read and apply config/agent_canary_demo.yaml's four owner-approval "
        "overrides on top of the loaded PlatformConfig -- never applied unless "
        "explicitly passed",
    )
    parser.add_argument(
        "--canary-permit-id",
        type=UUID,
        default=None,
        help="wires a real EntrySubmissionSink/CanaryPermitStore into ExecutionOrchestrator, "
        "scoped to exactly this one permit -- omit to stay preflight-only (order_send "
        "unreachable), the same non-sending guarantee every other script here proves",
    )
    parser.add_argument(
        "--gateway-credential-env",
        default=GATEWAY_CREDENTIAL_ENV,
        help="env-var NAME (not the value) this run reads its AgentGateway credential from "
        "-- defaults to the existing Stage C/ICT Agent's own var so omitting this flag is "
        "the exact prior behaviour. A run against a different registered AgentIdentity "
        "(e.g. the deterministic canary fixture) must pass its own credential's var name "
        "here explicitly -- never left to default to another identity's credential",
    )
    parser.add_argument(
        "--agent-token-env",
        default=AGENT_TOKEN_ENV,
        help="env-var NAME (not the value) this run reads its Agent-host bearer token from "
        "-- defaults to the existing Stage C/ICT Agent's own var so omitting this flag is "
        "the exact prior behaviour. A run against a different Agent host (e.g. the "
        "deterministic canary fixture) must pass its own token's var name here explicitly",
    )
    return parser.parse_args()


def _resolve_gateway_secrets(
    *, gateway_credential_env: str, agent_token_env: str
) -> tuple[str, str] | None:
    """Reads exactly the two named env vars -- never any other name, never a

    fallback to `GATEWAY_CREDENTIAL_ENV`/`AGENT_TOKEN_ENV` when a caller
    selected different ones. `None` (fail closed) if either is missing or
    empty; never prints either value.
    """
    credential = os.getenv(gateway_credential_env)
    agent_token = os.getenv(agent_token_env)
    if not credential or not agent_token:
        return None
    return credential, agent_token


def _apply_canary_config_overlay(config: PlatformConfig) -> PlatformConfig:
    overlay_path = REPO_ROOT / "config" / "agent_canary_demo.yaml"
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8")) or {}
    updates: dict[str, object] = {}
    if "risk" in overlay:
        updates["risk"] = config.risk.model_copy(update=overlay["risk"])
    if "execution" in overlay:
        updates["execution"] = config.execution.model_copy(update=overlay["execution"])
    return config.model_copy(update=updates)


class _RealPortfolioStateProvider:
    """A real, MT5-backed `PortfolioStateProvider` (agent_gateway

    .decision_path). One coherent observation per call
    (`capture_broker_state`, the same discipline `ExecutionOrchestrator
    ._process()` itself uses) -- never two independent reads that could
    disagree. `reconciliation_status` is genuinely derived from a fresh
    `reconcile()` against the flat expectation, not hardcoded MATCHED the
    way `PaperLitePortfolioProvider` correctly can (a paper broker *is*
    the broker; a real one is not).
    """

    def __init__(
        self,
        adapter: OrderCheckMt5Gateway,
        broker_state: BrokerStateStore,
        instrument_specs: InstrumentSpecStore,
        *,
        environment: Environment,
        canonical_symbol: str,
        account_guard: object,
        expected_spec_version: str | None,
    ) -> None:
        self._adapter = adapter
        self._broker_state = broker_state
        self._instrument_specs = instrument_specs
        self._environment = environment
        self._canonical_symbol = canonical_symbol
        self._account_guard = account_guard
        self._expected_spec_version = expected_spec_version

    def current(self) -> PortfolioSnapshot:
        observation = capture_broker_state(
            self._adapter.reader,
            environment=self._environment,
            canonical_symbol=self._canonical_symbol,
        )
        self._broker_state.record(observation)
        assert observation.account_state is not None
        expectation = ExpectedState.flat(
            self._account_guard,  # type: ignore[arg-type]
            canonical_symbol=self._canonical_symbol,
            expected_spec_version=self._expected_spec_version,
        )
        result = reconcile(
            self._broker_state,
            expectation,
            instrument_specs=self._instrument_specs,
            now=utc_now(),
        )
        return PortfolioSnapshot(
            account=observation.account_state,
            open_positions=observation.position_states,
            reconciliation_status=result.status,
        )


def _latest_snapshot(
    market_data: MarketDataStore,
    *,
    spec: InstrumentSpec,
    canonical_symbol: str,
    timeframe: str,
) -> MarketSnapshot | None:
    tick = market_data.latest_tick(canonical_symbol=canonical_symbol)
    bars = market_data.recent_bars(
        canonical_symbol=canonical_symbol, timeframe=timeframe, limit=400
    )
    if tick is None or not bars:
        return None
    return MarketSnapshot(
        snapshot_id=snapshot_id_for(canonical_symbol, tick.event_time_utc),
        symbol=canonical_symbol,
        event_time_utc=tick.event_time_utc,
        received_time_utc=tick.received_time_utc,
        bid=tick.bid,
        ask=tick.ask,
        spread_points=price_to_points(tick.ask - tick.bid, spec.point),
        timeframe=timeframe,
        bars=tuple(item.bar for item in bars),
        session_state=(
            SessionState.OPEN if is_market_open(tick.event_time_utc) else SessionState.CLOSED
        ),
        symbol_spec_version=spec.spec_version,
        data_quality=tick.data_quality,
    )


def main() -> int:
    args = parse_args()
    environment = Environment(args.environment)

    database_url = os.environ.get(DATABASE_URL_ENV_VAR)
    if not database_url:
        print(
            f"error: {DATABASE_URL_ENV_VAR} is not set. Point it at the same database "
            f"mt5_live_reader.py writes to -- never the shared test database "
            f"({DEFAULT_TEST_URL!r}).",
            file=sys.stderr,
        )
        return 2

    secrets = _resolve_gateway_secrets(
        gateway_credential_env=args.gateway_credential_env,
        agent_token_env=args.agent_token_env,
    )
    if secrets is None:
        print(
            f"error: set both {args.gateway_credential_env} and {args.agent_token_env}; "
            "secrets are never read from YAML or CLI arguments",
            file=sys.stderr,
        )
        return 2
    credential, agent_token = secrets

    config = load_config(environment, config_dir=REPO_ROOT / "config")
    if args.apply_canary_config:
        config = _apply_canary_config_overlay(config)

    try:
        mt5_credentials = read_credentials()
    except MissingCredentialsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    runtime = build_durable_runtime(
        environment=environment,
        state_file=args.safety_state_file,
        url=database_url,
    )

    def _kill_switch_is_halted() -> bool:
        # A plain `runtime.kill_switch.is_halted` read, wrapped so mypy
        # cannot (incorrectly) narrow it to a literal across this
        # function's later reads -- the kill switch is genuinely mutable
        # via later calls (evaluate_agent_trade_intent/run_once can trip
        # it) even though mypy cannot see that through this object graph.
        return runtime.kill_switch.is_halted

    if _kill_switch_is_halted():
        print("error: safety state is not RUNNING; this driver never resets it itself.")
        runtime.dispose()
        return 2

    identities = PostgresAgentIdentityStore(runtime.engine)
    assignments = PostgresTradingAssignmentStore(runtime.engine)
    assignment = assignments.current(args.assignment_id)
    if assignment is None:
        print(f"error: assignment {args.assignment_id} is not provisioned.", file=sys.stderr)
        runtime.dispose()
        return 2
    if assignment.allowed_agent_id != args.agent_id:
        print("error: the provisioned assignment belongs to another Agent.", file=sys.stderr)
        runtime.dispose()
        return 2
    if assignment.canonical_symbol != args.symbol or assignment.timeframe != args.timeframe:
        print(
            "error: runner symbol/timeframe does not match the provisioned assignment.",
            file=sys.stderr,
        )
        runtime.dispose()
        return 2

    client = Mt5Client()
    client.connect(mt5_credentials, terminal_path=os.environ.get("CRUMBLR_MT5_TERMINAL_PATH"))
    market = config.market_for(args.symbol)
    adapter = OrderCheckMt5Gateway(
        client,
        config.account_guard,
        canonical_symbol=args.symbol,
        expected_broker_symbol=market.broker_symbol if market is not None else None,
    )

    specs = InstrumentSpecStore(runtime.engine)
    spec = specs.latest(canonical_symbol=args.symbol)
    if spec is None:
        print("error: no real instrument spec is stored; start mt5_live_reader first.")
        runtime.dispose()
        return 2
    if market is None or market.expected_spec_version != spec.spec_version:
        print("error: latest real instrument spec does not match the approved config pin.")
        runtime.dispose()
        return 2

    gateway = AgentGateway(
        identities=identities,
        credentials=PostgresAgentCredentialStore(runtime.engine),
        assignments=assignments,
        contexts=PostgresDecisionContextBundleStore(runtime.engine),
        outcomes=PostgresAgentDecisionOutcomeStore(runtime.engine),
        feature_evidence=FeatureSnapshotStore(runtime.engine),
    )
    agent = HttpNeutralAgentClient(
        agent_id=args.agent_id,
        gateway_credential_secret=credential,
        client=StaticAgentClientConfig(base_url=args.agent_url, bearer_token=agent_token),
    )
    market_data = MarketDataStore(runtime.engine)
    broker_state = BrokerStateStore(runtime.engine)
    recorder = JournalRecorder(runtime.engine, environment=environment)
    portfolio_provider = _RealPortfolioStateProvider(
        adapter,
        broker_state,
        specs,
        environment=environment,
        canonical_symbol=args.symbol,
        account_guard=config.account_guard,
        expected_spec_version=market.expected_spec_version,
    )
    external_supervisor = (
        ReferenceSupervisor(
            ReferenceSupervisorConfig(
                supervisor_agent_id=args.agent_id,
                min_confidence=args.external_supervisor_min_confidence,
            )
        )
        if args.enable_external_supervisor
        else None
    )

    entry_submission_adapter = None
    canary_permit_store = None
    if args.canary_permit_id is not None:
        from crumblr.mt5_gateway.demo_execution import DemoOrderSendMt5Gateway

        entry_submission_adapter = DemoOrderSendMt5Gateway(adapter, client)
        canary_permit_store = CanaryPermitStore(runtime.engine)
        print(
            f"  CANARY SUBMISSION WIRED — permit_id={args.canary_permit_id} — a real "
            "order_send is now reachable, for the exact one capsule each cycle seals, "
            "if every gate and the permit's own exact scope check all pass.\n"
        )
    else:
        print("  preflight-only — order_send is not reachable from this run.\n")

    def _build_orchestrator(
        canary_config: CanaryEntrySubmissionConfig | None,
    ) -> ExecutionOrchestrator:
        # Dev 1 review BLOCK fix: a fresh ExecutionOrchestrator, built
        # from a fresh CanaryEntrySubmissionConfig naming the exact
        # capsule this cycle just sealed -- never one built upfront, once,
        # before any capsule existed. Cheap: no I/O of its own, only
        # references to the already-open engine/adapter/stores above.
        return ExecutionOrchestrator(
            config,
            capsules=CapsuleStore(runtime.engine),
            requests=ExecutionRequestStore(runtime.engine),
            events=ExecutionEventStore(runtime.engine),
            flatten_requests=FlattenRequestStore(runtime.engine),
            flatten_events=FlattenEventStore(runtime.engine),
            broker_state=broker_state,
            instrument_specs=specs,
            session_store=runtime.session_store,
            risk_ledger_lock=runtime.risk_ledger_lock,
            kill_switch=runtime.kill_switch,
            adapter=adapter,
            canonical_symbol=args.symbol,
            worker_id="agent_canary_execution",
            entry_submission_adapter=entry_submission_adapter,
            canary_permit_store=canary_permit_store,
            canary_config=canary_config,
        )

    # Preflight-only mode needs no per-cycle capsule_id binding -- one
    # orchestrator, built once, exactly like every other script here.
    static_orchestrator = None if args.canary_permit_id is not None else _build_orchestrator(None)

    print("=" * 78)
    print("  FEEDBACK.2.0 DEMO EXECUTION — Agent live decision + real preflight")
    print("=" * 78)
    print(f"  symbol={args.symbol} timeframe={args.timeframe} environment={environment.value}")
    print(f"  agent_id={args.agent_id} assignment_id={args.assignment_id}")
    print(f"  kill_switch={runtime.kill_switch.state.value}\n")

    iteration = 0
    try:
        while True:
            latest = _latest_snapshot(
                market_data, spec=spec, canonical_symbol=args.symbol, timeframe=args.timeframe
            )
            if latest is None:
                print(json.dumps({"status": "waiting_for_real_read_only_market_data"}))
            else:
                sealed_capsule_id = _run_one_decision_cycle(
                    gateway=gateway,
                    agent=agent,
                    assignment=assignment,
                    portfolio_provider=portfolio_provider,
                    recorder=recorder,
                    config=config,
                    runtime=runtime,
                    external_supervisor=external_supervisor,
                    code_commit=args.code_commit,
                    snapshot=latest,
                    spec=spec,
                )
                if static_orchestrator is not None:
                    run_orchestrator = static_orchestrator
                elif sealed_capsule_id is None:
                    # The Gateway rejected this cycle's proposal before any
                    # capsule was sealed (accepted=False) -- nothing for
                    # ExecutionOrchestrator to process this cycle.
                    run_orchestrator = None
                else:
                    run_orchestrator = _build_orchestrator(
                        CanaryEntrySubmissionConfig(
                            permit_id=args.canary_permit_id,
                            capsule_id=sealed_capsule_id,
                            agent_id=args.agent_id,
                            assignment_id=args.assignment_id,
                            strategy_artifact_hash=assignment.strategy_artifact_hash,
                        )
                    )
                outcomes = run_orchestrator.run_once() if run_orchestrator is not None else ()
                for outcome in outcomes:
                    print(
                        json.dumps(
                            {
                                "order_request_id": str(outcome.order_request_id),
                                "capsule_id": str(outcome.capsule_id),
                                "event_type": outcome.event_type.value,
                                "reason_codes": [code.value for code in outcome.reason_codes],
                            }
                        )
                    )
            iteration += 1
            if args.once:
                break
            time.sleep(args.poll_seconds)
    finally:
        halted = _kill_switch_is_halted()
        runtime.dispose()
    return 1 if halted else 0


def _run_one_decision_cycle(
    *,
    gateway: AgentGateway,
    agent: HttpNeutralAgentClient,
    assignment: TradingAssignment,
    portfolio_provider: _RealPortfolioStateProvider,
    recorder: JournalRecorder,
    config: PlatformConfig,
    runtime: DurableRuntime,
    external_supervisor: ExternalSupervisorProvider | None,
    code_commit: str,
    snapshot: MarketSnapshot,
    spec: InstrumentSpec,
) -> UUID | None:
    """Runs one Agent -> Gateway -> Risk -> Policy -> (optional) external

    Supervisor cycle and returns the exact `capsule_id` this cycle sealed
    via `evaluate_agent_trade_intent`'s own `RunRecorder.seal()` call --
    `None` only when the Gateway rejected the proposal/no-trade decision
    before any capsule was ever sealed (`gateway_result.accepted` is
    `False`). The caller (Dev 1 review BLOCK fix) uses this exact id, and
    no other, to construct this cycle's own `CanaryEntrySubmissionConfig`.
    """
    now = utc_now()
    portfolio = portfolio_provider.current()
    bundle = gateway.publish_context(
        assignment_id=assignment.assignment_id,
        symbol=snapshot.symbol,
        market_snapshot_id=snapshot.snapshot_id,
        instrument_spec_version=spec.spec_version,
        portfolio_summary_hash=fingerprint(
            {
                "account": portfolio.account.model_dump(mode="json"),
                "positions": [p.model_dump(mode="json") for p in portfolio.open_positions],
            }
        ),
        session_state=snapshot.session_state,
        data_quality=snapshot.data_quality,
        now=now,
        policy_hints=PolicyHints(
            max_intents_per_hour_hint=assignment.max_proposals_per_hour,
            min_stop_distance_points_hint=config.risk.min_stop_distance_points,
            session_blackout_active=not is_market_open(now),
            notes="agent_canary_execution-v1",
        ),
    )
    context = build_agent_market_context_v1(
        context_id=bundle.context_id,
        content_hash=bundle.content_hash,
        assignment_id=assignment.assignment_id,
        strategy_artifact_id=assignment.strategy_artifact_id,
        strategy_artifact_hash=assignment.strategy_artifact_hash,
        issued_at_utc=bundle.issued_at_utc,
        expires_at_utc=bundle.expires_at_utc,
        snapshot=snapshot,
        spec=spec,
        session_state=snapshot.session_state,
        safety_state=runtime.kill_switch.state,
        reconciliation_status=portfolio.reconciliation_status,
        feature_snapshot_id=bundle.feature_snapshot_id,
        open_position_count=len(portfolio.open_positions),
        open_risk_fraction=None,
        policy_hints=bundle.policy_hints,
    )
    decision = agent.decide(context)
    gateway_result = (
        gateway.submit_trade_proposal(
            agent_id=agent.agent_id,
            credential_secret=agent.credential_secret,
            proposal=decision,
            now=now,
        )
        if isinstance(decision, TradeProposal)
        else gateway.submit_no_trade(
            agent_id=agent.agent_id,
            credential_secret=agent.credential_secret,
            decision=decision,
            now=now,
        )
    )
    print(
        json.dumps(
            {
                "gateway_accepted": gateway_result.accepted,
                "outcome_id": str(gateway_result.outcome_id),
            }
        )
    )
    if not gateway_result.accepted:
        return None

    features = build_agent_context_evidence(
        symbol=snapshot.symbol,
        computed_at_utc=bundle.issued_at_utc,
        market_snapshot_id=snapshot.snapshot_id,
        instrument_spec_version=spec.spec_version,
        session_state=snapshot.session_state,
        data_quality=snapshot.data_quality,
    )
    intent = gateway_result.trade_intent if isinstance(decision, TradeProposal) else None
    decision_path_result = evaluate_agent_trade_intent(
        intent,
        outcome_id=gateway_result.outcome_id,
        strategy_version=assignment.strategy_artifact_hash,
        snapshot=snapshot,
        spec=spec,
        features=features,
        config=config,
        portfolio_state=portfolio_provider,
        session_store=runtime.session_store,
        risk_ledger_lock=runtime.risk_ledger_lock,
        kill_switch=runtime.kill_switch,
        recorder=recorder,
        environment=config.environment,
        code_commit=code_commit,
        now=now,
        incident_status=IncidentStatus.CLEAR,
        proposal=decision if isinstance(decision, TradeProposal) else None,
        external_supervisor=external_supervisor,
    )
    capsule_id: UUID = decision_path_result.capsule.capsule_id
    print(json.dumps({"sealed_capsule_id": str(capsule_id)}))
    return capsule_id


if __name__ == "__main__":
    raise SystemExit(main())
