"""Stage-C PostgreSQL backup gate (post-incident, 2026-09-09).

Every test here runs with no real PostgreSQL connection and no real
`docker exec` -- `subprocess.run` is monkeypatched where a failure path
needs to be simulated, and `recreate_scratch_database`/`create_db_engine`/
`verify_restored_database` are monkeypatched where only the digest-binding
or retention logic is under test. Nothing here dumps, restores, or deletes
against a real `crumblr_soak` or `crumblr_backup_verify` -- this is
implementation-only, per the owner's own instruction not to run the tool
against the real database yet.

**Digest binding (Dev 1 BLOCK, 2026-09-09).** The previous marker meant
only "a dump with this filename passed verification once" -- nothing tied
it to the file's current bytes. `_make_verified_dump` below now writes a
marker carrying the dump's real SHA-256/size, exactly what
`create_verified_backup` itself would write on a genuine success, so
these tests exercise the same schema the real code produces and consumes.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy import Engine

import crumblr.local_admin.backup as backup_module
from crumblr.local_admin.backup import (
    BACKUP_FILENAME_PREFIX,
    BACKUP_FILENAME_SUFFIX,
    MARKER_SCHEMA_VERSION,
    REQUIRED_ALEMBIC_REVISION,
    RUNTIME_ASSIGNMENT_ID,
    SCRATCH_DATABASE_ALLOWLIST,
    SCRATCH_VERIFY_DATABASE_NAME,
    SOURCE_DATABASE_NAME,
    BackupContext,
    BackupSourceMismatchError,
    ScratchDatabaseNotAllowedError,
    VerificationResult,
    apply_retention,
    backup_filename_for,
    build_pg_dump_command,
    build_pg_restore_command,
    create_verified_backup,
    inspect_backup_directory,
    parse_backup_timestamp,
    require_exact_source_database,
    require_scratch_database,
    select_files_to_delete_for_retention,
    select_verified_backups,
    sha256_and_size,
    verified_marker_path,
    verify_restored_database,
)
from crumblr.persistence.schema import APPEND_ONLY_TABLES

FAKE_PASSWORD = "super-secret-fake-password-not-real"
BACKUP_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "backup_crumblr_soak.py"


def _valid_marker_payload(path: Path) -> dict[str, object]:
    sha256, size = sha256_and_size(path)
    return {
        "schema_version": MARKER_SCHEMA_VERSION,
        "dump_filename": path.name,
        "sha256": sha256,
        "size_bytes": size,
        "verified_at_utc": "2026-01-01T00:00:00+00:00",
        "alembic_revision": REQUIRED_ALEMBIC_REVISION,
        "assignment_id": str(RUNTIME_ASSIGNMENT_ID),
    }


def _write_marker(path: Path, payload: dict[str, object]) -> None:
    verified_marker_path(path).write_text(json.dumps(payload), encoding="utf-8")


def _make_verified_dump(
    directory: Path, timestamp: datetime, *, content: bytes = b"fake-dump-content"
) -> Path:
    path = directory / backup_filename_for(timestamp)
    path.write_bytes(content)
    _write_marker(path, _valid_marker_payload(path))
    return path


@dataclass
class _FakeCompletedProcess:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


class _FakeEngine:
    """Just enough to satisfy `create_verified_backup`'s `engine.dispose()`

    call when `verify_restored_database` itself is separately monkeypatched
    and never actually touches the engine."""

    def dispose(self) -> None:
        pass


def _mock_successful_dump_and_restore(
    monkeypatch: pytest.MonkeyPatch, *, dump_content: bytes = b"real-dump-bytes"
) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> _FakeCompletedProcess:
        if "pg_dump" in cmd:
            return _FakeCompletedProcess(returncode=0, stdout=dump_content)
        return _FakeCompletedProcess(returncode=0, stdout=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(backup_module, "recreate_scratch_database", lambda **kwargs: None)
    monkeypatch.setattr(backup_module, "create_db_engine", lambda url: _FakeEngine())
    monkeypatch.setattr(
        backup_module,
        "verify_restored_database",
        lambda engine: VerificationResult(ok=True, detail="fake pass"),
    )


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


class TestUrlWithDatabaseNeverMasksThePassword:
    """A real bug, live-reproduced during the 2026-09-10 operational proof:

    `str(sqlalchemy.engine.URL)` masks the password as the literal text
    "***" by default. `recreate_scratch_database`'s admin connection and
    `create_verified_backup`'s scratch-database connection both built
    their DSN through `_url_with_database`, which used `str(...)` --
    every real connection attempt authenticated with the literal string
    "***" instead of the real password and failed. No prior test caught
    this: every test exercising these code paths either mocked the
    connection entirely or used a deliberately unreachable host that
    never got far enough to notice a wrong password. This test asserts
    on the actual string `_url_with_database` produces, not on whether a
    connection succeeds, so it fails the same way with no real database
    at all -- exactly why it was missing before."""

    def test_the_real_password_survives_a_database_swap(self) -> None:
        url = "postgresql+psycopg://crumblr:a-real-password-value@localhost:55432/crumblr_soak"
        result = backup_module._url_with_database(url, "postgres")
        assert "a-real-password-value" in result
        assert "***" not in result

    def test_the_database_component_is_actually_swapped(self) -> None:
        url = "postgresql+psycopg://crumblr:secret@localhost:55432/crumblr_soak"
        result = backup_module._url_with_database(url, "crumblr_backup_verify")
        assert result.endswith("/crumblr_backup_verify")
        assert "crumblr_soak" not in result


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


class TestDigestBoundVerification:
    """The core Dev 1 fix: a marker proves the CURRENT bytes, re-checked

    on every call, not merely "a marker file exists"."""

    def test_correct_dump_and_correct_digest_is_accepted(self, tmp_path: Path) -> None:
        path = _make_verified_dump(tmp_path, datetime(2026, 1, 1, tzinfo=UTC))
        assert select_verified_backups(tmp_path) == [path]

    def test_one_byte_mutation_after_marker_creation_is_rejected(self, tmp_path: Path) -> None:
        path = _make_verified_dump(
            tmp_path, datetime(2026, 1, 1, tzinfo=UTC), content=b"original-dump-bytes"
        )
        mutated = bytearray(path.read_bytes())
        mutated[0] ^= 0xFF
        path.write_bytes(bytes(mutated))

        assert select_verified_backups(tmp_path) == []
        _, invalid = inspect_backup_directory(tmp_path)
        assert len(invalid) == 1
        assert "sha256" in invalid[0].reason

    def test_truncation_is_rejected(self, tmp_path: Path) -> None:
        path = _make_verified_dump(
            tmp_path, datetime(2026, 1, 1, tzinfo=UTC), content=b"original-dump-bytes-are-long"
        )
        path.write_bytes(path.read_bytes()[:-4])
        assert select_verified_backups(tmp_path) == []

    def test_same_size_replacement_with_different_bytes_is_rejected(self, tmp_path: Path) -> None:
        original = b"AAAAAAAAAA"
        replacement = b"BBBBBBBBBB"
        assert len(original) == len(replacement)
        path = _make_verified_dump(tmp_path, datetime(2026, 1, 1, tzinfo=UTC), content=original)
        path.write_bytes(replacement)
        assert select_verified_backups(tmp_path) == []

    def test_malformed_sha_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / backup_filename_for(datetime(2026, 1, 1, tzinfo=UTC))
        path.write_bytes(b"content")
        payload = _valid_marker_payload(path)
        payload["sha256"] = "not-a-valid-hex-digest"
        _write_marker(path, payload)
        assert select_verified_backups(tmp_path) == []

    def test_missing_sha_field_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / backup_filename_for(datetime(2026, 1, 1, tzinfo=UTC))
        path.write_bytes(b"content")
        payload = _valid_marker_payload(path)
        del payload["sha256"]
        _write_marker(path, payload)
        assert select_verified_backups(tmp_path) == []

    def test_marker_filename_mismatch_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / backup_filename_for(datetime(2026, 1, 1, tzinfo=UTC))
        path.write_bytes(b"content")
        payload = _valid_marker_payload(path)
        payload["dump_filename"] = "crumblr_soak_99999999T999999Z.dump"
        _write_marker(path, payload)
        assert select_verified_backups(tmp_path) == []

    def test_marker_revision_mismatch_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / backup_filename_for(datetime(2026, 1, 1, tzinfo=UTC))
        path.write_bytes(b"content")
        payload = _valid_marker_payload(path)
        payload["alembic_revision"] = "some-other-revision"
        _write_marker(path, payload)
        assert select_verified_backups(tmp_path) == []

    def test_marker_assignment_mismatch_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / backup_filename_for(datetime(2026, 1, 1, tzinfo=UTC))
        path.write_bytes(b"content")
        payload = _valid_marker_payload(path)
        payload["assignment_id"] = str(uuid4())
        _write_marker(path, payload)
        assert select_verified_backups(tmp_path) == []

    def test_size_mismatch_against_current_file_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / backup_filename_for(datetime(2026, 1, 1, tzinfo=UTC))
        path.write_bytes(b"content")
        payload = _valid_marker_payload(path)
        payload["size_bytes"] = cast(int, payload["size_bytes"]) + 1
        _write_marker(path, payload)
        assert select_verified_backups(tmp_path) == []

    def test_malformed_json_marker_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / backup_filename_for(datetime(2026, 1, 1, tzinfo=UTC))
        path.write_bytes(b"content")
        verified_marker_path(path).write_text("{not valid json", encoding="utf-8")
        assert select_verified_backups(tmp_path) == []


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

    def test_corrupt_pairs_do_not_count_toward_72_and_are_not_deleted(self, tmp_path: Path) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i in range(70):
            _make_verified_dump(tmp_path, base + timedelta(hours=i))
        corrupt = _make_verified_dump(tmp_path, base + timedelta(hours=200))
        corrupt.write_bytes(b"CORRUPTED-AFTER-MARKER-WRITTEN")

        assert select_files_to_delete_for_retention(tmp_path, retain_max=72) == []
        deleted = apply_retention(tmp_path, retain_max=72)
        assert deleted == []
        assert corrupt.exists()
        assert verified_marker_path(corrupt).exists()  # left alone, not cleaned up either

    def test_retention_deletes_only_cryptographically_revalidated_older_pairs(
        self, tmp_path: Path
    ) -> None:
        base = datetime(2026, 1, 1, tzinfo=UTC)
        paths = [_make_verified_dump(tmp_path, base + timedelta(hours=i)) for i in range(75)]
        oldest = sorted(paths, key=lambda p: p.name)[0]
        oldest.write_bytes(b"CORRUPTED-OLDEST-PAIR")  # no longer revalidates

        deleted = apply_retention(tmp_path, retain_max=72)

        # 75 total, 1 corrupt (excluded entirely), 74 genuinely verified ->
        # only 2 deleted, and the corrupt one is never among them.
        assert len(deleted) == 2
        assert oldest not in deleted
        assert oldest.exists()


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
        assert len(calls) == 1
        assert FAKE_PASSWORD not in calls[0]

    def test_pg_restore_failure_leaves_the_dump_unverified_and_does_not_delete_existing_backups(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        assert set(select_verified_backups(tmp_path)) == set(existing)
        assert select_files_to_delete_for_retention(tmp_path, retain_max=72) == []

    def test_hash_change_during_verification_window_blocks_and_writes_no_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulates something touching the dump file between the pre- and

        post-verification hash checks (a concurrent process, a scan, a
        bug) -- `verify_restored_database` itself is monkeypatched to
        mutate the file as its side effect before reporting success, so
        the two hashes taken around it must disagree."""
        context = BackupContext(
            database_url=f"postgresql+psycopg://user:{FAKE_PASSWORD}@localhost:55432/crumblr_soak",
            username="user",
            password=FAKE_PASSWORD,
            backup_directory=tmp_path,
        )
        expected_final_path = tmp_path / backup_filename_for(context.now)

        def fake_run(cmd: list[str], **kwargs: object) -> _FakeCompletedProcess:
            if "pg_dump" in cmd:
                return _FakeCompletedProcess(returncode=0, stdout=b"original-dump-bytes")
            return _FakeCompletedProcess(returncode=0, stdout=b"")

        def fake_verify(engine: object) -> VerificationResult:
            expected_final_path.write_bytes(b"MUTATED-DURING-VERIFICATION-WINDOW")
            return VerificationResult(ok=True, detail="fake pass")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(backup_module, "recreate_scratch_database", lambda **kwargs: None)
        monkeypatch.setattr(backup_module, "create_db_engine", lambda url: _FakeEngine())
        monkeypatch.setattr(backup_module, "verify_restored_database", fake_verify)

        result = create_verified_backup(context)

        assert result.ok is False
        assert "changed during verification" in result.detail
        assert not verified_marker_path(expected_final_path).exists()
        assert select_verified_backups(tmp_path) == []

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


