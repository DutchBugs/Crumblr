"""trainer_bridge/dataset.py -- pure types and result-building for one
Trader-identity closed-trade dataset snapshot. No database, no network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from crumblr.trainer_bridge.dataset import (
    DatasetCollectionResult,
    ExcludedOutcome,
    TraderIdentity,
    build_dataset_result,
)
from crumblr.trainer_bridge.evidence import ClosedTradeEvidence, build_trade_id

FIXED_NOW = datetime(2026, 9, 15, 9, 38, 23, tzinfo=UTC)

IDENTITY = TraderIdentity(
    agent_id=uuid4(),
    assignment_id=uuid4(),
    strategy_artifact_hash="81894d6a9c44ddb0433c15f9779fb0a2e25c0e1eccee232e2fd145d72cb498c5",
    canonical_symbol="EUR/USD",
    timeframe="M5",
)


def make_evidence(**overrides: Any) -> ClosedTradeEvidence:
    fields: dict[str, Any] = {
        "order_request_id": uuid4(),
        "capsule_id": uuid4(),
        "intent_id": uuid4(),
        "canonical_symbol": "EUR/USD",
        "side": "BUY",
        "entry_type": "LIMIT",
        "strategy_version": IDENTITY.strategy_artifact_hash,
        "code_commit": "deadbeef",
        "reference_price": Decimal("1.15348"),
        "stop_loss_price": Decimal("1.15148"),
        "executed_entry_price": Decimal("1.15346"),
        "executed_volume": Decimal("0.02"),
        "fill_occurred_at_utc": FIXED_NOW,
        "mt5_order_ticket": 1,
        "mt5_deal_ticket": 1,
        "last_open_snapshot_at_utc": FIXED_NOW,
        "balance_before_close": Decimal("9993.55"),
        "balance_after_close": Decimal("9993.66"),
        "account_currency": "EUR",
    }
    fields.update(overrides)
    return ClosedTradeEvidence(**fields)


class TestDatasetCollectionResultAccounting:
    def test_every_discovered_outcome_must_be_accounted_for(self) -> None:
        with pytest.raises(ValueError):
            DatasetCollectionResult(identity=IDENTITY, discovered_count=2, eligible=(), excluded=())

    def test_a_correctly_accounted_result_constructs(self) -> None:
        excluded = (ExcludedOutcome(outcome_id=uuid4(), reason="no execution request"),)
        result = DatasetCollectionResult(
            identity=IDENTITY, discovered_count=1, eligible=(), excluded=excluded
        )
        assert result.excluded == excluded


class TestBuildDatasetResult:
    def test_zero_eligible_trades_produces_empty_parallel_arrays(self) -> None:
        collection = DatasetCollectionResult(
            identity=IDENTITY, discovered_count=0, eligible=(), excluded=()
        )
        dataset = build_dataset_result(collection, {}, transaction_costs_included=False)
        assert dataset["returns_r"] == []
        assert dataset["trade_ids"] == []
        assert dataset["pnl"] == []
        assert dataset["dates"] == []

    def test_one_eligible_trade_produces_matching_single_element_arrays(self) -> None:
        evidence = make_evidence()
        collection = DatasetCollectionResult(
            identity=IDENTITY, discovered_count=1, eligible=(evidence,), excluded=()
        )
        dataset = build_dataset_result(
            collection,
            {evidence.order_request_id: Decimal("0.5")},
            transaction_costs_included=False,
        )
        assert dataset["returns_r"] == [0.5]
        assert dataset["trade_ids"] == [build_trade_id(evidence)]
        assert dataset["pnl"] == [pytest.approx(0.11)]
        assert dataset["dates"] == ["2026-09-15"]

    def test_multiple_eligible_trades_are_ordered_by_fill_time_deterministically(self) -> None:
        later = make_evidence(
            order_request_id=uuid4(), fill_occurred_at_utc=FIXED_NOW.replace(hour=12)
        )
        earlier = make_evidence(
            order_request_id=uuid4(), fill_occurred_at_utc=FIXED_NOW.replace(hour=6)
        )
        collection = DatasetCollectionResult(
            identity=IDENTITY, discovered_count=2, eligible=(later, earlier), excluded=()
        )
        returns_r = {later.order_request_id: Decimal("1"), earlier.order_request_id: Decimal("2")}

        dataset = build_dataset_result(collection, returns_r, transaction_costs_included=False)

        assert dataset["trade_ids"] == [build_trade_id(earlier), build_trade_id(later)]
        assert dataset["returns_r"] == [2.0, 1.0]

    def test_ordering_is_stable_regardless_of_input_order(self) -> None:
        """Feeding the same two trades in the opposite order must produce
        byte-identical output -- determinism, not insertion-order luck."""
        a = make_evidence(order_request_id=uuid4(), fill_occurred_at_utc=FIXED_NOW.replace(hour=6))
        b = make_evidence(order_request_id=uuid4(), fill_occurred_at_utc=FIXED_NOW.replace(hour=12))
        returns_r = {a.order_request_id: Decimal("1"), b.order_request_id: Decimal("2")}

        forward = DatasetCollectionResult(
            identity=IDENTITY, discovered_count=2, eligible=(a, b), excluded=()
        )
        backward = DatasetCollectionResult(
            identity=IDENTITY, discovered_count=2, eligible=(b, a), excluded=()
        )

        forward_dataset = build_dataset_result(forward, returns_r, transaction_costs_included=False)
        backward_dataset = build_dataset_result(
            backward, returns_r, transaction_costs_included=False
        )
        assert forward_dataset["trade_ids"] == backward_dataset["trade_ids"]

    def test_identity_is_preserved_in_the_report_summary(self) -> None:
        collection = DatasetCollectionResult(
            identity=IDENTITY, discovered_count=0, eligible=(), excluded=()
        )
        dataset = build_dataset_result(collection, {}, transaction_costs_included=False)
        identity_block = dataset["report_summary"]["identity"]
        assert identity_block["agent_id"] == str(IDENTITY.agent_id)
        assert identity_block["assignment_id"] == str(IDENTITY.assignment_id)
        assert identity_block["strategy_artifact_hash"] == IDENTITY.strategy_artifact_hash
        assert identity_block["canonical_symbol"] == IDENTITY.canonical_symbol
        assert identity_block["timeframe"] == IDENTITY.timeframe

    def test_exclusion_reasons_are_preserved_verbatim(self) -> None:
        excluded = (
            ExcludedOutcome(outcome_id=uuid4(), reason="no execution request"),
            ExcludedOutcome(outcome_id=uuid4(), reason="0 FILLED events, expected exactly 1"),
        )
        collection = DatasetCollectionResult(
            identity=IDENTITY, discovered_count=2, eligible=(), excluded=excluded
        )
        dataset = build_dataset_result(collection, {}, transaction_costs_included=False)
        summary = dataset["report_summary"]
        assert summary["discovered_count"] == 2
        assert summary["eligible_count"] == 0
        assert summary["excluded_count"] == 2
        reasons = {item["reason"] for item in summary["excluded"]}
        assert reasons == {"no execution request", "0 FILLED events, expected exactly 1"}

    def test_never_silently_claims_transaction_costs_are_included(self) -> None:
        collection = DatasetCollectionResult(
            identity=IDENTITY, discovered_count=0, eligible=(), excluded=()
        )
        dataset = build_dataset_result(collection, {}, transaction_costs_included=False)
        assert dataset["transaction_costs_included"] is False
