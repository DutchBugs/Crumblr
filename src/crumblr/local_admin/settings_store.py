"""Non-secret local host settings: a plain JSON file, gitignored.

No secret-shaped field is ever accepted here -- `write_settings` rejects
any key outside `registry.ALLOWED_SETTINGS_KEYS`, and that allowlist itself
carries nothing secret. This is a deliberately separate file from
`config/agent_paper_soak.yaml` (PAPER_LITE's own tracked-shape settings,
read-only from this app's perspective) and from any `config/*.yaml`
(`base.yaml`/`paper.yaml`/`live.yaml`) -- this module never opens any of
those, so it has no path by which it could touch `ExecutionConfig` or
`live_trading_acknowledged` even by accident.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crumblr.local_admin.registry import ALLOWED_SETTINGS_KEYS

DEFAULT_SETTINGS_PATH = Path("config/local_host_settings.json")

DEFAULTS: dict[str, Any] = {
    "mt5_terminal_path": None,
    "static_agent_host": "127.0.0.1",
    "static_agent_port": 8765,
    "dashboard_host": "127.0.0.1",
    "dashboard_port": 8050,
    "local_host_admin_port": 8877,
    "agent_id": None,
    "assignment_id": None,
    "canonical_symbol": "EUR/USD",
    "timeframe": "M5",
}


class UnknownSettingError(ValueError):
    """A write named a key outside `registry.ALLOWED_SETTINGS_KEYS`."""


def read_settings(path: Path = DEFAULT_SETTINGS_PATH) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        merged.update({k: v for k, v in payload.items() if k in ALLOWED_SETTINGS_KEYS})
    return merged


def write_settings(updates: dict[str, Any], path: Path = DEFAULT_SETTINGS_PATH) -> dict[str, Any]:
    unknown = sorted(set(updates) - set(ALLOWED_SETTINGS_KEYS))
    if unknown:
        raise UnknownSettingError(f"not a recognised local host setting: {unknown}")
    current = read_settings(path)
    current.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return current
