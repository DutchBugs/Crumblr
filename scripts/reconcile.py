"""Run read-only reconciliation against the latest broker-state snapshot.

    uv run python scripts/reconcile.py

Compares the most recently captured `BrokerAccountSnapshot`/positions/
pending orders (F-047, written by `scripts/mt5_live_reader.py`) against what
the platform currently expects — before an execution path exists, that is
simply "flat" (`ExpectedState.flat()`; review 1.16 §8). Prints
`MATCHED`/`MISMATCHED`/`UNKNOWN` and every reason behind that verdict.

**Read-only, database-only.** This never imports `MetaTrader5` and never
opens an MT5 connection — it only reads PostgreSQL through
`persistence.broker_state.BrokerStateStore`, the same boundary
`scripts/run_dashboard.py` holds. Point `CRUMBLR_DATABASE_URL` at whichever
database `mt5_live_reader.py` has been writing broker-state snapshots into.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

from crumblr.application.reconciliation import DEFAULT_MAX_SNAPSHOT_AGE, ExpectedState, reconcile
from crumblr.config import load_config
from crumblr.domain.enums import Environment
from crumblr.domain.timeutils import utc_now
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, create_db_engine, database_url

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-symbol", default="EUR/USD")
    parser.add_argument("--environment", default=Environment.PAPER.value)
    parser.add_argument(
        "--max-snapshot-age",
        type=float,
        default=DEFAULT_MAX_SNAPSHOT_AGE.total_seconds(),
        help="seconds — a snapshot older than this reads as UNKNOWN, not MATCHED",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args()

    try:
        url = database_url()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        print(
            f"set {DATABASE_URL_ENV_VAR} to the database mt5_live_reader.py writes to",
            file=sys.stderr,
        )
        return 2

    config = load_config(Environment(args.environment), config_dir=REPO_ROOT / "config")
    engine = create_db_engine(url)
    store = BrokerStateStore(engine)
    expectation = ExpectedState.flat(config.account_guard, canonical_symbol=args.canonical_symbol)

    result = reconcile(
        store,
        expectation,
        now=utc_now(),
        max_snapshot_age=timedelta(seconds=args.max_snapshot_age),
    )
    engine.dispose()

    if args.json:
        print(json.dumps(result.to_payload(), indent=2))
    else:
        print(f"\n  {result.status.value}")
        if result.snapshot_id is not None:
            print(f"  snapshot_id: {result.snapshot_id}")
        for reason in result.reasons:
            print(f"    - {reason}")
        print()

    return 0 if result.status.value == "MATCHED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
