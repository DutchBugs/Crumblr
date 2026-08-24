"""A halt must survive a real process restart (review F-003, re-raised in 1.1).

The first attempt at this evidence constructed two `KillSwitch` objects inside
one interpreter. That proves the store round-trips; it does not prove the
property, because nothing was ever restarted. The reviewer was right to hold
the finding open.

These tests spawn a genuinely separate Python process, let it exit, and then
read the state from this one. The acceptance sequence from the review is
covered literally:

    process A writes HALT → process A exits → process B starts
    → process B starts with new orders disabled
    → only an explicit operator reset returns it to RUNNING
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from crumblr.domain.enums import KillSwitchState, ReasonCode
from crumblr.domain.timeutils import utc_now
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.safety_state import FileSafetyStateStore, SafetyState

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]

TRIP_IN_A_SEPARATE_PROCESS = """
import sys
from pathlib import Path

from crumblr.domain.enums import ReasonCode
from crumblr.domain.timeutils import utc_now
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.safety_state import FileSafetyStateStore

switch = KillSwitch(FileSafetyStateStore(Path(sys.argv[1])))
switch.trip(
    reason_codes=(ReasonCode.{reason},),
    tripped_by="process_a",
    occurred_at_utc=utc_now(),
    detail="tripped in a separate process",
)
print(switch.state.value)
"""


def run_in_separate_process(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Execute `script` in a fresh interpreter that exits before we look."""
    result = subprocess.run(
        [sys.executable, "-c", script, *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, f"child process failed:\n{result.stderr}"
    return result


class TestHaltSurvivesAProcessRestart:
    """The literal acceptance sequence from review 1.0, re-raised in 1.1."""

    def test_a_halt_written_by_another_process_is_still_in_force(self, tmp_path: Path) -> None:
        state_file = tmp_path / "safety.json"

        # 1 & 2 — process A writes HALT and exits.
        result = run_in_separate_process(
            TRIP_IN_A_SEPARATE_PROCESS.format(reason="DAILY_LOSS_LIMIT"), str(state_file)
        )
        assert result.stdout.strip() == "HALTED"
        assert state_file.exists()

        # 3 & 4 — this process starts from the persisted state, disabled.
        restarted = KillSwitch.on_startup(FileSafetyStateStore(state_file))
        assert restarted.is_halted
        assert restarted.state is KillSwitchState.HALTED
        assert ReasonCode.DAILY_LOSS_LIMIT in restarted.active_reasons

    def test_the_reason_and_author_survive_the_restart(self, tmp_path: Path) -> None:
        """A halt whose reason is lost is much harder to clear correctly."""
        state_file = tmp_path / "safety.json"
        run_in_separate_process(
            TRIP_IN_A_SEPARATE_PROCESS.format(reason="MAX_DRAWDOWN"), str(state_file)
        )

        restarted = KillSwitch.on_startup(FileSafetyStateStore(state_file))
        record = restarted.history[-1]
        assert record.tripped_by == "process_a"
        assert record.detail == "tripped in a separate process"
        assert ReasonCode.MAX_DRAWDOWN in record.reason_codes

    def test_only_an_operator_reset_returns_it_to_running(self, tmp_path: Path) -> None:
        """Step 5: nothing automatic clears it, across processes either."""
        state_file = tmp_path / "safety.json"
        run_in_separate_process(
            TRIP_IN_A_SEPARATE_PROCESS.format(reason="MANUAL_HALT"), str(state_file)
        )

        # A second restart, with no operator action, is still halted.
        assert KillSwitch.on_startup(FileSafetyStateStore(state_file)).is_halted

        operator_process = KillSwitch.on_startup(FileSafetyStateStore(state_file))
        operator_process.reset(operator="levi", incident_note="INC-12 closed: verified")

        # And a further restart now sees RUNNING.
        assert not KillSwitch.on_startup(FileSafetyStateStore(state_file)).is_halted

    def test_restarting_twice_does_not_erode_the_halt(self, tmp_path: Path) -> None:
        """Repeated restarts must not eventually produce a running system."""
        state_file = tmp_path / "safety.json"
        run_in_separate_process(
            TRIP_IN_A_SEPARATE_PROCESS.format(reason="RECONCILIATION_MISMATCH"), str(state_file)
        )
        for attempt in range(5):
            switch = KillSwitch.on_startup(FileSafetyStateStore(state_file))
            assert switch.is_halted, f"halt was lost on restart {attempt + 1}"


class TestCorruptedStateFailsClosed:
    """Every way the record can be unusable must resolve to halted."""

    @staticmethod
    def _startup(path: Path) -> KillSwitch:
        return KillSwitch.on_startup(FileSafetyStateStore(path))

    def test_a_missing_file_fails_closed(self, tmp_path: Path) -> None:
        assert self._startup(tmp_path / "absent.json").state is KillSwitchState.UNKNOWN

    def test_a_truncated_file_fails_closed(self, tmp_path: Path) -> None:
        """The shape a crash mid-write would leave, if writes were not atomic."""
        path = tmp_path / "safety.json"
        full = json.dumps(
            SafetyState(
                state=KillSwitchState.HALTED,
                reason_codes=(ReasonCode.MANUAL_HALT,),
                recorded_at_utc=utc_now(),
            ).to_payload()
        )
        path.write_text(full[: len(full) // 2], encoding="utf-8")
        assert self._startup(path).is_halted

    def test_an_empty_file_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "safety.json"
        path.write_text("", encoding="utf-8")
        assert self._startup(path).is_halted

    def test_a_json_array_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "safety.json"
        path.write_text("[]", encoding="utf-8")
        assert self._startup(path).is_halted

    def test_an_unsupported_schema_version_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "safety.json"
        path.write_text(json.dumps({"schema_version": 2, "state": "RUNNING"}), encoding="utf-8")
        assert self._startup(path).is_halted

    def test_a_running_record_with_a_broken_timestamp_fails_closed(self, tmp_path: Path) -> None:
        """The dangerous case: a record that claims RUNNING but cannot be trusted."""
        path = tmp_path / "safety.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "RUNNING",
                    "reason_codes": [],
                    "recorded_at_utc": "not-a-timestamp",
                }
            ),
            encoding="utf-8",
        )
        assert self._startup(path).is_halted

    def test_a_naive_timestamp_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "safety.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "state": "RUNNING",
                    "reason_codes": [],
                    "recorded_at_utc": "2026-08-17T12:00:00",
                }
            ),
            encoding="utf-8",
        )
        assert self._startup(path).is_halted


