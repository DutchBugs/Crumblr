"""Read `LiveReader`'s health from the JSON snapshot file, not from MT5.

`scripts/mt5_live_reader.py` runs in its own process (the only one allowed to
import `MetaTrader5`) and writes `ReaderHealth.to_payload()` — already
established as containing no credential-shaped field (review F-031) — to disk
after every poll. This module only ever reads that file back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crumblr.observability.logging import get_logger

_log = get_logger("dashboard")


def read_health_snapshot(path: Path) -> dict[str, Any] | None:
    """The most recent `ReaderHealth.to_payload()`, or `None` if unavailable.

    Every failure mode — the file does not exist yet, the writer is mid-write
    on a host where replace is not atomic, the JSON is truncated — resolves to
    `None` rather than raising, because a dashboard whose health panel crashes
    because the health data was momentarily unavailable has made the problem
    worse, not shown it.
    """
    try:
        # "utf-8-sig" strips a leading byte-order mark if one is present and
        # is otherwise identical to "utf-8" — a snapshot written or hand-
        # edited by a Windows tool (PowerShell's default `Out-File` among
        # them) commonly carries one, and a BOM in front of "{" is invalid
        # JSON, which would otherwise silently read as an unreadable
        # snapshot rather than the health it actually contains.
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        payload: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as error:
        _log.warning("dashboard.health_snapshot_unreadable", path=str(path), error=str(error))
        return None
    return payload
