"""Run the live/shadow decision pipeline against real persisted state.

    uv run python scripts/live_decision.py --poll-interval 30

Polls for the newest closed real M5 bar (persisted by
`scripts/mt5_live_reader.py`) and, whenever a new one appears, runs one
decision cycle: feature pipeline -> Trading Agent -> intent-time Risk
Engine -> Supervisor -> persist. See `application/live_decision.py` for
what each stage reuses and why this is a separate process from the reader.

**EXECUTION IS DISABLED.** This script never imports `MetaTrader5`, never
opens an MT5 connection, and there is no `ApprovedOrder`/`order_check`/
`order_send` anywhere in `LiveDecisionOrchestrator`'s call graph. Every
decision this process makes stops at the Supervisor's verdict — review
1.16 §9's "STOP HERE FOR SHADOW MODE".

Reads three things through PostgreSQL, never MT5 directly:
    market data          — persisted by mt5_live_reader.py
    broker-state snapshots (F-047) — persisted by mt5_live_reader.py
    the instrument spec   — persisted by mt5_live_reader.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from crumblr.application.bootstrap import build_durable_runtime
from crumblr.application.live_decision import LiveDecisionOrchestrator
from crumblr.config import load_config
from crumblr.domain.enums import Environment
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.decision_window import PostgresDecisionWindowStore
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, DEFAULT_TEST_URL
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.market_data import MarketDataStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-symbol", default="EUR/USD")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument(
        "--poll-interval", type=float, default=30.0, help="seconds between checks for a new bar"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="stop after this many polls; omit to run until Ctrl+C",
    )
    parser.add_argument("--environment", default=Environment.PAPER.value)
    parser.add_argument(
        "--strategy-id",
        default=None,
        help=(
            "override config/paper.yaml's trading_agent.strategy_id for this run only "
            "(e.g. F-051 part 2 evidence with baseline_v1 while real bar history is still "
            "short of ict_v1's warm-up requirement) — never edits the shipped config file"
        ),
    )
    args = parser.parse_args()

    database_url = os.environ.get(DATABASE_URL_ENV_VAR)
    if not database_url:
        print(
            f"error: {DATABASE_URL_ENV_VAR} is not set. Point it at the same database "
            f"mt5_live_reader.py writes to — never the shared test database "
            f"({DEFAULT_TEST_URL!r}), which tests/integration drops at teardown.",
            file=sys.stderr,
        )
        return 2

    environment = Environment(args.environment)
    config = load_config(environment, config_dir=REPO_ROOT / "config")
    if args.strategy_id is not None:
        agent = config.trading_agent.model_copy(update={"strategy_id": args.strategy_id})
        config = config.model_copy(update={"trading_agent": agent})
    runtime = build_durable_runtime(
        environment=environment,
        state_file=REPO_ROOT / "var" / "safety_state.json",
        url=database_url,
    )

    orchestrator = LiveDecisionOrchestrator(
        config,
        market_data=MarketDataStore(runtime.engine),
        broker_state=BrokerStateStore(runtime.engine),
        instrument_specs=InstrumentSpecStore(runtime.engine),
        recorder=runtime.recorder,
        kill_switch=runtime.kill_switch,
        session_store=runtime.session_store,
        risk_ledger_lock=runtime.risk_ledger_lock,
        decision_window_store=PostgresDecisionWindowStore(runtime.engine),
        canonical_symbol=args.canonical_symbol,
        timeframe=args.timeframe,
    )

    print("\n" + "=" * 78)
    print("  LIVE/SHADOW DECISION PIPELINE — review 1.16 F-048")
    print("=" * 78)
    print(f"  symbol={args.canonical_symbol} timeframe={args.timeframe}")
    print(f"  strategy={config.trading_agent.strategy_id} environment={environment.value}")
    print(f"  kill_switch={runtime.kill_switch.state.value}")
    print("  EXECUTION DISABLED — no order path is reachable from this process.")
    print(
        f"  stopping after {args.max_iterations} polls"
        if args.max_iterations is not None
        else "  running until Ctrl+C"
    )
    print("  Ctrl+C to stop cleanly.\n")

    iteration = 0
    try:
        while args.max_iterations is None or iteration < args.max_iterations:
            outcome = orchestrator.decide_once()
            if outcome.skipped:
                print(f"  [{iteration:>4}] skipped — {outcome.skipped_reason}")
            elif outcome.capsule is not None:
                capsule = outcome.capsule
                verdict = (
                    capsule.supervisor_decision.verdict.value
                    if capsule.supervisor_decision is not None
                    else (
                        capsule.risk_decision.verdict.value
                        if capsule.risk_decision is not None
                        else "NO_TRADE"
                    )
                )
                print(
                    f"  [{iteration:>4}] decided — capsule={capsule.capsule_id} verdict={verdict} "
                    f"kill_switch={runtime.kill_switch.state.value}"
                )
            iteration += 1
            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        print("\n  Interrupted — stopping.\n")

    runtime.dispose()
    return 0 if not runtime.kill_switch.is_halted else 1


if __name__ == "__main__":
    raise SystemExit(main())
