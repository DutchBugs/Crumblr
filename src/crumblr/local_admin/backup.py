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

**Digest binding (Dev 1 BLOCK, 2026-09-09).** A marker used to mean only
"a dump with this filename passed verification at some point" -- nothing
tied it to the *current bytes* of the file. A `.verified.json` marker now
records the dump's SHA-256 and byte size at the moment verification
completed, and `select_verified_backups()` recomputes the hash of the
*current* file on every call and requires it to still match -- a dump
that was later truncated, mutated, or replaced (same name, different
bytes, even the same size) is no longer "verified" the instant its bytes
diverge from what was actually checked, with no separate re-scan step
needed to notice. The hash is also taken twice around the restore/verify
window itself (`create_verified_backup`): once right after the dump is
written, once right after database verification finishes. If those two
disagree, the file changed *during* its own verification and the run
blocks -- no marker is written for a file that cannot be trusted to be
the same bytes that were actually restored and checked.

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

import hashlib
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

MARKER_SCHEMA_VERSION = 1

_MARKER_REQUIRED_FIELD_TYPES: dict[str, type] = {
    "schema_version": int,
    "dump_filename": str,
    "sha256": str,
    "size_bytes": int,
    "verified_at_utc": str,
    "alembic_revision": str,
    "assignment_id": str,
}
"""Every field a marker must carry, and its expected JSON-decoded type --

`bool` is deliberately excluded from `int`-typed fields by checking type
exactly rather than via `isinstance` alone, since Python's `bool` is a
subclass of `int` and `size_bytes: true` would otherwise pass silently."""

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


