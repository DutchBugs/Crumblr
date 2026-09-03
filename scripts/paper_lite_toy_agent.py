"""Run the local-only deterministic PAPER_LITE toy Agent."""

from __future__ import annotations

import argparse
import os
from decimal import Decimal
from uuid import UUID

import uvicorn

from crumblr.application.paper_lite_toy_agent import ToyAgentMode, create_toy_agent_app

TOKEN_ENV = "CRUMBLR_PAPER_LITE_TOY_TOKEN"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-id", type=UUID, required=True)
    parser.add_argument("--mode", type=ToyAgentMode, choices=list(ToyAgentMode), required=True)
    parser.add_argument("--requested-risk", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = os.getenv(TOKEN_ENV)
    if not token:
        raise SystemExit(f"set {TOKEN_ENV}; the toy Agent never accepts unauthenticated calls")
    app = create_toy_agent_app(
        agent_id=args.agent_id,
        mode=args.mode,
        requested_risk_fraction=args.requested_risk,
        bearer_token=token,
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
