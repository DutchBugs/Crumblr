"""trainer_bridge/evidence.py -- pure derivation of one Trainer MODE_2
result record from an already-resolved closed real trade. No database, no
network: every input here is a plain, hand-constructed `ClosedTradeEvidence`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from crumblr.trainer_bridge.evidence import (
    ClosedTradeEvidence,
    build_normalized_result,
    build_source_reference,
    build_trade_id,
    derive_return_r,
    implied_exit_price,
    pre_trade_stop_distance,
    realized_pnl,
)
from tests.conftest import make_instrument_spec

FIXED_NOW = datetime(2026, 9, 15, 9, 38, 23, tzinfo=UTC)


def make_evidence(**overrides: Any) -> ClosedTradeEvidence:
    fields: dict[str, Any] = {
        "order_request_id": uuid4(),
        "capsule_id": uuid4(),
        "intent_id": uuid4(),
        "canonical_symbol": "EUR/USD",
        "side": "BUY",
        "entry_type": "MARKET",
        "strategy_version": "owner-demo-execution-canary-fixture-v2-rf0005-sl200-tp400",
        "code_commit": "6fa6066e50b9abfd6328735b391b6b17fcb4a261",
        "reference_price": Decimal("1.15348"),
        "stop_loss_price": Decimal("1.15148"),
        "executed_entry_price": Decimal("1.15346"),
        "executed_volume": Decimal("0.02"),
        "fill_occurred_at_utc": FIXED_NOW,
        "mt5_order_ticket": 89517316,
        "mt5_deal_ticket": 59602997,
        "last_open_snapshot_at_utc": FIXED_NOW,
        "balance_before_close": Decimal("9993.55"),
        "balance_after_close": Decimal("9993.66"),
        "close_observed_at_utc": FIXED_NOW,
        "account_currency": "EUR",
    }
    fields.update(overrides)
    return ClosedTradeEvidence(**fields)


class TestPreTradeStopDistance:
    def test_uses_the_intent_time_reference_and_stop_never_the_executed_price(self) -> None:
        evidence = make_evidence(
            reference_price=Decimal("1.15348"),
            stop_loss_price=Decimal("1.15148"),
            executed_entry_price=Decimal("1.15346"),  # deliberately different -- must be ignored
        )
        assert pre_trade_stop_distance(evidence) == Decimal("0.00200")

    def test_is_a_positive_magnitude_regardless_of_side(self) -> None:
        buy = make_evidence(
            side="BUY", reference_price=Decimal("1.10000"), stop_loss_price=Decimal("1.09800")
        )
        sell = make_evidence(
            side="SELL", reference_price=Decimal("1.10000"), stop_loss_price=Decimal("1.10200")
        )
        assert pre_trade_stop_distance(buy) == Decimal("0.00200")
        assert pre_trade_stop_distance(sell) == Decimal("0.00200")


class TestRealizedPnl:
    def test_is_the_account_balance_delta_across_the_close(self) -> None:
        evidence = make_evidence(
            balance_before_close=Decimal("9993.55"), balance_after_close=Decimal("9993.66")
        )
        assert realized_pnl(evidence) == Decimal("0.11")

    def test_a_loss_is_negative(self) -> None:
        evidence = make_evidence(
            balance_before_close=Decimal("10000.00"), balance_after_close=Decimal("9998.00")
        )
        assert realized_pnl(evidence) == Decimal("-2.00")


class TestDeriveReturnR:
    def test_reproduces_the_real_attempt_7_risk_amount_and_r(self) -> None:
        """Cross-checked by hand against the real durable evidence: the
        real `FINAL_RISK_PASSED` event for this exact trade recorded
        `risk_amount=3.466985629344566400` for a 200-point stop at 0.02
        lots against `tick_size=0.00001`/`tick_value=0.8667464073361416`
        -- reusing `risk.sizing.realised_risk` here must reproduce that
        same number, not a different, hand-rolled pip-value formula.
        """
        spec = make_instrument_spec(
            tick_size=Decimal("0.00001"), tick_value=Decimal("0.8667464073361416")
        )
        evidence = make_evidence(
            reference_price=Decimal("1.15348"),
            stop_loss_price=Decimal("1.15148"),
            executed_volume=Decimal("0.02"),
            balance_before_close=Decimal("9993.55"),
            balance_after_close=Decimal("9993.66"),
        )
        r = derive_return_r(evidence, spec)
        assert r == pytest.approx(Decimal("0.11") / Decimal("3.466985629344566400"))

    def test_a_loss_of_exactly_the_planned_risk_is_minus_one_r(self) -> None:
        spec = make_instrument_spec(tick_size=Decimal("0.00001"), tick_value=Decimal("1"))
        # risk_amount = volume * (stop_distance/tick_size) * tick_value
        #             = 1 * (0.0020/0.00001) * 1 = 200
        evidence = make_evidence(
            reference_price=Decimal("1.10000"),
            stop_loss_price=Decimal("1.09800"),
            executed_volume=Decimal("1"),
            balance_before_close=Decimal("10000"),
            balance_after_close=Decimal("9800"),
        )
        assert derive_return_r(evidence, spec) == Decimal("-1")

    def test_flat_pnl_is_zero_r(self) -> None:
        spec = make_instrument_spec()
        evidence = make_evidence(
            balance_before_close=Decimal("10000"), balance_after_close=Decimal("10000")
        )
        assert derive_return_r(evidence, spec) == Decimal("0")

    def test_a_sell_profit_is_positive_r_without_any_side_specific_sign_handling(self) -> None:
        spec = make_instrument_spec(tick_size=Decimal("0.00001"), tick_value=Decimal("1"))
        evidence = make_evidence(
            side="SELL",
            reference_price=Decimal("1.10000"),
            stop_loss_price=Decimal("1.10200"),
            executed_volume=Decimal("1"),
            balance_before_close=Decimal("10000"),
            balance_after_close=Decimal("10100"),  # SELL profited as price fell
        )
        assert derive_return_r(evidence, spec) == Decimal("0.5")

    def test_raises_on_a_degenerate_zero_stop_distance(self) -> None:
        spec = make_instrument_spec()
        evidence = make_evidence(
            reference_price=Decimal("1.10000"), stop_loss_price=Decimal("1.10000")
        )
        with pytest.raises(ValueError):
            derive_return_r(evidence, spec)


class TestImpliedExitPrice:
    def test_is_reported_as_derived_never_as_an_observed_fill(self) -> None:
        spec = make_instrument_spec(
            tick_size=Decimal("0.00001"), tick_value=Decimal("0.8667464073361416")
        )
        evidence = make_evidence(
            executed_entry_price=Decimal("1.15346"),
            executed_volume=Decimal("0.02"),
            balance_before_close=Decimal("9993.55"),
            balance_after_close=Decimal("9993.66"),
        )
        price = implied_exit_price(evidence, spec)
        # A BUY that profited must imply an exit price above the entry.
        assert price > evidence.executed_entry_price

    def test_a_sell_profit_implies_a_lower_exit_price(self) -> None:
        spec = make_instrument_spec(tick_size=Decimal("0.00001"), tick_value=Decimal("1"))
        evidence = make_evidence(
            side="SELL",
            executed_entry_price=Decimal("1.10000"),
            executed_volume=Decimal("1"),
            balance_before_close=Decimal("10000"),
            balance_after_close=Decimal("10100"),
        )
        assert implied_exit_price(evidence, spec) < evidence.executed_entry_price

    def test_is_algebraically_consistent_with_derive_return_r(self) -> None:
        """(implied_exit - entry)/stop_distance, side-adjusted, must equal
        the R this module actually reports -- these are two different
        formulas over the same evidence and must never silently diverge."""
        spec = make_instrument_spec(
            tick_size=Decimal("0.00001"), tick_value=Decimal("0.8667464073361416")
        )
        evidence = make_evidence()
        r = derive_return_r(evidence, spec)
        exit_price = implied_exit_price(evidence, spec)
        stop_distance = pre_trade_stop_distance(evidence)
        if evidence.side == "SELL":
            recomputed = (evidence.executed_entry_price - exit_price) / stop_distance
        else:
            recomputed = (exit_price - evidence.executed_entry_price) / stop_distance
        assert recomputed == pytest.approx(r)


class TestBuildTradeId:
    def test_is_stable_and_derived_from_the_durable_order_request_id(self) -> None:
        order_request_id = uuid4()
        evidence = make_evidence(order_request_id=order_request_id)
        assert build_trade_id(evidence) == f"crumblr:{order_request_id}"
        # Calling it twice on the same evidence must yield the same id.
        assert build_trade_id(evidence) == build_trade_id(evidence)


class TestBuildSourceReference:
    def test_names_every_durable_crumblr_identifier_needed_to_reproduce_this(self) -> None:
        evidence = make_evidence()
        reference = build_source_reference(evidence)
        assert str(evidence.capsule_id) in reference
        assert str(evidence.order_request_id) in reference
        assert str(evidence.intent_id) in reference
        assert str(evidence.mt5_order_ticket) in reference
        assert str(evidence.mt5_deal_ticket) in reference
        assert evidence.code_commit in reference

    def test_stays_well_under_trainers_2000_character_limit(self) -> None:
        evidence = make_evidence()
        assert len(build_source_reference(evidence)) < 2000


class TestBuildNormalizedResult:
    def test_matches_trainers_result_contract_shape(self) -> None:
        evidence = make_evidence()
        result = build_normalized_result(
            evidence, Decimal("0.03172785"), transaction_costs_included=False
        )
        assert result["returns_r"] == [pytest.approx(0.03172785)]
        assert result["trade_ids"] == [build_trade_id(evidence)]
        assert result["pnl"] == [pytest.approx(0.11)]
        assert result["dates"] == ["2026-09-15"]
        assert result["transaction_costs_included"] is False
        assert isinstance(result["report_summary"], dict)

    def test_never_silently_claims_transaction_costs_are_included(self) -> None:
        """The exporter's own default is `False` (see
        `scripts/export_crumblr_trade_to_trainer.py`'s `--transaction-costs
        -included` flag) -- this function must not override that itself,
        since only the caller has evidence about broker costs."""
        evidence = make_evidence()
        result = build_normalized_result(evidence, Decimal("1"), transaction_costs_included=False)
        assert result["transaction_costs_included"] is False

    def test_can_report_true_when_the_caller_asserts_it(self) -> None:
        evidence = make_evidence()
        result = build_normalized_result(evidence, Decimal("1"), transaction_costs_included=True)
        assert result["transaction_costs_included"] is True

    def test_all_lists_share_the_same_length(self) -> None:
        evidence = make_evidence()
        result = build_normalized_result(evidence, Decimal("1"), transaction_costs_included=False)
        assert (
            len(result["returns_r"])
            == len(result["trade_ids"])
            == len(result["pnl"])
            == len(result["dates"])
        )
