"""Read-only eligibility check: may the host supervisor issue PAPER_LITE's

automatic incident-CLEAR this boot?

    uv run python scripts/check_boot_clear_eligible.py <marker path> <current boot time ISO-8601>

Fail-closed by construction (Dev 1 BLOCK, 2026-09-09): the current boot is
eligible for exactly one automatic incident-CLEAR only if the marker file
exists, is valid JSON, is a JSON object with a `boot_time_utc` field that
parses as a valid, timezone-aware timestamp, and that timestamp is
STRICTLY OLDER than the current boot -- proving at least one real reboot
has happened since that marker was last written. Every other condition
(missing marker, unreadable file, invalid JSON, not an object, missing/
unparsable `boot_time_utc`, a future boot time, or a boot time equal to
the current boot) blocks.

`host_supervisor.ps1` calls this immediately before it would claim the
marker (`claim_boot_clear.py`) and start PAPER_LITE. The current boot time
is passed in as an argument (from `Get-CimInstance Win32_OperatingSystem`)
rather than rediscovered here, so there is exactly one source of truth for
"what is the current boot" -- this script only validates the marker
against it.

Never writes anything -- see `claim_boot_clear.py` for the write side.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path


class BootClearBlockedError(Exception):
    """Carries the human-readable reason the current boot is not eligible."""


def parse_utc_timestamp(raw: str, *, label: str) -> datetime:
    """A strict, timezone-required ISO-8601 parse -- a naive timestamp is

    ambiguous and treated the same as an unparsable one."""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise BootClearBlockedError(
            f"{label} is not a parsable ISO-8601 timestamp: {raw!r} ({error})"
        ) from error
    if parsed.tzinfo is None:
        raise BootClearBlockedError(f"{label} has no timezone information: {raw!r}")
    return parsed.astimezone(UTC)


def evaluate_eligibility(marker_path: Path, current_boot_time_utc: datetime) -> None:
    """Raises `BootClearBlockedError` with the reason if not eligible. Returns

    normally -- does not raise -- only when the current boot is eligible
    for its one automatic incident-CLEAR."""
    if not marker_path.exists():
        raise BootClearBlockedError(f"marker missing: {marker_path}")

    try:
        raw = marker_path.read_text(encoding="utf-8")
    except OSError as error:
        raise BootClearBlockedError(f"marker unreadable: {error}") from error

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise BootClearBlockedError(f"marker is not valid JSON: {error}") from error

    if not isinstance(payload, dict):
        raise BootClearBlockedError(f"marker is not a JSON object: {payload!r}")

    boot_time_raw = payload.get("boot_time_utc")
    if not isinstance(boot_time_raw, str) or not boot_time_raw:
        raise BootClearBlockedError(f"marker has no usable 'boot_time_utc' field: {payload!r}")

    marker_boot_time = parse_utc_timestamp(boot_time_raw, label="marker boot_time_utc")

    if marker_boot_time > current_boot_time_utc:
        raise BootClearBlockedError(
            f"marker boot time {marker_boot_time.isoformat()} is in the future "
            f"relative to the current boot {current_boot_time_utc.isoformat()}"
        )
    if marker_boot_time == current_boot_time_utc:
        raise BootClearBlockedError(
            "marker records the current boot -- CLEAR already consumed this boot"
        )

    # Strictly older than the current boot -- eligible.


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_boot_clear_eligible.py <marker path> <current boot time ISO-8601>")
        return 2
    marker_path = Path(sys.argv[1])

    try:
        current_boot_time_utc = parse_utc_timestamp(sys.argv[2], label="current boot time argument")
        evaluate_eligibility(marker_path, current_boot_time_utc)
    except BootClearBlockedError as error:
        print(f"BOOT_CLEAR_BLOCKED: {error}")
        return 1

    print("BOOT_CLEAR_ELIGIBLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
