"""Pure derivation logic for one closed-trade Trainer MODE_2 evidence record.

Everything here is a pure function over already-resolved values -- no
database or network I/O. `scripts/export_crumblr_trade_to_trainer.py` is
the only caller that touches Postgres or HTTP; keeping the arithmetic here
means it can be unit-tested against known numbers without either.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from crumblr.domain.models import InstrumentSpec
from crumblr.risk.sizing import realised_risk


@dataclass(frozen=True)
class ClosedTradeEvidence:
    """Everything durably observed about one already-closed real trade,
    resolved from Crumblr's own append-only evidence -- never a fresh
    broker call. One instance names exactly one `order_request_id`."""

    order_request_id: UUID
    capsule_id: UUID
    intent_id: UUID
    canonical_symbol: str
    side: str
    entry_type: str
    strategy_version: str
    code_commit: str
    reference_price: Decimal
    """Intent-time reference price (pre-execution), the basis the original
    pre-trade stop distance is measured from -- never the execution-time
    revalidated price."""
    stop_loss_price: Decimal
    """Intent-time stop-loss price -- the ORIGINAL pre-trade stop, as
    approved by intent-time Risk, before any execution-time revalidation."""
    executed_entry_price: Decimal
    executed_volume: Decimal
    fill_occurred_at_utc: datetime
    mt5_order_ticket: int
    mt5_deal_ticket: int
    last_open_snapshot_at_utc: datetime
    """The last durable `broker_position_snapshots` observation showing
    this ticket still open -- evidence the position was genuinely open
    until at least this moment."""
    balance_before_close: Decimal
    """Account balance at `last_open_snapshot_at_utc`'s own snapshot cycle."""
    balance_after_close: Decimal
    """Account balance at the first subsequent snapshot cycle in which the
    ticket no longer appears among open positions."""
    account_currency: str


def pre_trade_stop_distance(evidence: ClosedTradeEvidence) -> Decimal:
    """The ORIGINAL pre-trade stop distance, in price terms -- the distance
    between the intent-time reference price and the intent-time stop-loss
    price. This is what defines "1R" for this trade; it is deliberately
    never the execution-time revalidated distance (ADR-001's FINAL Risk may
    see a slightly different distance after slippage -- that answers a
    different question, "was this still within budget to fill", not "what
    was this trade's own planned risk unit")."""
    return abs(evidence.reference_price - evidence.stop_loss_price)


def realized_pnl(evidence: ClosedTradeEvidence) -> Decimal:
    """Realized PnL in account currency, from the durable broker-account
    balance delta across the close.

    Crumblr's one-shot close runner (`scripts/close_demo_canary_position.py`)
    verifies a close by the ticket's absence from a fresh `positions()`
    read; it does not durably record a `CLOSED` execution event or a fill
    price anywhere in Crumblr's own database (confirmed by inspection, not
    assumed). The account ledger's own balance is the one place a
    broker-applied cost cannot help but show up -- any commission, swap or
    spread the broker actually charged changed this number, so it is the
    true net realized outcome, not a price-only approximation of it.
    """
    return evidence.balance_after_close - evidence.balance_before_close


def derive_return_r(evidence: ClosedTradeEvidence, spec: InstrumentSpec) -> Decimal:
    """One deterministic R-multiple: realized PnL measured in units of the
    original pre-trade risk, reusing `risk.sizing.realised_risk` directly --
    the identical function ADR-001's FINAL Risk already trusts to price a
    stop distance in account currency, not a second, hand-rolled pip-value
    formula that could silently drift from it.

    `R = realized_pnl / realised_risk(volume, pre_trade_stop_distance, spec)`

    This is algebraically identical to `(exit_price - entry_price) /
    stop_distance` (both sides of that equation cancel `volume`,
    `tick_size` and `tick_value`) and needs no side-specific sign handling:
    MT5's own realized PnL already encodes the correct sign for a BUY or a
    SELL, so a profitable trade always yields a positive R here regardless
    of side.
    """
    stop_distance = pre_trade_stop_distance(evidence)
    risk_amount = realised_risk(evidence.executed_volume, stop_distance, spec)
    if risk_amount <= Decimal(0):
        raise ValueError("degenerate risk amount -- cannot derive an R-multiple")
    return realized_pnl(evidence) / risk_amount


