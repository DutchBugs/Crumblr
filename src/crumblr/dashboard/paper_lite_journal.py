"""Read PAPER_LITE's own append-only journal file, never construct it.

`persistence.paper_lite.DurablePaperBroker.__init__` writes a
`PORTFOLIO_CREATED` header entry to disk the first time it is constructed
against a journal path that does not exist yet. A read-only dashboard must
never do that — loading this page before PAPER_LITE has ever run must not
fabricate a portfolio-created entry in the real journal. This module never
imports `DurablePaperBroker`/`SimulatedBroker`; it only opens the JSON-lines
file directly and parses it, mirroring `reader_health.py`'s exact resilience
pattern for a missing file.

Unlike a missing file (`reader_health.py`'s "absence is not an error"), an
*unreadable line in an existing file* is a different claim — evidence may be
incomplete, not merely absent — and must not be silently swallowed. Review
feedback on the first version of this module: a corrupt line was logged
server-side and then quietly skipped, after which the caller could still
render a confident outcome as though nothing were wrong. `read_journal_entries`
now reports that distinction back to the caller via `had_corruption` so a
consumer can fail closed (render `DEGRADED`/`UNKNOWN`) instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crumblr.observability.logging import get_logger

_log = get_logger("dashboard")


@dataclass(frozen=True)
class JournalReadResult:
    entries: tuple[dict[str, Any], ...]
    """Every line that parsed successfully, oldest first."""

    had_corruption: bool
    """`True` if at least one line existed but could not be parsed — the

    caller must treat `entries` as potentially incomplete, not as the full
    picture. `False` for a missing file (absence, not corruption) and for a
    file where every line parsed cleanly."""


def read_journal_entries(path: Path) -> JournalReadResult:
    """The journal's entries plus whether any line was unreadable.

    A missing file (PAPER_LITE has never run) returns
    `JournalReadResult((), had_corruption=False)` — absence is not an error.
    A single unreadable line is skipped (so the surrounding, readable lines
    are still available) but flips `had_corruption` to `True`; the journal's
    own hash-chain verification is `DurablePaperBroker`'s job, not this
    read-only viewer's — this only distinguishes "clean" from "at least one
    line I could not parse" for the caller's own fail-closed handling.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return JournalReadResult((), had_corruption=False)
    entries: list[dict[str, Any]] = []
    had_corruption = False
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as error:
            had_corruption = True
            _log.warning(
                "dashboard.paper_lite_journal_line_unreadable",
                path=str(path),
                line=line_number,
                error=str(error),
            )
    return JournalReadResult(tuple(entries), had_corruption=had_corruption)
