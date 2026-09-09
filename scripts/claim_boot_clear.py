"""Atomically claim this boot's one automatic incident-CLEAR capability.

    uv run python scripts/claim_boot_clear.py <marker path> <boot time ISO-8601>

Writes the marker recording the given boot time as consumed, via a
temp-file-then-replace (`Path.replace`, atomic on both POSIX and Windows --
the same pattern `mt5_live_reader.py::_write_health_snapshot` already
uses), so a crash mid-write can never leave a corrupt or partially-written
marker for a later run to misinterpret.

`host_supervisor.ps1` calls this immediately before starting PAPER_LITE
with `--confirm-paper-incident-clear`, only after
`check_boot_clear_eligible.py` has already confirmed the current boot is
eligible -- and only starts PAPER_LITE if this claim itself succeeds. The
claim happens *before* the child process is spawned, not after it is
confirmed to stay running: PAPER_LITE records the CLEAR assertion
durably as soon as it starts, so a crash moments later must not be read
as "the CLEAR never happened" and permit a second automatic one this boot.

`scripts/bootstrap_boot_clear_marker.ps1` reuses this exact primitive for
the manual, one-time, first-installation bootstrap -- recording an older
boot's consumption is mechanically identical to claiming the current one;
only the caller and its intent differ, never this function.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def claim(marker_path: Path, boot_time_utc: datetime) -> None:
    payload = {
        "boot_time_utc": boot_time_utc.astimezone(UTC).isoformat(),
        "consumed_at_utc": datetime.now(UTC).isoformat(),
        "schema_version": 1,
    }
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = marker_path.with_suffix(marker_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(marker_path)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: claim_boot_clear.py <marker path> <boot time ISO-8601>")
        return 2
    marker_path = Path(sys.argv[1])

    try:
        boot_time_utc = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
        if boot_time_utc.tzinfo is None:
            raise ValueError("boot time argument has no timezone information")
    except ValueError as error:
        print(f"BOOT_CLEAR_CLAIM_FAILED: invalid boot time argument: {error}")
        return 1

    try:
        claim(marker_path, boot_time_utc)
    except OSError as error:
        print(f"BOOT_CLEAR_CLAIM_FAILED: {error}")
        return 1

    print("BOOT_CLEAR_CLAIMED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
