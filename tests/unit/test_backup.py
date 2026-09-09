"""Stage-C PostgreSQL backup gate (post-incident, 2026-09-09).

Every test here runs with no real PostgreSQL connection and no real
`docker exec` -- `subprocess.run` is monkeypatched where a failure path
needs to be simulated, and `recreate_scratch_database` is monkeypatched
to a no-op where only the dump/restore/retention logic is under test.
Nothing here dumps, restores, or deletes against a real `crumblr_soak` or
`crumblr_backup_verify` -- this is implementation-only, per the owner's
own instruction not to run the tool against the real database yet.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import crumblr.local_admin.backup as backup_module
from crumblr.local_admin.backup import (
    BACKUP_FILENAME_PREFIX,
    BACKUP_FILENAME_SUFFIX,
    SCRATCH_DATABASE_ALLOWLIST,
    SCRATCH_VERIFY_DATABASE_NAME,
    SOURCE_DATABASE_NAME,
    BackupContext,
    BackupSourceMismatchError,
    ScratchDatabaseNotAllowedError,
    apply_retention,
    backup_filename_for,
    build_pg_dump_command,
    build_pg_restore_command,
    create_verified_backup,
    parse_backup_timestamp,
    require_exact_source_database,
    require_scratch_database,
    select_files_to_delete_for_retention,
    select_verified_backups,
    verified_marker_path,
)

FAKE_PASSWORD = "super-secret-fake-password-not-real"
BACKUP_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "backup_crumblr_soak.py"


def _make_verified_dump(directory: Path, timestamp: datetime) -> Path:
    path = directory / backup_filename_for(timestamp)
    path.write_bytes(b"fake-dump-content")
    verified_marker_path(path).write_text("{}", encoding="utf-8")
    return path


@dataclass
class _FakeCompletedProcess:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class TestExactSourceDatabaseGuard:
    def test_the_real_source_name_is_accepted(self) -> None:
        require_exact_source_database(SOURCE_DATABASE_NAME)  # must not raise

    def test_crumblr_is_rejected(self) -> None:
        with pytest.raises(BackupSourceMismatchError, match="crumblr_soak"):
            require_exact_source_database("crumblr")

    def test_an_arbitrary_other_name_is_rejected(self) -> None:
        with pytest.raises(BackupSourceMismatchError):
            require_exact_source_database("some_other_database")

    def test_a_name_merely_containing_soak_is_rejected(self) -> None:
        """No substring/pattern matching -- only the exact name passes."""
        with pytest.raises(BackupSourceMismatchError):
            require_exact_source_database("some_other_soak_db")

    def test_a_name_that_is_a_prefix_or_suffix_variant_is_rejected(self) -> None:
        with pytest.raises(BackupSourceMismatchError):
            require_exact_source_database("crumblr_soak_backup")
        with pytest.raises(BackupSourceMismatchError):
            require_exact_source_database("old_crumblr_soak")


class TestExactScratchDatabaseGuard:
    def test_the_real_scratch_name_is_accepted(self) -> None:
        require_scratch_database(SCRATCH_VERIFY_DATABASE_NAME)  # must not raise

    @pytest.mark.parametrize(
        "name",
        [
            "crumblr_soak",
            "crumblr",
            "crumblr_test_dev1",
            "crumblr_test_dev2",
            "crumblr_test_dev3",
        ],
    )
    def test_every_real_database_name_is_rejected(self, name: str) -> None:
        with pytest.raises(ScratchDatabaseNotAllowedError):
            require_scratch_database(name)

    @pytest.mark.parametrize(
        "name",
        ["crumblr_backup", "some_backup_verify", "crumblr_backup_verify2", "test_scratch"],
    )
    def test_a_name_containing_backup_test_or_soak_is_still_rejected(self, name: str) -> None:
        """No 'contains backup/test/soak' matching -- exact allowlist only."""
        with pytest.raises(ScratchDatabaseNotAllowedError):
            require_scratch_database(name)

    def test_the_allowlist_never_contains_a_real_database_name(self) -> None:
        assert "crumblr_soak" not in SCRATCH_DATABASE_ALLOWLIST
        assert "crumblr" not in SCRATCH_DATABASE_ALLOWLIST
        assert "crumblr_test_dev1" not in SCRATCH_DATABASE_ALLOWLIST

    def test_recreate_scratch_database_refuses_before_attempting_any_connection(self) -> None:
        """`recreate_scratch_database` itself, not just the standalone

        guard -- proving the guard is actually wired into the one function
        that issues DROP/CREATE DATABASE, not merely defined and unused.
        A deliberately unreachable host (RFC 5737 TEST-NET-1) in the URL
        means a passing rejection here also proves no connection was
        attempted first -- if the guard were missing, this would hang or
        fail with a connection error instead of ScratchDatabaseNotAllowedError.
        """
        with pytest.raises(ScratchDatabaseNotAllowedError, match="crumblr_soak"):
            backup_module.recreate_scratch_database(
                database_url="postgresql+psycopg://user:pass@192.0.2.1:55432/crumblr_soak",
                name="crumblr_soak",
            )


class TestBackupFilenameConvention:
    def test_round_trips_through_parse(self) -> None:
        now = datetime(2026, 9, 9, 14, 30, 0, tzinfo=UTC)
        name = backup_filename_for(now)
        assert parse_backup_timestamp(name) == now

    def test_an_unrelated_filename_does_not_parse(self) -> None:
        assert parse_backup_timestamp("not_a_backup.dump") is None
        assert parse_backup_timestamp("crumblr_soak_backup.dump") is None
        assert parse_backup_timestamp(f"{BACKUP_FILENAME_PREFIX}20260909T143000Z.dump.tmp") is None

    def test_the_verified_marker_lives_alongside_the_dump(self, tmp_path: Path) -> None:
        dump = tmp_path / backup_filename_for(datetime(2026, 9, 9, tzinfo=UTC))
        marker = verified_marker_path(dump)
        assert marker.parent == dump.parent
        assert marker.name == dump.name + ".verified.json"


class TestRetentionSelection:
    def test_keeps_the_newest_72_and_selects_the_rest(self, tmp_path: Path) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        paths = [_make_verified_dump(tmp_path, base + timedelta(hours=i)) for i in range(80)]
        to_delete = select_files_to_delete_for_retention(tmp_path, retain_max=72)
        assert len(to_delete) == 8
        oldest_eight = sorted(paths, key=lambda p: p.name)[:8]
        assert set(to_delete) == set(oldest_eight)

    def test_exactly_72_files_selects_nothing(self, tmp_path: Path) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(72):
            _make_verified_dump(tmp_path, base + timedelta(hours=i))
        assert select_files_to_delete_for_retention(tmp_path, retain_max=72) == []

    def test_unverified_dumps_are_never_selected(self, tmp_path: Path) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(5):
            _make_verified_dump(tmp_path, base + timedelta(hours=i))
        unverified = tmp_path / backup_filename_for(base + timedelta(hours=100))
        unverified.write_bytes(b"never verified")
        to_delete = select_files_to_delete_for_retention(tmp_path, retain_max=3)
        assert unverified not in to_delete
        assert len(to_delete) == 2

    def test_files_not_matching_the_naming_convention_are_never_selected(
        self, tmp_path: Path
    ) -> None:
        stray = tmp_path / "not_a_backup.dump"
        stray.write_bytes(b"whatever")
        verified_marker_path(stray).write_text("{}", encoding="utf-8")
        assert select_files_to_delete_for_retention(tmp_path, retain_max=0) == []

    def test_apply_retention_deletes_only_the_selected_files_and_their_markers(
        self, tmp_path: Path
    ) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(75):
            _make_verified_dump(tmp_path, base + timedelta(hours=i))
        deleted = apply_retention(tmp_path, retain_max=72)
        assert len(deleted) == 3
        for path in deleted:
            assert not path.exists()
            assert not verified_marker_path(path).exists()
        assert len(select_verified_backups(tmp_path)) == 72

    def test_apply_retention_on_an_empty_directory_deletes_nothing(self, tmp_path: Path) -> None:
        assert apply_retention(tmp_path, retain_max=72) == []


class TestFailedRunNeverTriggersRetentionDeletion:
    def test_pg_dump_failure_blocks_and_creates_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> _FakeCompletedProcess:
            calls.append(cmd)
            return _FakeCompletedProcess(returncode=1, stderr=b"simulated pg_dump failure")

        monkeypatch.setattr(subprocess, "run", fake_run)

        context = BackupContext(
            database_url=f"postgresql+psycopg://user:{FAKE_PASSWORD}@localhost:55432/crumblr_soak",
            username="user",
            password=FAKE_PASSWORD,
            backup_directory=tmp_path,
        )
        result = create_verified_backup(context)

        assert result.ok is False
        assert "pg_dump failed" in result.detail
        assert list(tmp_path.glob(f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}")) == []
        assert select_verified_backups(tmp_path) == []
        # The argv actually invoked never carries the password.
        assert len(calls) == 1
        assert FAKE_PASSWORD not in calls[0]

    def test_pg_restore_failure_leaves_the_dump_unverified_and_does_not_delete_existing_backups(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Two pre-existing, already-verified backups -- must survive untouched.
        base = datetime(2026, 1, 1, tzinfo=UTC)
        existing = [_make_verified_dump(tmp_path, base + timedelta(hours=i)) for i in range(2)]

        call_count = {"n": 0}

        def fake_run(cmd: list[str], **kwargs: object) -> _FakeCompletedProcess:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _FakeCompletedProcess(returncode=0, stdout=b"fake-dump-bytes")
            return _FakeCompletedProcess(returncode=1, stderr=b"simulated pg_restore failure")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(backup_module, "recreate_scratch_database", lambda **kwargs: None)

        context = BackupContext(
            database_url=f"postgresql+psycopg://user:{FAKE_PASSWORD}@localhost:55432/crumblr_soak",
            username="user",
            password=FAKE_PASSWORD,
            backup_directory=tmp_path,
        )
        result = create_verified_backup(context)

        assert result.ok is False
        assert "pg_restore failed" in result.detail
        new_dumps = [
            p
            for p in tmp_path.glob(f"{BACKUP_FILENAME_PREFIX}*{BACKUP_FILENAME_SUFFIX}")
            if p not in existing
        ]
        assert len(new_dumps) == 1
        assert not verified_marker_path(new_dumps[0]).exists()

        # Existing good backups: untouched, still selectable, retention
        # would not have deleted anything even if run.
        assert set(select_verified_backups(tmp_path)) == set(existing)
        assert select_files_to_delete_for_retention(tmp_path, retain_max=72) == []

    def test_the_cli_script_only_calls_apply_retention_after_a_successful_result(self) -> None:
        """Static assertion over the CLI entrypoint's own source: retention

        must be structurally unreachable from the failure branch, not just
        conventionally avoided."""
        source = BACKUP_SCRIPT_PATH.read_text(encoding="utf-8")
        blocked_branch, _, rest = source.partition("if not result.ok:")
        assert blocked_branch, "expected an 'if not result.ok:' branch in the CLI script"
        failure_block, _, success_block = rest.partition("BACKUP_VERIFIED")
        assert "apply_retention" not in failure_block
        assert "apply_retention" in success_block


class TestCommandsNeverCarryASecretArgument:
    def test_pg_dump_command_never_embeds_a_password(self) -> None:
        cmd = build_pg_dump_command(
            container="crumblr-pg", username="crumblr", database="crumblr_soak"
        )
        assert FAKE_PASSWORD not in cmd
        assert not any("://" in arg for arg in cmd), "no DSN/connection-string argument"
        assert not any(arg.startswith("PGPASSWORD=") for arg in cmd)
        assert "-e" in cmd
        assert cmd[cmd.index("-e") + 1] == "PGPASSWORD", (
            "PGPASSWORD must be passed as a bare name (env passthrough), never a literal value"
        )

    def test_pg_restore_command_never_embeds_a_password(self) -> None:
        cmd = build_pg_restore_command(
            container="crumblr-pg", username="crumblr", database=SCRATCH_VERIFY_DATABASE_NAME
        )
        assert FAKE_PASSWORD not in cmd
        assert not any("://" in arg for arg in cmd)
        assert not any(arg.startswith("PGPASSWORD=") for arg in cmd)
        assert "-e" in cmd
        assert cmd[cmd.index("-e") + 1] == "PGPASSWORD"

    def test_create_verified_backup_never_passes_the_password_as_an_argument(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured_argvs: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> _FakeCompletedProcess:
            captured_argvs.append(cmd)
            return _FakeCompletedProcess(returncode=1, stderr=b"stop here")

        monkeypatch.setattr(subprocess, "run", fake_run)

        context = BackupContext(
            database_url=f"postgresql+psycopg://user:{FAKE_PASSWORD}@localhost:55432/crumblr_soak",
            username="user",
            password=FAKE_PASSWORD,
            backup_directory=tmp_path,
        )
        create_verified_backup(context)

        for argv in captured_argvs:
            assert FAKE_PASSWORD not in argv
            joined = " ".join(argv)
            assert FAKE_PASSWORD not in joined

    def test_the_password_is_passed_only_via_the_child_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured_envs: list[dict[str, str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> _FakeCompletedProcess:
            env = kwargs.get("env")
            assert isinstance(env, dict)
            captured_envs.append(env)
            return _FakeCompletedProcess(returncode=1, stderr=b"stop here")

        monkeypatch.setattr(subprocess, "run", fake_run)

        context = BackupContext(
            database_url=f"postgresql+psycopg://user:{FAKE_PASSWORD}@localhost:55432/crumblr_soak",
            username="user",
            password=FAKE_PASSWORD,
            backup_directory=tmp_path,
        )
        create_verified_backup(context)

        assert captured_envs
        assert captured_envs[0]["PGPASSWORD"] == FAKE_PASSWORD


class TestSubprocessCallsUseNoShell:
    def test_pg_dump_and_pg_restore_are_never_run_through_a_shell(self) -> None:
        """A guard against `shell=True` ever being introduced -- that would

        make the exact-argv-list guarantees above meaningless (a shell can
        reinterpret arguments) and reopen an injection surface."""
        source = Path(backup_module.__file__).read_text(encoding="utf-8")
        assert "shell=True" not in source
