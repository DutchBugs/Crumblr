"""Runs tests/js/dashboard_paper_portfolio_test.mjs against the real

dashboard.html template's own <script> block -- proves renderPaperPortfolio()
actually rebuilds the paper portfolio containers on every poll and renders
each of the three statuses (OK/DEGRADED/NO EVIDENCE) distinctly, against the
real shipped code rather than a Python reimplementation of the JS. See
test_dashboard_js_broker_refresh.py for the same rationale.

Skipped, not failed, when `node` is not on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).resolve().parents[1] / "js" / "dashboard_paper_portfolio_test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node is not on PATH in this environment")
def test_the_shipped_dashboard_js_paper_portfolio_renderer_matches_the_server() -> None:
    assert _NODE is not None  # narrows for mypy; skipif above guarantees this at runtime
    result = subprocess.run(
        [_NODE, str(_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"dashboard_paper_portfolio_test.mjs failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "all assertions passed" in result.stdout
