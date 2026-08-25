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


def test_a_leading_byte_order_mark_does_not_make_it_unreadable(tmp_path: Path) -> None:
    """A snapshot written or hand-edited by a Windows tool (PowerShell's

    default `Out-File` among them) commonly carries a UTF-8 BOM — found by
    manually testing the dashboard against a hand-written snapshot file,
    which then rendered as UNKNOWN instead of the STALE it actually said.
    """
    path = tmp_path / "health.json"
    path.write_text('{"status": "STALE", "reconnect_count": 2}', encoding="utf-8-sig")

    snapshot = read_health_snapshot(path)

    assert snapshot == {"status": "STALE", "reconnect_count": 2}
