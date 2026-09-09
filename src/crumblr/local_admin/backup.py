"""Stage-C PostgreSQL backup gate for `crumblr_soak` (post-incident, 2026-09-09).

Every dump this module creates is restore-verified before it is trusted:
dump `crumblr_soak` -> restore into a dedicated, exact-name scratch
database -> confirm the restore is readable, at the exact expected Alembic
revision, has the expected tables, and carries the exact provisioned
runtime assignment -- only then is the dump marked verified and eligible
for retention accounting. A dump that fails any of that is left on disk,
unmarked, and retention never touches it or any existing good backup
(fail-closed: "on any dump/restore/read verification failure... keep
existing good backups, perform no retention deletion").

Two guards, both exact-name allowlists, never substring/pattern matching
(mirrors `crumblr.persistence.engine.require_disposable_test_database`,
the same discipline the 2026-09-09 incident hotfix (37a9190) established):

- `require_exact_source_database` -- the backup source must be exactly
  `crumblr_soak`, nothing that merely looks like it.
- `require_scratch_database` -- every DESTRUCTIVE operation this module
  performs (DROP DATABASE / CREATE DATABASE) targets only a name on
  `SCRATCH_DATABASE_ALLOWLIST`. `crumblr_soak`, the ordinary `crumblr`,
  and every `crumblr_test_*` name are never in that allowlist and can
  never be dropped or recreated by this module.

`pg_dump`/`pg_restore` are invoked inside the `crumblr-pg` container via
`docker exec` (no local PostgreSQL client tools are installed on this
host) with the dump piped through stdin/stdout -- never written to a path
inside the container, so no volume mount is required. `DROP DATABASE` /
`CREATE DATABASE` and every verification query use a direct connection
from this process (the same `create_db_engine` path every other script in
this project already uses) against the host-mapped port.

The Postgres password is read once from Windows Credential Manager and
never appears in a command-line argument, a log line, a filename, or a
config file. Where `docker exec` needs it (defensive: this dev container's
default local-socket trust makes this not strictly required today, but a
production Postgres would enforce it), it is passed via `docker exec -e
PGPASSWORD` -- the bare *name*, not `PGPASSWORD=<value>` -- so Docker
forwards the value from this process's own environment into the
container's exec'd process without the value ever appearing in `docker
exec`'s own argv (verified live during this patch: `docker exec -e
SOME_VAR container sh -c 'echo $SOME_VAR'` sees the caller's value with
nothing but the bare name on the visible command line).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import sql
from sqlalchemy import Engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from crumblr.persistence.engine import create_db_engine
from crumblr.persistence.migrations import current_revision
from crumblr.persistence.schema import APPEND_ONLY_TABLES

SOURCE_DATABASE_NAME = "crumblr_soak"
"""The one and only backup source. Never accepted as a parameter with a

default that could be overridden by accident -- every call site that
matters passes this constant explicitly."""

SCRATCH_VERIFY_DATABASE_NAME = "crumblr_backup_verify"

SCRATCH_DATABASE_ALLOWLIST = frozenset({SCRATCH_VERIFY_DATABASE_NAME})
"""Exact names only -- adding a second scratch database name means adding

a second named constant here, never loosening this to a pattern."""

ADMIN_DATABASE_NAME = "postgres"
"""The maintenance database every real PostgreSQL server always has --