class TestAtomicMarkerOnlyAppearsAfterFullVerification:
    def test_a_successful_run_writes_exactly_one_marker_and_no_leftover_temp_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_successful_dump_and_restore(monkeypatch)
        context = BackupContext(
            database_url=f"postgresql+psycopg://user:{FAKE_PASSWORD}@localhost:55432/crumblr_soak",
            username="user",
            password=FAKE_PASSWORD,
            backup_directory=tmp_path,
        )

        result = create_verified_backup(context)

        assert result.ok is True
        assert result.dump_path is not None
        marker = verified_marker_path(result.dump_path)
        assert marker.exists()
        assert not marker.with_suffix(marker.suffix + ".tmp").exists()
        assert select_verified_backups(tmp_path) == [result.dump_path]

    def test_the_written_marker_carries_every_required_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _mock_successful_dump_and_restore(monkeypatch, dump_content=b"specific-content-to-hash")
        context = BackupContext(
            database_url=f"postgresql+psycopg://user:{FAKE_PASSWORD}@localhost:55432/crumblr_soak",
            username="user",
            password=FAKE_PASSWORD,
            backup_directory=tmp_path,
        )

        result = create_verified_backup(context)
        assert result.ok is True
        assert result.dump_path is not None

        payload = json.loads(verified_marker_path(result.dump_path).read_text(encoding="utf-8"))
        expected_sha256, expected_size = sha256_and_size(result.dump_path)
        assert payload["schema_version"] == MARKER_SCHEMA_VERSION
        assert payload["dump_filename"] == result.dump_path.name
        assert payload["sha256"] == expected_sha256
        assert payload["size_bytes"] == expected_size
        assert payload["alembic_revision"] == REQUIRED_ALEMBIC_REVISION
        assert payload["assignment_id"] == str(RUNTIME_ASSIGNMENT_ID)
        assert "verified_at_utc" in payload

    def test_the_marker_never_contains_the_password_or_database_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        database_url = f"postgresql+psycopg://user:{FAKE_PASSWORD}@localhost:55432/crumblr_soak"
        _mock_successful_dump_and_restore(monkeypatch)
        context = BackupContext(
            database_url=database_url,
            username="user",
            password=FAKE_PASSWORD,
            backup_directory=tmp_path,
        )

        result = create_verified_backup(context)
        assert result.ok is True
        assert result.dump_path is not None

        raw = verified_marker_path(result.dump_path).read_text(encoding="utf-8")
        assert FAKE_PASSWORD not in raw
        assert database_url not in raw
        assert "://" not in raw


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


