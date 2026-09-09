"""Run the Local Host Admin app — manual/on-demand only, never in the

Scheduled Task (owner's amendment 2: this must not join the trading-runtime
supervision chain).

    uv run python scripts/local_host_admin.py

Serves a localhost-only settings/secrets UI, separate process and port from
Dashboard v0. A fresh admin token is generated every run and printed below —
paste it into the page's token field. Nothing here is written to disk except
Windows Credential Manager entries (on explicit Save) and
`config/local_host_settings.json` (on explicit Save) — never to Git, YAML,
`crumblr_soak`, a journal, or a log line.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

import uvicorn

from crumblr.local_admin.api import create_app
from crumblr.local_admin.settings_store import DEFAULT_SETTINGS_PATH

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument(
        "--settings-path",
        type=Path,
        default=REPO_ROOT / DEFAULT_SETTINGS_PATH,
    )
    args = parser.parse_args()

    if args.host != "127.0.0.1":
        print("error: Local Host Admin must bind to 127.0.0.1 only", file=sys.stderr)
        return 2

    admin_token = secrets.token_urlsafe(32)
    app = create_app(admin_token=admin_token, settings_path=args.settings_path)

    print(f"Crumblr Local Host Admin at http://{args.host}:{args.port}/")
    print(f"  admin token (paste into the page): {admin_token}")
    print("  manual/on-demand only — not started by the Scheduled Task supervisor")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
