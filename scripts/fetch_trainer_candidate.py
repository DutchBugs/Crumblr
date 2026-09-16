"""TRAINER/TRADER/CRUMBLR V1 -- SLICE 3: fetch a Trainer candidate artifact.

    uv run python scripts/fetch_trainer_candidate.py \\
        --trainer-base-url http://127.0.0.1:8766 \\
        --campaign-id CAM-CRUMBLR-SLICE1 \\
        --out candidate.json

Read-only against the Trainer's existing, already-reviewed
`GET /api/v1/campaigns/{campaign_id}/candidate-artifact` endpoint. Makes no
Crumblr database connection, no MT5 connection, no write of any kind on the
Crumblr side. Does not translate, verify, or otherwise interpret the
artifact -- that is the Static Agent's `candidate_verifier` module. This
script's only job is retrieving the candidate Trainer serves and saving it
to a local file, so it can be carried from Trainer to the Static Agent
host without either side needing direct network access to the other.
`get_candidate_artifact()` decodes the response as JSON and this script
re-serializes that decoded object -- it preserves the logical candidate
(every field and value Trainer sent), not the literal response bytes
(whitespace/formatting are not preserved). Downstream hash verification
(`candidate_verifier.translate_candidate`) recomputes its own canonical
JSON from the decoded object, so this is not a correctness concern.

Trainer's own `ConflictError` (no `RESEARCH_PROMISING` candidate exists yet
for this campaign, or the strategy has no executable `local_strategy`) is a
real, meaningful answer, not a transport failure -- printed and exited
non-zero, same as any other non-2xx response.
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
    get_candidate_artifact,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--trainer-base-url", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--trainer-api-key", default=None)
    parser.add_argument(
        "--out", required=True, help="path to write the fetched candidate artifact JSON to"
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--status-json",
        type=Path,
        default=None,
        help="also write a small always-written availability status (distinct from "
        "--out, which is only written on a 2xx response) -- for the dashboard's "
        "candidate-availability panel, which reads this file, never calls this "
        "script itself",
    )
    return parser.parse_args()


def _write_status(
    path: Path | None,
    *,
    run_id: str | None,
    campaign_id: str,
    http_status: int | None,
    available: bool,
    detail: Any,
    candidate: dict[str, Any] | None,
) -> None:
    if path is None:
        return
    identity = candidate.get("strategy_identity") if candidate is not None else None
    lineage = candidate.get("research_lineage") if candidate is not None else None
    evaluation = candidate.get("evaluation") if candidate is not None else None
    status = {
        "schema_version": 1,
        "run_id": run_id,
        "checked_at_utc": utc_now().isoformat(),
        "campaign_id": campaign_id,
        "http_status": http_status,
        "available": available,
        "detail": detail if not available else None,
        "candidate_strategy_spec_hash": (
            identity.get("candidate_strategy_spec_hash") if identity is not None else None
        ),
        "parent_strategy_key": (
            identity.get("parent_strategy_key") if identity is not None else None
        ),
        "parent_source_hash": (
            identity.get("parent_source_hash") if identity is not None else None
        ),
        "research_experiment_id": (lineage.get("experiment_id") if lineage is not None else None),
        "research_status": (evaluation.get("research_status") if evaluation is not None else None),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, default=str) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()

    config = TrainerClientConfig(base_url=args.trainer_base_url, api_key=args.trainer_api_key)
    try:
        status, body = get_candidate_artifact(config, campaign_id=args.campaign_id)
    except TrainerTransportError as error:
        print(f"BLOCKED: Trainer call failed: {error}", file=sys.stderr)
        _write_status(
            args.status_json,
            run_id=args.run_id,
            campaign_id=args.campaign_id,
            http_status=None,
            available=False,
            detail=str(error),
            candidate=None,
        )
        return 2

    print(f"=== Trainer response (HTTP {status}) ===")
    print(json.dumps(body, indent=2))

    if not (200 <= status < 300):
        print(f"\nnot writing {args.out}: Trainer returned HTTP {status}", file=sys.stderr)
        _write_status(
            args.status_json,
            run_id=args.run_id,
            campaign_id=args.campaign_id,
            http_status=status,
            available=False,
            detail=body,
            candidate=None,
        )
        return 1

    with Path(args.out).open("w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2)
        handle.write("\n")
    print(f"\nwrote {args.out}")
    _write_status(
        args.status_json,
        run_id=args.run_id,
        campaign_id=args.campaign_id,
        http_status=status,
        available=True,
        detail=None,
        candidate=body,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
