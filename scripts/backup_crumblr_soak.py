"""Stage-C backup gate: dump crumblr_soak, restore-verify it, apply retention.

    uv run python scripts/backup_crumblr_soak.py

Intended hourly cadence (not yet scheduled -- no Scheduled Task registers
this). Every run: dumps `crumblr_soak` (custom-format `pg_dump`, via
`docker exec` since no local client tools are installed), restores the
dump into the dedicated `crumblr_backup_verify` scratch database, and
confirms the restore is genuinely usable (exact Alembic revision, expected
tables, the provisioned runtime assignment present) before marking the
dump verified. Retention (keep the newest 72 verified dumps) runs only
after a successful, verified backup -- a failed run leaves every existing
verified backup untouched and deletes nothing.

The database credential is read once from Windows Credential Manager
(`CRUMBLR_DATABASE_URL`) and never appears in a command-line argument, a
log line, a filename, or a config file -- see `crumblr.local_admin.backup`'s
own module docstring for exactly how it reaches `pg_dump`/`pg_restore`.

Does not touch PAPER_LITE's runtime state (journal, safety latch, the
provisioned assignment) at all -- this only reads `crumblr_soak` (a dump
is a read-only operation against its source) and manages its own scratch
database and backup files.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from crumblr.local_admin import credential_store
from crumblr.local_admin.backup import (
    BackupContext,
    apply_retention,
    create_verified_backup,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    url = credential_store.read("CRUMBLR_DATABASE_URL")
    if not url:
        print(
            "error: CRUMBLR_DATABASE_URL is not configured in Credential Manager",
            file=sys.stderr,
        )
        return 2

    parsed = make_url(url)
    username, password = parsed.username, parsed.password
    if not username or not password:
        print("error: CRUMBLR_DATABASE_URL has no username/password component", file=sys.stderr)
        return 2

    context = BackupContext(database_url=url, username=username, password=password)
    result = create_verified_backup(context)

    if not result.ok:
        print(f"BACKUP_BLOCKED: {result.detail}")
        if result.dump_path is not None:
            print(f"  dump left on disk, unverified: {result.dump_path}")
        print("  no retention deletion performed")
        return 1

    print(f"BACKUP_VERIFIED: {result.dump_path}")
    deleted = apply_retention(context.backup_directory)
    if deleted:
        print(f"retention: removed {len(deleted)} older verified backup(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
