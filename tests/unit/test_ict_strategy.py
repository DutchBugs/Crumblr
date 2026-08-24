"""The ICT entry model as a whole.

One fixture builds a complete, textbook setup: an old low, a sweep of it, a
displacement leg up leaving a fair value gap, and a retracement back into that
gap during the New York killzone. The model must take it.

Every other test removes exactly one clause from that fixture and asserts the
model declines — which is what "required condition" has to mean if the word is
to carry any weight.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

import pytest

from crumblr.domain.enums import DataQuality, SessionState, Side
from crumblr.domain.models import Bar, MarketSnapshot
from crumblr.trading_agent import ict
from crumblr.trading_agent.base import AgentContext, StrategyOutcome
from crumblr.trading_agent.sessions import Killzone, killzone_at
from tests.conftest import make_instrument_spec

SPEC = make_instrument_spec()

# 13:30 UTC on Thursday 15 January 2026 is 08:30 EST — inside the New York
# killzone, and a weekday the market is open.
SETUP_END = datetime(2026, 1, 15, 13, 30, tzinfo=UTC)


def _bar(time: datetime, o: str, h: str, l: str, c: str) -> Bar:  # noqa: E741
    return Bar(
        open_time_utc=time,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        tick_volume=200,
    )


def build_setup(
    *,
    with_sweep: bool = True,
    with_displacement: bool = True,
    retrace_into_gap: bool = True,
) -> tuple[Bar, ...]:
    """A complete bullish ICT setup, ending at SETUP_END.

    Shape, in order: a base that establishes an old swing low at 1.09800, a
    sweep that pierces it to 1.09700 and closes back above, a displacement leg
    up to 1.10400 leaving a gap, then a retracement back into that gap.
    """
    # Collected as plain quotes first, then timestamped backwards from
    # SETUP_END, so the fixture's length and its end time stay independent.
    quotes: list[tuple[str, str, str, str]] = []

    def add(o: str, h: str, l: str, c: str) -> None:  # noqa: E741
        quotes.append((o, h, l, c))

    # --- Base: an oscillation that leaves swing lows near 1.09800 ----------
    for _ in range(24):
        add("1.10000", "1.10040", "1.09960", "1.09980")
        add("1.09980", "1.10000", "1.09940", "1.09960")
        add("1.09960", "1.09990", "1.09800", "1.09900")  # the low that will rest
        add("1.09900", "1.10020", "1.09880", "1.10000")
        add("1.10000", "1.10050", "1.09970", "1.10010")

    # --- Quiet approach, so ATR is modest before the displacement ----------
    for _ in range(8):
        add("1.10000", "1.10030", "1.09970", "1.10000")

    # --- The sweep: pierce the old low, close back above it ----------------
    # Closing back above 1.09800 is what makes this a sweep. A bar that pierces
    # and closes below has broken the level, not swept it.
    if with_sweep:
        add("1.10000", "1.10010", "1.09700", "1.09850")
    else:
        add("1.10000", "1.10010", "1.09950", "1.09960")

    # --- Displacement up, leaving a gap low in the leg ---------------------
    # The gap must form early in the move. A gap made halfway up the leg can
    # never be reached at a 0.62-0.79 retracement, so "inside the gap" and
    # "inside the OTE band" would be mutually exclusive.
    if with_displacement:
        add("1.09850", "1.09880", "1.09820", "1.09860")  # gap lower edge 1.09880
        add("1.09860", "1.10250", "1.09850", "1.10230")  # the displacement bar
        add("1.10230", "1.10420", "1.09950", "1.10400")  # gap upper edge 1.09950
    else:
        for _ in range(3):
            add("1.09850", "1.09880", "1.09820", "1.09860")

    # --- Retrace back into the gap -----------------------------------------
    if retrace_into_gap:
        add("1.10400", "1.10410", "1.10050", "1.10100")
        add("1.10100", "1.10110", "1.09900", "1.09910")  # closes inside the gap
    else:
        add("1.10400", "1.10460", "1.10380", "1.10440")
        add("1.10440", "1.10500", "1.10420", "1.10480")

    start = SETUP_END - timedelta(minutes=5 * (len(quotes) - 1))
    return tuple(
        _bar(start + timedelta(minutes=5 * index), *quote) for index, quote in enumerate(quotes)
    )


def snapshot_for(bars: tuple[Bar, ...], *, at: datetime | None = None) -> MarketSnapshot:
    """A snapshot quoting around the last bar's close."""
    last = bars[-1]
    moment = at or last.open_time_utc
    half_spread = Decimal("0.00004")
    return MarketSnapshot(
        snapshot_id=uuid5(NAMESPACE_URL, f"test:{moment.isoformat()}"),
        symbol="EUR/USD",
        event_time_utc=moment,
        received_time_utc=moment + timedelta(milliseconds=10),
        bid=last.close - half_spread,
        ask=last.close + half_spread,
        spread_points=8,
        timeframe="M5",
        bars=bars,
        session_state=SessionState.OPEN,
        symbol_spec_version=SPEC.spec_version,
        data_quality=DataQuality.GOOD,
    )


