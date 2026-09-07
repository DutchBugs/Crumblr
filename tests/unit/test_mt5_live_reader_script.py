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
