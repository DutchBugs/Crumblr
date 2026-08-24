"""Exactness at the model boundary (build.md §6, §12.2).

Two classes of silent corruption are refused here rather than detected later:
binary floats standing in for prices, and naive datetimes standing in for
instants.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from crumblr.domain.money import (
    points_to_price,
    price_to_points,
    quantize_price,
    tick_size_for_digits,
)
from crumblr.domain.timeutils import age_ms
from tests.conftest import FIXED_NOW, make_intent, make_snapshot


class TestFloatsAreRefused:
    def test_float_price_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="float is not accepted"):
            make_intent(reference_price=1.085)

    def test_float_risk_fraction_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="float is not accepted"):
            make_intent(requested_risk_fraction=0.005)

    def test_decimal_and_string_are_both_accepted(self) -> None:
        from_decimal = make_intent(reference_price=Decimal("1.08500"))
        from_string = make_intent(reference_price="1.08500")
        assert from_decimal.reference_price == from_string.reference_price

    def test_confidence_stays_a_float(self) -> None:
        """Confidence is a model score, not money; it has no exactness requirement."""
        assert make_intent(confidence=0.62).confidence == pytest.approx(0.62)


class TestNaiveDatetimesAreRefused:
    def test_naive_datetime_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive datetime is not accepted"):
            make_intent(created_at_utc=datetime(2026, 8, 17, 12, 0, 0))  # noqa: DTZ001

    def test_aware_non_utc_datetime_is_normalised(self) -> None:
        amsterdam = timezone(timedelta(hours=2))
        intent = make_intent(created_at_utc=datetime(2026, 8, 17, 14, 0, 0, tzinfo=amsterdam))
        assert intent.created_at_utc == datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
        assert intent.created_at_utc.tzinfo is UTC


class TestPointConversion:
    def test_round_trip_preserves_points(self) -> None:
        point = Decimal("0.00001")
        assert price_to_points(points_to_price(200, point), point) == 200

    def test_spread_in_points_matches_a_five_digit_quote(self) -> None:
        snapshot = make_snapshot()
        point = Decimal("0.00001")
        assert price_to_points(snapshot.ask - snapshot.bid, point) == snapshot.spread_points

    def test_distance_is_signed(self) -> None:
        point = Decimal("0.00001")
        assert price_to_points(Decimal("-0.00200"), point) == -200

    def test_zero_point_size_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="point size must be positive"):
            price_to_points(Decimal("0.001"), Decimal("0"))


class TestQuantisation:
    def test_tick_size_matches_digits(self) -> None:
        assert tick_size_for_digits(5) == Decimal("0.00001")
        assert tick_size_for_digits(3) == Decimal("0.001")

    def test_price_is_snapped_to_quoting_precision(self) -> None:
        assert quantize_price(Decimal("1.0850049"), 5) == Decimal("1.08500")

    def test_negative_digits_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="digits must be non-negative"):
            tick_size_for_digits(-1)


class TestDataAge:
    def test_age_is_positive_for_past_events(self) -> None:
        assert age_ms(FIXED_NOW - timedelta(milliseconds=1500), now=FIXED_NOW) == 1500

    def test_future_event_reports_negative_age(self) -> None:
        """Clock skew must be visible to the risk engine, not clamped to zero."""
        assert age_ms(FIXED_NOW + timedelta(seconds=1), now=FIXED_NOW) == -1000
