"""The explicit allowlists Local Host Admin is permitted to read or write.

Both are allowlists, not blocklists, deliberately: a name has to be listed
here *before* it is reachable through the Credential Manager or
`local_host_settings.json` surface, so a future addition to either store
can never become reachable through this admin app just because nobody
remembered to add it to an exclusion list. Nothing here, and nothing
reachable from this whole `local_admin` package, names or imports
`submission_enabled`, `feedback_2_0_approved`, `flatten_submission_enabled`,
`live_trading_acknowledged`, a HALT reset, or a `TradingAssignment` mutation
-- see `tests/unit/test_local_admin_boundary.py`.
"""

from __future__ import annotations

ALLOWED_SECRET_NAMES: tuple[str, ...] = (
    "CRUMBLR_MT5_LOGIN",
    "CRUMBLR_MT5_PASSWORD",
    "CRUMBLR_MT5_SERVER",
    "CRUMBLR_DATABASE_URL",
    "CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL",
    "LOCAL_AGENT_SERVICE_TOKEN",
)
"""Six Credential Manager entries. `LOCAL_AGENT_SERVICE_TOKEN` is the one

shared bearer the Static Agent server and PAPER_LITE's client both use --
there is deliberately no separate `CRUMBLR_TRADER_SERVICE_TOKEN` /
`CRUMBLR_PAPER_LITE_AGENT_TOKEN` pair to keep in sync by hand; the
supervisor injects this single stored value under both env-var names."""

ALLOWED_SETTINGS_KEYS: tuple[str, ...] = (
    "mt5_terminal_path",
    "static_agent_host",
    "static_agent_port",
    "dashboard_host",
    "dashboard_port",
    "local_host_admin_port",
    "agent_id",
    "assignment_id",
    "canonical_symbol",
    "timeframe",
)
"""Non-secret, plain-JSON settings. Deliberately excludes anything that

would let this surface influence a trading decision, an execution gate, or
which `TradingAssignment`/`StrategyArtifact` is authoritative -- `agent_id`/
`assignment_id` here are *display* pointers this app never issues or
mutates in `crumblr_soak`, only shows back to the owner alongside a Test
result. Code-commit pins (`c775333`/`dcc3770`) are deliberately absent too:
those are read from `git rev-parse` for display, never a settable field --
making them editable here would be exactly the silent-drift risk the
"exact runtime SHAs remain" requirement guards against."""
