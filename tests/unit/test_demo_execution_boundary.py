"""Static guards on the Pepperstone DEMO execution boundary (docs/demo_execution_boundary.md).

These do not talk to a broker. They pin the *shape* of the boundary so a later change
cannot quietly widen it: who may reach the real MT5 `order_send`, which flags ship
closed, that no LIVE configuration exists, and that the canary wiring is opt-in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from crumblr.config import load_config
from crumblr.domain.enums import Environment

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "crumblr"
SCRIPTS = ROOT / "scripts"
CONFIG = ROOT / "config"

ALLOWED_DEMO_GATEWAY_IMPORTERS = {
    "scripts/agent_canary_execution.py",
    "scripts/close_demo_canary_position.py",
    "scripts/cancel_demo_canary_pending_order.py",
}


def _py_files(base: Path) -> list[Path]:
    return [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


class TestOnlyOneModuleCanReachTheRealOrderSend:
    def test_the_mt5_module_order_send_is_called_only_from_demo_execution(self) -> None:
        pattern = re.compile(
            r"\bmodule\.order_send\(|\bmt5\.order_send\(|\bMetaTrader5\.order_send\("
        )
        offenders = {
            _rel(p)
            for p in _py_files(SRC) + _py_files(SCRIPTS)
            if pattern.search(p.read_text(encoding="utf-8"))
        }
        assert offenders == {"src/crumblr/mt5_gateway/demo_execution.py"}

    def test_the_real_gateway_is_imported_only_by_the_three_reviewed_scripts(self) -> None:
        importer = re.compile(
            r"^\s*(?:from\s+crumblr\.mt5_gateway\.demo_execution\s+import|"
            r"import\s+crumblr\.mt5_gateway\.demo_execution)",
            re.MULTILINE,
        )
        importers = {
            _rel(p)
            for p in _py_files(SRC) + _py_files(SCRIPTS)
            if importer.search(p.read_text(encoding="utf-8"))
        }
        assert importers == ALLOWED_DEMO_GATEWAY_IMPORTERS


class TestCanaryWiringIsOptInAndScopedToAPermit:
    def _script(self) -> str:
        return (SCRIPTS / "agent_canary_execution.py").read_text(encoding="utf-8")

    def test_the_real_adapter_is_constructed_only_inside_the_permit_branch(self) -> None:
        text = self._script()
        assert "entry_submission_adapter = None" in text
        branch = text.index("if args.canary_permit_id is not None:")
        construction = text.index("DemoOrderSendMt5Gateway(adapter, client)")
        assert construction > branch
        assert text.count("DemoOrderSendMt5Gateway(adapter, client)") == 1

    def test_without_a_permit_there_is_no_activation_watermark_so_every_proposal_is_ineligible(
        self,
    ) -> None:
        text = self._script()
        assert "canary_activation_watermark = None" in text
        assert "activation_watermark=canary_activation_watermark" in text

    def test_the_canary_overlay_is_applied_only_when_explicitly_requested(self) -> None:
        text = self._script()
        assert '"--apply-canary-config"' in text
        assert 'action="store_true"' in text[text.index('"--apply-canary-config"') :][:200]
        assert "if args.apply_canary_config:" in text


class TestShippedConfigurationIsClosedAndDemoPinned:
    @pytest.fixture()
    def paper(self):  # type: ignore[no-untyped-def]
        return load_config(Environment.PAPER, config_dir=CONFIG)

    def test_no_live_configuration_exists(self) -> None:
        assert not (CONFIG / "live.yaml").exists()

    def test_loading_a_live_configuration_is_refused_without_the_explicit_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CRUMBLR_ALLOW_LIVE", raising=False)
        with pytest.raises(PermissionError):
            load_config(Environment.LIVE, config_dir=CONFIG)

    def test_every_submission_flag_ships_closed(self, paper) -> None:  # type: ignore[no-untyped-def]
        assert paper.execution.submission_enabled is False
        assert paper.execution.feedback_2_0_approved is False
        assert paper.execution.flatten_submission_enabled is False
        assert paper.execution.approved_canary_account_ref is None
        assert paper.live_trading_acknowledged is False

    def test_the_account_guard_pins_the_pepperstone_demo_target(self, paper) -> None:  # type: ignore[no-untyped-def]
        guard = paper.account_guard
        assert guard.require_demo_account is True
        assert guard.expected_server == "PepperstoneUK-Demo"
        assert guard.expected_currency == "EUR"
        assert guard.expected_leverage == 30
        assert paper.environment is Environment.PAPER

    def test_the_canary_overlay_exists_but_is_not_part_of_the_default_load(self, paper) -> None:  # type: ignore[no-untyped-def]
        overlay = (CONFIG / "agent_canary_demo.yaml").read_text(encoding="utf-8")
        assert "submission_enabled: true" in overlay  # it does open the gate...
        assert paper.execution.submission_enabled is False  # ...but only when applied explicitly

    def test_the_canary_overlay_never_opens_the_automatic_flatten_flag(self) -> None:
        overlay = (CONFIG / "agent_canary_demo.yaml").read_text(encoding="utf-8")
        assert "flatten_submission_enabled: true" not in overlay
