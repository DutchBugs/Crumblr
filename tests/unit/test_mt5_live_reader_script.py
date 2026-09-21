"""`scripts/mt5_live_reader.py::_write_health_snapshot` -- the health-snapshot

writer crashed with an uncaught `FileNotFoundError` during a real-terminal
dashboard smoke test (2026-09-07) the first time `--json` pointed at a path
whose parent directory (`var/`) had never been created in that worktree --
a real, observed script crash, not a hypothetical. Every other durable
writer in this codebase (e.g. `DurablePaperBroker.__init__`) already creates
its own parent directory before writing; this one did not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from scripts.mt5_live_reader import _write_health_snapshot

from crumblr.application.live_reader import BrokerStateHealth, ReaderHealth, ReaderStatus
from crumblr.domain.enums import SnapshotCompleteness

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def test_writes_the_snapshot_even_when_the_parent_directory_does_not_exist_yet(
    tmp_path: Path,
) -> None:
    target = tmp_path / "does" / "not" / "exist" / "yet" / "live_reader_health.json"
    health = ReaderHealth(status=ReaderStatus.HEALTHY, connected=True, last_tick_at_utc=NOW)
    broker_state_health = BrokerStateHealth(
        last_snapshot_at_utc=NOW,
        position_set_state=SnapshotCompleteness.COMPLETE,
        pending_order_set_state=SnapshotCompleteness.COMPLETE,
    )

    _write_health_snapshot(target, health, broker_state_health)

    assert target.exists()
    assert '"status": "HEALTHY"' in target.read_text(encoding="utf-8")


def test_writes_the_snapshot_normally_when_the_parent_directory_already_exists(
    tmp_path: Path,
) -> None:
    target = tmp_path / "live_reader_health.json"
    health = ReaderHealth(status=ReaderStatus.STALE, connected=True)
    broker_state_health = BrokerStateHealth()

    _write_health_snapshot(target, health, broker_state_health)

    assert target.exists()
    assert '"status": "STALE"' in target.read_text(encoding="utf-8")


def test_a_transient_windows_rename_failure_is_retried_and_then_succeeds(
    tmp_path: Path,
) -> None:
    """A real continuous run (2026-09-16) crashed the whole reader on

    `PermissionError: [WinError 5]` from `Path.replace()` -- something else
    briefly held `live_reader_health.json` open (dashboard read, AV/indexer/
    cloud-sync scan). That must never take down real tick/bar collection;
    a transient failure here should retry and succeed silently.
    """
    target = tmp_path / "live_reader_health.json"
    health = ReaderHealth(status=ReaderStatus.HEALTHY, connected=True, last_tick_at_utc=NOW)
    broker_state_health = BrokerStateHealth()

    real_replace = Path.replace
    calls = {"count": 0}

    def flaky_replace(self: Path, target_path: object) -> object:
        calls["count"] += 1
        if calls["count"] < 2:
            raise PermissionError(5, "Access is denied")
        return real_replace(self, target_path)  # type: ignore[arg-type]

    with patch.object(Path, "replace", flaky_replace):
        _write_health_snapshot(target, health, broker_state_health)

    assert calls["count"] == 2
    assert target.exists()
    assert '"status": "HEALTHY"' in target.read_text(encoding="utf-8")


def test_a_persistent_rename_failure_is_logged_and_swallowed_not_raised(
    tmp_path: Path, capsys: object
) -> None:
    """If the destination stays locked for all three attempts, this poll's

    write is skipped (logged to stderr) rather than propagating and
    crashing the reader -- the next successful poll's write recovers, and
    genuine staleness still surfaces via `heartbeat_max_age_seconds`.
    """
    target = tmp_path / "live_reader_health.json"
    health = ReaderHealth(status=ReaderStatus.HEALTHY, connected=True, last_tick_at_utc=NOW)
    broker_state_health = BrokerStateHealth()

    def always_fails(self: Path, target_path: object) -> object:
        raise PermissionError(5, "Access is denied")

    with patch.object(Path, "replace", always_fails):
        _write_health_snapshot(target, health, broker_state_health)  # must not raise

    assert not target.exists()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "warning" in captured.err
    assert "skipping this poll" in captured.err