DROP DATABASE / CREATE DATABASE for the scratch database connect here,
since a database cannot drop or recreate itself."""

DEFAULT_BACKUP_DIRECTORY = Path(r"C:\CrumblrBackups\crumblr_soak")

RETENTION_MAX_VERIFIED_BACKUPS = 72

REQUIRED_ALEMBIC_REVISION = "8801080869a6"

RUNTIME_ASSIGNMENT_ID = UUID("f98c0396-dd13-4a99-b1e9-b83ea0f15ed7")

PG_CONTAINER = "crumblr-pg"

BACKUP_FILENAME_PREFIX = "crumblr_soak_"
BACKUP_FILENAME_SUFFIX = ".dump"
VERIFIED_MARKER_SUFFIX = ".verified.json"
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_FILENAME_PATTERN = re.compile(
    rf"^{re.escape(BACKUP_FILENAME_PREFIX)}(\d{{8}}T\d{{6}}Z){re.escape(BACKUP_FILENAME_SUFFIX)}$"
)


class BackupSourceMismatchError(RuntimeError):
    """A backup was about to run against something other than crumblr_soak."""


class ScratchDatabaseNotAllowedError(RuntimeError):
    """A destructive operation targeted a database outside the scratch allowlist."""


def require_exact_source_database(name: str) -> None:
    if name != SOURCE_DATABASE_NAME:
        raise BackupSourceMismatchError(
            f"backup source must be exactly {SOURCE_DATABASE_NAME!r}; got {name!r}"
        )


def require_scratch_database(name: str) -> None:
    if name not in SCRATCH_DATABASE_ALLOWLIST:
        raise ScratchDatabaseNotAllowedError(
            f"refusing a destructive scratch-database operation on {name!r}; "
            f"allowed exactly: {sorted(SCRATCH_DATABASE_ALLOWLIST)}"
        )


def backup_filename_for(now: datetime) -> str:
    return f"{BACKUP_FILENAME_PREFIX}{now.strftime(_TIMESTAMP_FORMAT)}{BACKUP_FILENAME_SUFFIX}"


def parse_backup_timestamp(filename: str) -> datetime | None:
    """The UTC timestamp encoded in a dump's filename, or `None` if the

    name does not match this tool's own naming convention exactly --
    retention must never touch a file it cannot positively identify as
    its own."""
    match = _FILENAME_PATTERN.match(filename)
    if not match:
        return None
    return datetime.strptime(match.group(1), _TIMESTAMP_FORMAT).replace(tzinfo=UTC)


def verified_marker_path(dump_path: Path) -> Path:
    return dump_path.with_name(dump_path.name + VERIFIED_MARKER_SUFFIX)


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    detail: str


def verify_restored_database(engine: Engine) -> VerificationResult:
    """Confirm a restored scratch database is genuinely usable: the exact

    expected Alembic revision, the expected table set, and the exact
    provisioned runtime assignment -- not merely "pg_restore exited 0"."""
    try:
        revision = current_revision(engine)
        if revision != REQUIRED_ALEMBIC_REVISION:
            return VerificationResult(
                ok=False,
                detail=f"alembic revision {revision!r} != required {REQUIRED_ALEMBIC_REVISION!r}",
            )

        tables = set(inspect(engine).get_table_names())
        missing_tables = set(APPEND_ONLY_TABLES) - tables
        if missing_tables:
            return VerificationResult(ok=False, detail=f"missing tables: {sorted(missing_tables)}")

        with engine.connect() as connection:
            row = connection.execute(
                text("select 1 from agent_trading_assignments where assignment_id = :id"),
                {"id": RUNTIME_ASSIGNMENT_ID},
            ).first()
        if row is None:
            return VerificationResult(
                ok=False,
                detail=f"runtime assignment {RUNTIME_ASSIGNMENT_ID} not present in restored backup",
            )
    except SQLAlchemyError as error:
        return VerificationResult(ok=False, detail=f"restored database unreadable: {error}")

    return VerificationResult(ok=True, detail="alembic revision, tables, and assignment all match")


def build_pg_dump_command(*, container: str, username: str, database: str) -> list[str]:
    """`docker exec` with `-e PGPASSWORD` as a bare name (passthrough, no

    value on this command line) -- output goes to stdout, the caller
    captures it and writes the host-side file itself, so no volume mount
    into the container is required."""
    return [
        "docker",
        "exec",
        "-e",
        "PGPASSWORD",
        container,
        "pg_dump",
        "--format=custom",
        "-U",
        username,
        "-d",
        database,
    ]


def build_pg_restore_command(*, container: str, username: str, database: str) -> list[str]:
    """`-i` for stdin: the dump bytes are piped in by the caller, never

    written to a path inside the container."""
    return [
        "docker",
        "exec",
        "-i",
        "-e",
        "PGPASSWORD",
        container,
        "pg_restore",
        "--clean",
        "--if-exists",
        "-U",
        username,
        "-d",
        database,
    ]


@dataclass(frozen=True)
class BackupResult:
    ok: bool
    detail: str
    dump_path: Path | None = None
    verification: VerificationResult | None = None


@dataclass(frozen=True)
class BackupContext:
    """Everything a real dump/restore/verify run needs, gathered in one

    place so the orchestration function below takes no bare positional
    secrets and is easy to call correctly from the CLI entrypoint."""

    database_url: str
    username: str
    password: str
    backup_directory: Path = DEFAULT_BACKUP_DIRECTORY
    container: str = PG_CONTAINER
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


def _psycopg_dsn(url: str) -> str:
    # CRUMBLR_DATABASE_URL carries the SQLAlchemy dialect form
    # (`postgresql+psycopg://...`), which a bare psycopg driver cannot
    # parse -- same fix as scripts/check_soak_safety_coherent.py.
    scheme, _, rest = url.partition("://")
    return f"{scheme.split('+', 1)[0]}://{rest}" if "+" in scheme else url


def _url_with_database(url: str, database: str) -> str:
    return str(make_url(url).set(database=database))


def recreate_scratch_database(*, database_url: str, name: str) -> None:
    """DROP then CREATE `name`, guarded by the exact scratch-database

    allowlist. Connects to `ADMIN_DATABASE_NAME`, autocommit (DROP/CREATE
    DATABASE cannot run inside a transaction block), never to `name`
    itself -- a database cannot drop or recreate itself."""
    require_scratch_database(name)
    admin_dsn = _psycopg_dsn(_url_with_database(database_url, ADMIN_DATABASE_NAME))
    with psycopg.connect(admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def create_verified_backup(context: BackupContext) -> BackupResult:
    """Dump `crumblr_soak`, restore it into the scratch database, verify

    the restore, and mark the dump verified only on full success. Never
    touches retention -- that is the caller's separate, explicit step,
    run only after this returns `ok=True`."""
    require_exact_source_database(SOURCE_DATABASE_NAME)

    context.backup_directory.mkdir(parents=True, exist_ok=True)
    final_path = context.backup_directory / backup_filename_for(context.now)
    tmp_path = final_path.with_suffix(final_path.suffix + ".tmp")

    child_env = {**os.environ, "PGPASSWORD": context.password}

    dump_command = build_pg_dump_command(
        container=context.container, username=context.username, database=SOURCE_DATABASE_NAME
    )
    dump_proc = subprocess.run(dump_command, capture_output=True, env=child_env, check=False)
    if dump_proc.returncode != 0:
        detail = dump_proc.stderr[-2000:].decode(errors="replace")
        return BackupResult(ok=False, detail=f"pg_dump failed: {detail}")
    if not dump_proc.stdout:
        return BackupResult(ok=False, detail="pg_dump produced an empty backup")

    # Written under a temp name and only renamed to the final name once
    # pg_dump has fully succeeded -- "the file exists under its real name"
    # implies "pg_dump exited 0 with real content", never a partial write.
    tmp_path.write_bytes(dump_proc.stdout)
    tmp_path.replace(final_path)

    try:
        recreate_scratch_database(
            database_url=context.database_url, name=SCRATCH_VERIFY_DATABASE_NAME
        )
    except (psycopg.Error, ScratchDatabaseNotAllowedError) as error:
        return BackupResult(
            ok=False, detail=f"could not prepare scratch database: {error}", dump_path=final_path
        )

    restore_command = build_pg_restore_command(
        container=context.container,
        username=context.username,
        database=SCRATCH_VERIFY_DATABASE_NAME,
    )
    restore_proc = subprocess.run(
        restore_command, input=dump_proc.stdout, capture_output=True, env=child_env, check=False
    )
    if restore_proc.returncode != 0:
        detail = restore_proc.stderr[-2000:].decode(errors="replace")
        return BackupResult(ok=False, detail=f"pg_restore failed: {detail}", dump_path=final_path)

    scratch_url = _url_with_database(context.database_url, SCRATCH_VERIFY_DATABASE_NAME)
    engine = create_db_engine(scratch_url)
    try:
        verification = verify_restored_database(engine)
    finally:
        engine.dispose()

    if not verification.ok:
        return BackupResult(
            ok=False, detail=verification.detail, dump_path=final_path, verification=verification
        )

    verified_marker_path(final_path).write_text(
        json.dumps(
            {"verified_at_utc": datetime.now(UTC).isoformat(), "detail": verification.detail},
            indent=2,
        ),
        encoding="utf-8",
    )
    return BackupResult(
        ok=True,
        detail="dump created and restore-verified",
        dump_path=final_path,
        verification=verification,
    )


def select_verified_backups(directory: Path) -> list[Path]:
    """Every dump in `directory` matching this tool's own naming

    convention AND carrying a `.verified.json` marker, newest first. A
    dump that failed verification (no marker) or a stray unrelated file
    is never returned -- retention only ever sees what this list contains."""
    candidates: list[tuple[datetime, Path]] = []
    for path in directory.glob(f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}"):
        timestamp = parse_backup_timestamp(path.name)
        if timestamp is None:
            continue
        if not verified_marker_path(path).exists():
            continue
        candidates.append((timestamp, path))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return [path for _, path in candidates]


def select_files_to_delete_for_retention(
    directory: Path, *, retain_max: int = RETENTION_MAX_VERIFIED_BACKUPS
) -> list[Path]:
    """The verified dumps beyond the newest `retain_max` -- pure selection,

    no filesystem mutation. Called on its own by tests; `apply_retention`
    below is the only function that actually deletes anything."""
    return select_verified_backups(directory)[retain_max:]


def apply_retention(
    directory: Path, *, retain_max: int = RETENTION_MAX_VERIFIED_BACKUPS
) -> list[Path]:
    """Deletes only what `select_files_to_delete_for_retention` names --

    a verified dump beyond the retention window, and its own marker.
    Never called by `create_verified_backup` itself; the CLI entrypoint
    calls this only after a backup run returns `ok=True`, so a failed run
    never reaches retention at all."""
    to_delete = select_files_to_delete_for_retention(directory, retain_max=retain_max)
    deleted = []
    for dump_path in to_delete:
        verified_marker_path(dump_path).unlink(missing_ok=True)
        dump_path.unlink(missing_ok=True)
        deleted.append(dump_path)
    return deleted
