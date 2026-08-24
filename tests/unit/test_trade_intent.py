"""The Trading Agent's output contract.

build.md §1: the agent proposes, the risk engine constrains. Most of these
tests exist to prove the agent cannot reach past that boundary.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from crumblr.domain.enums import EntryType, Side
from crumblr.domain.models import TradeIntent
from tests.conftest import FIXED_NOW, make_intent


class TestAgentCannotSizePositions:
    """build.md §6.2: 'The Trading Agent does not send lot_size.'"""

    @pytest.mark.parametrize(
        "forbidden_field", ["lot_size", "volume", "lots", "position_size", "quantity"]
    )
    def test_no_sizing_field_exists_on_the_contract(self, forbidden_field: str) -> None:
        assert forbidden_field not in TradeIntent.model_fields

    @pytest.mark.parametrize(
        "forbidden_field", ["lot_size", "volume", "lots", "position_size", "quantity"]
    )
    def test_sizing_field_cannot_be_smuggled_in(self, forbidden_field: str) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            make_intent(**{forbidden_field: Decimal("1.0")})

    def test_risk_fraction_is_capped_at_full_equity(self) -> None:
        with pytest.raises(ValidationError):
            make_intent(requested_risk_fraction=Decimal("1.5"))


class TestStopLossIsMandatoryForDirectionalIntents:
    """Sizing is derived from stop distance, so an unstopped intent is unsizeable."""

    @pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
    def test_directional_intent_requires_a_stop(self, side: Side) -> None:
        reference = Decimal("1.08500")
        stop = Decimal("1.08300") if side is Side.BUY else Decimal("1.08700")
        take_profit = Decimal("1.08900") if side is Side.BUY else Decimal("1.08100")
        with pytest.raises(ValidationError, match="requires a stop_loss_price"):
            make_intent(
                side=side,
                reference_price=reference,
                stop_loss_price=None,
                take_profit_price=take_profit,
            )
        # The same intent with its stop restored is valid.
        assert (
            make_intent(
                side=side,
                reference_price=reference,
                stop_loss_price=stop,
                take_profit_price=take_profit,
            ).stop_loss_price
            == stop
        )

    @pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
    def test_directional_intent_requires_a_risk_fraction(self, side: Side) -> None:
        stop = Decimal("1.08300") if side is Side.BUY else Decimal("1.08700")
        take_profit = Decimal("1.08900") if side is Side.BUY else Decimal("1.08100")
        with pytest.raises(ValidationError, match="requires a requested_risk_fraction"):
            make_intent(
                side=side,
                stop_loss_price=stop,
                take_profit_price=take_profit,
                requested_risk_fraction=None,
            )

    @pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
    def test_directional_intent_must_explain_itself(self, side: Side) -> None:
        stop = Decimal("1.08300") if side is Side.BUY else Decimal("1.08700")
        take_profit = Decimal("1.08900") if side is Side.BUY else Decimal("1.08100")
        with pytest.raises(ValidationError, match="at least one reason code"):
            make_intent(
                side=side,
                stop_loss_price=stop,
                take_profit_price=take_profit,
                reason_codes=(),
            )


class TestStopDirection:
    """A stop on the wrong side of entry accelerates losses instead of capping them."""

    def test_buy_stop_above_entry_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="BUY stop_loss_price must be below"):
            make_intent(side=Side.BUY, stop_loss_price=Decimal("1.08700"))

    def test_buy_target_below_entry_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="BUY take_profit_price must be above"):
            make_intent(side=Side.BUY, take_profit_price=Decimal("1.08100"))

    def test_sell_stop_below_entry_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="SELL stop_loss_price must be above"):
            make_intent(
                side=Side.SELL,
                stop_loss_price=Decimal("1.08300"),
                take_profit_price=Decimal("1.08100"),
            )

    def test_sell_target_above_entry_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="SELL take_profit_price must be below"):
            make_intent(
                side=Side.SELL,
                stop_loss_price=Decimal("1.08700"),
                take_profit_price=Decimal("1.08900"),
            )


class TestFlatIntent:
    """FLAT is a close instruction, not a trade. build.md §30.4 treats it as first class."""

    def test_flat_intent_is_valid_without_stops_or_risk(self) -> None:
        intent = make_intent(
            side=Side.FLAT,
            stop_loss_price=None,
            take_profit_price=None,
            requested_risk_fraction=None,
        )
        assert intent.side is Side.FLAT

    def test_flat_intent_may_not_carry_stops(self) -> None:
        with pytest.raises(ValidationError, match="FLAT intent must not carry stop"):
            make_intent(side=Side.FLAT, requested_risk_fraction=None)

    def test_flat_intent_may_not_request_risk(self) -> None:
        with pytest.raises(ValidationError, match="FLAT intent must not request a risk fraction"):
            make_intent(
                side=Side.FLAT,
                stop_loss_price=None,
                take_profit_price=None,
                requested_risk_fraction=Decimal("0.01"),
            )


class TestLifetime:
    def test_expiry_must_follow_creation(self) -> None:
        with pytest.raises(ValidationError, match="expires_at_utc must be after"):
            make_intent(expires_at_utc=FIXED_NOW - timedelta(seconds=1))

    def test_zero_length_lifetime_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="expires_at_utc must be after"):
            make_intent(expires_at_utc=FIXED_NOW)

    def test_is_expired_uses_the_supplied_clock(self) -> None:
        intent = make_intent()
        assert not intent.is_expired(at=FIXED_NOW + timedelta(seconds=29))
        assert intent.is_expired(at=FIXED_NOW + timedelta(seconds=30))


class TestDecisionHash:
    """build.md §13.3: the same input must reproduce the same result."""

    def test_hash_is_stable_across_identical_intents(self) -> None:
        shared = {"feature_snapshot_id": make_intent().feature_snapshot_id}
        first = make_intent(**shared)
        second = make_intent(**shared)
        assert first.intent_id != second.intent_id
        assert first.decision_hash == second.decision_hash

    def test_hash_survives_a_json_round_trip(self) -> None:
        intent = make_intent()
        restored = TradeIntent.model_validate(intent.model_dump(mode="json"))
        assert restored.decision_hash == intent.decision_hash
        assert restored == intent

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("reference_price", Decimal("1.08501")),
            ("stop_loss_price", Decimal("1.08301")),
            ("confidence", 0.63),
            ("strategy_version", "0.1.1"),
            ("side", Side.FLAT),
            ("entry_type", EntryType.LIMIT),
        ],
    )
    def test_hash_changes_when_the_decision_changes(self, field: str, value: object) -> None:
        baseline = make_intent()
        if field == "side":
            changed = make_intent(
                side=Side.FLAT,
                stop_loss_price=None,
                take_profit_price=None,
                requested_risk_fraction=None,
                feature_snapshot_id=baseline.feature_snapshot_id,
                intent_id=baseline.intent_id,
            )
        else:
            changed = make_intent(
                **{field: value},
                feature_snapshot_id=baseline.feature_snapshot_id,
                intent_id=baseline.intent_id,
            )
        assert changed.decision_hash != baseline.decision_hash

    def test_trailing_zeros_do_not_change_the_hash(self) -> None:
        """1.085 and 1.08500 are the same price and must fingerprint alike."""
        shared = {"feature_snapshot_id": make_intent().feature_snapshot_id}
        compact = make_intent(reference_price=Decimal("1.085"), **shared)
        padded = make_intent(reference_price=Decimal("1.08500"), **shared)
        assert compact.decision_hash == padded.decision_hash


class TestImmutability:
    def test_intent_cannot_be_mutated_after_construction(self) -> None:
        intent = make_intent()
        with pytest.raises(ValidationError):
            intent.confidence = 0.99

    def test_reason_codes_are_an_immutable_sequence(self) -> None:
        intent = make_intent()
        assert isinstance(intent.reason_codes, tuple)
