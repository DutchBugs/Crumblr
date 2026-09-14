"""FIRST DEMO CANARY — a minimal, temporary, deterministic external Agent

fixture speaking the exact same `neutral-agent-response-1.0` HTTP contract
(`agent_gateway/neutral_agent_client.py`) as the real Static Agent, so the
first real broker-side canary does not need to wait for a real ICT signal.

    uv run python scripts/deterministic_canary_agent.py \\
        --agent-id <this fixture's own registered agent_id> \\
        --port 8766

**Does not touch the real ICT Static Agent or Crumblr Core in any way.**
This is a separate HTTP process, on its own port, with its own registered
`AgentIdentity`/`TradingAssignment`/`StrategyArtifact` (provisioned
separately via the existing, unmodified `scripts/setup_paper_lite_agent.py`
— never a new provisioning path). `scripts/agent_canary_execution.py` is
used completely unchanged against this fixture's URL; nothing in Core
(`AgentGateway`, `agent_gateway.decision_path`, `ExecutionOrchestrator`,
`DecisionCapsule`) is aware this is a fixture rather than the real fork.

**One-shot by construction.** The first `POST /v1/trader/evaluate` this
process ever answers returns exactly one deterministic `TradeProposal`
(a fixed BUY test vector — never a market view, never an ICT signal) and
durably marks itself used (`--marker-file`, survives a restart of this
process). Every subsequent call, for the rest of this process's life or
any future run sharing the same marker file, returns `NoTradeDecision`
instead. This is a second, independent one-shot boundary on top of the
real submission limiter (the one-shot `CanaryPermit` downstream) — not a
replacement for it.

**No direct broker call, no bypass.** This process never imports MT5,
never touches PostgreSQL, never talks to `AgentGateway`/Risk/Policy/
Supervisor/`ExecutionOrchestrator` itself — it only answers the one HTTP
request the real Agent Gateway client (`HttpNeutralAgentClient`) sends,
exactly as a real Agent host would, and lets the *real*, unmodified
Gateway -> Core Risk -> Policy -> Supervisor -> `DecisionCapsule` chain
decide everything downstream. Reference price, instrument point/
stops_level and the assignment/context identity fields it echoes back all
come from the real inbound context Crumblr itself already built from real
broker/market state (`agent_gateway/market_context.py`) — nothing here is
fabricated market data.

**Deliberately zero-dependency** (stdlib `http.server`, same philosophy
`static_agent_client.py`'s own docstring states for the outbound side).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from datetime import timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from crumblr.agent_gateway.contracts import NoTradeDecision, TradeProposal
from crumblr.agent_gateway.market_context import AgentMarketContextV1
from crumblr.agent_gateway.neutral_agent_client import NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION
from crumblr.domain.enums import EntryType, Side
from crumblr.domain.timeutils import utc_now

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_REQUEST_BYTES = 1024 * 1024
_FIVE_MINUTES = timedelta(minutes=5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--agent-id",
        type=UUID,
        required=True,
        help="this fixture's own registered AgentIdentity -- must exactly match what "
        "scripts/setup_paper_lite_agent.py registered, and what agent_canary_execution.py's "
        "own --agent-id is called with",
    )
    parser.add_argument(
        "--bearer-token-env",
        default="LOCAL_AGENT_SERVICE_TOKEN",
        help="env var name (already set in this process's own environment, sourced fresh "
        "from Credential Manager by the launcher -- never a CLI value) holding the shared "
        "bearer token this fixture requires on every request, matching "
        "CRUMBLR_PAPER_LITE_AGENT_TOKEN on the caller's side",
    )
    parser.add_argument(
        "--marker-file",
        type=Path,
        default=REPO_ROOT / "var" / "deterministic_canary_agent_used.marker",
        help="durable one-shot marker -- once this file exists, every future request from "
        "any process sharing it returns NO_TRADE, never a second TradeProposal",
    )
    parser.add_argument(
        "--side",
        default="BUY",
        choices=("BUY", "SELL"),
        help="the fixed, deterministic test-vector side -- not a market view",
    )
    parser.add_argument("--stop-distance-points", type=int, default=100)
    parser.add_argument("--target-distance-points", type=int, default=200)
    parser.add_argument("--requested-risk-fraction", type=Decimal, default=Decimal("0.001"))
    parser.add_argument("--reason-code", default="OWNER_DEMO_EXECUTION_CANARY")
    return parser.parse_args()


def _build_proposal(
    context: AgentMarketContextV1,
    *,
    agent_id: UUID,
    side: Side,
    stop_distance_points: int,
    target_distance_points: int,
    requested_risk_fraction: Decimal,
    reason_code: str,
) -> TradeProposal:
    """Pure: every field either echoes the real inbound context or is one

    of this fixture's own fixed, deterministic constants. No lot size, no
    broker call, no strategy detection -- Risk/Policy/order_check remain
    fully authoritative downstream, exactly as the module docstring states.
    """
    point = context.instrument.point
    stops_floor = max(context.instrument.stops_level, 0)
    # Never below the broker's own stops_level floor, whatever this
    # fixture's own configured distance asked for.
    effective_stop_points = max(stop_distance_points, stops_floor + 1)
    effective_target_points = max(target_distance_points, effective_stop_points + 1)

    if side is Side.BUY:
        reference_price = context.market.ask
        stop_loss_price = reference_price - (Decimal(effective_stop_points) * point)
        take_profit_price = reference_price + (Decimal(effective_target_points) * point)
    else:
        reference_price = context.market.bid
        stop_loss_price = reference_price + (Decimal(effective_stop_points) * point)
        take_profit_price = reference_price - (Decimal(effective_target_points) * point)

    now = utc_now()
    return TradeProposal(
        proposal_id=uuid4(),
        agent_id=agent_id,
        assignment_id=context.provenance.assignment_id,
        context_hash=context.provenance.content_hash,
        strategy_artifact_hash=context.provenance.strategy_artifact_hash,
        side=side,
        entry_type=EntryType.MARKET,
        reference_price=reference_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        confidence=1.0,
        requested_risk_fraction=requested_risk_fraction,
        reason_codes=(reason_code,),
        evidence_refs=(),
        submitted_at_utc=now,
        expires_at_utc=now + _FIVE_MINUTES,
    )


def _build_no_trade(
    context: AgentMarketContextV1, *, agent_id: UUID, reason_code: str
) -> NoTradeDecision:
    return NoTradeDecision(
        decision_id=uuid4(),
        agent_id=agent_id,
        assignment_id=context.provenance.assignment_id,
        context_hash=context.provenance.content_hash,
        reason_codes=(reason_code,),
        decided_at_utc=utc_now(),
    )


def _claim_one_shot(marker_file: Path) -> bool:
    """Atomically claims the one-shot slot. Returns `True` only for the

    caller that actually created the marker file -- `os.O_EXCL` makes a
    concurrent second claim impossible to win, the same discipline every
    durable claim elsewhere in this codebase uses (`INSERT ... ON CONFLICT
    DO NOTHING RETURNING`, here at the filesystem level instead of SQL).
    """
    marker_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(marker_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"claimed_at_utc={utc_now().isoformat()}\n")
    return True


def _make_handler(args: argparse.Namespace) -> type[BaseHTTPRequestHandler]:
    expected_token = os.environ.get(args.bearer_token_env)
    if not expected_token:
        print(f"error: {args.bearer_token_env} is not set in this process's environment")
        raise SystemExit(2)
    side = Side(args.side)

    class Handler(BaseHTTPRequestHandler):
        # No log_message() override: the base class's default already
        # writes exactly this to stderr, which the launcher redirects to
        # this fixture's own log file.

        def _reject(self, status: int, detail: str) -> None:
            body = json.dumps({"error": detail}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/v1/trader/evaluate":
                self._reject(404, "not found")
                return
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {expected_token}":
                self._reject(401, "unauthorized")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                self._reject(413, "request too large or empty")
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
                context = AgentMarketContextV1.model_validate(payload)
            except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
                self._reject(422, f"invalid AgentMarketContextV1: {error}")
                return

            decision: TradeProposal | NoTradeDecision
            if _claim_one_shot(args.marker_file):
                decision_type = "TRADE_PROPOSAL"
                decision = _build_proposal(
                    context,
                    agent_id=args.agent_id,
                    side=side,
                    stop_distance_points=args.stop_distance_points,
                    target_distance_points=args.target_distance_points,
                    requested_risk_fraction=args.requested_risk_fraction,
                    reason_code=args.reason_code,
                )
            else:
                decision_type = "NO_TRADE"
                decision = _build_no_trade(
                    context,
                    agent_id=args.agent_id,
                    reason_code=f"{args.reason_code}_ALREADY_USED",
                )

            body = json.dumps(
                {
                    "schema_version": NEUTRAL_AGENT_RESPONSE_SCHEMA_VERSION,
                    "decision_type": decision_type,
                    "decision": decision.model_dump(mode="json"),
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main() -> int:
    args = parse_args()
    already_used = args.marker_file.exists()
    handler = _make_handler(args)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"DETERMINISTIC_CANARY_AGENT listening on http://{args.host}:{args.port} "
        f"agent_id={args.agent_id} side={args.side} "
        f"already_used={already_used} marker_file={args.marker_file}"
    )
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
