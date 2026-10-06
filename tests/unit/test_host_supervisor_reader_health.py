"""Behavioural tests for host_supervisor.ps1's reader-health evidence predicate.

Operational acceptance (2026-10-05) observed the supervisor's reader stage
accept a days-old `var/live_reader_health.json` as "HEALTHY" within a second,
before the freshly started reader had written anything: it only compared
`status` and `spec_version`. `Test-ReaderHealthEvidence` now also requires
`connected`, a parseable heartbeat no older than 60 s (never more lenient than
the file's own `heartbeat_max_age_seconds`) and not future-dated.

The function is extracted from the real script with PowerShell's own parser and
executed with fixture snapshots, so these tests exercise the shipped code, not a
copy. Skipped where no PowerShell is installed (e.g. Linux CI).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "host_supervisor.ps1"
SHELLS = [s for s in ("powershell", "pwsh") if shutil.which(s)]
pytestmark = pytest.mark.skipif(not SHELLS, reason="no PowerShell available")

SPEC = "bcd6a59271173c8fc49f4d88d522a9bd55d9e0e5ba44137b6d8c9b4d00283bd4"
NOW = "2026-10-06T07:00:00Z"

_DRIVER = r"""
$ErrorActionPreference = 'Stop'
$parser = [System.Management.Automation.Language.Parser]
$ast = $parser::ParseFile($env:SUPERVISOR_PS1, [ref]$null, [ref]$null)
$isFn = [System.Management.Automation.Language.FunctionDefinitionAst]
$fn = $ast.FindAll({ param($n) $n -is $isFn -and $n.Name -eq 'Test-ReaderHealthEvidence' }, $true)
$fn = $fn | Select-Object -First 1
if (-not $fn) { 'MISSING-FUNCTION'; exit 3 }
Invoke-Expression $fn.Extent.Text
$health = $env:HEALTH_JSON | ConvertFrom-Json
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$styles = [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor `
    [System.Globalization.DateTimeStyles]::AssumeUniversal
$now = [datetime]::Parse($env:NOW_UTC, $inv, $styles)
$expected = if ($env:EXPECTED_SPEC -eq '<null>') { $null } else { $env:EXPECTED_SPEC }
$result = Test-ReaderHealthEvidence -Health $health -ExpectedSpec $expected -NowUtc $now
"RESULT=$result"
"""


def _health(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "status": "HEALTHY",
        "connected": True,
        "spec_version": SPEC,
        "heartbeat_at_utc": "2026-10-06T06:59:58.000000+00:00",
        "heartbeat_max_age_seconds": 180.0,
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None or k in ("status", "connected")}


def _run(shell: str, health: object, *, expected: str | None = SPEC) -> bool:
    env = dict(os.environ)
    env.update(
        SUPERVISOR_PS1=str(SCRIPT),
        HEALTH_JSON=json.dumps(health),
        NOW_UTC=NOW,
        EXPECTED_SPEC=expected if expected is not None else "<null>",
    )
    flags = ["-NoProfile", "-NonInteractive"]
    if shell == "powershell":
        flags += ["-ExecutionPolicy", "Bypass"]
    completed = subprocess.run(
        [shell, *flags, "-Command", _DRIVER], capture_output=True, text=True, env=env, timeout=60
    )
    assert "RESULT=" in completed.stdout, completed.stdout + completed.stderr
    return completed.stdout.strip().splitlines()[-1] == "RESULT=True"


@pytest.mark.parametrize("shell", SHELLS)
class TestReaderHealthEvidence:
    def test_fresh_healthy_connected_matching_spec_passes(self, shell: str) -> None:
        assert _run(shell, _health()) is True

    def test_a_three_day_old_snapshot_is_rejected_the_observed_defect(self, shell: str) -> None:
        stale = _health(heartbeat_at_utc="2026-10-02T20:28:42.299331+00:00")
        assert _run(shell, stale) is False

    def test_the_old_status_and_spec_only_condition_would_have_accepted_it(
        self, shell: str
    ) -> None:
        stale = _health(heartbeat_at_utc="2026-10-02T20:28:42.299331+00:00")
        # Regression anchor: everything the OLD condition looked at is fine here.
        assert stale["status"] == "HEALTHY" and stale["spec_version"] == SPEC
        assert _run(shell, stale) is False

    def test_heartbeat_age_boundary_is_sixty_seconds(self, shell: str) -> None:
        assert _run(shell, _health(heartbeat_at_utc="2026-10-06T06:59:00Z")) is True  # 60 s
        assert _run(shell, _health(heartbeat_at_utc="2026-10-06T06:58:59Z")) is False  # 61 s

    def test_never_more_lenient_than_the_files_own_max_age(self, shell: str) -> None:
        tight = _health(heartbeat_at_utc="2026-10-06T06:59:30Z", heartbeat_max_age_seconds=20.0)
        assert _run(shell, tight) is False  # 30 s old > the file's own 20 s

    def test_a_future_dated_heartbeat_is_rejected_beyond_clock_skew(self, shell: str) -> None:
        assert _run(shell, _health(heartbeat_at_utc="2026-10-06T07:00:03Z")) is True  # 3 s skew
        assert _run(shell, _health(heartbeat_at_utc="2026-10-06T07:05:00Z")) is False

    @pytest.mark.parametrize("status", ["DEGRADED", "STALE", "DISCONNECTED", "UNKNOWN", ""])
    def test_any_non_healthy_status_is_rejected(self, shell: str, status: str) -> None:
        assert _run(shell, _health(status=status)) is False

    def test_connected_false_is_rejected_even_when_status_says_healthy(self, shell: str) -> None:
        assert _run(shell, _health(connected=False)) is False

    def test_spec_mismatch_or_missing_expected_spec_is_rejected(self, shell: str) -> None:
        assert _run(shell, _health(spec_version="0" * 64)) is False
        assert _run(shell, _health(), expected=None) is False

    @pytest.mark.parametrize("bad", [None, "", "not-a-timestamp", "2026-13-45T99:00:00Z"])
    def test_missing_or_unparseable_heartbeat_fails_closed(self, shell: str, bad: object) -> None:
        health = _health()
        if bad is None:
            health.pop("heartbeat_at_utc")
        else:
            health["heartbeat_at_utc"] = bad
        assert _run(shell, health) is False

    def test_an_older_format_snapshot_without_a_heartbeat_field_is_rejected(
        self, shell: str
    ) -> None:
        legacy = {"status": "HEALTHY", "connected": True, "spec_version": SPEC}
        assert _run(shell, legacy) is False


class TestStageActuallyUsesThePredicate:
    def test_the_reader_stage_calls_the_predicate_and_no_longer_status_only(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        stage = source[source.index("function Start-ReaderStage") :]
        stage = stage[: stage.index("# ---- Stage 5")]
        assert "Test-ReaderHealthEvidence -Health $health" in stage
        assert (
            '$health.status -eq "HEALTHY" -and $health.spec_version -eq $expectedSpec' not in stage
        )
