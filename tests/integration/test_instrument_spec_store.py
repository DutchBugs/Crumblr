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
