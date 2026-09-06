"""`read_journal_entries`: a missing file is absence, never an error — but

an unreadable *line in an existing file* must be reported back to the
caller (`had_corruption`), never silently dropped and then treated as a
clean read (review feedback on the first version of this dashboard).

The dashboard must never construct `DurablePaperBroker` (its constructor
writes a `PORTFOLIO_CREATED` header to disk the first time a journal path
does not exist yet) — this reader only ever opens the file and parses it.
"""

from __future__ import annotations

from pathlib import Path

from crumblr.dashboard.paper_lite_journal import read_journal_entries


def test_a_missing_file_reads_as_empty_and_not_corrupt(tmp_path: Path) -> None:
    result = read_journal_entries(tmp_path / "does_not_exist.jsonl")

    assert result.entries == ()
    assert result.had_corruption is False


def test_a_truncated_line_is_skipped_but_flagged_as_corruption(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"sequence": 0, "event_type": "PORTFOLIO_CREATED", "payload": {}}\n'
        '{"sequence": 1, "event_type": "AUDIT_FACT", "payl',
        encoding="utf-8",
    )

    result = read_journal_entries(path)

    assert len(result.entries) == 1
    assert result.entries[0]["sequence"] == 0
    assert result.had_corruption is True


def test_well_formed_lines_are_returned_in_order_and_not_corrupt(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"sequence": 0, "event_type": "PORTFOLIO_CREATED", "payload": {}}\n'
        '{"sequence": 1, "event_type": "AUDIT_FACT", '
        '"payload": {"fact": "PAPER_LITE_DECISION_WINDOW_CLAIMED"}}\n',
        encoding="utf-8",
    )

    result = read_journal_entries(path)

    assert [entry["sequence"] for entry in result.entries] == [0, 1]
    assert result.entries[1]["payload"]["fact"] == "PAPER_LITE_DECISION_WINDOW_CLAIMED"
    assert result.had_corruption is False


def test_blank_lines_are_ignored_and_not_corruption(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(
        '{"sequence": 0, "event_type": "PORTFOLIO_CREATED", "payload": {}}\n\n',
        encoding="utf-8",
    )

    result = read_journal_entries(path)

    assert len(result.entries) == 1
    assert result.had_corruption is False
