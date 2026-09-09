"""`drop_schema()`'s disposable-test-database guard (2026-09-09 incident, D-0XX).

`tests/integration/conftest.py`'s `engine` fixture and
`tests/integration/test_migrations.py`'s `empty_database` fixture both
resolve their target database through `database_url(DEFAULT_TEST_URL)`,
which honours `CRUMBLR_DATABASE_URL` from the environment first. A shell
with that variable set to a real database (`crumblr_soak`, the ordinary/
shared `crumblr`, or anything else) for an unrelated, legitimate reason
would silently redirect every destructive test fixture at it too -- nothing
checked the target's identity before calling `drop_schema()`.

Every test here runs with no real PostgreSQL connection at all: reading
`engine.url.database` and raising before `metadata.drop_all()` is ever
reached is a pure, local, un-connected operation, so a deliberately
unreachable host in each URL below both proves the guard rejects the name
*and* proves it never attempted a connection first -- if `drop_schema()`
tried to connect before checking, these tests would fail with a connection
error instead of `NotADisposableTestDatabaseError`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine

from crumblr.persistence.engine import (
    DISPOSABLE_TEST_DATABASE_NAMES,
    NotADisposableTestDatabaseError,
    drop_schema,
    require_disposable_test_database,
)

_UNREACHABLE_HOST = "192.0.2.1"
"""TEST-NET-1 (RFC 5737) — reserved for documentation, guaranteed to never

route anywhere real, so any test that accidentally reached past the guard
and tried to connect would hang/fail loudly on its own, not silently
succeed against a real database."""


def _engine_for(database: str | None) -> Engine:
    return create_engine(
        f"postgresql+psycopg://crumblr:crumblr@{_UNREACHABLE_HOST}:55432/{database}"
        if database is not None
        else f"postgresql+psycopg://crumblr:crumblr@{_UNREACHABLE_HOST}:55432/"
    )


class TestRequireDisposableTestDatabaseRejectsRealDatabases:
    def test_crumblr_soak_is_rejected(self) -> None:
        with pytest.raises(NotADisposableTestDatabaseError, match="crumblr_soak"):
            require_disposable_test_database(_engine_for("crumblr_soak"))

    def test_the_ordinary_shared_crumblr_database_is_rejected(self) -> None:
        """The bare `crumblr` name — not disposable by default, even though

        it used to be `DEFAULT_TEST_URL`'s own target before this incident."""
        with pytest.raises(NotADisposableTestDatabaseError, match="crumblr"):
            require_disposable_test_database(_engine_for("crumblr"))

    def test_an_arbitrary_other_database_is_rejected(self) -> None:
        with pytest.raises(NotADisposableTestDatabaseError):
            require_disposable_test_database(_engine_for("some_other_database"))

    def test_a_database_name_containing_the_word_test_is_still_rejected(self) -> None:
        """No fuzzy/substring matching — a name merely *looking* disposable

        is not enough; it must be exactly one of the three allow-listed
        names."""
        with pytest.raises(NotADisposableTestDatabaseError):
            require_disposable_test_database(_engine_for("crumblr_test_dev1_backup"))

    def test_a_missing_database_component_is_rejected(self) -> None:
        with pytest.raises(NotADisposableTestDatabaseError):
            require_disposable_test_database(_engine_for(None))


class TestRequireDisposableTestDatabaseAcceptsTheAllowlist:
    @pytest.mark.parametrize("name", sorted(DISPOSABLE_TEST_DATABASE_NAMES))
    def test_each_allowlisted_name_is_accepted(self, name: str) -> None:
        require_disposable_test_database(_engine_for(name))  # must not raise


class TestDropSchemaRefusesBeforeTouchingAnything:
    """`drop_schema()` itself, not just the standalone guard function --

    proving the guard is actually wired into the one function every
    destructive fixture calls, not merely available and unused."""

    def test_drop_schema_refuses_crumblr_soak(self) -> None:
        with pytest.raises(NotADisposableTestDatabaseError, match="crumblr_soak"):
            drop_schema(_engine_for("crumblr_soak"))

    def test_drop_schema_refuses_the_ordinary_shared_crumblr_database(self) -> None:
        with pytest.raises(NotADisposableTestDatabaseError, match="crumblr"):
            drop_schema(_engine_for("crumblr"))

    def test_drop_schema_refuses_an_arbitrary_other_database(self) -> None:
        with pytest.raises(NotADisposableTestDatabaseError):
            drop_schema(_engine_for("production"))


def test_the_allowlist_itself_never_contains_a_real_database_name() -> None:
    """A guard against this whole file quietly testing nothing: the

    allow-list itself must never grow to include `crumblr_soak` or the bare
    `crumblr` -- if it ever did, every test above that expects a rejection
    would need to be lying about what it proves."""
    assert "crumblr_soak" not in DISPOSABLE_TEST_DATABASE_NAMES
    assert "crumblr" not in DISPOSABLE_TEST_DATABASE_NAMES
