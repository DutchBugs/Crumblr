"""Runs tests/js/dashboard_broker_refresh_test.mjs against the real
dashboard.html template's own <script> block.

`dashboard.html`'s JS previously never touched the broker account/
positions/pending-orders panels on a poll refresh -- only the initial
server-rendered HTML ever showed them, so the page would keep displaying
whatever broker state existed at the moment the page was first loaded (owner
review of commit e19da39). This proves the fix without a reimplementation
that could silently drift from the shipped code: the Node script requires
the actual template file and drives its real `renderBrokerPanels` function
against a small DOM stub.

Skipped, not failed, when `node` is not on PATH -- this adds coverage where
available rather than making the whole gate depend on a JS runtime being
installed everywhere these tests might run.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_NODE = shutil.which("node")
_SCRIPT = Path(__file__).resolve().parents[1] / "js" / "dashboard_broker_refresh_test.mjs"


@pytest.mark.skipif(_NODE is None, reason="node is not on PATH in this environment")
def test_the_shipped_dashboard_js_replaces_broker_state_on_a_new_poll() -> None:
    assert _NODE is not None  # narrows for mypy; skipif above guarantees this at runtime
    result = subprocess.run(
        [_NODE, str(_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"dashboard_broker_refresh_test.mjs failed:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "all assertions passed" in result.stdout
