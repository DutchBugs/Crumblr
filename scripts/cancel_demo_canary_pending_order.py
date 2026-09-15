"""ICT LIMIT DEMO EXECUTION — EXPLICIT PENDING ORDER CANCEL.

The smallest possible one-shot runner around the already-reviewed
`DemoOrderSendMt5Gateway.cancel_pending_order(order_id)`. Mirrors
`close_demo_canary_position.py` as closely as possible. Never touches Risk,
Policy, Supervisor, entry execution, canary submission logic, or the
automatic-flatten subsystem — this is a manual, owner-directed, one-shot
cancel of one already-identified resting pending order, nothing else.
`cancel_pending_orders` (plural, cancel-all) is never used here.

    uv run python scripts/cancel_demo_canary_pending_order.py \\
        --order-id 800001 \\
        --originating-order-request-id <order_request_id> \\
        --expected-broker-symbol EURUSD \\
        --environment paper

Refuses (exit 2, no mutating call attempted) unless every one of these holds
against a fresh broker read taken immediately before any mutation:

  - exactly one pending order exists for `--order-id`;
  - its `broker_symbol` matches `--expected-broker-symbol` exactly;
  - its `magic` equals `mt5_magic_number(--originating-order-request-id)` —
    proof the pending order actually came from that request, not a
    coincidence of order-id numbering.

On a clean match: calls `cancel_pending_order(order_id)` exactly once. No
retry loop — a rejected or transport-ambiguous cancel is reported and this
script exits non-zero; running it again is a new, separate, explicitly
authorized decision, never this script's own job.

A fresh broker readback (another `pending_orders()` call) follows the
cancel attempt regardless of its outcome, and is the sole basis (together
with the cancel response, for the non-ambiguous case) for the PASS/BLOCKED
verdict this script prints. A transport-ambiguous cancel never reports
PASS, however the readback looks.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from uuid import UUID

from crumblr.config import load_config
from crumblr.domain.enums import Environment
from crumblr.domain.hashing import mt5_magic_number
from crumblr.domain.models import PendingOrderState
from crumblr.mt5_gateway.client import (
    MissingCredentialsError,
    Mt5CallFailedError,
    Mt5Client,
    read_credentials,
)
from crumblr.mt5_gateway.demo_execution import DemoOrderSendMt5Gateway, PendingOrderCancelResult
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.mt5_gateway.readonly import AccountGuardError

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--order-id", type=int, required=True)
    parser.add_argument(
        "--originating-order-request-id",
        type=UUID,
        required=True,
        help="the order_request_id whose SUBMITTED event placed this pending order -- "
        "verified against the order's own magic, not trusted blindly",
    )
    parser.add_argument("--canonical-symbol", default="EUR/USD")
    parser.add_argument("--expected-broker-symbol", required=True)
    parser.add_argument("--environment", default="paper")
    return parser.parse_args()


def _find_target_pending_order(
    pending_orders: tuple[PendingOrderState, ...], *, order_id: int
) -> tuple[PendingOrderState | None, str | None]:
    """Pure: locate `order_id` among `pending_orders`, or say exactly why not.

    Returns `(order, None)` on a clean single match, or `(None, reason)` —
    never an ambiguous partial match. Kept pure and separate from any MT5
    call so it is unit-testable without a terminal.
    """
    matches = [o for o in pending_orders if o.order_id == order_id]
    if not matches:
        return (
            None,
            f"order_id {order_id} not found among {len(pending_orders)} pending order(s)",
        )
    if len(matches) > 1:
        return (
            None,
            f"order_id {order_id} matched {len(matches)} pending orders -- expected exactly one",
        )
    return matches[0], None


def _verify_target_pending_order(
    order: PendingOrderState,
    *,
    expected_broker_symbol: str,
    originating_order_request_id: UUID,
) -> list[str]:
    """Pure: every reason `order` does NOT match what the caller named.

    Collected rather than short-circuited, so one BLOCKED report shows
    every mismatch at once, not just the first (the same discipline
    `close_demo_canary_position.py::_verify_target_position` already uses).
    """
    problems: list[str] = []
    if order.broker_symbol != expected_broker_symbol:
        problems.append(
            f"broker_symbol mismatch: expected {expected_broker_symbol!r}, "
            f"observed {order.broker_symbol!r}"
        )
    expected_magic = mt5_magic_number(originating_order_request_id)
    if order.magic != expected_magic:
        problems.append(
            f"magic mismatch: expected {expected_magic} (derived from "
            f"{originating_order_request_id}), observed {order.magic!r} -- this "
            "pending order may not be the one that request actually placed"
        )
    return problems


def _decide_outcome(
    result: PendingOrderCancelResult | None,
    transport_error: Mt5CallFailedError | None,
    *,
    ticket_absent: bool,
) -> tuple[int, str]:
    """Pure: the exit code and final report line, from the cancel attempt's

    outcome and the fresh post-cancel readback -- never from either alone.

    Exactly one of `result`/`transport_error` is non-`None` (`main()`'s own
    single `cancel_pending_order()` call produces one or the other, never
    both, never neither). A transport-ambiguous cancel (`transport_error`
    set) never returns exit 0/PASS, even when `ticket_absent` is `True` --
    an ambiguous broker response is not the same thing as a confirmed
    accepted response, and this runner must not conflate a reassuring
    readback with proof the cancel it just attempted is what produced it.
    """
    if transport_error is not None:
        verdict = "TICKET ABSENT" if ticket_absent else "TICKET STILL RESTING"
        return 3, (
            f"DEMO CANARY EXPLICIT PENDING CANCEL -- TRANSPORT AMBIGUOUS -- BROKER READBACK "
            f"{verdict} (cancel_pending_order transport error: {transport_error})"
        )

    assert result is not None  # no transport_error means cancel_pending_order returned normally
    if result.accepted and ticket_absent:
        return 0, "DEMO CANARY EXPLICIT PENDING CANCEL -- PASS"
    return 3, (
        "DEMO CANARY EXPLICIT PENDING CANCEL -- BLOCKED (cancel rejected or ticket still resting)"
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
        print("  DEMO CANARY EXPLICIT PENDING ORDER CANCEL")
        print("=" * 78)

        try:
            # Explicit, visible DEMO account/server guard re-run -- separate
            # from, and before, cancel_pending_order()'s own internal guard call.
            account = gateway.account()
        except AccountGuardError as error:
            print(f"BLOCKED: account guard failed: {error}")
            return 2
        print(f"  account guard PASSED -- server={account.server} is_demo={account.is_demo}")

        # Fresh broker pending-order read, before any mutation.
        pending_orders = gateway.pending_orders()
        print(f"  pre-cancel pending orders (fresh read) = {len(pending_orders)}")

        target, not_found_reason = _find_target_pending_order(
            pending_orders, order_id=args.order_id
        )
        if target is None:
            print(f"BLOCKED: {not_found_reason}")
            return 2

        problems = _verify_target_pending_order(
            target,
            expected_broker_symbol=args.expected_broker_symbol,
            originating_order_request_id=args.originating_order_request_id,
        )
        if problems:
            print("BLOCKED: target pending order does not match the expected shape:")
            for problem in problems:
                print(f"  - {problem}")
            return 2

        print(
            f"  target pending order verified: order_id={target.order_id} "
            f"{target.broker_symbol} {target.order_type} magic={target.magic}"
        )

        result: PendingOrderCancelResult | None = None
        transport_error: Mt5CallFailedError | None = None
        try:
            result = gateway.cancel_pending_order(args.order_id)
        except Mt5CallFailedError as error:
            # Never retried -- exactly one cancel_pending_order() call
            # happened above, transport-ambiguous or not. The fresh
            # readback below still runs: an ambiguous transport response
            # is not knowledge of broker state, and this is the only way
            # to get any.
            transport_error = error
            print(f"  cancel_pending_order transport failure: {error}")

        if result is not None:
            print(
                f"  cancel_pending_order result: accepted={result.accepted} "
                f"retcode={result.retcode} comment={result.retcode_comment!r}"
            )

        # Fresh broker readback after the cancel attempt, regardless of its
        # outcome (including a transport-ambiguous failure) -- this, not
        # the cancel response alone, is what decides PASS/BLOCKED below.
        post_pending_orders = gateway.pending_orders()
        ticket_absent = not any(order.order_id == args.order_id for order in post_pending_orders)

        print(f"  post-cancel pending orders (fresh read) = {len(post_pending_orders)}")
        print(f"  order_id {args.order_id} still resting? {not ticket_absent}")

        exit_code, message = _decide_outcome(result, transport_error, ticket_absent=ticket_absent)
        print(f"\n{message}")
        return exit_code
    finally:
        client.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
