"""Schema versioning, and proof a backup can be restored (F-020, F-023).

`metadata.create_all` was fine while the database was disposable. Since D-030
closed it holds a journal, sealed capsules, risk-session snapshots and raw
market data, so the schema has a history and the history needs versions.

Two properties are worth more than the existence of a `migrations/` directory.

**A migrated database and the application's own metadata must not disagree.**
A migration that drifts from the code it serves is the failure mode that
matters here: everything passes until a query hits a column the migration
never made. `compare_metadata` answers that directly.

**A backup must produce a database the application can still read.** Review
1.6 F-023 asks for exactly this and not for a documented procedure — a restore
nobody has run is a plan, not a capability. The test needs `pg_dump` and skips
loudly rather than quietly passing when it is unavailable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from scripts.run_replay import build_instrument_spec
from sqlalchemy import Engine, inspect
from sqlalchemy.engine import make_url

from crumblr.application.bootstrap import build_durable_runtime
from crumblr.application.orchestration import ReplayOrchestrator
from crumblr.application.reconstruction import reconstruct_from_journal
from crumblr.config import load_config
from crumblr.domain.enums import Environment
from crumblr.market_data.synthetic import SyntheticMarketConfig, generate_ticks
from crumblr.mt5_gateway.simulated import SimulatedBroker
from crumblr.persistence.engine import (
    DEFAULT_TEST_URL,
    bootstrap_schema,
    create_db_engine,
    database_url,
    drop_schema,
)
from crumblr.persistence.journal import EventJournal
from crumblr.persistence.market_data import MarketDataStore
from crumblr.persistence.migrations import (
    VERSION_TABLE,
    current_revision,
    downgrade_to_base,
    head_revision,
    upgrade_to_head,
)
from crumblr.persistence.schema import APPEND_ONLY_TABLES, metadata

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BALANCE = Decimal("10000")
PG_CONTAINER = os.getenv("CRUMBLR_PG_CONTAINER", "crumblr-pg")
TEST_URL = database_url(DEFAULT_TEST_URL)
"""Resolved once, honouring `CRUMBLR_DATABASE_URL` — see `test_live_decision.py`'s

`TEST_URL` for the full reasoning (workspace database isolation). The
`pg_dump`/restore tests below target this URL's own database name inside
`PG_CONTAINER`, so they stay isolated too, not only the schema-level
tests."""
_test_db_name = make_url(TEST_URL).database
assert _test_db_name is not None, f"{TEST_URL!r} has no database component"
TEST_DB_NAME: str = _test_db_name
"""The database name alone, for `pg_dump`/`psql -d`, which take a bare

