"""Read PAPER_LITE's own append-only journal file, never construct it.

`persistence.paper_lite.DurablePaperBroker.__init__` writes a
`PORTFOLIO_CREATED` header entry to disk the first time it is constructed
against a journal path that does not exist yet. A read-only dashboard must
never do that — loading this page before PAPER_LITE has ever run must not
fabricate a portfolio-created entry in the real journal. This module never
imports `DurablePaperBroker`/`SimulatedBroker`; it only opens the JSON-lines
file directly and parses it, mirroring `reader_health.py`'s exact resilience
pattern (missing file, or any unreadable line, resolves to absence rather
than raising).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crumblr.observability.logging import get_logger

_log = get_logger("dashboard")


def read_journal_entries(path: Path) -> tuple[dict[str, Any], ...]:
    """Every line of the journal, oldest first, as raw dicts.

    A missing file (PAPER_LITE has never run) returns `()`, not an error —
    the same "absence is not an error" contract `reader_health
    .read_health_snapshot()` already established for the reader's own health
    file. A single unreadable line is skipped and logged rather than
    invalidating every entry around it; the journal's own hash-chain
    verification is `DurablePaperBroker`'s job, not this read-only viewer's.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ()
    entries: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as error:
            _log.warning(
                "dashboard.paper_lite_journal_line_unreadable",
                path=str(path),
                line=line_number,
                error=str(error),
            )
    return tuple(entries)
