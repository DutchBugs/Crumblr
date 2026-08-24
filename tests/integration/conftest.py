"""Fixtures for tests that need a real PostgreSQL.

The invariants under test — NUMERIC fidelity, TIMESTAMPTZ round-trips,
`ON CONFLICT DO NOTHING`, permission-enforced append-only — are properties of
PostgreSQL specifically. Running them against SQLite would produce green tests
that prove nothing, so when no database is reachable they skip loudly instead.

Start one with:

    docker run -d --name crumblr-pg \
      -e POSTGRES_USER=crumblr -e POSTGRES_PASSWORD=crumblr -e POSTGRES_DB=crumblr \
      -p 55432:5432 postgres:17-alpine
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from crumblr.persistence.engine import (
    DEFAULT_TEST_URL,
    bootstrap_schema,
    create_db_engine,
    database_url,
    drop_schema,
    is_available,
)


@pytest.fixture(scope="session")
def database_available() -> bool:
    return is_available(database_url(DEFAULT_TEST_URL))


@pytest.fixture
def engine(database_available: bool) -> Iterator[Engine]:
    """A clean schema per test.

    Dropping and recreating rather than truncating: several tests care about
    sequence values, and a truncate that leaves sequences advanced would make
    them order-dependent.
    """
    if not database_available:
        pytest.skip(
            "no PostgreSQL reachable — see tests/integration/conftest.py for how to start one"
        )

    db_engine = create_db_engine(database_url(DEFAULT_TEST_URL))
    drop_schema(db_engine)
    bootstrap_schema(db_engine)
    try:
        yield db_engine
    finally:
        drop_schema(db_engine)
        db_engine.dispose()
