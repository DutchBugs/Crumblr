"""Database connection handling.

Kept deliberately small. The interesting decisions live in `schema.py` (what is
guaranteed) and `journal.py` (how writes behave); this module only builds an
engine and creates the tables.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

from crumblr.observability.logging import get_logger
from crumblr.persistence.schema import append_only_grants, metadata

_log = get_logger("persistence")

DATABASE_URL_ENV_VAR = "CRUMBLR_DATABASE_URL"

DEFAULT_TEST_URL = "postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr_test_dev1"
"""Local development database. Never a production default — production supplies
the URL through the environment, and credentials never live in the repository.

Named `crumblr_test_dev1`, not the bare `crumblr` (2026-09-09 incident,
D-0XX): the ordinary/shared `crumblr` database is not an implicitly
disposable target, and `drop_schema()` below now refuses to run against
anything but `DISPOSABLE_TEST_DATABASE_NAMES` — a fallback that still
pointed at `crumblr` would make every integration test skip/fail with no
`CRUMBLR_DATABASE_URL` set at all, which is the wrong direction to fail in
for something this safety-relevant. `crumblr_test_dev1` was already the
de facto shared disposable database several developer worktrees used."""

DISPOSABLE_TEST_DATABASE_NAMES = frozenset(
    {"crumblr_test_dev1", "crumblr_test_dev2", "crumblr_test_dev3"}
)
"""Exact-name allow-list, deliberately not fuzzy/substring matching
(2026-09-09 incident, D-0XX).

`tests/integration/conftest.py`'s `engine` fixture and `test_migrations.py`'s
`empty_database` fixture both resolve their target through
`database_url(DEFAULT_TEST_URL)`, which honours `CRUMBLR_DATABASE_URL` from
the environment first. A shell where that variable was set to a real
database (`crumblr_soak`, the ordinary/shared `crumblr`, or anything else)
for a legitimate, unrelated reason -- reading soak evidence, running a real
script -- would silently redirect every destructive test fixture at it too,
since nothing checked the target's identity before calling `drop_schema()`.
This is that check: `drop_schema()` refuses to run at all unless the
engine's own database name is exactly one of these three. Absence from
this list is the safe default; a database is never disposable by
inference (a name pattern, a missing table, "it looked like a test db") --
only by being named here."""


class NotADisposableTestDatabaseError(RuntimeError):
    """`drop_schema()` refused: the target database is not on the explicit
    disposable-test allow-list (`DISPOSABLE_TEST_DATABASE_NAMES`)."""


def require_disposable_test_database(engine: Engine) -> None:
    """Fail closed unless `engine` is connected to an allow-listed disposable
    test database.

    Reads only `engine.url.database` -- a local attribute access, not a
    network call -- so a wrong target is refused before a single connection
    is opened, let alone a statement executed. Centralised here rather than
    checked separately by each caller: `drop_schema()` calls this itself
    (see below), so every current and future caller of `drop_schema()` is
    protected structurally, not by remembering to add a check at each call
    site.
    """
    database = engine.url.database
    if database not in DISPOSABLE_TEST_DATABASE_NAMES:
        allowed = ", ".join(sorted(DISPOSABLE_TEST_DATABASE_NAMES))
        raise NotADisposableTestDatabaseError(
            f"refusing a destructive schema operation on {database!r} -- it is not one "
            f"of the explicitly allow-listed disposable test databases ({allowed}). "
            f"If {DATABASE_URL_ENV_VAR} is pointing at a real database (crumblr_soak, "
            "the ordinary/shared crumblr, or anything else), fix the environment before "
            "running destructive tests. A database is never treated as disposable by "
            "inference -- only by exact name, on this list."
        )


def database_url(default: str | None = None) -> str:
    """The configured database URL.

    Read from the environment rather than configuration files, because a URL
    carries credentials and build.md §21 keeps those out of the repository.
    """
    url = os.getenv(DATABASE_URL_ENV_VAR) or default
    if not url:
        raise RuntimeError(f"no database URL: set {DATABASE_URL_ENV_VAR} or pass one explicitly")
    return url


def create_db_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Build an engine.

    `pool_pre_ping` costs a round trip per checkout and buys back the case
    where a connection died while idle — which, for a process that may sit
    quiet between decision windows, is the normal case rather than the
    exceptional one.
    """
    return create_engine(
        url or database_url(DEFAULT_TEST_URL),
        echo=echo,
        pool_pre_ping=True,
        future=True,
    )


def bootstrap_schema(engine: Engine, *, apply_grants: bool = False) -> None:
    """Create the tables if they do not exist.

    Schema *migration* is deliberately not handled here — see deviation D-029.
    This creates a schema from nothing, which is what a fresh development
    database and the test suite need.
    """
    metadata.create_all(engine)
    if apply_grants:
        with engine.begin() as connection:
            for statement in append_only_grants():
                connection.execute(text(statement))
    _log.info("persistence.schema_ready", tables=sorted(metadata.tables))


def drop_schema(engine: Engine) -> None:
    """Drop everything. For tests only; there is no production caller.

    Fail-closed database-identity guard (2026-09-09 incident): refuses to
    touch anything unless `engine`'s own database name is exactly one of
    `DISPOSABLE_TEST_DATABASE_NAMES` -- see `require_disposable_test_database`.
    This is the single, central choke point every destructive fixture in
    `tests/integration/` goes through (`conftest.py::engine`,
    `test_migrations.py::empty_database`'s `_wipe`), so the guard protects
    every one of them, and any future caller, without needing a matching
    check added at each call site.
    """
    require_disposable_test_database(engine)
    metadata.drop_all(engine)


@contextmanager
def transaction(engine: Engine) -> Iterator[Connection]:
    """One atomic unit of work.

    ADR-003 invariant 5: a state transition spanning several rows commits as
    one, so a safety-critical transition is never observable half-done.
    """
    with engine.begin() as connection:
        yield connection


def is_available(url: str | None = None) -> bool:
    """Whether a database is reachable. Used to skip integration tests."""
    try:
        engine = create_db_engine(url)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        return False
    return True
