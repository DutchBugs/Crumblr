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
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    config = TrainerClientConfig(base_url=args.trainer_base_url, api_key=args.trainer_api_key)
    try:
        status, body = get_candidate_artifact(config, campaign_id=args.campaign_id)
    except TrainerTransportError as error:
        print(f"BLOCKED: Trainer call failed: {error}", file=sys.stderr)
        return 2

    print(f"=== Trainer response (HTTP {status}) ===")
    print(json.dumps(body, indent=2))

    if not (200 <= status < 300):
        print(f"\nnot writing {args.out}: Trainer returned HTTP {status}", file=sys.stderr)
        return 1

    with Path(args.out).open("w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2)
        handle.write("\n")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
