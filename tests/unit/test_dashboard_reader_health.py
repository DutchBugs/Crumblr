"""`read_health_snapshot`: every failure mode resolves to `None`, never raises.

A dashboard panel that crashes because the reader-health file was mid-write
or has not been created yet has made a momentary gap worse, not shown it.
"""

from __future__ import annotations

from pathlib import Path

from crumblr.dashboard.reader_health import read_health_snapshot


def test_a_missing_file_reads_as_none(tmp_path: Path) -> None:
    assert read_health_snapshot(tmp_path / "does_not_exist.json") is None


def test_truncated_json_reads_as_none_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    path.write_text('{"status": "HEALTHY", "connec', encoding="utf-8")

    assert read_health_snapshot(path) is None


def test_a_well_formed_snapshot_is_returned_verbatim(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    path.write_text('{"status": "HEALTHY", "reconnect_count": 2}', encoding="utf-8")

    snapshot = read_health_snapshot(path)

    assert snapshot == {"status": "HEALTHY", "reconnect_count": 2}
