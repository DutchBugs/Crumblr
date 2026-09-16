"""FULL RUN 1: read-only Trainer reachability/campaign status check.

    uv run python scripts/check_trainer_status.py \\
        --trainer-base-url http://127.0.0.1:8766 \\
        --campaign-id CAM-FULLRUN1-20260916 \\
        --json var/trainer_status.json \\
        --run-id FULLRUN1-20260916-abcdef

Read-only: two outbound HTTP GETs (`/healthz`, `/api/v1/campaigns/{id}`),
no POST, no Crumblr database access, no MT5. Meant to be re-run
periodically by hand (or a scheduled task) during a FULL RUN observation
window; the dashboard only ever reads the `--json` file this script
writes, never calls Trainer itself -- see `dashboard/trainer_panel.py`.

Writes the snapshot even when Trainer is unreachable or the campaign does
not exist yet -- the dashboard must be able to show "Trainer
UNREACHABLE" or "campaign NOT FOUND" as a real, distinguishable status,
not silently keep showing a stale success from an earlier run. Only a
hard local error (cannot write the output file) exits non-zero.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from crumblr.domain.timeutils import utc_now
from crumblr.trainer_bridge.trainer_client import (
    TrainerClientConfig,
    TrainerTransportError,
    get_campaign,
    get_healthz,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--trainer-base-url", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--trainer-api-key", default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--json", required=True, type=Path, help="path to write the status snapshot JSON to"
    )
    return parser.parse_args()


def _check_reachability(config: TrainerClientConfig) -> dict[str, Any]:
    try:
        status, body = get_healthz(config)
    except TrainerTransportError as error:
        return {"reachable": False, "http_status": None, "detail": str(error)}
    return {"reachable": 200 <= status < 300, "http_status": status, "detail": body}


def _check_campaign(config: TrainerClientConfig, *, campaign_id: str) -> dict[str, Any]:
    try:
        status, body = get_campaign(config, campaign_id=campaign_id)
    except TrainerTransportError as error:
        return {"found": False, "http_status": None, "detail": str(error), "campaign": None}
    if 200 <= status < 300:
        return {"found": True, "http_status": status, "detail": None, "campaign": body}
    return {"found": False, "http_status": status, "detail": body, "campaign": None}


def main() -> int:
    args = parse_args()
    config = TrainerClientConfig(base_url=args.trainer_base_url, api_key=args.trainer_api_key)

    reachability = _check_reachability(config)
    campaign = _check_campaign(config, campaign_id=args.campaign_id)

    snapshot = {
        "schema_version": 1,
        "run_id": args.run_id,
        "checked_at_utc": utc_now().isoformat(),
        "trainer_base_url": args.trainer_base_url,
        "campaign_id": args.campaign_id,
        "reachability": reachability,
        "campaign": campaign,
    }

    print(json.dumps(snapshot, indent=2, default=str))

    try:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(snapshot, indent=2, default=str) + "\n", encoding="utf-8")
    except OSError as error:
        print(f"error: could not write {args.json}: {error}", file=sys.stderr)
        return 2

    print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
