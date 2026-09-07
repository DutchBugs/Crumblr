"""Runs tests/js/dashboard_state_class_test.mjs against the real
dashboard.html template's own <script> block.

The browser's stateClass() had silently drifted from app.py::state_class()
into "good, warn, or else bad" -- so any value not explicitly listed (e.g.
SnapshotCompleteness.COMPLETE, NO_TRADE) rendered as an alarming "bad"
purely by falling through, not because it was ever classified that way
(owner review of commit 1d0687e). This proves parity against the real
shipped code -- see test_dashboard_js_broker_refresh.py for why this runs
via Node rather than a Python reimplementation of the JS.

Skipped, not failed, when `node` is not on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).resolve().parents[1] / "js" / "dashboard_state_class_test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node is not on PATH in this environment")
def test_the_shipped_dashboard_js_state_class_matches_the_server_exactly() -> None:
    assert _NODE is not None  # narrows for mypy; skipif above guarantees this at runtime
    result = subprocess.run(
        [_NODE, str(_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"dashboard_state_class_test.mjs failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "all assertions passed" in result.stdout
