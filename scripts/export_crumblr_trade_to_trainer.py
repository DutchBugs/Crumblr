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
`broker_position_snapshots`/`broker_account_snapshots` for the close --
Crumblr's one-shot close runner never durably records a `CLOSED` execution
event or a fill price, so the realized outcome is read from the account
ledger's own balance delta, the one number a broker-applied cost cannot
help but show up in). Derives one deterministic `return_r`
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
from crumblr.persistence.schema import broker_account_snapshots, broker_position_snapshots
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


def _last_open_position_snapshot(
    connection: Connection, *, ticket: int
) -> tuple[datetime, Decimal]:
    statement = (
        select(
            broker_position_snapshots.c.observed_at_utc,
        )
        .where(broker_position_snapshots.c.ticket == ticket)
        .order_by(broker_position_snapshots.c.observed_at_utc.desc())
        .limit(1)
    )
    row = connection.execute(statement).first()
    if row is None:
        raise EvidenceIncompleteError(
            f"no broker_position_snapshots row was ever recorded for ticket {ticket} "
            "-- cannot establish this trade was ever durably observed open"
        )
    last_open_at = row[0]

    balance_statement = (
        select(broker_account_snapshots.c.balance)
        .where(broker_account_snapshots.c.observed_at_utc == last_open_at)
        .order_by(broker_account_snapshots.c.observed_at_utc)
        .limit(1)
    )
    balance_row = connection.execute(balance_statement).first()
    if balance_row is None:
        raise EvidenceIncompleteError(
            f"no broker_account_snapshots row at {last_open_at} -- cannot read the "
            "balance in force while the position was still open"
        )
    return last_open_at, balance_row[0]


def _first_flat_account_snapshot_after(
    connection: Connection, *, ticket: int, after: datetime
) -> Decimal:
    still_open_statement = (
        select(broker_position_snapshots.c.observed_at_utc)
        .where(
            broker_position_snapshots.c.ticket == ticket,
            broker_position_snapshots.c.observed_at_utc > after,
        )
        .limit(1)
    )
    if connection.execute(still_open_statement).first() is not None:
        raise EvidenceIncompleteError(
            f"ticket {ticket} still appears in broker_position_snapshots after "
            f"{after} -- this trade is not durably confirmed closed yet"
        )

    statement = (
        select(broker_account_snapshots.c.balance)
        .where(broker_account_snapshots.c.observed_at_utc > after)
        .order_by(broker_account_snapshots.c.observed_at_utc)
        .limit(1)
    )
    row = connection.execute(statement).first()
    if row is None:
        raise EvidenceIncompleteError(
            f"no broker_account_snapshots row exists after {after} -- cannot confirm "
            "the post-close balance"
        )
    return Decimal(row[0])


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
        last_open_at, balance_before_close = _last_open_position_snapshot(
            connection, ticket=int(fill_payload["mt5_order_ticket"])
        )
        balance_after_close = _first_flat_account_snapshot_after(
            connection, ticket=int(fill_payload["mt5_order_ticket"]), after=last_open_at
        )

    account_row_statement = (
        select(broker_account_snapshots.c.currency)
        .where(broker_account_snapshots.c.observed_at_utc == last_open_at)
        .limit(1)
    )
    with engine.connect() as connection:
        currency_row = connection.execute(account_row_statement).first()
    account_currency = currency_row[0] if currency_row is not None else "UNKNOWN"

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
        last_open_snapshot_at_utc=last_open_at,
        balance_before_close=Decimal(str(balance_before_close)),
        balance_after_close=Decimal(str(balance_after_close)),
        account_currency=account_currency,
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
