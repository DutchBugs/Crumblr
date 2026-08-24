"""Run a deterministic replay and print what the platform did.

    uv run python scripts/run_replay.py
    uv run python scripts/run_replay.py --chaos --bars 1200

The market data is synthetic (see `crumblr.market_data.synthetic`), so any
profit or loss reported here is a property of the random seed and nothing else.
What the run does demonstrate is the control flow: which decisions were made,
which were refused, by which component, and for which recorded reason.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from crumblr.application.bootstrap import DurableRuntime, build_durable_runtime
from crumblr.application.orchestration import ReplayOrchestrator, RunResult
from crumblr.config import PlatformConfig, load_config
from crumblr.domain.enums import Environment
from crumblr.domain.models import InstrumentSpec
from crumblr.market_data.synthetic import (
    REPLAY_EPOCH,
    FaultInjection,
    SyntheticMarketConfig,
    generate_ticks,
)
from crumblr.mt5_gateway.simulated import SimulatedBroker

REPO_ROOT = Path(__file__).resolve().parent.parent


def build_instrument_spec(broker_symbol: str = "EURUSD") -> InstrumentSpec:
    """A conventional 5-digit EUR/USD specification.

    At M1 this comes from `symbol_info` on the live terminal instead. The
    shape is identical, which is the point of the instrument registry.
    """
    return InstrumentSpec(
        canonical_symbol="EUR/USD",
        broker_symbol=broker_symbol,
        currency_base="EUR",
        currency_profit="USD",
        contract_size=Decimal("100000"),
        digits=5,
        point=Decimal("0.00001"),
        tick_size=Decimal("0.00001"),
        tick_value=Decimal("1"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
        stops_level=10,
        freeze_level=0,
        filling_modes=("IOC", "FOK"),
        trade_mode="FULL",
        captured_at_utc=REPLAY_EPOCH,
    )


def run(
    config: PlatformConfig,
    *,
    bars: int,
    seed: int,
    chaos: bool,
    starting_balance: Decimal,
    wrong_server: bool = False,
    runtime: DurableRuntime | None = None,
) -> RunResult:
    spec = build_instrument_spec()
    faults = (
        FaultInjection(spread_spike_every=37, stale_tick_every=53, suspect_quality_every=71)
        if chaos
        else FaultInjection()
    )
    market = SyntheticMarketConfig(seed=seed, bar_count=bars, faults=faults)

    broker = SimulatedBroker(
        spec,
        starting_balance=starting_balance,
        # Matching the configured server is what lets the account guard pass;
        # change either side and the risk engine halts on WRONG_ACCOUNT.
        server=("SomeOtherBroker-Live" if wrong_server else config.account_guard.expected_server),
        account_currency="EUR",
    )
    ticks = list(generate_ticks(market, spec))
    orchestrator = ReplayOrchestrator(
        config,
        spec,
        broker,
        starting_equity=starting_balance,
        # Without a runtime these default to the forgetful implementations,
        # which is what the determinism gate and a quick look at the flow
        # want. With one, the same run leaves an audit trail behind.
        recorder=runtime.recorder if runtime else None,
        kill_switch=runtime.kill_switch if runtime else None,
        session_store=runtime.session_store if runtime else None,
    )
    return orchestrator.run(ticks)


def _bar(count: int, total: int, width: int = 28) -> str:
    if total <= 0:
        return ""
    filled = round(width * count / total)
    return "█" * filled + "·" * (width - filled)


def _money(value: Decimal) -> str:
    return f"{value:>12,.2f}"


def _print_breakdown(title: str, counts: dict[str, int], total: int) -> None:
    if not counts:
        return
    print(f"\n  {title}")
    for reason, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        share = f"{100 * count / total:5.1f}%" if total else "     "
        print(f"    {reason:<34} {count:>6}  {share}  {_bar(count, total, 18)}")


def report(result: RunResult, *, config: PlatformConfig) -> None:
    tally = result.tally

    print("\n" + "=" * 78)
    print("  CRUMBLR — deterministic replay (synthetic market data)")
    print("=" * 78)
    print(f"  environment      {config.environment.value}")
    print(f"  config version   {config.config_version[:16]}")
    print(f"  symbols          {', '.join(config.enabled_symbols())}")
    print(f"  demo required    {config.account_guard.require_demo_account}")

    print("\n  DECISION FLOW (build.md §3)")
    print("  " + "-" * 74)
    rows = [
        ("windows observed", tally.windows),
        ("  warming up (no features yet)", tally.features_unavailable),
        ("  NO_TRADE", tally.no_trade),
        ("  intents proposed", tally.intents),
        ("    risk PASS", tally.risk_passed),
        ("    risk BLOCK", tally.risk_blocked),
        ("    risk HALT", tally.risk_halted),
        ("      supervisor APPROVE", tally.supervisor_approved),
        ("      supervisor VETO", tally.supervisor_vetoed),
        ("      supervisor HALT", tally.supervisor_halted),
        ("        order_check rejected", tally.order_check_rejected),
        ("        orders filled", tally.orders_filled),
    ]
    for label, count in rows:
        print(f"  {label:<38} {count:>7}  {_bar(count, tally.windows)}")

    _print_breakdown("NO_TRADE reasons (agent)", tally.no_trade_reasons, tally.no_trade)
    _print_breakdown("Risk refusals", tally.risk_reasons, tally.intents)
    _print_breakdown("Supervisor refusals", tally.supervisor_reasons, tally.intents)
    if tally.injected_faults:
        _print_breakdown("Injected faults", tally.injected_faults, tally.windows)

    print("\n  EXECUTION")
    print("  " + "-" * 74)
    trades = result.closed_trades
    wins = [t for t in trades if t.profit > 0]
    losses = [t for t in trades if t.profit <= 0]
    print(f"  closed trades    {len(trades):>7}   wins {len(wins)}   losses {len(losses)}")
    if trades:
        avg_slip = sum(t.entry_slippage_points for t in trades) / len(trades)
        stops = sum(1 for t in trades if t.exit_reason == "stop_loss")
        targets = sum(1 for t in trades if t.exit_reason == "take_profit")
        print(f"  exits            {stops:>7} stop-loss, {targets} take-profit")
        print(f"  avg entry slip   {avg_slip:>7.1f} points")

    print("\n  ACCOUNT")
    print("  " + "-" * 74)
    print(f"  starting equity {_money(result.starting_equity)}")
    print(f"  final equity    {_money(result.final_equity)}")
    print(f"  net             {_money(result.net_profit)}")
    print(f"  peak equity     {_money(result.peak_equity)}")
    print(f"  max drawdown    {result.max_drawdown_fraction * 100:>11.2f}%")

    print("\n  CONTROL STATE")
    print("  " + "-" * 74)
    if result.halted:
        reasons = ", ".join(r.value for r in result.halt_reasons)
        print(f"  KILL SWITCH      HALTED — {reasons}")
        if result.halt_detail:
            print(f"  detail           {result.halt_detail}")
        print("  reset            requires an operator and an incident note")
    else:
        print("  KILL SWITCH      RUNNING")
    print(f"  capsules sealed  {len(result.capsules):>7}  (one per evaluated window)")
    if result.uncalibrated_checks:
        names = ", ".join(result.uncalibrated_checks)
        count = len(result.uncalibrated_checks)
        print(f"  SUPERVISOR       {count} check(s) NOT IN FORCE: {names}")
        print("                   an approval above does not mean these passed")

    print("\n  Synthetic data: any P&L above is an artefact of the seed, not an edge.")
    print("=" * 78 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=900, help="number of bars to replay")
    parser.add_argument("--seed", type=int, default=20260817, help="market generator seed")
    parser.add_argument(
        "--chaos",
        action="store_true",
        help="inject spread spikes, stale ticks and suspect data (build.md §20)",
    )
    parser.add_argument(
        "--balance", type=Decimal, default=Decimal("10000"), help="starting balance"
    )
    parser.add_argument(
        "--max-daily-loss",
        type=Decimal,
        default=None,
        help=(
            "override the configured daily-loss gate, as a fraction of equity. "
            "Use a small value (e.g. 0.0005) to watch the kill switch trip."
        ),
    )
    parser.add_argument(
        "--wrong-server",
        action="store_true",
        help="point the broker at an unexpected server, to watch the account guard halt",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help=(
            "write the run to PostgreSQL and recover safety and risk-session "
            "state from it. Needs CRUMBLR_DATABASE_URL or the local development "
            "database; see HANDOVER.md §2"
        ),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=REPO_ROOT / ".crumblr" / "safety_state.json",
        help="local safety latch, read alongside the journal (ADR-002)",
    )
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="create the tables if they are absent. Development only — see D-029",
    )
    parser.add_argument(
        "--operator",
        default=None,
        help=(
            "clear a recorded halt before running, as an operator. Requires "
            "--incident-note. Nothing automatic may do this (build.md §8.2)"
        ),
    )
    parser.add_argument(
        "--incident-note",
        default=None,
        help="why the halt is being cleared. Recorded with the reset",
    )
    args = parser.parse_args()

    config = load_config(Environment.PAPER, config_dir=REPO_ROOT / "config")
    if args.max_daily_loss is not None:
        config = config.model_copy(
            update={"risk": config.risk.model_copy(update={"max_daily_loss": args.max_daily_loss})}
        )

    runtime = _open_runtime(config, args) if args.persist else None
    try:
        result = run(
            config,
            bars=args.bars,
            seed=args.seed,
            chaos=args.chaos,
            starting_balance=args.balance,
            wrong_server=args.wrong_server,
            runtime=runtime,
        )
        report(result, config=config)
        if runtime is not None:
            print(
                f"  Persisted: {runtime.recorder.events_written} events, "
                f"{runtime.recorder.capsules_written} capsules, "
                f"{runtime.recorder.ticks_written} ticks, "
                f"{runtime.recorder.bars_written} bars "
                f"→ {args.state_file}\n"
            )
    finally:
        if runtime is not None:
            runtime.dispose()


def _open_runtime(config: PlatformConfig, args: argparse.Namespace) -> DurableRuntime:
    """Open the durable runtime, and arm it only if an operator says so.

    A halted system is left halted. The run still happens — observing and
    refusing is a legitimate thing to watch — and the report says so.
    """
    runtime = build_durable_runtime(
        environment=config.environment,
        state_file=args.state_file,
        create_schema=args.create_schema,
    )
    if runtime.kill_switch.is_halted:
        if args.operator and args.incident_note:
            runtime.kill_switch.reset(operator=args.operator, incident_note=args.incident_note)
        else:
            print(
                f"\n  Recorded safety state is {runtime.kill_switch.state.value}: "
                f"{runtime.kill_switch.startup_detail or 'no detail recorded'}\n"
                "  New orders stay disabled. Clear it with "
                "--operator NAME --incident-note NOTE."
            )
    return runtime


if __name__ == "__main__":
    main()
