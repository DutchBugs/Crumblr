"""Pure types and result-building for one Trader-identity closed-trade
dataset snapshot (TRAINER/TRADER/CRUMBLR V1 Slice 2).

Everything here is a plain value or a pure function over already-resolved
values -- no database or network I/O, exactly like `evidence.py`.
`scripts/collect_crumblr_trader_dataset.py` is the only caller that
touches Postgres or HTTP; it discovers candidate trades, resolves each one
through `evidence.py`'s existing single-trade primitives (unchanged, not
weakened), and hands the eligible/excluded split here to build one
deterministic dataset payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from crumblr.trainer_bridge.evidence import (
    ClosedTradeEvidence,
    build_trade_id,
    pre_trade_stop_distance,
    realized_pnl,
)


@dataclass(frozen=True)
class TraderIdentity:
    """The exact, explicit identity a dataset snapshot is scoped to --
    every field is a hard filter, not a hint. Two datasets are never
    merged across identities; a canary-fixture agent/assignment and the
    real Static ICT Trader's agent/assignment always produce entirely
    separate snapshots even if both happen to trade the same symbol."""

    agent_id: UUID
    assignment_id: UUID
    strategy_artifact_hash: str
    canonical_symbol: str
    timeframe: str


@dataclass(frozen=True)
class ExcludedOutcome:
    """One discovered `TRADE_PROPOSAL` outcome that did not become an
    eligible dataset row, and exactly why -- never silently dropped."""

    outcome_id: UUID
    reason: str


@dataclass(frozen=True)
class DatasetCollectionResult:
    """The full accounting for one collection run against one identity:
    every discovered candidate is accounted for as either `eligible` or
    `excluded`, never simply absent."""

    identity: TraderIdentity
    discovered_count: int
    eligible: tuple[ClosedTradeEvidence, ...]
    excluded: tuple[ExcludedOutcome, ...]

    def __post_init__(self) -> None:
        accounted = len(self.eligible) + len(self.excluded)
        if accounted != self.discovered_count:
            raise ValueError(
                f"discovered_count={self.discovered_count} but eligible"
                f"({len(self.eligible)}) + excluded({len(self.excluded)}) = {accounted} -- "
                "every discovered outcome must be accounted for exactly once"
            )


def build_dataset_result(
    collection: DatasetCollectionResult,
    returns_r: dict[UUID, Decimal],
    *,
    transaction_costs_included: bool,
) -> dict[str, Any]:
    """Trainer's exact `docs/RESULT_CONTRACT.md` shape, generalized from
    `evidence.py::build_normalized_result`'s single-trade version to one
    parallel-array entry per eligible trade in `collection`.

    Deterministically ordered by `(close_observed_at_utc, order_request_id)`
    -- the durable post-close observation, never the entry `FILLED` time
    (a trade's chronological place in a closed-trade dataset is when it
    closed, not when it opened) and never database insertion order, which
    is not itself a stable replay-independent key. `returns_r` keys every
    eligible trade's `order_request_id` to its already-derived R-multiple
    (computed once, upstream, by the unmodified `evidence.derive_return_r`);
    this function only orders and assembles, it derives nothing new.
    """
    ordered = sorted(
        collection.eligible, key=lambda e: (e.close_observed_at_utc, e.order_request_id)
    )
    identity = collection.identity

    returns_r_list: list[float] = []
    trade_ids: list[str] = []
    pnl_list: list[float] = []
    dates: list[str] = []
    per_trade_summaries: list[dict[str, Any]] = []

    for evidence in ordered:
        r = returns_r[evidence.order_request_id]
        pnl = realized_pnl(evidence)
        returns_r_list.append(float(r))
        trade_ids.append(build_trade_id(evidence))
        pnl_list.append(float(pnl))
        dates.append(evidence.close_observed_at_utc.date().isoformat())
        per_trade_summaries.append(
            {
                "trade_id": build_trade_id(evidence),
                "order_request_id": str(evidence.order_request_id),
                "capsule_id": str(evidence.capsule_id),
                "intent_id": str(evidence.intent_id),
                "mt5_order_ticket": evidence.mt5_order_ticket,
                "mt5_deal_ticket": evidence.mt5_deal_ticket,
                "side": evidence.side,
                "entry_type": evidence.entry_type,
                "executed_volume": str(evidence.executed_volume),
                "executed_entry_price": str(evidence.executed_entry_price),
                "pre_trade_stop_distance_price": str(pre_trade_stop_distance(evidence)),
                "realized_pnl_account_currency": str(pnl),
                "account_currency": evidence.account_currency,
                "fill_occurred_at_utc": evidence.fill_occurred_at_utc.isoformat(),
                "close_observed_at_utc": evidence.close_observed_at_utc.isoformat(),
            }
        )

    return {
        "returns_r": returns_r_list,
        "trade_ids": trade_ids,
        "pnl": pnl_list,
        "dates": dates,
        "transaction_costs_included": transaction_costs_included,
        "report_summary": {
            "source": "crumblr_agent",
            "kind": "trader_identity_closed_trade_dataset_snapshot",
            "identity": {
                "agent_id": str(identity.agent_id),
                "assignment_id": str(identity.assignment_id),
                "strategy_artifact_hash": identity.strategy_artifact_hash,
                "canonical_symbol": identity.canonical_symbol,
                "timeframe": identity.timeframe,
            },
            "discovered_count": collection.discovered_count,
            "eligible_count": len(collection.eligible),
            "excluded_count": len(collection.excluded),
            "excluded": [
                {"outcome_id": str(item.outcome_id), "reason": item.reason}
                for item in collection.excluded
            ],
            "trades": per_trade_summaries,
        },
    }
