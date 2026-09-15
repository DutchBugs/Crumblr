"""FIRST REAL DEMO CANARY — EXPLICIT POSITION CLOSE.

The smallest possible one-shot runner around the already-reviewed
`DemoOrderSendMt5Gateway.close_position(FlattenInstruction)`. Never touches
Risk, Policy, Supervisor, entry execution, canary submission logic, or the
automatic-flatten subsystem (`OperatorControls`/`FlattenRequestStore`/
`FlattenEventStore`/`ExecutionConfig.flatten_submission_enabled`) — this is a
manual, owner-directed, one-shot close of one already-identified broker
position, nothing else.

    uv run python scripts/close_demo_canary_position.py \\
        --ticket 89517316 \\
        --originating-order-request-id e845ddac-028e-5bf7-9f86-785119674752 \\
        --expected-broker-symbol EURUSD \\
        --expected-side BUY \\
        --expected-volume 0.02 \\
        --environment paper

Refuses (exit 2, no mutating call attempted) unless every one of these holds
against a fresh broker read taken immediately before any mutation:

  - exactly one open position exists for `--ticket`;
  - its symbol/side/volume match `--expected-broker-symbol`/`--expected-side`/
    `--expected-volume` exactly;
  - its `magic` equals `mt5_magic_number(--originating-order-request-id)` —
    proof the position actually came from that order, not a coincidence of
    ticket numbering.

On a clean match: constructs exactly one `FlattenInstruction` (`close_side`
is always the inverse of the observed position side, `volume` is always the
position's own currently-open volume, never re-derived or hardcoded) and
calls `close_position()` exactly once. No retry loop — a rejected,
partially-filled, or transport-ambiguous close is reported and this script
exits non-zero; running it again is a new, separate, explicitly authorized
decision, never this script's own job.

A fresh broker readback (another `positions()` call) follows the close call
regardless of its outcome, and is the sole basis for the PASS/BLOCKED
verdict this script prints.
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from crumblr.config import load_config
from crumblr.domain.enums import Environment, OrderState, Side
from crumblr.domain.hashing import mt5_magic_number
from crumblr.domain.models import FlattenInstruction, PositionState
from crumblr.domain.timeutils import utc_now
from crumblr.mt5_gateway.client import (
    MissingCredentialsError,
    Mt5CallFailedError,
    Mt5Client,
    read_credentials,
)
from crumblr.mt5_gateway.demo_execution import DemoOrderSendMt5Gateway
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.mt5_gateway.readonly import AccountGuardError

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ticket", type=int, required=True)
    parser.add_argument(
        "--originating-order-request-id",
        type=UUID,
        required=True,
        help="the order_request_id whose SUBMISSION_STARTED/FILLED event opened this "
        "ticket -- verified against the position's own magic, not trusted blindly",
    )
    parser.add_argument("--canonical-symbol", default="EUR/USD")
    parser.add_argument("--expected-broker-symbol", required=True)
    parser.add_argument("--expected-side", type=Side, required=True, choices=(Side.BUY, Side.SELL))
    parser.add_argument("--expected-volume", type=Decimal, required=True)
    parser.add_argument("--environment", default="paper")
    return parser.parse_args()


def _find_target_position(
    positions: tuple[PositionState, ...], *, ticket: int
) -> tuple[PositionState | None, str | None]:
    """Pure: locate `ticket` among `positions`, or say exactly why not.

    Returns `(position, None)` on a clean single match, or `(None, reason)`
    — never an ambiguous partial match. Kept pure and separate from any MT5
    call so it is unit-testable without a terminal.
    """
    matches = [p for p in positions if p.ticket == ticket]
    if not matches:
        return None, f"ticket {ticket} not found among {len(positions)} open position(s)"
    if len(matches) > 1:
        return None, f"ticket {ticket} matched {len(matches)} positions -- expected exactly one"
    return matches[0], None


def _verify_target_position(
    position: PositionState,
    *,
    expected_broker_symbol: str,
    expected_side: Side,
    expected_volume: Decimal,
    originating_order_request_id: UUID,
) -> list[str]:
    """Pure: every reason `position` does NOT match what the caller named.

    Collected rather than short-circuited, so one BLOCKED report shows
    every mismatch at once, not just the first (the same discipline
    `risk/policies.py::evaluate()` and `_canary_permit_scope_mismatches()`
    already use for the same reason).
    """
    problems: list[str] = []
    if position.broker_symbol != expected_broker_symbol:
        problems.append(
            f"broker_symbol mismatch: expected {expected_broker_symbol!r}, "
            f"observed {position.broker_symbol!r}"
        )
    if position.side is not expected_side:
        problems.append(
            f"side mismatch: expected {expected_side.value}, observed {position.side.value}"
        )
    if position.volume != expected_volume:
        problems.append(f"volume mismatch: expected {expected_volume}, observed {position.volume}")
    expected_magic = mt5_magic_number(originating_order_request_id)
    if position.magic != expected_magic:
        problems.append(
            f"magic mismatch: expected {expected_magic} (derived from "
            f"{originating_order_request_id}), observed {position.magic!r} -- this "
            "position may not be the one that order actually opened"
        )
    return problems


def _build_close_instruction(position: PositionState) -> FlattenInstruction:
    """Exactly `DemoOrderSendMt5Gateway.close_all_positions`'s own

    construction, for one named position instead of every open one:
    `close_side` is always the inverse of the observed side, `volume` is
    always the position's own currently-open volume. `crossed_weekly_close
    =False` — this is a same-day manual close, not the automatic weekly-
    flatten path (ADR-012), which never runs here.
    """
    close_side = Side.SELL if position.side is Side.BUY else Side.BUY
    return FlattenInstruction(
        flatten_request_id=uuid4(),
        ticket=position.ticket,
        broker_symbol=position.broker_symbol,
        position_side=position.side,
        close_side=close_side,
        volume=position.volume,
        open_price=position.open_price,
        opened_at_utc=position.opened_at_utc,
        magic=position.magic,
        crossed_weekly_close=False,
        observed_at_utc=utc_now(),
    )


def main() -> int:
    args = parse_args()
    environment = Environment(args.environment)

    try:
        mt5_credentials = read_credentials()
    except MissingCredentialsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    config = load_config(environment, config_dir=REPO_ROOT / "config")

    client = Mt5Client()
    client.connect(mt5_credentials, terminal_path=os.environ.get("CRUMBLR_MT5_TERMINAL_PATH"))
    try:
        order_check_gateway = OrderCheckMt5Gateway(
            client,
            config.account_guard,
            canonical_symbol=args.canonical_symbol,
            expected_broker_symbol=args.expected_broker_symbol,
        )
        gateway = DemoOrderSendMt5Gateway(order_check_gateway, client)

        print("=" * 78)
        print("  DEMO CANARY EXPLICIT POSITION CLOSE")
        print("=" * 78)

        try:
            # Explicit, visible DEMO account/server guard re-run -- separate
            # from, and before, close_position()'s own internal guard call.
            account = gateway.account()
        except AccountGuardError as error:
            print(f"BLOCKED: account guard failed: {error}")
            return 2
        print(f"  account guard PASSED -- server={account.server} is_demo={account.is_demo}")

        # Fresh broker position read, before any mutation.
        positions = gateway.positions()
        print(f"  pre-close open positions (fresh read) = {len(positions)}")

        position, not_found_reason = _find_target_position(positions, ticket=args.ticket)
        if position is None:
            print(f"BLOCKED: {not_found_reason}")
            return 2

        problems = _verify_target_position(
            position,
            expected_broker_symbol=args.expected_broker_symbol,
            expected_side=args.expected_side,
            expected_volume=args.expected_volume,
            originating_order_request_id=args.originating_order_request_id,
        )
        if problems:
            print("BLOCKED: target position does not match the expected shape:")
            for problem in problems:
                print(f"  - {problem}")
            return 2

        print(
            f"  target position verified: ticket={position.ticket} "
            f"{position.broker_symbol} {position.side.value} {position.volume} "
            f"magic={position.magic} opened_at_utc={position.opened_at_utc.isoformat()}"
        )

        instruction = _build_close_instruction(position)
        print(
            f"  closing: flatten_request_id={instruction.flatten_request_id} "
            f"close_side={instruction.close_side.value} volume={instruction.volume}"
        )
        try:
            result = gateway.close_position(instruction)
        except Mt5CallFailedError as error:
            print(f"BLOCKED: close_position transport failure: {error}")
            return 3

        print(
            f"  close_position result: state={result.state.value} retcode={result.retcode} "
            f"comment={result.retcode_comment!r}"
        )
        print(
            f"  mt5_order_ticket={result.mt5_order_ticket} mt5_deal_ticket={result.mt5_deal_ticket}"
        )
        print(f"  executed_price={result.executed_price} executed_volume={result.executed_volume}")

        # Fresh broker readback after the close call, regardless of its
        # outcome -- this, not the close response alone, is what decides
        # PASS/BLOCKED below.
        post_positions = gateway.positions()
        still_open = [p for p in post_positions if p.ticket == args.ticket]
        symbol_count = sum(
            1 for p in post_positions if p.broker_symbol == args.expected_broker_symbol
        )
        opposite_side_opened = [
            p
            for p in post_positions
            if p.broker_symbol == args.expected_broker_symbol
            and p.side is not args.expected_side
            and p.ticket != args.ticket
        ]

        print(f"  post-close open positions (fresh read) = {len(post_positions)}")
        print(f"  ticket {args.ticket} still open? {bool(still_open)}")
        print(f"  {args.expected_broker_symbol} open position count = {symbol_count}")
        print(f"  opposite-side position accidentally opened? {bool(opposite_side_opened)}")

        success = (
            result.state is OrderState.FILLED
            and not still_open
            and symbol_count == 0
            and not opposite_side_opened
        )

        if success:
            print("\nDEMO CANARY EXPLICIT CLOSE -- PASS")
            return 0

        print("\nDEMO CANARY EXPLICIT CLOSE -- BLOCKED (post-close state not flat / not clean)")
        return 3
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
