"""Property-based checks on the contract layer (build.md §4.1).

Example-based tests prove a contract holds for the cases someone thought of.
These check the invariants that must hold for every input the type system
admits — particularly reproducibility, which build.md §13.3 makes a promotion
requirement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from crumblr.domain.enums import EntryType, Environment, Side
from crumblr.domain.events import build_event, decode_event
from crumblr.domain.hashing import canonical_json, fingerprint
from crumblr.domain.models import TradeIntent
from crumblr.domain.money import points_to_price, price_to_points, quantize_price

PRICES = st.decimals(
    min_value=Decimal("0.5"),
    max_value=Decimal("2.0"),
    places=5,
    allow_nan=False,
    allow_infinity=False,
)
POINT_SIZES = st.sampled_from(
    [Decimal("0.00001"), Decimal("0.0001"), Decimal("0.001"), Decimal("0.01"), Decimal("1")]
)
UUIDS = st.uuids()
# Hypothesis bounds are naive by construction; UTC is attached by the map below.
TIMESTAMPS = st.datetimes(
    min_value=datetime(2020, 1, 1),  # noqa: DTZ001
    max_value=datetime(2035, 1, 1),  # noqa: DTZ001
).map(lambda value: value.replace(tzinfo=UTC))


@st.composite
def directional_intents(draw: st.DrawFn) -> TradeIntent:
    """Generate a coherent BUY or SELL intent with a correctly placed stop."""
    side = draw(st.sampled_from([Side.BUY, Side.SELL]))
    reference = draw(PRICES)
    offset = draw(st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("0.2"), places=5))
    target_offset = draw(
        st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("0.2"), places=5)
    )
    if side is Side.BUY:
        stop = reference - offset
        target = reference + target_offset
    else:
        stop = reference + offset
        target = reference - target_offset
    assume(stop > 0 and target > 0)

    created = draw(TIMESTAMPS)
    return TradeIntent(
        intent_id=draw(UUIDS),
        strategy_id=draw(st.text(min_size=1, max_size=32).filter(lambda s: s.strip())),
        strategy_version=draw(st.text(min_size=1, max_size=16).filter(lambda s: s.strip())),
        model_version=None,
        symbol="EUR/USD",
        side=side,
        created_at_utc=created,
        expires_at_utc=created + timedelta(seconds=draw(st.integers(1, 3600))),
        entry_type=draw(st.sampled_from(list(EntryType))),
        reference_price=reference,
        stop_loss_price=stop,
        take_profit_price=target,
        confidence=draw(st.floats(0.0, 1.0, allow_nan=False, allow_infinity=False)),
        reason_codes=tuple(
            draw(st.lists(st.text(min_size=1, max_size=16), min_size=1, max_size=4))
        ),
        requested_risk_fraction=draw(
            st.decimals(min_value=Decimal("0.0001"), max_value=Decimal("1"), places=4)
        ),
        feature_snapshot_id=draw(UUIDS),
    )


class TestFingerprintDeterminism:
    """build.md §11: a decision must be provable against the inputs behind it."""

    @given(
        st.dictionaries(
            st.text(min_size=1, max_size=8),
            st.one_of(st.integers(), st.text(max_size=8), st.none(), st.booleans()),
            min_size=1,
            max_size=6,
        )
    )
    def test_key_order_does_not_affect_the_fingerprint(self, payload: dict[str, object]) -> None:
        reversed_payload = dict(reversed(list(payload.items())))
        assert fingerprint(payload) == fingerprint(reversed_payload)

    @given(st.integers(min_value=0, max_value=10**6), st.integers(min_value=0, max_value=6))
    def test_decimal_scale_does_not_affect_the_fingerprint(
        self, unscaled: int, extra_zeros: int
    ) -> None:
        compact = Decimal(unscaled)
        padded = Decimal(f"{unscaled}.{'0' * extra_zeros}") if extra_zeros else compact
        assert fingerprint({"v": compact}) == fingerprint({"v": padded})

    @given(st.dictionaries(st.text(min_size=1, max_size=8), st.integers(), max_size=5))
    def test_canonical_json_is_stable(self, payload: dict[str, int]) -> None:
        assert canonical_json(payload) == canonical_json(dict(payload))


class TestPointArithmetic:
    @given(st.integers(min_value=-(10**6), max_value=10**6), POINT_SIZES)
    def test_point_conversion_round_trips(self, points: int, point: Decimal) -> None:
        assert price_to_points(points_to_price(points, point), point) == points

    @given(PRICES, st.integers(min_value=0, max_value=5))
    def test_quantisation_never_exceeds_the_requested_precision(
        self, price: Decimal, digits: int
    ) -> None:
        quantized = quantize_price(price, digits)
        assert -quantized.as_tuple().exponent <= digits  # type: ignore[operator]

    @given(PRICES, st.integers(min_value=0, max_value=5))
    def test_quantisation_is_idempotent(self, price: Decimal, digits: int) -> None:
        once = quantize_price(price, digits)
        assert quantize_price(once, digits) == once


class TestIntentInvariants:
    @given(directional_intents())
    @settings(max_examples=200)
    def test_stop_is_always_on_the_protective_side(self, intent: TradeIntent) -> None:
        assert intent.stop_loss_price is not None
        if intent.side is Side.BUY:
            assert intent.stop_loss_price < intent.reference_price
        else:
            assert intent.stop_loss_price > intent.reference_price

    @given(directional_intents())
    @settings(max_examples=200)
    def test_intent_round_trips_through_json_unchanged(self, intent: TradeIntent) -> None:
        restored = TradeIntent.model_validate(intent.model_dump(mode="json"))
        assert restored == intent
        assert restored.decision_hash == intent.decision_hash

    @given(directional_intents())
    @settings(max_examples=200)
    def test_hash_is_independent_of_the_intent_id(self, intent: TradeIntent) -> None:
        other_id = UUID(int=(intent.intent_id.int + 1) % (2**128))
        twin = intent.model_copy(update={"intent_id": other_id})
        assert twin.decision_hash == intent.decision_hash

    @given(directional_intents())
    @settings(max_examples=100)
    def test_intent_survives_the_event_journal(self, intent: TradeIntent) -> None:
        from uuid import uuid4

        event = build_event(
            intent,
            correlation_id=uuid4(),
            environment=Environment.REPLAY,
            source="trading_agent",
        )
        restored = decode_event(event.model_dump(mode="json"))
        assert restored.payload == intent

    @given(directional_intents())
    @settings(max_examples=100)
    def test_expiry_always_follows_creation(self, intent: TradeIntent) -> None:
        assert intent.expires_at_utc > intent.created_at_utc
        assert not intent.is_expired(at=intent.created_at_utc)
        assert intent.is_expired(at=intent.expires_at_utc)
