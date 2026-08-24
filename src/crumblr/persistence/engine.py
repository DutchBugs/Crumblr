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

DEFAULT_TEST_URL = "postgresql+psycopg://crumblr:crumblr@localhost:55432/crumblr"
"""Local development database. Never a production default — production supplies
the URL through the environment, and credentials never live in the repository."""


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
    """Drop everything. For tests only; there is no production caller."""
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
