"""Ticks into bars, and what the stream did on the way (review 1.6 F-022).

build.md §26 makes "gaps/out-of-order data detected" an acceptance criterion
for Milestone 2. Detection is a claim that can only be tested with streams that
are deliberately broken, so most of this file feeds the pipeline bad data and
asserts on what it noticed.

The cases are hand-constructed with known answers rather than generated. A
pipeline tested against its own output agrees with itself, which is not the
property anyone needs from it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crumblr.domain.enums import BarOrigin, DataQuality, StreamAnomaly
from crumblr.domain.models import Bar, MarketTick
from crumblr.market_data.pipeline import (
    PIPELINE_NAME,
    BarBuildResult,
    PriceBasis,
    bars_from_ticks,
    bucket_start,
    interval_for,
    normalize_bars,
    pipeline_version,
)
from crumblr.persistence.market_data import tick_identity
from tests.conftest import make_instrument_spec

SOURCE = "test:feed"
START = datetime(2026, 1, 5, 8, 0, tzinfo=UTC)
SPEC = make_instrument_spec()


def tick(
    minute: float,
    bid: str,
    *,
    spread: str = "0.00010",
    quality: DataQuality = DataQuality.GOOD,
    second: int = 0,
) -> MarketTick:
    """One quote at `minute` minutes past the start of the series."""
    at = START + timedelta(minutes=minute, seconds=second)
    bid_price = Decimal(bid)
    ask_price = bid_price + Decimal(spread)
    return MarketTick(
        tick_id=tick_identity(
            source=SOURCE,
            canonical_symbol=SPEC.canonical_symbol,
            event_time_utc=at,
            bid=bid_price,
            ask=ask_price,
        ),
        source=SOURCE,
        canonical_symbol=SPEC.canonical_symbol,
        broker_symbol=SPEC.broker_symbol,
        event_time_utc=at,
        received_time_utc=at,
        bid=bid_price,
        ask=ask_price,
        data_quality=quality,
    )


def build(ticks: list[MarketTick], *, basis: PriceBasis = PriceBasis.BID) -> BarBuildResult:
    return bars_from_ticks(ticks, timeframe="M5", spec=SPEC, source=SOURCE, basis=basis)


class TestAggregation:
    def test_ticks_inside_one_interval_become_one_bar(self) -> None:
        result = build([tick(0, "1.0850"), tick(1, "1.0860"), tick(2, "1.0840"), tick(3, "1.0855")])

        assert len(result.bars) == 1
        bar = result.bars[0].bar
        assert (bar.open, bar.high, bar.low, bar.close) == (
            Decimal("1.0850"),
            Decimal("1.0860"),
            Decimal("1.0840"),
            Decimal("1.0855"),
        )
        assert bar.tick_volume == 4

    def test_a_bar_opens_on_the_interval_boundary_not_on_the_first_tick(self) -> None:
        """Two runs over different slices of one feed must agree on boundaries."""
        result = build([tick(2, "1.0850"), tick(3, "1.0851")])

        assert result.bars[0].bar.open_time_utc == START

    def test_ticks_spanning_intervals_become_separate_bars(self) -> None:
        result = build([tick(0, "1.0850"), tick(6, "1.0860"), tick(11, "1.0870")])

        assert [b.bar.open_time_utc for b in result.bars] == [
            START,
            START + timedelta(minutes=5),
            START + timedelta(minutes=10),
        ]

    def test_every_derived_bar_names_the_transformation_that_made_it(self) -> None:
        """Without this a derived bar is indistinguishable from a delivered one."""
        result = build([tick(0, "1.0850")])
        stored = result.bars[0]

        assert stored.origin is BarOrigin.AGGREGATED_FROM_TICKS
        assert stored.pipeline_version == f"{PIPELINE_NAME}/bid"
        assert stored.tick_count == 1

    def test_the_price_basis_is_part_of_the_identity_not_a_hidden_constant(self) -> None:
        """A mid-price series and a bid series are different series."""
        ticks = [tick(0, "1.0850"), tick(1, "1.0860")]

        bid = build(ticks).bars[0]
        mid = build(ticks, basis=PriceBasis.MID).bars[0]

        assert bid.bar.close != mid.bar.close
        assert bid.pipeline_version != mid.pipeline_version
        assert mid.pipeline_version == pipeline_version(PriceBasis.MID)

    def test_rebuilding_the_same_ticks_produces_the_same_bar_ids(self) -> None:
        """Re-ingesting a series must be a no-op, which needs stable identity."""
        ticks = [tick(0, "1.0850"), tick(6, "1.0860")]

        first = [b.bar_id for b in build(ticks).bars]
        second = [b.bar_id for b in build(ticks).bars]

        assert first == second


class TestGapsAreDetectedAndNotFilled:
    def test_a_missing_interval_is_reported(self) -> None:
        # 08:00 and 08:10 have ticks; 08:05 has none.
        result = build([tick(0, "1.0850"), tick(11, "1.0860")])

        gaps = result.anomalies_of(StreamAnomaly.GAP)
        assert len(gaps) == 1
        assert "1 M5 interval(s)" in gaps[0].detail

    def test_the_gap_is_not_papered_over_with_an_invented_bar(self) -> None:
        """A flat bar for an interval nobody quoted is a fabricated observation."""
        result = build([tick(0, "1.0850"), tick(11, "1.0860")])

        assert len(result.bars) == 2, "the missing interval must not produce a bar"

    def test_the_bar_after_a_gap_carries_the_flag(self) -> None:
        """A strategy needing continuity has to be able to see it did not have any."""
        result = build([tick(0, "1.0850"), tick(11, "1.0860")])

        after = result.bars[-1]
        assert StreamAnomaly.GAP in after.anomalies
        assert after.data_quality is DataQuality.GAPPED

    def test_several_missing_intervals_are_counted(self) -> None:
        result = build([tick(0, "1.0850"), tick(21, "1.0860")])

        assert "3 M5 interval(s)" in result.anomalies_of(StreamAnomaly.GAP)[0].detail

    def test_a_continuous_stream_reports_nothing(self) -> None:
        result = build([tick(0, "1.0850"), tick(6, "1.0860"), tick(11, "1.0870")])

        assert result.is_clean


class TestOutOfOrderAndDuplicates:
    def test_a_late_tick_is_reported(self) -> None:
        result = build([tick(6, "1.0860"), tick(1, "1.0850")])

        assert result.anomalies_of(StreamAnomaly.OUT_OF_ORDER)

    def test_a_late_tick_is_kept_and_placed_where_it_happened(self) -> None:
        """Detected, not discarded. Losing the observation is the worse error."""
        result = build([tick(6, "1.0860"), tick(1, "1.0850")])

        assert [b.bar.open_time_utc for b in result.bars] == [
            START,
            START + timedelta(minutes=5),
        ]
        assert result.ticks_used == 2

    def test_an_identical_tick_delivered_twice_is_counted_once(self) -> None:
        repeated = tick(0, "1.0850")
        result = build([repeated, repeated])

        assert result.anomalies_of(StreamAnomaly.DUPLICATE)
        assert result.bars[0].bar.tick_volume == 1

    def test_two_different_quotes_in_the_same_instant_are_both_kept(self) -> None:
        """Real feeds do this. Collapsing them would discard real observations."""
        result = build([tick(0, "1.0850"), tick(0, "1.0851")])

        assert not result.anomalies_of(StreamAnomaly.DUPLICATE)
        assert result.bars[0].bar.tick_volume == 2


class TestQuotesThatAreNotPrices:
    def test_a_crossed_quote_is_refused_as_a_price_source(self) -> None:
        """An ask below a bid never existed as a tradeable price."""
        crossed = tick(1, "1.0850", spread="-0.00050")
        result = build([tick(0, "1.0840"), crossed])

        assert result.anomalies_of(StreamAnomaly.CROSSED_QUOTE)
        assert result.bars[0].bar.tick_volume == 1
        assert result.bars[0].bar.high == Decimal("1.0840")

    def test_a_window_of_only_crossed_quotes_produces_no_bar(self) -> None:
        result = build([tick(0, "1.0850", spread="-0.00010")])

        assert result.bars == ()
        assert result.anomalies_of(StreamAnomaly.CROSSED_QUOTE)

    def test_suspect_ticks_taint_the_bar_they_are_in(self) -> None:
        result = build([tick(0, "1.0850"), tick(1, "1.0860", quality=DataQuality.SUSPECT)])

        assert result.bars[0].data_quality is DataQuality.SUSPECT


class TestNormalizingADeliveredSeries:
    """A broker's own bars get the same scrutiny as ones built here."""

    @staticmethod
    def bar(minute: int, close: str = "1.0850") -> Bar:
        return Bar(
            open_time_utc=START + timedelta(minutes=minute),
            open=Decimal("1.0840"),
            high=Decimal("1.0860"),
            low=Decimal("1.0830"),
            close=Decimal(close),
            tick_volume=10,
        )

    def normalize(self, bars: list[Bar]) -> BarBuildResult:
        return normalize_bars(
            bars,
            timeframe="M5",
            spec=SPEC,
            source=SOURCE,
            origin=BarOrigin.BROKER,
            received_time_utc=START,
        )

    def test_a_delivered_series_is_stamped_with_its_origin(self) -> None:
        result = self.normalize([self.bar(0), self.bar(5)])

        assert all(b.origin is BarOrigin.BROKER for b in result.bars)
        assert all(b.pipeline_version is None for b in result.bars), (
            "a delivered bar went through no transformation and must not claim one"
        )

    def test_a_missing_interval_in_a_delivered_series_is_detected(self) -> None:
        result = self.normalize([self.bar(0), self.bar(10)])

        assert result.anomalies_of(StreamAnomaly.GAP)
        assert result.bars[-1].data_quality is DataQuality.GAPPED

    def test_a_repeated_bar_is_dropped_and_reported(self) -> None:
        result = self.normalize([self.bar(0), self.bar(0, close="1.0859")])

        assert len(result.bars) == 1
        assert result.anomalies_of(StreamAnomaly.DUPLICATE)

    def test_a_bar_arriving_late_is_flagged(self) -> None:
        result = self.normalize([self.bar(10), self.bar(0)])

        assert result.anomalies_of(StreamAnomaly.OUT_OF_ORDER)
        assert result.bars[-1].data_quality is DataQuality.OUT_OF_ORDER

    def test_a_misaligned_bar_is_reported(self) -> None:
        """An M5 bar opening at 08:02 is not an M5 bar."""
        result = self.normalize([self.bar(2)])

        assert result.anomalies_of(StreamAnomaly.OUT_OF_ORDER)

    def test_aggregated_origin_is_refused_here(self) -> None:
        """Only the tick path may claim to have aggregated anything."""
        with pytest.raises(ValueError, match="bars_from_ticks"):
            normalize_bars(
                [self.bar(0)],
                timeframe="M5",
                spec=SPEC,
                source=SOURCE,
                origin=BarOrigin.AGGREGATED_FROM_TICKS,
                received_time_utc=START,
            )


class TestTimeframes:
    def test_an_unknown_timeframe_fails_loudly(self) -> None:
        with pytest.raises(KeyError, match="unknown timeframe"):
            interval_for("M7")

    @pytest.mark.parametrize(
        ("timeframe", "minutes"),
        [("M1", 1), ("M5", 5), ("M15", 15), ("M30", 30), ("H1", 60), ("H4", 240)],
    )
    def test_intervals_are_what_their_names_say(self, timeframe: str, minutes: int) -> None:
        assert interval_for(timeframe) == timedelta(minutes=minutes)

    def test_bucket_boundaries_are_anchored_on_the_epoch(self) -> None:
        moment = datetime(2026, 1, 5, 8, 7, 30, tzinfo=UTC)

        assert bucket_start(moment, timedelta(minutes=5)) == datetime(2026, 1, 5, 8, 5, tzinfo=UTC)