def implied_exit_price(evidence: ClosedTradeEvidence, spec: InstrumentSpec) -> Decimal:
    """The exit price implied by the realized account-currency PnL, given
    the executed entry price/volume and the instrument's own tick economics
    -- reported for human transparency/manual reproduction, never used as
    an input to `derive_return_r` (which computes R directly from PnL and
    never needs to reconstruct a price).

    Inverts `risk.sizing.loss_per_lot`'s own formula
    (`(price_distance / tick_size) * tick_value`) rather than a second,
    independent pip-value calculation:

        price_distance = pnl / (volume * tick_value / tick_size)

    **This is an IMPLIED price, not an independently observed broker fill**
    -- Crumblr's own database never durably recorded one for this trade's
    close (see `realized_pnl`'s docstring). Report it as implied/derived,
    never as an observed fill price.
    """
    price_distance = (
        realized_pnl(evidence) * spec.tick_size / (evidence.executed_volume * spec.tick_value)
    )
    if evidence.side == "SELL":
        return evidence.executed_entry_price - price_distance
    return evidence.executed_entry_price + price_distance


def build_trade_id(evidence: ClosedTradeEvidence) -> str:
    """A stable, deterministic trade id -- the durable `order_request_id`
    itself, the one identifier every event in this trade's lifecycle
    (`SUBMISSION_STARTED`, `FILLED`) already carries as its append-only
    claim key. Prefixed so it reads unambiguously in a Trainer journal
    shared with MT5-native experiment ids."""
    return f"crumblr:{evidence.order_request_id}"


def build_source_reference(evidence: ClosedTradeEvidence) -> str:
    """A compact, deterministic JSON string naming every Crumblr record
    this export was derived from, for independent manual reproduction --
    passed through Trainer's `source_reference` field verbatim."""
    payload = {
        "capsule_id": str(evidence.capsule_id),
        "order_request_id": str(evidence.order_request_id),
        "intent_id": str(evidence.intent_id),
        "mt5_order_ticket": evidence.mt5_order_ticket,
        "mt5_deal_ticket": evidence.mt5_deal_ticket,
        "code_commit": evidence.code_commit,
        "strategy_version": evidence.strategy_version,
        "fill_occurred_at_utc": evidence.fill_occurred_at_utc.isoformat(),
        "last_open_snapshot_at_utc": evidence.last_open_snapshot_at_utc.isoformat(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def build_normalized_result(
    evidence: ClosedTradeEvidence,
    return_r: Decimal,
    *,
    transaction_costs_included: bool,
) -> dict[str, Any]:
    """Trainer's exact `docs/RESULT_CONTRACT.md` shape for one closed trade:
    `returns_r` (mandatory), `trade_ids`/`pnl`/`dates` (same length, all
    optional but supplied here since we have them), an explicit
    `transaction_costs_included` flag, and a `report_summary` naming the
    derivation for anyone reading the Trainer journal later.
    """
    pnl = realized_pnl(evidence)
    trade_date = evidence.fill_occurred_at_utc.date().isoformat()
    return {
        "returns_r": [float(return_r)],
        "trade_ids": [build_trade_id(evidence)],
        "pnl": [float(pnl)],
        "dates": [trade_date],
        "transaction_costs_included": transaction_costs_included,
        "report_summary": {
            "source": "crumblr_agent",
            "kind": "single_real_trade_smoke_proof",
            "canonical_symbol": evidence.canonical_symbol,
            "side": evidence.side,
            "entry_type": evidence.entry_type,
            "executed_volume": str(evidence.executed_volume),
            "executed_entry_price": str(evidence.executed_entry_price),
            "pre_trade_stop_distance_price": str(pre_trade_stop_distance(evidence)),
            "realized_pnl_account_currency": str(pnl),
            "account_currency": evidence.account_currency,
        },
    }