# ---- verify_restored_database(), isolated: no real database at all -------


class _FakeQueryResult:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def first(self) -> object | None:
        return self._row


class _FakeConnection:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def execute(self, *args: object, **kwargs: object) -> _FakeQueryResult:
        return _FakeQueryResult(self._row)

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeInspector:
    def __init__(self, tables: set[str]) -> None:
        self._tables = tables

    def get_table_names(self) -> set[str]:
        return self._tables


class _FakeVerifyEngine:
    def __init__(self, assignment_row: object | None) -> None:
        self._assignment_row = assignment_row

    def connect(self) -> _FakeConnection:
        return _FakeConnection(self._assignment_row)


def _patch_revision_and_tables(
    monkeypatch: pytest.MonkeyPatch, *, revision: str | None, tables: set[str]
) -> None:
    monkeypatch.setattr(backup_module, "current_revision", lambda engine: revision)
    monkeypatch.setattr(backup_module, "inspect", lambda engine: _FakeInspector(tables))


class TestVerifyRestoredDatabaseIsolated:
    """No real PostgreSQL connection anywhere in this class --

    `current_revision`/`inspect` are monkeypatched at the module level,
    and the engine is a tiny hand-written fake satisfying only the
    `.connect()`/`.execute()`/`.first()` shape `verify_restored_database`
    actually uses."""

    def test_exact_success_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_revision_and_tables(
            monkeypatch, revision=REQUIRED_ALEMBIC_REVISION, tables=set(APPEND_ONLY_TABLES)
        )
        engine = _FakeVerifyEngine(assignment_row=(1,))
        result = verify_restored_database(cast(Engine, engine))
        assert result.ok is True

    def test_wrong_alembic_revision_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_revision_and_tables(
            monkeypatch, revision="some-other-revision", tables=set(APPEND_ONLY_TABLES)
        )
        engine = _FakeVerifyEngine(assignment_row=(1,))
        result = verify_restored_database(cast(Engine, engine))
        assert result.ok is False
        assert "alembic revision" in result.detail

    def test_missing_alembic_revision_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_revision_and_tables(monkeypatch, revision=None, tables=set(APPEND_ONLY_TABLES))
        engine = _FakeVerifyEngine(assignment_row=(1,))
        result = verify_restored_database(cast(Engine, engine))
        assert result.ok is False
        assert "alembic revision" in result.detail

    def test_missing_table_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        missing = next(iter(APPEND_ONLY_TABLES))
        tables = set(APPEND_ONLY_TABLES) - {missing}
        _patch_revision_and_tables(monkeypatch, revision=REQUIRED_ALEMBIC_REVISION, tables=tables)
        engine = _FakeVerifyEngine(assignment_row=(1,))
        result = verify_restored_database(cast(Engine, engine))
        assert result.ok is False
        assert "missing tables" in result.detail
        assert missing in result.detail

    def test_missing_assignment_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_revision_and_tables(
            monkeypatch, revision=REQUIRED_ALEMBIC_REVISION, tables=set(APPEND_ONLY_TABLES)
        )
        engine = _FakeVerifyEngine(assignment_row=None)
        result = verify_restored_database(cast(Engine, engine))
        assert result.ok is False
        assert "not present" in result.detail
        assert str(RUNTIME_ASSIGNMENT_ID) in result.detail