def sha256_and_size(path: Path) -> tuple[str, int]:
    """The file's SHA-256 hex digest and byte size, read in chunks so an

    arbitrarily large dump never needs to be held in memory whole just to
    hash it."""
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _parse_marker(raw: str) -> dict[str, object] | None:
    """`None` for anything that is not valid JSON, not an object, or

    missing/mistyped one of the required fields -- never partially
    trusted."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    for field_name, field_type in _MARKER_REQUIRED_FIELD_TYPES.items():
        if field_name not in payload:
            return None
        value = payload[field_name]
        # Exact type, not isinstance: bool is an int subclass in Python,
        # so isinstance(True, int) is True -- a marker with
        # "size_bytes": true must not pass an int-typed field check.
        if type(value) is not field_type:
            return None
    return payload


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

    pre_sha256, pre_size = sha256_and_size(final_path)

    try:
        recreate_scratch_database(
            database_url=context.database_url, name=SCRATCH_VERIFY_DATABASE_NAME
        )
    except (psycopg.Error, ScratchDatabaseNotAllowedError) as error:
        return BackupResult(
            ok=False, detail=f"could not prepare scratch database: {error}", dump_path=final_path
        )

    # Restored from the bytes actually sitting on disk right now, not the
    # in-memory copy captured above -- what gets restored, and later
    # digest-checked, is the real persisted artifact, not a snapshot that
    # could theoretically have already diverged from it.
    dump_bytes_on_disk = final_path.read_bytes()

    restore_command = build_pg_restore_command(
        container=context.container,
        username=context.username,
        database=SCRATCH_VERIFY_DATABASE_NAME,
    )
    restore_proc = subprocess.run(
        restore_command,
        input=dump_bytes_on_disk,
        capture_output=True,
        env=child_env,
        check=False,
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

    # The digest re-check that binds the marker to these exact bytes: if
    # the file changed at any point between the two hashes -- during
    # restore, during database verification, from any other process --
    # this refuses to mark it verified rather than trust a snapshot that
    # is no longer provably what was actually checked.
    post_sha256, post_size = sha256_and_size(final_path)
    if post_sha256 != pre_sha256 or post_size != pre_size:
        return BackupResult(
            ok=False,
            detail=(
                "dump file changed during verification -- refusing to mark it verified "
                f"(pre: {pre_size} bytes, sha256 {pre_sha256[:12]}...; "
                f"post: {post_size} bytes, sha256 {post_sha256[:12]}...)"
            ),
            dump_path=final_path,
            verification=verification,
        )

    marker_payload = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "dump_filename": final_path.name,
        "sha256": post_sha256,
        "size_bytes": post_size,
        "verified_at_utc": datetime.now(UTC).isoformat(),
        "alembic_revision": REQUIRED_ALEMBIC_REVISION,
        "assignment_id": str(RUNTIME_ASSIGNMENT_ID),
    }
    marker_path = verified_marker_path(final_path)
    marker_tmp_path = marker_path.with_suffix(marker_path.suffix + ".tmp")
    marker_tmp_path.write_text(json.dumps(marker_payload, indent=2), encoding="utf-8")
    marker_tmp_path.replace(marker_path)

    return BackupResult(
        ok=True,
        detail="dump created, restore-verified, and digest-bound",
        dump_path=final_path,
        verification=verification,
    )


@dataclass(frozen=True)
class BackupPairStatus:
    """The outcome of re-verifying one dump+marker pair against the

    dump's CURRENT bytes -- `reason` is always safe to print or log: it
    only ever names a filename, a field, a size, or a hash prefix, never
    a database URL, credential, or dump content."""

    dump_path: Path
    verified: bool
    reason: str
    timestamp: datetime | None = None


def _inspect_backup_pair(dump_path: Path) -> BackupPairStatus:
    timestamp = parse_backup_timestamp(dump_path.name)
    if timestamp is None:
        return BackupPairStatus(dump_path, False, "filename does not match the naming convention")

    marker_path = verified_marker_path(dump_path)
    if not marker_path.exists():
        return BackupPairStatus(dump_path, False, "no marker file", timestamp)

    try:
        raw = marker_path.read_text(encoding="utf-8")
    except OSError as error:
        return BackupPairStatus(dump_path, False, f"marker unreadable: {error}", timestamp)

    payload = _parse_marker(raw)
    if payload is None:
        return BackupPairStatus(
            dump_path, False, "marker is malformed or missing a required field", timestamp
        )

    recorded_filename = payload["dump_filename"]
    if recorded_filename != dump_path.name:
        return BackupPairStatus(
            dump_path,
            False,
            f"marker dump_filename {recorded_filename!r} does not match this file's name",
            timestamp,
        )

    recorded_sha256 = payload["sha256"]
    assert isinstance(recorded_sha256, str)
    if not _SHA256_PATTERN.match(recorded_sha256):
        return BackupPairStatus(
            dump_path, False, "marker sha256 is not structurally valid", timestamp
        )

    try:
        current_size = dump_path.stat().st_size
    except OSError as error:
        return BackupPairStatus(dump_path, False, f"dump file unreadable: {error}", timestamp)

    if payload["size_bytes"] != current_size:
        return BackupPairStatus(
            dump_path,
            False,
            f"marker size_bytes {payload['size_bytes']} != current file size {current_size}",
            timestamp,
        )

    if payload["alembic_revision"] != REQUIRED_ALEMBIC_REVISION:
        return BackupPairStatus(
            dump_path,
            False,
            "marker alembic_revision does not match the canonical value",
            timestamp,
        )

    if payload["assignment_id"] != str(RUNTIME_ASSIGNMENT_ID):
        return BackupPairStatus(
            dump_path, False, "marker assignment_id does not match the canonical value", timestamp
        )

    current_sha256, _ = sha256_and_size(dump_path)
    if current_sha256 != recorded_sha256:
        return BackupPairStatus(
            dump_path, False, "current dump bytes do not match the recorded sha256", timestamp
        )

    return BackupPairStatus(dump_path, True, "verified", timestamp)


def inspect_backup_directory(
    directory: Path,
) -> tuple[list[BackupPairStatus], list[BackupPairStatus]]:
    """Every candidate dump in `directory`, classified verified or invalid

    -- `(verified, invalid)`, verified newest first. Invalid/corrupt pairs
    are reported here, not silently dropped: a caller (the CLI entrypoint)
    can print `reason` for each without ever touching a secret."""
    verified: list[BackupPairStatus] = []
    invalid: list[BackupPairStatus] = []
    for path in sorted(directory.glob(f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}")):
        status = _inspect_backup_pair(path)
        (verified if status.verified else invalid).append(status)
    epoch = datetime.min.replace(tzinfo=UTC)
    verified.sort(key=lambda status: status.timestamp or epoch, reverse=True)
    return verified, invalid


def select_verified_backups(directory: Path) -> list[Path]:
    """Every dump in `directory` that re-verifies against its OWN current

    bytes right now -- not a cached "a marker exists" check. A dump that
    was truncated, mutated, or replaced since its marker was written no
    longer appears here the moment its bytes diverge, with no separate
    re-scan step required to notice."""
    verified, _ = inspect_backup_directory(directory)
    return [status.dump_path for status in verified]


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
