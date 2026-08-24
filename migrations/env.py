"""Alembic environment (review 1.4 F-020, review 1.6 F-023).

Two rules, both of which exist because a migration runner points at whatever
database you tell it to and does what it is told.

**The URL comes from the environment and has no default.** It carries
credentials (build.md §21), and a fallback URL in a repository is a fallback
that eventually runs against something it should not have. An unset variable is
an error here, not an invitation to guess.

**The metadata is the application's own.** `target_metadata` is the same
`MetaData` the platform builds its queries against, so `alembic revision
--autogenerate` compares migrations to what the code actually expects rather
than to a copy that can drift.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, database_url
from crumblr.persistence.schema import metadata

config = context.config

# Only when Alembic is driven from its own CLI. `logging.config.fileConfig`
# reconfigures the root logger, and doing that inside a running application
# would silently replace this platform's structured JSON logging with
# Alembic's format — routing audit-relevant records somewhere nobody is
# reading. `persistence.migrations` therefore sets `configure_logging` to
# False, and even on the CLI path existing loggers are left alone.
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def _url() -> str:
    """The database to migrate. No default, deliberately.

    A programmatic caller (`persistence.migrations`) passes the URL through
    `config.attributes` rather than through the ini file, because a URL may
    contain a `%` and ConfigParser would try to interpolate it. The CLI passes
    nothing and the environment answers.
    """
    supplied = config.attributes.get("sqlalchemy.url")
    if supplied:
        return str(supplied)
    try:
        return database_url()
    except RuntimeError as error:  # pragma: no cover - operator-facing path
        raise RuntimeError(
            f"alembic needs a database: set {DATABASE_URL_ENV_VAR}. "
            "It is read from the environment rather than from alembic.ini so that "
            "no credential-bearing URL is ever committed."
        ) from error


def run_migrations_offline() -> None:
    """Emit SQL without connecting, for review before it is run anywhere."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run against a live database, in one transaction."""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Catch a column whose type changed as well as one that appeared.
            # A NUMERIC quietly becoming a float is exactly the kind of change
            # this project cannot afford to miss.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
