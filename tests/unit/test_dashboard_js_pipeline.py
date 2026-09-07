"""Runs tests/js/dashboard_pipeline_test.mjs against the real dashboard.html
template's own <script> block.

The pre-restaging "Decision pipeline" section was rendered once server-side
and never touched again by applyState() -- the same "never refreshed on
poll" gap already fixed for the broker/activity panels. This proves the
restaged version's renderPipeline()/pipelineStageClass() run against the
real shipped code, not a Python reimplementation of the JS -- see
test_dashboard_js_broker_refresh.py for the same rationale.

Skipped, not failed, when `node` is not on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).resolve().parents[1] / "js" / "dashboard_pipeline_test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node is not on PATH in this environment")
def test_the_shipped_dashboard_js_pipeline_renderer_matches_the_server() -> None:
    assert _NODE is not None  # narrows for mypy; skipif above guarantees this at runtime
    result = subprocess.run(
        [_NODE, str(_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"dashboard_pipeline_test.mjs failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "all assertions passed" in result.stdout
