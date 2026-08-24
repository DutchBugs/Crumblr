"""Run Dashboard v0 — read-only (review 1.9 F-035, review 1.12 §8).

    uv run python scripts/run_dashboard.py
    uv run python scripts/run_dashboard.py --reader-health var/live_reader_health.json

Serves a single status page plus a `/api/state` JSON endpoint, both reading
PostgreSQL and the `LiveReader` health snapshot file. This process never
imports `MetaTrader5`, never reads MT5 credentials, and registers no route
that can mutate anything — see `src/crumblr/dashboard/__init__.py` for the
boundary and `review/DEVIATIONS.md` D-043 for what is deliberately not in v0.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from crumblr.config import load_config
from crumblr.dashboard.app import create_app
from crumblr.domain.enums import Environment
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, create_db_engine, database_url

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--canonical-symbol", default="EUR/USD")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--environment", default=Environment.PAPER.value)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument(
        "--reader-health",
        type=Path,
        default=REPO_ROOT / "var" / "live_reader_health.json",
        help="path mt5_live_reader.py's --json snapshot is written to",
    )
    args = parser.parse_args()

    try:
        url = database_url()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        print(f"set {DATABASE_URL_ENV_VAR} to the database the reader writes to", file=sys.stderr)
        return 2

    config = load_config(Environment(args.environment), config_dir=REPO_ROOT / "config")
    engine = create_db_engine(url)
    app = create_app(
        engine=engine,
        guard=config.account_guard,
        environment=Environment(args.environment),
        canonical_symbol=args.canonical_symbol,
        timeframe=args.timeframe,
        reader_health_path=args.reader_health,
    )

    print(f"Dashboard v0 (read-only) at http://{args.host}:{args.port}/")
    print(f"  reading database:       {url.split('@')[-1]}")
    print(f"  reading reader health:  {args.reader_health}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")

    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