class TestUnwritableDestination:
    """A halt that cannot be recorded must not be reported as taken."""

    def test_an_unwritable_destination_raises_rather_than_pretending(self, tmp_path: Path) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        state_file = locked / "safety.json"
        locked.chmod(stat.S_IRUSR | stat.S_IXUSR)  # read + execute, no write

        try:
            if os.access(locked, os.W_OK):
                pytest.skip("filesystem or user ignores directory permissions")

            switch = KillSwitch(FileSafetyStateStore(state_file))
            with pytest.raises(OSError):
                switch.trip(
                    reason_codes=(ReasonCode.MANUAL_HALT,),
                    tripped_by="operator",
                    occurred_at_utc=utc_now(),
                )
            assert not state_file.exists()
        finally:
            locked.chmod(stat.S_IRWXU)

    def test_a_failed_write_leaves_no_partial_record(self, tmp_path: Path) -> None:
        """A partial record would read back as UNKNOWN, which is survivable —
        but leaving none at all is cleaner, and the atomic rename guarantees it."""
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            if os.access(locked, os.W_OK):
                pytest.skip("filesystem or user ignores directory permissions")
            with pytest.raises(OSError):
                FileSafetyStateStore(locked / "safety.json").save(
                    SafetyState(
                        state=KillSwitchState.HALTED,
                        reason_codes=(ReasonCode.MANUAL_HALT,),
                        recorded_at_utc=utc_now(),
                    )
                )
            assert list(locked.iterdir()) == []
        finally:
            locked.chmod(stat.S_IRWXU)