name rather than a full URL — must match `TEST_URL`'s database, not the
literal `"crumblr"` default, or the dump/restore pair would silently
target the shared database regardless of `CRUMBLR_DATABASE_URL`."""


@pytest.fixture
def empty_database(database_available: bool) -> Iterator[Engine]:
    """A database with nothing in it — not even a version table."""
    if not database_available:
        pytest.skip("no PostgreSQL reachable — see tests/integration/conftest.py")
    engine = create_db_engine(TEST_URL)
    _wipe(engine)
    try:
        yield engine
    finally:
        _wipe(engine)
        engine.dispose()


def _wipe(engine: Engine) -> None:
    from sqlalchemy import text

    drop_schema(engine)
    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {VERSION_TABLE}"))


class TestTheBaselineBuildsWhatTheCodeExpects:
    def test_migrating_an_empty_database_creates_every_table(self, empty_database: Engine) -> None:
        upgrade_to_head(TEST_URL)

        tables = set(inspect(empty_database).get_table_names())
        assert set(APPEND_ONLY_TABLES) <= tables

    def test_a_migrated_database_does_not_disagree_with_the_metadata(
        self, empty_database: Engine
    ) -> None:
        """The drift check.

        A migration that has fallen behind the code passes every test until a
        query reaches a column it never created.
        """
        upgrade_to_head(TEST_URL)

        with empty_database.connect() as connection:
            differences = compare_metadata(MigrationContext.configure(connection), metadata)

        assert differences == [], "the migrations and crumblr.persistence.schema have drifted apart"

    def test_the_database_records_which_revision_it_is_at(self, empty_database: Engine) -> None:
        assert current_revision(empty_database) is None

        upgrade_to_head(TEST_URL)

        assert current_revision(empty_database) == head_revision()

    def test_migrating_twice_changes_nothing(self, empty_database: Engine) -> None:
        upgrade_to_head(TEST_URL)
        first = current_revision(empty_database)

        upgrade_to_head(TEST_URL)

        assert current_revision(empty_database) == first

    def test_the_baseline_can_be_unwound(self, empty_database: Engine) -> None:
        """A migration that cannot be reversed is one nobody dares to apply."""
        upgrade_to_head(TEST_URL)
        downgrade_to_base(TEST_URL)

        remaining = set(inspect(empty_database).get_table_names())
        assert not (set(APPEND_ONLY_TABLES) & remaining)

    def test_create_all_and_the_migration_produce_the_same_schema(
        self, empty_database: Engine
    ) -> None:
        """Both paths exist; they must not be two different schemas.

        The test fixtures still use `create_all` because it is faster per
        test. That is only defensible while the two agree.
        """
        bootstrap_schema(empty_database)
        with empty_database.connect() as connection:
            from_create_all = {
                (table, tuple(sorted(c["name"] for c in inspect(connection).get_columns(table))))
                for table in APPEND_ONLY_TABLES
            }

        _wipe(empty_database)
        upgrade_to_head(TEST_URL)
        with empty_database.connect() as connection:
            from_migration = {
                (table, tuple(sorted(c["name"] for c in inspect(connection).get_columns(table))))
                for table in APPEND_ONLY_TABLES
            }

        assert from_create_all == from_migration


class TestTheRuntimeUsesTheMigrations:
    def test_building_a_runtime_with_create_schema_migrates(
        self, empty_database: Engine, tmp_path: Path
    ) -> None:
        """The ordinary local path exercises the same mechanism a deployment does."""
        runtime = build_durable_runtime(
            environment=Environment.PAPER,
            state_file=tmp_path / "safety.json",
            url=TEST_URL,
            create_schema=True,
        )
        try:
            assert current_revision(empty_database) == head_revision()
        finally:
            runtime.dispose()


def _pg_dump_command() -> list[str] | None:
    """Where to find `pg_dump`: on this host, or inside the dev container."""
    local = shutil.which("pg_dump")
    if local:
        return [local]
    if shutil.which("docker") is None:
        return None
    probe = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "which", "pg_dump"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    return ["docker", "exec", "-i", PG_CONTAINER, "pg_dump"]


def _psql_command() -> list[str] | None:
    local = shutil.which("psql")
    if local:
        return [local]
    if shutil.which("docker") is None:
        return None
    probe = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "which", "psql"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return None
    return ["docker", "exec", "-i", PG_CONTAINER, "psql"]


class TestABackupCanBeRestored:
    """Review 1.6 F-023: prove it, do not merely document it."""

    def _run_and_capture(self, engine: Engine, tmp_path: Path) -> tuple[str, dict[str, int]]:
        """One ordinary replay, then what it left behind."""
        shipped = load_config(Environment.PAPER, config_dir=REPO_ROOT / "config")
        agent = shipped.trading_agent.model_copy(update={"strategy_id": "baseline_v1"})
        config = shipped.model_copy(update={"trading_agent": agent})

        runtime = build_durable_runtime(
            environment=Environment.PAPER,
            state_file=tmp_path / "safety.json",
            url=TEST_URL,
            create_schema=True,
        )
        runtime.kill_switch.reset(operator="test", incident_note="arming a fresh test database")
        spec = build_instrument_spec()
        broker = SimulatedBroker(
            spec, starting_balance=BALANCE, server=config.account_guard.expected_server
        )
        ReplayOrchestrator(
            config,
            spec,
            broker,
            starting_equity=BALANCE,
            recorder=runtime.recorder,
            kill_switch=runtime.kill_switch,
            session_store=runtime.session_store,
        ).run(list(generate_ticks(SyntheticMarketConfig(bar_count=200), spec)))
        runtime.dispose()

        fingerprint = reconstruct_from_journal(EventJournal(engine)).fingerprint
        return fingerprint, MarketDataStore(engine).counts()

    def test_a_dump_restores_into_a_database_the_application_can_read(
        self, empty_database: Engine, tmp_path: Path
    ) -> None:
        dump_command = _pg_dump_command()
        restore_command = _psql_command()
        if dump_command is None or restore_command is None:
            pytest.skip(
                "pg_dump/psql not available on this host and not reachable in the "
                f"{PG_CONTAINER!r} container; install the PostgreSQL client tools or "
                "set CRUMBLR_PG_CONTAINER"
            )

        before_fingerprint, before_counts = self._run_and_capture(empty_database, tmp_path)
        assert before_counts["bars"] > 0, "nothing was written; the test proves nothing"

        dump = subprocess.run(
            [*dump_command, "-U", "crumblr", "-d", TEST_DB_NAME, "--clean", "--if-exists"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert dump.returncode == 0, f"pg_dump failed:\n{dump.stderr[-2000:]}"
        assert dump.stdout, "pg_dump produced an empty backup"

        # Destroy it, then restore from the backup alone.
        _wipe(empty_database)
        assert current_revision(empty_database) is None

        restore = subprocess.run(
            [*restore_command, "-U", "crumblr", "-d", TEST_DB_NAME, "-v", "ON_ERROR_STOP=1"],
            input=dump.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        assert restore.returncode == 0, f"restore failed:\n{restore.stderr[-2000:]}"

        # The claim that matters: the audit state is reconstructable afterwards.
        restored = create_db_engine(TEST_URL)
        try:
            assert current_revision(restored) == head_revision(), (
                "the restored database does not know which revision it is at"
            )
            assert reconstruct_from_journal(EventJournal(restored)).fingerprint == (
                before_fingerprint
            ), "the restored journal does not reproduce the run"
            assert MarketDataStore(restored).counts() == before_counts, (
                "the restored database lost the market data the decisions were made on"
            )
        finally:
            restored.dispose()
