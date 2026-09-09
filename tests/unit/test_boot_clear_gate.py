"""The boot-CLEAR gate: fail-closed by construction (Dev 1 BLOCK, 2026-09-09).

`host_supervisor.ps1` may issue PAPER_LITE's required
`--confirm-paper-incident-clear` automatically at most once per Windows
boot, tracked via a marker file. Every test here works against
`scripts.check_boot_clear_eligible.evaluate_eligibility` and
`scripts.claim_boot_clear.claim` directly -- no real OS boot time, no
subprocess, so these are fast and deterministic. `test_local_admin_boundary.py`'s
own convention (importing `scripts.*` as real Python modules) is reused
here, same as `test_migrations.py` already does with `scripts.run_replay`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from scripts.check_boot_clear_eligible import BootClearBlockedError, evaluate_eligibility
from scripts.claim_boot_clear import claim

BOOT_N = datetime(2026, 9, 8, 7, 0, 0, tzinfo=UTC)
BOOT_N_PLUS_1 = datetime(2026, 9, 9, 7, 0, 0, tzinfo=UTC)


def _write_marker(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")


class TestEligibilityIsFailClosed:
    def test_missing_marker_blocks(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        assert not marker.exists()
        with pytest.raises(BootClearBlockedError, match="missing"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)

    def test_malformed_json_blocks(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        _write_marker(marker, "{not valid json")
        with pytest.raises(BootClearBlockedError, match="not valid JSON"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)

    def test_empty_object_blocks(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        _write_marker(marker, {})
        with pytest.raises(BootClearBlockedError, match="boot_time_utc"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)

    def test_non_object_json_blocks(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        _write_marker(marker, [1, 2, 3])
        with pytest.raises(BootClearBlockedError, match="not a JSON object"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)

    def test_invalid_timestamp_blocks(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        _write_marker(marker, {"boot_time_utc": "not-a-timestamp"})
        with pytest.raises(BootClearBlockedError, match="not a parsable ISO-8601 timestamp"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)

    def test_naive_timestamp_blocks(self, tmp_path: Path) -> None:
        """No timezone is treated as unparsable, not assumed-UTC -- an

        ambiguous marker must not silently pass."""
        marker = tmp_path / "boot_clear.json"
        _write_marker(marker, {"boot_time_utc": "2026-09-08T07:00:00"})
        with pytest.raises(BootClearBlockedError, match="no timezone information"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)

    def test_future_boot_timestamp_blocks(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        _write_marker(marker, {"boot_time_utc": BOOT_N_PLUS_1.isoformat()})
        with pytest.raises(BootClearBlockedError, match="in the future"):
            evaluate_eligibility(marker, BOOT_N)  # current boot is EARLIER than the marker

    def test_current_boot_blocks(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        _write_marker(marker, {"boot_time_utc": BOOT_N_PLUS_1.isoformat()})
        with pytest.raises(BootClearBlockedError, match="already consumed this boot"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)  # exact match

    def test_valid_older_boot_permits(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        _write_marker(marker, {"boot_time_utc": BOOT_N.isoformat()})
        evaluate_eligibility(marker, BOOT_N_PLUS_1)  # does not raise -- eligible

    def test_one_microsecond_older_is_still_strictly_older_and_permits(
        self, tmp_path: Path
    ) -> None:
        marker = tmp_path / "boot_clear.json"
        just_before = BOOT_N_PLUS_1 - timedelta(microseconds=1)
        _write_marker(marker, {"boot_time_utc": just_before.isoformat()})
        evaluate_eligibility(marker, BOOT_N_PLUS_1)


class TestClaimIsAtomicAndPersisted:
    def test_claim_is_persisted_before_child_launch(self, tmp_path: Path) -> None:
        """The property host_supervisor.ps1 relies on: immediately after

        claim() returns, evaluate_eligibility() against that same boot
        reads it back as consumed -- no separate confirmation step
        needed, no window where the claim isn't yet durable."""
        marker = tmp_path / "boot_clear.json"
        claim(marker, BOOT_N_PLUS_1)

        with pytest.raises(BootClearBlockedError, match="already consumed this boot"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)

    def test_claim_overwrites_a_prior_marker_rather_than_merging(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        claim(marker, BOOT_N)
        claim(marker, BOOT_N_PLUS_1)

        payload = json.loads(marker.read_text(encoding="utf-8"))
        assert payload["boot_time_utc"] == BOOT_N_PLUS_1.isoformat()

    def test_claim_leaves_no_leftover_temp_file(self, tmp_path: Path) -> None:
        marker = tmp_path / "boot_clear.json"
        claim(marker, BOOT_N_PLUS_1)
        assert not (tmp_path / "boot_clear.json.tmp").exists()

    def test_failed_marker_write_means_no_child_launch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A write failure must propagate, not be swallowed -- the caller

        (host_supervisor.ps1) only starts PAPER_LITE if claim() succeeds,
        so claim() raising is exactly what stops that from happening."""
        marker = tmp_path / "boot_clear.json"

        def _boom(self: Path, *args: object, **kwargs: object) -> None:
            raise OSError("simulated disk failure")

        monkeypatch.setattr(Path, "write_text", _boom)

        with pytest.raises(OSError, match="simulated disk failure"):
            claim(marker, BOOT_N_PLUS_1)

        # And critically: no marker was left behind claiming success.
        assert not marker.exists()

    def test_child_crash_after_claim_does_not_permit_another_clear_same_boot(
        self, tmp_path: Path
    ) -> None:
        """Simulates: supervisor claims, starts PAPER_LITE, PAPER_LITE

        crashes immediately, supervisor (or the Scheduled Task's own
        retry) runs again within the same boot. The second eligibility
        check must still block."""
        marker = tmp_path / "boot_clear.json"

        claim(marker, BOOT_N_PLUS_1)  # first attempt: claims and would start PAPER_LITE
        # ... PAPER_LITE crashes moments later (not modeled here -- the
        # marker is the only durable record the supervisor consults) ...
        with pytest.raises(BootClearBlockedError, match="already consumed this boot"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)  # second attempt, same boot


class TestBootstrapSequence:
    def test_the_documented_bootstrap_then_reboot_sequence_works_end_to_end(
        self, tmp_path: Path
    ) -> None:
        """manual bootstrap on boot N -> marker=N/consumed -> controlled

        reboot -> boot N+1 sees a valid older marker -> claim N+1
        atomically -> exactly one automatic CLEAR."""
        marker = tmp_path / "boot_clear.json"

        # Manual bootstrap, on boot N: records boot N as already consumed.
        claim(marker, BOOT_N)

        # A real reboot happens. host_supervisor.ps1 runs on boot N+1.
        evaluate_eligibility(marker, BOOT_N_PLUS_1)  # eligible: does not raise

        # It claims the marker for boot N+1 before starting PAPER_LITE.
        claim(marker, BOOT_N_PLUS_1)

        # A second automatic attempt within boot N+1 (e.g. after a crash)
        # must now be blocked.
        with pytest.raises(BootClearBlockedError, match="already consumed this boot"):
            evaluate_eligibility(marker, BOOT_N_PLUS_1)