def run(
    bars: tuple[Bar, ...] | None = None,
    *,
    config: ict.IctConfig | None = None,
    at: datetime | None = None,
    open_sides: tuple[Side, ...] = (),
) -> StrategyOutcome:
    sequence = bars if bars is not None else build_setup()
    return ict.evaluate(
        snapshot_for(sequence, at=at),
        sequence,
        SPEC,
        AgentContext(
            open_position_sides=open_sides,
            requested_risk_fraction=Decimal("0.005"),
            min_stop_distance_points=50,
        ),
        config or ict.IctConfig(),
    )


class TestTheFixtureIsWhatItClaims:
    """If the fixture drifts, every test below becomes meaningless."""

    def test_the_setup_ends_in_the_new_york_killzone(self) -> None:
        assert killzone_at(SETUP_END) is Killzone.NEW_YORK_AM

    def test_the_fixture_produces_enough_history(self) -> None:
        assert len(build_setup()) >= ict.MINIMUM_BARS

    def test_the_sweep_pierces_the_old_low(self) -> None:
        bars = build_setup()
        assert min(bar.low for bar in bars) == Decimal("1.09700")

    def test_the_final_price_sits_inside_the_displacement_gap(self) -> None:
        bars = build_setup()
        assert Decimal("1.09880") < bars[-1].close < Decimal("1.09950")

    def test_the_sweep_bar_closes_back_above_the_swept_level(self) -> None:
        """The distinction between a sweep and a break."""
        bars = build_setup()
        swept = next(bar for bar in bars if bar.low == Decimal("1.09700"))
        assert swept.close > Decimal("1.09800")


class TestACompleteSetupIsTaken:
    def test_the_model_proposes_a_long(self) -> None:
        outcome = run()
        assert outcome.decision.intent is not None, (
            f"complete setup declined: {outcome.decision.reason_codes}"
        )
        assert outcome.decision.intent.side is Side.BUY

    def test_the_stop_sits_below_the_swept_low(self) -> None:
        intent = run().decision.intent
        assert intent is not None
        assert intent.stop_loss_price is not None
        assert intent.stop_loss_price < Decimal("1.09700")

    def test_the_target_is_above_the_entry(self) -> None:
        intent = run().decision.intent
        assert intent is not None
        assert intent.take_profit_price is not None
        assert intent.take_profit_price > intent.reference_price

    def test_the_evidence_records_every_condition_met(self) -> None:
        features = run().features
        assert isinstance(features, ict.IctFeatureSnapshot)
        for condition in (
            "market_open",
            "killzone",
            "liquidity_sweep",
            "structure_shift",
            "fair_value_gap",
            "price_in_zone",
            "discount_premium",
            "optimal_trade_entry",
        ):
            assert condition in features.conditions_met, f"{condition} was not met"

    def test_no_required_condition_failed(self) -> None:
        """An optional condition may fail; a required one may not."""
        features = run().features
        assert isinstance(features, ict.IctFeatureSnapshot)
        optional = {"order_block", "liquidity_target"}
        assert set(features.conditions_failed) <= optional, (
            f"a required condition failed: {set(features.conditions_failed) - optional}"
        )

    def test_a_target_is_still_set_when_no_liquidity_pool_qualifies(self) -> None:
        """Falling back to a reward multiple, rather than declining the trade."""
        intent = run().decision.intent
        assert intent is not None
        assert intent.take_profit_price is not None

    def test_the_evidence_records_the_swept_level(self) -> None:
        features = run().features
        assert isinstance(features, ict.IctFeatureSnapshot)
        assert features.swept_level == Decimal("1.09800")
        assert features.sweep_direction is Side.BUY

    def test_the_decision_is_reproducible(self) -> None:
        first, second = run().decision.intent, run().decision.intent
        assert first is not None and second is not None
        assert first.decision_hash == second.decision_hash
        assert first.intent_id == second.intent_id


