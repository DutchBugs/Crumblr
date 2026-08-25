"""Run the continuous MT5 reader against a real terminal (review 1.9 §5).

    uv run python scripts/mt5_live_reader.py --duration 1800
    uv run python scripts/mt5_live_reader.py --max-iterations 20 --json var/live_reader_health.json

This is the soak-test tool, not a one-shot check like `mt5_probe.py`. It
connects, reads real EUR/USD ticks and M5 bars, persists them to PostgreSQL,
and keeps doing that — reconnecting and fully revalidating the account and
symbol on every reconnect — until told to stop. Ctrl+C stops it cleanly and
prints the final health.

**Still read-only.** `application.live_reader.LiveReader` holds a
`ReadOnlyMt5Gateway`; there is no order interface reachable from this file,
the same guarantee `mt5_probe.py` carries.

**A deliberate interruption** — closing the terminal, unplugging the network,
whatever the operator chooses — is how review 1.9 §5's soak-test requirement
gets exercised. Run this, cause one interruption partway through, and confirm
in the printed status that the reader noticed, reconnected and revalidated
rather than silently continuing.

The health snapshot this script can write with `--json` carries no
credential-shaped field — `ReaderHealth.to_payload()` was written for exactly
this — so unlike the raw probe output, it is safe to attach anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

from crumblr.application.live_reader import (
    BrokerStateHealth,
    LiveReader,
    ReaderHealth,
    ReaderStatus,
)
from crumblr.config import load_config
from crumblr.domain.enums import Environment
from crumblr.mt5_gateway.client import (
    MissingCredentialsError,
    Mt5Client,
    read_credentials,
)
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, DEFAULT_TEST_URL, create_db_engine
from crumblr.persistence.market_data import MarketDataStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_health_snapshot(
    path: Path, health: ReaderHealth, broker_state_health: BrokerStateHealth
) -> None:
    """Write the snapshot atomically, so a concurrent reader never sees a

    half-written file — `os.replace` is atomic on both POSIX and Windows.
    Review 1.16 F-050: broker-state health is nested under its own key
    rather than merged into `ReaderHealth`'s payload — two concepts, not one
    overloaded status.
    """
    payload = {**health.to_payload(), "broker_state": broker_state_health.to_payload()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _print_status(
    iteration: int, health: ReaderHealth, broker_state_health: BrokerStateHealth
) -> None:
    print(
        f"  [{iteration:>4}] {health.status.value:<12} "
        f"connected={health.connected!s:<5} "
        f"reconnects={health.reconnect_count} "
        f"failures={health.consecutive_failures} "
        f"last_tick={health.last_tick_at_utc.isoformat() if health.last_tick_at_utc else '-'} "
        f"last_bar={health.last_bar_at_utc.isoformat() if health.last_bar_at_utc else '-'}"
        + (f"  -- {health.detail}" if health.detail else "")
    )
    snapshot_at = broker_state_health.last_snapshot_at_utc
    positions_state = broker_state_health.position_set_state
    pending_orders_state = broker_state_health.pending_order_set_state
    print(
        f"         broker_state last_snapshot={snapshot_at.isoformat() if snapshot_at else '-'} "
        f"positions={positions_state.value if positions_state else '-'} "
        f"pending_orders={pending_orders_state.value if pending_orders_state else '-'}"
        + (f"  -- {broker_state_health.last_error}" if broker_state_health.last_error else "")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-symbol", default="EUR/USD")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument(
        "--poll-interval", type=float, default=5.0, help="seconds between polls while healthy"
    )
    parser.add_argument(
        "--stale-after",
        type=float,
        default=60.0,
        help="seconds of silence before the reader reports STALE",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="stop after this many polls; omit together with --duration to run until Ctrl+C",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="stop after roughly this many seconds (converted to --max-iterations)",
    )
    parser.add_argument("--environment", default=Environment.PAPER.value)
    parser.add_argument(
        "--broker-state-interval",
        type=float,
        default=60.0,
        help=(
            "seconds between broker-state captures (review 1.15 F-047) — account "
            "balance/equity/margin and open positions/pending orders, persisted "
            "durably rather than held only in memory. Also captured on every "
            "reconnect regardless of this interval"
        ),
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help=(
            "write the health snapshot as JSON after every poll, not only at exit — "
            "no account number in it (F-031). Dashboard v0 (review 1.12 §8) reads this "
            "file to show LiveReader health from a separate process, without importing "
            "MetaTrader5 or touching credentials itself"
        ),
    )
    args = parser.parse_args()

    config = load_config(Environment(args.environment), config_dir=REPO_ROOT / "config")

    try:
        credentials = read_credentials()
    except MissingCredentialsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    database_url = os.environ.get(DATABASE_URL_ENV_VAR)
    if not database_url:
        print(
            f"error: {DATABASE_URL_ENV_VAR} is not set. This script writes real broker data and "
            f"must not silently fall back to the shared development/test database "
            f"({DEFAULT_TEST_URL!r}) — tests/integration drops that schema at teardown (D-042). "
            f"Point {DATABASE_URL_ENV_VAR} at a database dedicated to real-terminal runs, e.g. "
            f"...:55432/crumblr_soak (same server, separate database, own migrations).",
            file=sys.stderr,
        )
        return 2

    max_iterations = args.max_iterations
    if args.duration is not None and max_iterations is None:
        max_iterations = max(1, int(args.duration / max(args.poll_interval, 0.1)))

    engine = create_db_engine(database_url)
    store = MarketDataStore(engine)
    broker_state_store = BrokerStateStore(engine)
    reader = LiveReader(
        Mt5Client(),
        credentials,
        config.account_guard,
        store,
        canonical_symbol=args.canonical_symbol,
        timeframe=args.timeframe,
        poll_interval=timedelta(seconds=args.poll_interval),
        stale_after=timedelta(seconds=args.stale_after),
        terminal_path=os.environ.get("CRUMBLR_MT5_TERMINAL_PATH") or None,
        environment=Environment(args.environment),
        broker_state_store=broker_state_store,
        broker_state_interval=timedelta(seconds=args.broker_state_interval),
    )

    print("\n" + "=" * 78)
    print("  MT5 CONTINUOUS READER — read-only, review 1.9 F-034")
    print("=" * 78)
    print(f"  symbol={args.canonical_symbol} timeframe={args.timeframe} ")
    print(f"  poll_interval={args.poll_interval}s stale_after={args.stale_after}s")
    print(
        f"  stopping after {max_iterations} polls"
        if max_iterations is not None
        else "  running until Ctrl+C"
    )
    print("  Ctrl+C to stop cleanly.\n")

    backoff = timedelta(seconds=5)
    max_backoff = timedelta(minutes=5)
    iteration = 0
    try:
        while max_iterations is None or iteration < max_iterations:
            health = reader.poll_once()
            _print_status(iteration, health, reader.broker_state_health)
            if args.json is not None:
                _write_health_snapshot(args.json, health, reader.broker_state_health)
            if health.status is ReaderStatus.UNHEALTHY:
                print(
                    "\n  UNHEALTHY — this does not clear itself. Investigate, then call "
                    "LiveReader.acknowledge(operator=..., note=...) — not available from "
                    "this CLI by design; restart the script once the cause is understood.\n"
                )
                break
            if health.status is ReaderStatus.DISCONNECTED:
                time.sleep(backoff.total_seconds())
                backoff = min(backoff * 2, max_backoff)
            else:
                backoff = timedelta(seconds=5)
                time.sleep(args.poll_interval)
            iteration += 1
    except KeyboardInterrupt:
        print("\n  Interrupted — stopping.\n")

    final = reader.health
    final_broker_state = reader.broker_state_health
    print("\n" + "-" * 78)
    print("  Final health")
    print("-" * 78)
    for key, value in final.to_payload().items():
        print(f"    {key:<24} {value}")
    print("  broker_state:")
    for key, value in final_broker_state.to_payload().items():
        print(f"    {key:<24} {value}")
    print()

    if args.json is not None:
        _write_health_snapshot(args.json, final, final_broker_state)
        print(f"  Health snapshot written to {args.json}\n")

    engine.dispose()
    return 0 if final.status is not ReaderStatus.UNHEALTHY else 1


if __name__ == "__main__":
    raise SystemExit(main())
