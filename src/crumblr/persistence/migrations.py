"""Running migrations from inside the application (review 1.6 F-023).

`metadata.create_all` builds whatever the current code says, which is exactly
right for a disposable test database and exactly wrong for one holding data
somebody would miss. Since D-030 closed, an ordinary run writes a journal,
sealed capsules, risk-session snapshots and raw market data — so the schema
now has a history, and a history needs versions.

The split this module draws:

    bootstrap_schema()   tests. Fast, disposable, no version recorded.
    upgrade_to_head()    everything else. Versioned, repeatable, reversible.

`build_durable_runtime(create_schema=True)` uses the second, so the ordinary
local path exercises the migrations on every run rather than only in the test
that checks them.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect

from crumblr.observability.logging import get_logger
from crumblr.persistence.engine import database_url

_log = get_logger("migrations")

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
VERSION_TABLE = "alembic_version"


def alembic_config(url: str | None = None) -> Config:
    """Alembic's configuration, with the URL supplied rather than committed.

    `alembic.ini` deliberately carries no `sqlalchemy.url`; it would be a
    credential-bearing string in the repository, which build.md §21 forbids.
    """
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    # Through `attributes`, not `set_main_option`: a password containing a `%`
    # would otherwise be mangled by ConfigParser's interpolation.
    config.attributes["sqlalchemy.url"] = url or database_url()
    # Leave this process's logging alone. See the comment in migrations/env.py.
    config.attributes["configure_logging"] = False
    return config


def upgrade_to_head(url: str | None = None) -> None:
    """Bring a database up to the latest revision. Safe to run repeatedly."""
    target = url or database_url()
    command.upgrade(alembic_config(target), "head")
    _log.info("migrations.upgraded", revision="head")


def downgrade_to_base(url: str | None = None) -> None:
    """Unwind every revision. For tests and for a disposable database only."""
    command.downgrade(alembic_config(url or database_url()), "base")
    _log.warning("migrations.downgraded", revision="base")


def current_revision(engine: Engine) -> str | None:
    """Which revision a database is at, or None if it has never been migrated.

    Read through the version table rather than through Alembic's own runtime,
    so that asking the question does not itself require a working migration
    environment.
    """
    from sqlalchemy import text

    if VERSION_TABLE not in inspect(engine).get_table_names():
        return None
    with engine.connect() as connection:
        row = connection.execute(text(f"SELECT version_num FROM {VERSION_TABLE}")).first()
    return str(row[0]) if row else None


def head_revision() -> str:
    """The newest revision the repository defines."""
    from alembic.script import ScriptDirectory

    # The URL is irrelevant here — reading the revision files touches no
    # database — but `alembic_config` requires one, so it is named as unused.
    revision = ScriptDirectory.from_config(
        alembic_config("postgresql+psycopg://unused")
    ).get_current_head()
    if revision is None:  # pragma: no cover - a repository with no migrations
        raise RuntimeError("no migrations are defined")
    return revision