class TestEachConditionCanRefuse:
    """Removing one clause must stop the trade, or the clause is decoration."""

    def test_no_sweep_no_trade(self) -> None:
        outcome = run(build_setup(with_sweep=False))
        assert outcome.decision.intent is None
        assert "no_liquidity_sweep" in outcome.decision.reason_codes

    def test_no_displacement_no_trade(self) -> None:
        outcome = run(build_setup(with_displacement=False))
        assert outcome.decision.intent is None

    def test_no_retracement_into_the_gap_no_trade(self) -> None:
        """The entry trigger: price must come back to the imbalance."""
        outcome = run(build_setup(retrace_into_gap=False))
        assert outcome.decision.intent is None
        assert "price_not_in_zone" in outcome.decision.reason_codes

    def test_outside_a_killzone_no_trade(self) -> None:
        # 18:00 UTC is 13:00 EST — past every configured window.
        outcome = run(at=datetime(2026, 1, 15, 18, 0, tzinfo=UTC))
        assert outcome.decision.intent is None
        assert any("outside_killzone" in code for code in outcome.decision.reason_codes)

    def test_a_closed_market_no_trade(self) -> None:
        # Saturday.
        outcome = run(at=datetime(2026, 1, 17, 13, 30, tzinfo=UTC))
        assert outcome.decision.intent is None
        assert "market_closed" in outcome.decision.reason_codes

    def test_an_existing_position_in_the_same_direction_no_trade(self) -> None:
        outcome = run(open_sides=(Side.BUY,))
        assert outcome.decision.intent is None
        assert "already_positioned" in outcome.decision.reason_codes

    def test_too_little_history_no_trade(self) -> None:
        outcome = run(build_setup()[-10:])
        assert outcome.decision.intent is None
        assert outcome.is_warming_up


class TestRelaxingAConditionIsMeasurable:
    """build.md §9.3: a condition's contribution has to be measurable."""

    def test_relaxing_the_killzone_admits_a_setup_it_would_have_refused(self) -> None:
        outside = datetime(2026, 1, 15, 18, 0, tzinfo=UTC)
        strict = run(at=outside)
        assert strict.decision.intent is None

        relaxed_conditions = replace(ict.IctConditions(), require_killzone=False)
        relaxed = run(at=outside, config=ict.IctConfig(conditions=relaxed_conditions))
        assert relaxed.decision.intent is not None

    def test_a_disabled_condition_is_still_recorded_as_failed(self) -> None:
        """Turning a condition off must not hide that it did not hold."""
        outside = datetime(2026, 1, 15, 18, 0, tzinfo=UTC)
        relaxed_conditions = replace(ict.IctConditions(), require_killzone=False)
        outcome = run(at=outside, config=ict.IctConfig(conditions=relaxed_conditions))
        features = outcome.features
        assert isinstance(features, ict.IctFeatureSnapshot)
        assert "killzone" in features.conditions_failed


class TestSupervisorCompatibility:
    """The model must not produce intents its own supervisor always vetoes."""

    def test_a_valid_setup_reports_a_known_regime(self) -> None:
        features = run().features
        assert features is not None
        from crumblr.domain.enums import Regime

        assert features.regime is not Regime.UNKNOWN, (
            "a setup entered on a structure shift must not report an unknown regime, "
            "or the supervisor vetoes every trade this model exists to take"
        )

    def test_confidence_is_within_the_supervisor_envelope(self) -> None:
        intent = run().decision.intent
        assert intent is not None
        assert 0.0 <= intent.confidence <= 1.0


class TestStrategyRegistry:
    def test_both_strategies_are_registered(self) -> None:
        from crumblr.trading_agent import registry

        assert set(registry.STRATEGIES) == {"baseline_v1", "ict_v1"}

    def test_an_unknown_strategy_id_fails_loudly(self) -> None:
        from crumblr.trading_agent import registry

        with pytest.raises(KeyError, match="unknown strategy_id"):
            registry.resolve("ict_v2_experimental")

    def test_the_registered_version_matches_the_module(self) -> None:
        from crumblr.trading_agent import registry

        assert registry.resolve("ict_v1").version == ict.STRATEGY_VERSION
