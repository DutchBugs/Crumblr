"""Deliberately reset the soak database, staying on the Alembic path (F-041).

    uv run python scripts/reset_soak_database.py --yes

Review 1.12 F-041: the third real Phase A attempt was recovered by calling
`bootstrap_schema()` (`metadata.create_all`) against `crumblr_soak` after its
tables had been dropped by hand, while the database's `alembic_version` row
was left untouched. `alembic upgrade head` then believed the database was
already current and did nothing, leaving the tables missing. That worked as
an emergency fix but is exactly the two-paths-disagreeing failure mode
`migrations.py`'s own docstring warns about: `create_all` is for tests,
`alembic` is the durable, versioned path, and a real reset must not mix them.

This script does the coherent thing instead: `alembic downgrade base` (which
walks the migrations backwards, so `alembic_version` and the tables move
together) followed by `alembic upgrade head`. `tests/integration/test_migrations.py
::TestTheBaselineBuildsWhatTheCodeExpects::test_the_baseline_can_be_unwound`
already proves that round trip leaves nothing behind — this script is that
same proven pair, run deliberately against the soak database instead of a
disposable test one.

**Not for the shared development/test database.** Refuses to run unless
`CRUMBLR_DATABASE_URL` (or `--url`) points at a database whose name contains
"soak", so a stray invocation cannot wipe `crumblr` out from under the test
suite — the same class of mistake D-042 already cost a real soak attempt.
"""

from __future__ import annotations

import argparse
import sys

from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, create_db_engine, database_url
from crumblr.persistence.migrations import current_revision, downgrade_to_base, upgrade_to_head


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=None,
        help=f"database URL to reset; defaults to ${DATABASE_URL_ENV_VAR}",
    )
    parser.add_argument(
        "--yes", action="store_true", help="required: acknowledges this destroys the database"
    )
    args = parser.parse_args()

    try:
        url = args.url or database_url()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if "soak" not in url:
        print(
            "error: this URL does not look like a soak database (no 'soak' in it) — "
            "refusing to reset it. Pass --url explicitly if this is intentional.",
            file=sys.stderr,
        )
        return 2

    if not args.yes:
        print(
            "This drops and rebuilds every table in the target database via "
            "alembic downgrade base -> upgrade head. Re-run with --yes to proceed.",
            file=sys.stderr,
        )
        return 2

    print(f"Resetting via Alembic (downgrade base -> upgrade head): {url.split('@')[-1]}")
    downgrade_to_base(url)
    upgrade_to_head(url)

    engine = create_db_engine(url)
    try:
        print(f"Done. Current revision: {current_revision(engine) or '<none>'}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
