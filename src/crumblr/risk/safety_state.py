"""Durable system safety state (review finding F-003).

An in-memory kill switch forgets a halt when the process dies, which turns the
most consequential decision the system can make into the one least likely to
survive:

    critical condition → HALT → restart → halt forgotten → trading resumes

So the halt is written down, and startup begins with new orders **disabled**
until the recorded state has been read back and understood. A store that cannot
be read is not an absent halt; it is an unknown one, and unknown fails closed.

The store is a protocol with a file-backed implementation. When the event
journal exists (M2) a PostgreSQL implementation replaces it without the calling
code changing — the safety property lives in this module's contract, not in the
choice of backing store.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from crumblr.domain.enums import KillSwitchState, ReasonCode
from crumblr.domain.timeutils import UtcDatetime, utc_now

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SafetyState:
    """What the system knew about its own safety at the moment it was written."""

    state: KillSwitchState
    reason_codes: tuple[ReasonCode, ...]
    recorded_at_utc: UtcDatetime
    tripped_by: str | None = None
    detail: str | None = None
    schema_version: int = SCHEMA_VERSION

    @property
    def permits_new_orders(self) -> bool:
        """Only an explicitly RUNNING state permits new orders.

        HALTED and UNKNOWN both refuse. Writing it this way rather than
        `state is not HALTED` means a state member added later defaults to
        refusing rather than to permitting.
        """
        return self.state is KillSwitchState.RUNNING

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state": self.state.value,
            "reason_codes": [code.value for code in self.reason_codes],
            "recorded_at_utc": self.recorded_at_utc.isoformat(),
            "tripped_by": self.tripped_by,
            "detail": self.detail,
        }


def unknown_state(detail: str) -> SafetyState:
    """The state to assume when the recorded one cannot be established."""
    return SafetyState(
        state=KillSwitchState.UNKNOWN,
        reason_codes=(ReasonCode.SAFETY_STATE_UNKNOWN,),
        recorded_at_utc=utc_now(),
        tripped_by="startup_guard",
        detail=detail,
    )


class SafetyStateStore(Protocol):
    """Where system safety state is persisted between runs."""

    def load(self) -> SafetyState:
        """Read the recorded state.

        Implementations must never raise on a missing, unreadable or corrupt
        record — they return an `UNKNOWN` state instead, so the caller cannot
        accidentally treat a failed read as an absent halt.
        """
        ...

    def save(self, state: SafetyState) -> None: ...


class FileSafetyStateStore:
    """File-backed store, written atomically.

    A halt that is half-written is a halt that may read back as garbage, so the
    record is written to a temporary file in the same directory and moved into
    place — an atomic rename on POSIX. A crash mid-write leaves the previous
    record intact rather than a truncated one.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> SafetyState:
        if not self._path.exists():
            return unknown_state(f"no safety state recorded at {self._path}")
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return unknown_state(f"safety state at {self._path} is unreadable: {error}")

        if not isinstance(payload, dict):
            return unknown_state(f"safety state at {self._path} is not an object")
        if payload.get("schema_version") != SCHEMA_VERSION:
            # A record written by a different version may mean something else.
            return unknown_state(
                f"safety state schema {payload.get('schema_version')!r} "
                f"is not the expected {SCHEMA_VERSION}"
            )

        try:
            return SafetyState(
                state=KillSwitchState(payload["state"]),
                reason_codes=tuple(ReasonCode(code) for code in payload["reason_codes"]),
                recorded_at_utc=_parse_time(payload["recorded_at_utc"]),
                tripped_by=payload.get("tripped_by"),
                detail=payload.get("detail"),
            )
        except (KeyError, ValueError, TypeError) as error:
            return unknown_state(f"safety state at {self._path} is malformed: {error}")

    def save(self, state: SafetyState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=f".{self._path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(state.to_payload(), stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            Path(temporary).replace(self._path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


class InMemorySafetyStateStore:
    """For tests and replay, where nothing should outlive the process."""

    def __init__(self, initial: SafetyState | None = None) -> None:
        self._state = initial

    def load(self) -> SafetyState:
        if self._state is None:
            return unknown_state("no safety state recorded in memory")
        return self._state

    def save(self, state: SafetyState) -> None:
        self._state = state


def _parse_time(raw: object) -> UtcDatetime:
    from datetime import datetime

    if not isinstance(raw, str):
        raise TypeError(f"recorded_at_utc must be a string, got {type(raw).__name__}")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError("recorded_at_utc must carry a timezone")
    return parsed
