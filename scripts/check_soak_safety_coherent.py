"""Read-only pre-flight check: is the soak's safety state coherently RUNNING
on both backing stores?

Used by the Host Auto-Recovery supervisor (`scripts/host_supervisor.ps1`)
immediately before starting PAPER_LITE. PAPER_LITE's own `CompositeSafetyStateStore`
is dual-backed (PostgreSQL + a local file latch); this script checks both
independently and requires them to *agree* on `RUNNING`, not just each read
without error. If either store is unreadable, either reports anything other
than RUNNING, or the two disagree, this exits non-zero and prints exactly why
-- the supervisor must then leave PAPER_LITE stopped rather than guess.

Never writes anything. Never resets, initializes, or clears a HALT -- that
stays an exclusively human, `--initialize-paper-safety`-gated action, deliberately
not reachable from here.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg


def _file_latch_state(path: Path) -> str | None:
    if not path.exists():
        print(f"file latch missing: {path}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"file latch unreadable: {error}")
        return None
    state = payload.get("state")
    if not isinstance(state, str):
        print(f"file latch has no usable 'state' field: {payload!r}")
        return None
    return state


def _postgres_state(database_url: str) -> str | None:
    # CRUMBLR_DATABASE_URL carries the SQLAlchemy dialect form
    # (`postgresql+psycopg://...`, see `persistence/engine.py`); the raw
    # `psycopg` driver used here only understands the plain `postgresql://`
    # scheme, so the `+psycopg` (or any other `+driver`) suffix is stripped
    # before connecting. This mirrors what SQLAlchemy's own dialect parsing
    # does internally -- it is not a different connection target.
    scheme, _, rest = database_url.partition("://")
    dsn = f"{scheme.split('+', 1)[0]}://{rest}" if "+" in scheme else database_url
    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn, conn.cursor() as cur:
            cur.execute(
                "select state from safety_state_events order by recorded_at_utc desc limit 1"
            )
            row = cur.fetchone()
    except psycopg.Error as error:
        print(f"Postgres safety_state_events unreadable: {error}")
        return None
    if row is None:
        print("Postgres safety_state_events has no rows at all")
        return None
    return str(row[0])


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_soak_safety_coherent.py <safety_latch.json path> <DATABASE_URL>")
        return 2
    latch_path = Path(sys.argv[1])
    database_url = sys.argv[2]

    file_state = _file_latch_state(latch_path)
    pg_state = _postgres_state(database_url)

    if file_state is None or pg_state is None:
        print("SAFETY_STATE_UNKNOWN: could not read one or both stores")
        return 1
    if file_state != pg_state:
        print(f"SAFETY_STATE_INCOHERENT: file={file_state!r} postgres={pg_state!r}")
        return 1
    if file_state != "RUNNING":
        print(f"SAFETY_STATE_NOT_RUNNING: both stores agree on {file_state!r}")
        return 1

    print("SAFETY_STATE_COHERENT_RUNNING")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
