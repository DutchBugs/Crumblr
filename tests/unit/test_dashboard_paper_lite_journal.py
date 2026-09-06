"""`read_journal_entries`: every failure mode resolves to absence, never raises.

The dashboard must never construct `DurablePaperBroker` (its constructor
writes a `PORTFOLIO_CREATED` header to disk the first time a journal path
does not exist yet) — this reader only ever opens the file and parses it.
"""

from __future__ import annotations

from pathlib import Path

from crumblr.dashboard.paper_lite_journal import read_journal_entries


def test_a_missing_file_reads_as_empty(tmp_path: Path) -> None:
    assert read_journal_entries(tmp_path / "does_not_exist.jsonl") == ()


def test_a_truncated_line_is_skipped_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"sequence": 0, "event_type": "PORTFOLIO_CREATED", "payload": {}}\n'
        '{"sequence": 1, "event_type": "AUDIT_FACT", "payl',
        encoding="utf-8",
    )

    entries = read_journal_entries(path)

    assert len(entries) == 1
    assert entries[0]["sequence"] == 0


def test_well_formed_lines_are_returned_in_order(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"sequence": 0, "event_type": "PORTFOLIO_CREATED", "payload": {}}\n'
        '{"sequence": 1, "event_type": "AUDIT_FACT", '
        '"payload": {"fact": "PAPER_LITE_DECISION_WINDOW_CLAIMED"}}\n',
        encoding="utf-8",
    )

    entries = read_journal_entries(path)

    assert [entry["sequence"] for entry in entries] == [0, 1]
    assert entries[1]["payload"]["fact"] == "PAPER_LITE_DECISION_WINDOW_CLAIMED"


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"sequence": 0, "event_type": "PORTFOLIO_CREATED", "payload": {}}\n\n',
        encoding="utf-8",
    )

    entries = read_journal_entries(path)

    assert len(entries) == 1
