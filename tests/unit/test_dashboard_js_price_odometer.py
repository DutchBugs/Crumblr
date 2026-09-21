"""Runs tests/js/dashboard_price_odometer_test.mjs against the real

dashboard.html template's own <script> block (CONTINUOUS LIVE DASHBOARD
work order §1: stable 5-decimal BID/ASK display, digit-only roll
animation, no animation on unchanged ticks or first render, correct
decimal-carry handling). See test_dashboard_js_broker_refresh.py for why
this runs via Node rather than a Python reimplementation of the JS.

Skipped, not failed, when `node` is not on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).resolve().parents[1] / "js" / "dashboard_price_odometer_test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node is not on PATH in this environment")
def test_the_shipped_dashboard_js_price_odometer_matches_the_spec() -> None:
    assert _NODE is not None  # narrows for mypy; skipif above guarantees this at runtime
    result = subprocess.run(
        [_NODE, str(_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"dashboard_price_odometer_test.mjs failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "all assertions passed" in result.stdout
