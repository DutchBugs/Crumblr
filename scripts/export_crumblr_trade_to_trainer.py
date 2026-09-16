"""TRAINER/TRADER/CRUMBLR V1 -- SLICE 1: real Crumblr trade -> Trainer MODE_2.

    uv run python scripts/export_crumblr_trade_to_trainer.py \\
        --order-request-id e845ddac-028e-5bf7-9f86-785119674752 \\
        --trainer-base-url http://127.0.0.1:8766 \\
        --campaign-id CAM-CRUMBLR-SLICE1 \\
        --agent-id owner-demo-execution-canary-fixture-v2-rf0005-sl200-tp400

Read-only on the Crumblr side, one outbound HTTP POST on success. Never
writes to Crumblr's own database, never opens an MT5 connection, never
touches `TradingAssignment`/agent registration, never issues a permit.

Resolves exactly one already-closed real trade from Crumblr's own durable
evidence (`execution_requests` -> `decision_capsules` for the intent-time
stop/reference price, `execution_events` for the real `FILLED` fill, and
`broker_position_snapshots`/`broker_account_snapshots` for the close).
Crumblr's one-shot close runner never durably records a `CLOSED` execution
event or a fill price -- there is no observed durable broker close-fill
price anywhere in Crumblr's own database for this trade. Absent that, the
realized outcome is read as an account-balance delta across a close window
`_resolve_isolated_close_window` first proves is isolated to this one
ticket alone (no other open position, no other order's `FILLED` event
anywhere inside it, and a fully flat book at the close observation) --
refusing (`EvidenceIncompleteError`) rather than exporting a delta that
could silently include another trade's outcome. This is what Slice 1's
smoke proof can establish honestly, not a claim that account-balance-delta
derivation is Crumblr's canonical or long-term closed-trade accounting
contract. Derives one deterministic `return_r`
(`crumblr.trainer_bridge.evidence.derive_return_r`) and POSTs Trainer's
exact `docs/RESULT_CONTRACT.md` shape through the already-existing,
already-reviewed `POST /api/v1/campaigns/{campaign_id}/agent-data` MODE_2
endpoint.

`--transaction-costs-included` defaults to false and must be passed
explicitly to override -- do not claim broker costs are proven included
merely because a number came out of the ledger; see the module's own
accounting note in `evidence.build_normalized_result`.

Exits non-zero (no POST attempted) if any required durable record is
missing -- an incomplete trade is refused, never exported with a guessed
close.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.engine import Connection

from crumblr.domain.enums import ExecutionEventType
from crumblr.persistence.engine import DATABASE_URL_ENV_VAR, create_db_engine, database_url
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore
from crumblr.persistence.schema import (
    broker_account_snapshots,
    broker_position_snapshots,
    execution_events,
)
from crumblr.trainer_bridge.evidence import (
    ClosedTradeEvidence,
    build_normalized_result,
    build_source_reference,
    derive_return_r,
    implied_exit_price,
    pre_trade_stop_distance,
    realized_pnl,
)
from crumblr.trainer_bridge.trainer_client import (
    TrainerClientConfig,
    TrainerTransportError,
    post_agent_data,
)


class EvidenceIncompleteError(RuntimeError):
    """A required durable record is missing -- refuse rather than export a
    partially-reconstructed or guessed trade outcome."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--order-request-id", type=UUID, required=True)
    parser.add_argument("--trainer-base-url", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--trainer-api-key", default=None)
    parser.add_argument(
        "--transaction-costs-included",
        action="store_true",
        default=False,
        help="only pass this if the source evidence proves ALL applicable broker "
        "costs for this closed trade -- defaults to false",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve and print the derivation and payload, do not POST",
    )
    return parser.parse_args()


def _tickets_in_snapshot(connection: Connection, snapshot_id: UUID) -> set[int]:
    statement = select(broker_position_snapshots.c.ticket).where(
        broker_position_snapshots.c.snapshot_id == snapshot_id
    )
    return {int(row[0]) for row in connection.execute(statement)}


def _account_balance_and_currency(connection: Connection, snapshot_id: UUID) -> tuple[Decimal, str]:
    statement = select(
        broker_account_snapshots.c.balance, broker_account_snapshots.c.currency
    ).where(broker_account_snapshots.c.snapshot_id == snapshot_id)
    row = connection.execute(statement).first()
    if row is None:
        raise EvidenceIncompleteError(
            f"no broker_account_snapshots row for snapshot_id {snapshot_id}"
        )
    return Decimal(str(row[0])), row[1]


@dataclass(frozen=True)
class _CloseWindow:
    last_open_at: datetime
    balance_before_close: Decimal
    post_close_at: datetime
    balance_after_close: Decimal
    account_currency: str


def _resolve_isolated_close_window(
    connection: Connection, *, ticket: int, order_request_id: UUID
) -> _CloseWindow:
    """Resolve the account-balance delta across one closed trade's window,
    refusing (`EvidenceIncompleteError`) unless that window is demonstrably
    isolated to `ticket` alone -- an account-balance delta is only a valid
    proxy for one trade's realized outcome if nothing else moved money
    during the window it spans. Five checks, in order:

    1. at the last observation showing `ticket` open, no other ticket may
       also be open;
    2. walk forward to the immediate next observation after that point at
       which `ticket` is absent;
    3. every observation in between must still show no ticket other than
       `ticket` open;
    4. no *different* order's `FILLED` execution event may fall strictly
       inside the window -- a same-cycle open+close between two polls
       would be invisible to position snapshots but visible here;
    5. the close observation itself must be fully account-flat, not merely
       `ticket`-absent -- Slice 1 does not attempt multi-position
       accounting.
    """
    last_open_statement = (
        select(broker_position_snapshots.c.snapshot_id, broker_position_snapshots.c.observed_at_utc)
        .where(broker_position_snapshots.c.ticket == ticket)
        .order_by(broker_position_snapshots.c.observed_at_utc.desc())
        .limit(1)
    )
    last_open_row = connection.execute(last_open_statement).first()
    if last_open_row is None:
        raise EvidenceIncompleteError(
            f"no broker_position_snapshots row was ever recorded for ticket {ticket} "
            "-- cannot establish this trade was ever durably observed open"
        )
    last_open_snapshot_id, last_open_at = last_open_row

    # Step 1.
    last_open_tickets = _tickets_in_snapshot(connection, last_open_snapshot_id)
    if last_open_tickets != {ticket}:
        raise EvidenceIncompleteError(
            f"snapshot {last_open_snapshot_id} at {last_open_at} is not isolated to "
            f"ticket {ticket} alone (also open: {sorted(last_open_tickets - {ticket})}) "
            "-- refusing an account-balance derivation across a contaminated window"
        )
    balance_before_close, account_currency = _account_balance_and_currency(
        connection, last_open_snapshot_id
    )

    # Steps 2/3/5: walk forward to the immediate next observation without
    # `ticket`, refusing on any foreign ticket seen along the way, and
    # requiring that first target-absent observation to be fully flat.
    candidates_statement = (
        select(broker_account_snapshots.c.snapshot_id, broker_account_snapshots.c.observed_at_utc)
        .where(broker_account_snapshots.c.observed_at_utc > last_open_at)
        .order_by(broker_account_snapshots.c.observed_at_utc)
    )
    post_close_snapshot_id: UUID | None = None
    post_close_at: datetime | None = None
    for candidate_snapshot_id, candidate_observed_at in connection.execute(candidates_statement):
        tickets = _tickets_in_snapshot(connection, candidate_snapshot_id)
        if ticket in tickets:
            if tickets != {ticket}:
                raise EvidenceIncompleteError(
                    f"snapshot {candidate_snapshot_id} at {candidate_observed_at} is not "
                    f"isolated to ticket {ticket} alone (also open: "
                    f"{sorted(tickets - {ticket})}) -- refusing an account-balance "
                    "derivation across a contaminated window"
                )
            continue
        if tickets:
            raise EvidenceIncompleteError(
                f"snapshot {candidate_snapshot_id} at {candidate_observed_at} is the "
                f"first observation without ticket {ticket}, but the account is not "
                f"flat there (open: {sorted(tickets)}) -- Slice 1 requires a flat close"
            )
        post_close_snapshot_id, post_close_at = candidate_snapshot_id, candidate_observed_at
        break

    if post_close_snapshot_id is None or post_close_at is None:
        raise EvidenceIncompleteError(
            f"no broker_account_snapshots row exists after {last_open_at} showing "
            f"ticket {ticket} closed -- this trade is not durably confirmed closed yet"
        )

    # Step 4.
    foreign_fill_statement = (
        select(execution_events.c.event_id)
        .where(
            execution_events.c.event_type == ExecutionEventType.FILLED.value,
            execution_events.c.order_request_id != order_request_id,
            execution_events.c.occurred_at_utc > last_open_at,
            execution_events.c.occurred_at_utc < post_close_at,
        )
        .limit(1)
    )
    foreign_fill = connection.execute(foreign_fill_statement).first()
    if foreign_fill is not None:
        raise EvidenceIncompleteError(
            f"a different order's FILLED event ({foreign_fill[0]}) occurred between "
            f"{last_open_at} and {post_close_at} -- refusing an account-balance "
            "derivation across a window that may include another trade's outcome"
        )

    balance_after_close, _ = _account_balance_and_currency(connection, post_close_snapshot_id)

    return _CloseWindow(
        last_open_at=last_open_at,
        balance_before_close=balance_before_close,
        post_close_at=post_close_at,
        balance_after_close=balance_after_close,
        account_currency=account_currency,
    )


def resolve_evidence(engine: Engine, order_request_id: UUID) -> ClosedTradeEvidence:
    requests_store = ExecutionRequestStore(engine)
    capsule_id = requests_store.capsule_id_for(order_request_id)
    if capsule_id is None:
        raise EvidenceIncompleteError(
            f"order_request_id {order_request_id} was never claimed -- no "
            "execution_requests row exists"
        )

    capsule = CapsuleStore(engine).get(capsule_id)
    if capsule is None:
        raise EvidenceIncompleteError(f"capsule {capsule_id} not found")
    if capsule.trade_intent is None:
        raise EvidenceIncompleteError(f"capsule {capsule_id} sealed a NO_TRADE, not a real order")

    events = ExecutionEventStore(engine).events_for(order_request_id)
    filled = [event for event in events if event.event_type == ExecutionEventType.FILLED]
    if len(filled) != 1:
        raise EvidenceIncompleteError(
            f"order_request_id {order_request_id} has {len(filled)} FILLED events, "
            "expected exactly 1"
        )
    fill_payload = filled[0].payload
    if fill_payload is None:
        raise EvidenceIncompleteError(f"FILLED event {filled[0].event_id} has no payload")

    with engine.connect() as connection:
        window = _resolve_isolated_close_window(
            connection,
            ticket=int(fill_payload["mt5_order_ticket"]),
            order_request_id=order_request_id,
        )

    trade_intent = capsule.trade_intent
    if trade_intent.stop_loss_price is None:
        raise EvidenceIncompleteError(
            f"capsule {capsule_id}'s trade_intent has no stop_loss_price -- cannot "
            "derive a pre-trade stop distance"
        )
    return ClosedTradeEvidence(
        order_request_id=order_request_id,
        capsule_id=capsule_id,
        intent_id=trade_intent.intent_id,
        canonical_symbol=capsule.canonical_symbol,
        side=trade_intent.side.value,
        entry_type=trade_intent.entry_type.value,
        strategy_version=capsule.strategy_version,
        code_commit=capsule.code_commit,
        reference_price=trade_intent.reference_price,
        stop_loss_price=trade_intent.stop_loss_price,
        executed_entry_price=Decimal(str(fill_payload["executed_price"])),
        executed_volume=Decimal(str(fill_payload["executed_volume"])),
        fill_occurred_at_utc=filled[0].occurred_at_utc,
        mt5_order_ticket=int(fill_payload["mt5_order_ticket"]),
        mt5_deal_ticket=int(fill_payload["mt5_deal_ticket"]),
        last_open_snapshot_at_utc=window.last_open_at,
        balance_before_close=window.balance_before_close,
        balance_after_close=window.balance_after_close,
        close_observed_at_utc=window.post_close_at,
        account_currency=window.account_currency,
    )


def main() -> int:
    args = parse_args()

    try:
        url = database_url()
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        print(f"set {DATABASE_URL_ENV_VAR} to Crumblr's own database", file=sys.stderr)
        return 2

    engine = create_db_engine(url)
    try:
        evidence = resolve_evidence(engine, args.order_request_id)
    except EvidenceIncompleteError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2

    spec = InstrumentSpecStore(engine).at_or_before(
        canonical_symbol=evidence.canonical_symbol, at=evidence.fill_occurred_at_utc
    )
    engine.dispose()
    if spec is None:
        print(
            f"BLOCKED: no instrument_specs row for {evidence.canonical_symbol} at or "
            f"before {evidence.fill_occurred_at_utc}",
            file=sys.stderr,
        )
        return 2

    return_r = derive_return_r(evidence, spec)
    result = build_normalized_result(
        evidence, return_r, transaction_costs_included=args.transaction_costs_included
    )
    source_reference = build_source_reference(evidence)

    currency = evidence.account_currency
    print("=== derivation ===")
    print(f"  order_request_id:            {evidence.order_request_id}")
    print(f"  capsule_id:                  {evidence.capsule_id}")
    print(f"  intent_id:                   {evidence.intent_id}")
    print(f"  side / entry_type:           {evidence.side} / {evidence.entry_type}")
    print(f"  reference_price (intent):    {evidence.reference_price}")
    print(f"  stop_loss_price (intent):    {evidence.stop_loss_price}")
    print(f"  pre_trade_stop_distance:     {pre_trade_stop_distance(evidence)}")
    print(f"  executed_entry_price:        {evidence.executed_entry_price}")
    print(f"  executed_volume:             {evidence.executed_volume}")
    print(f"  mt5_order_ticket:            {evidence.mt5_order_ticket}")
    print(f"  mt5_deal_ticket:             {evidence.mt5_deal_ticket}")
    print(f"  balance_before_close:        {evidence.balance_before_close} {currency}")
    print(f"  balance_after_close:         {evidence.balance_after_close} {currency}")
    print(f"  realized_pnl:                {realized_pnl(evidence)} {currency}")
    print(f"  implied_exit_price (derived, not observed): {implied_exit_price(evidence, spec)}")
    print(f"  return_r:                    {return_r}")
    print()
    print("=== normalized result (Trainer RESULT_CONTRACT.md shape) ===")
    print(json.dumps(result, indent=2))
    print()
    print(f"=== source_reference ({len(source_reference)} chars) ===")
    print(source_reference)

    if args.dry_run:
        print("\n--dry-run: not posting to Trainer")
        return 0

    config = TrainerClientConfig(base_url=args.trainer_base_url, api_key=args.trainer_api_key)
    try:
        status, body = post_agent_data(
            config,
            campaign_id=args.campaign_id,
            agent_id=args.agent_id,
            result=result,
            source_reference=source_reference,
        )
    except TrainerTransportError as error:
        print(f"BLOCKED: Trainer call failed: {error}", file=sys.stderr)
        return 2

    print(f"\n=== Trainer response (HTTP {status}) ===")
    print(json.dumps(body, indent=2))
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
