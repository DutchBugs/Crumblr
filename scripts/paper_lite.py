"""Run PAPER_LITE from the real read-only market-data stores into paper fills."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from uuid import UUID

from sqlalchemy.engine import make_url

from crumblr.agent_gateway.gateway import AgentGateway
from crumblr.agent_gateway.static_agent_client import StaticAgentClientConfig
from crumblr.application.paper_lite import (
    PaperLiteOrchestrator,
    PaperLiteOutcomeType,
    load_paper_lite_settings,
)
from crumblr.application.paper_lite_agent import HttpPaperLiteTradingAgent
from crumblr.application.recording import JournalRecorder
from crumblr.config import load_config
from crumblr.domain.enums import Environment, IncidentStatus, SessionState
from crumblr.domain.models import InstrumentSpec, MarketSnapshot
from crumblr.domain.money import price_to_points
from crumblr.market_data.synthetic import snapshot_id_for
from crumblr.persistence.agent_gateway import (
    PostgresAgentCredentialStore,
    PostgresAgentDecisionOutcomeStore,
    PostgresAgentIdentityStore,
    PostgresDecisionContextBundleStore,
    PostgresTradingAssignmentStore,
)
from crumblr.persistence.engine import create_db_engine, database_url
from crumblr.persistence.features import FeatureSnapshotStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.market_data import MarketDataStore
from crumblr.persistence.paper_lite import DurablePaperBroker
from crumblr.persistence.risk_session import PostgresRiskSessionStore
from crumblr.persistence.safety_state import CompositeSafetyStateStore, PostgresSafetyStateStore
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.safety_state import FileSafetyStateStore
from crumblr.trading_agent.sessions import is_market_open

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_CREDENTIAL_ENV = "CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL"
AGENT_TOKEN_ENV = "CRUMBLR_PAPER_LITE_AGENT_TOKEN"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", type=UUID, required=True)
    parser.add_argument("--assignment-id", type=UUID, required=True)
    parser.add_argument("--agent-url", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--settings", type=Path, default=REPO_ROOT / "config/paper_lite.yaml")
    parser.add_argument("--symbol", default="EUR/USD")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--confirm-paper-incident-clear", action="store_true")
    parser.add_argument("--initialize-paper-safety", action="store_true")
    parser.add_argument("--operator")
    parser.add_argument("--incident-note")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    # No default URL: the dedicated Dev-3 database must be chosen explicitly.
    configured_database_url = database_url()
    if make_url(configured_database_url).database != "crumblr_test_dev3":
        raise SystemExit(
            "PAPER_LITE requires the dedicated crumblr_test_dev3 database; refusing another target"
        )
    engine = create_db_engine(configured_database_url)
    settings = load_paper_lite_settings(args.settings)
    base = load_config(Environment.PAPER, config_dir=REPO_ROOT / "config")
    config = settings.platform_config(base)
    if not args.confirm_paper_incident_clear:
        raise SystemExit(
            "PAPER_LITE incident status defaults to UNKNOWN; an operator must pass "
            "--confirm-paper-incident-clear after checking the paper integration scope"
        )

    credential = os.getenv(GATEWAY_CREDENTIAL_ENV)
    agent_token = os.getenv(AGENT_TOKEN_ENV)
    if not credential or not agent_token:
        raise SystemExit(
            f"set both {GATEWAY_CREDENTIAL_ENV} and {AGENT_TOKEN_ENV}; secrets are never read "
            "from YAML or CLI arguments"
        )

    assignments = PostgresTradingAssignmentStore(engine)
    assignment = assignments.current(args.assignment_id)
    if assignment is None:
        raise SystemExit(
            f"assignment {args.assignment_id} is not provisioned; see the PAPER_LITE runbook"
        )
    if assignment.allowed_agent_id != args.agent_id:
        raise SystemExit("the provisioned assignment belongs to another Agent")
    if assignment.canonical_symbol != args.symbol or assignment.timeframe != args.timeframe:
        raise SystemExit("runner symbol/timeframe does not match the provisioned assignment")

    specs = InstrumentSpecStore(engine)
    spec = specs.latest(canonical_symbol=args.symbol)
    if spec is None:
        raise SystemExit("no real read-only instrument spec is stored; start mt5_live_reader first")
    market_config = config.market_for(args.symbol)
    if (
        market_config is None
        or market_config.expected_spec_version is None
        or market_config.expected_spec_version != spec.spec_version
    ):
        raise SystemExit(
            "latest real instrument spec does not match the explicitly approved config pin"
        )

    journal_path = _resolve_runtime_path(settings.journal_path)
    safety_path = _resolve_runtime_path(settings.safety_latch_path)
    broker = DurablePaperBroker(
        journal_path,
        spec,
        starting_balance=settings.starting_balance,
        account_currency=settings.account_currency,
        leverage=settings.leverage,
    )
    safety_store = CompositeSafetyStateStore(
        PostgresSafetyStateStore(engine), FileSafetyStateStore(safety_path)
    )
    if args.initialize_paper_safety:
        if not args.operator or not args.incident_note:
            raise SystemExit(
                "--initialize-paper-safety requires --operator and --incident-note; "
                "PAPER_LITE never resets HALT automatically"
            )
        kill_switch = KillSwitch(safety_store)
        kill_switch.reset(operator=args.operator, incident_note=args.incident_note)
    else:
        kill_switch = KillSwitch.on_startup(safety_store)
    if kill_switch.is_halted:
        raise SystemExit(
            "PAPER_LITE safety state is not explicitly RUNNING; initialize/reset it by an "
            "authorized operator with an audit note"
        )

    gateway = AgentGateway(
        identities=PostgresAgentIdentityStore(engine),
        credentials=PostgresAgentCredentialStore(engine),
        assignments=assignments,
        contexts=PostgresDecisionContextBundleStore(engine),
        outcomes=PostgresAgentDecisionOutcomeStore(engine),
        feature_evidence=FeatureSnapshotStore(engine),
    )
    agent = HttpPaperLiteTradingAgent(
        agent_id=args.agent_id,
        gateway_credential_secret=credential,
        client=StaticAgentClientConfig(
            base_url=args.agent_url,
            bearer_token=agent_token,
        ),
    )
    market_data = MarketDataStore(engine)
    orchestrator = PaperLiteOrchestrator(
        config,
        settings=settings,
        assignment=assignment,
        agent=agent,
        gateway=gateway,
        broker=broker,
        recorder=JournalRecorder(engine, environment=Environment.PAPER),
        session_store=PostgresRiskSessionStore(engine),
        kill_switch=kill_switch,
        code_commit=args.code_commit,
    )

    try:
        while True:
            latest_spec = specs.latest(canonical_symbol=args.symbol)
            if latest_spec is None or latest_spec.spec_version != spec.spec_version:
                raise SystemExit(
                    "real instrument spec changed or disappeared during PAPER_LITE; "
                    "review before restart"
                )
            latest = _latest_snapshot(
                market_data,
                spec=spec,
                canonical_symbol=args.symbol,
                timeframe=args.timeframe,
            )
            if latest is None:
                print(json.dumps({"status": "waiting_for_real_read_only_market_data"}))
            else:
                outcome = orchestrator.process(
                    latest,
                    spec,
                    incident_status=IncidentStatus.CLEAR,
                )
                print(
                    json.dumps(
                        {
                            "outcome": outcome.outcome_type.value,
                            "detail": outcome.detail,
                            "paper_equity": str(outcome.portfolio.equity),
                            "paper_balance": str(outcome.portfolio.balance),
                            "paper_unrealized_profit": str(outcome.portfolio.unrealised_profit),
                            "paper_realized_profit": str(outcome.portfolio.realized_profit),
                            "authorized_open_risk_amount": str(
                                outcome.portfolio.authorized_open_risk_amount
                            ),
                            "exact_open_risk_amount": str(outcome.portfolio.exact_open_risk_amount),
                            "exact_open_risk_fraction": str(
                                outcome.portfolio.exact_open_risk_fraction
                            ),
                            "open_positions": outcome.portfolio.open_position_count,
                            "closed_trades": outcome.portfolio.closed_trade_count,
                            "simulated_fill": (
                                outcome.outcome_type is PaperLiteOutcomeType.PAPER_FILLED
                            ),
                        },
                        sort_keys=True,
                    )
                )
            if args.once:
                return
            time.sleep(args.poll_seconds)
    finally:
        engine.dispose()


def _resolve_runtime_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _latest_snapshot(
    market_data: MarketDataStore,
    *,
    spec: InstrumentSpec,
    canonical_symbol: str,
    timeframe: str,
) -> MarketSnapshot | None:
    tick = market_data.latest_tick(canonical_symbol=canonical_symbol)
    bars = market_data.recent_bars(
        canonical_symbol=canonical_symbol,
        timeframe=timeframe,
        limit=400,
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


if __name__ == "__main__":
    main()
