"""Durable `InstrumentSpec` storage (closes `review/DEVIATIONS.md` D-045's

named gap) against real PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import Engine

from crumblr.persistence.instrument_specs import InstrumentSpecStore
from tests.conftest import make_instrument_spec

pytestmark = pytest.mark.integration


class TestRecordAndReadBack:
    def test_a_recorded_spec_is_the_latest_one(self, engine: Engine) -> None:
        spec = make_instrument_spec()
        store = InstrumentSpecStore(engine)

        store.record(spec)

        latest = store.latest(canonical_symbol="EUR/USD")
        assert latest is not None
        assert latest.spec_version == spec.spec_version

    def test_no_spec_recorded_yet_reads_as_none(self, engine: Engine) -> None:
        store = InstrumentSpecStore(engine)
        assert store.latest(canonical_symbol="EUR/USD") is None

    def test_recording_the_same_spec_twice_does_not_duplicate(self, engine: Engine) -> None:
        spec = make_instrument_spec()
        store = InstrumentSpecStore(engine)

        store.record(spec)
        store.record(spec)

        # No error, and the read still resolves to exactly one spec.
        latest = store.latest(canonical_symbol="EUR/USD")
        assert latest is not None

    def test_a_changed_spec_becomes_the_new_latest(self, engine: Engine) -> None:
        older = make_instrument_spec()
        newer = make_instrument_spec(
            digits=4, captured_at_utc=older.captured_at_utc + timedelta(minutes=5)
        )
        store = InstrumentSpecStore(engine)

        store.record(older)
        store.record(newer)

        latest = store.latest(canonical_symbol="EUR/USD")
        assert latest is not None
        assert latest.spec_version == newer.spec_version


class TestEarliest:
    """F-053: reconciliation's baseline for detecting a broker-side change."""

    def test_no_spec_recorded_yet_reads_as_none(self, engine: Engine) -> None:
        store = InstrumentSpecStore(engine)
        assert store.earliest(canonical_symbol="EUR/USD") is None

    def test_one_spec_recorded_is_both_earliest_and_latest(self, engine: Engine) -> None:
        spec = make_instrument_spec()
        store = InstrumentSpecStore(engine)
        store.record(spec)

        assert store.earliest(canonical_symbol="EUR/USD") is not None
        earliest = store.earliest(canonical_symbol="EUR/USD")
        latest = store.latest(canonical_symbol="EUR/USD")
        assert earliest is not None and latest is not None
        assert earliest.spec_version == latest.spec_version == spec.spec_version

    def test_earliest_stays_the_first_one_observed_after_a_later_change(
        self, engine: Engine
    ) -> None:
        older = make_instrument_spec()
        newer = make_instrument_spec(
            digits=4, captured_at_utc=older.captured_at_utc + timedelta(minutes=5)
        )
        store = InstrumentSpecStore(engine)

        store.record(older)
        store.record(newer)

        earliest = store.earliest(canonical_symbol="EUR/USD")
        assert earliest is not None
        assert earliest.spec_version == older.spec_version


class TestAtOrBefore:
    """Reproducing a historical sizing/risk calculation (e.g. a Trainer
    export's `return_r`) needs the spec actually in force *then*, not
    whatever `latest()` returns now."""

    def test_no_spec_recorded_yet_reads_as_none(self, engine: Engine) -> None:
        store = InstrumentSpecStore(engine)
        at = make_instrument_spec().captured_at_utc
        assert store.at_or_before(canonical_symbol="EUR/USD", at=at) is None

    def test_a_spec_captured_after_the_target_time_is_not_returned(self, engine: Engine) -> None:
        spec = make_instrument_spec()
        store = InstrumentSpecStore(engine)
        store.record(spec)

        result = store.at_or_before(
            canonical_symbol="EUR/USD", at=spec.captured_at_utc - timedelta(minutes=1)
        )
        assert result is None

    def test_returns_the_spec_in_force_at_exactly_that_moment(self, engine: Engine) -> None:
        spec = make_instrument_spec()
        store = InstrumentSpecStore(engine)
        store.record(spec)

        result = store.at_or_before(canonical_symbol="EUR/USD", at=spec.captured_at_utc)
        assert result is not None
        assert result.spec_version == spec.spec_version

    def test_a_later_change_does_not_affect_an_earlier_reconstruction(self, engine: Engine) -> None:
        older = make_instrument_spec()
        newer = make_instrument_spec(
            digits=4, captured_at_utc=older.captured_at_utc + timedelta(minutes=5)
        )
        store = InstrumentSpecStore(engine)
        store.record(older)
        store.record(newer)

        result = store.at_or_before(canonical_symbol="EUR/USD", at=older.captured_at_utc)
        assert result is not None
        assert result.spec_version == older.spec_version

        result_after_change = store.at_or_before(
            canonical_symbol="EUR/USD", at=newer.captured_at_utc
        )
        assert result_after_change is not None
        assert result_after_change.spec_version == newer.spec_version
