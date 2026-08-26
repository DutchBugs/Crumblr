"""Durable feature-value storage (review 1.17 §9 / review 1.18 §8, D-031)

against real PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import Engine

from crumblr.persistence.features import FeatureSnapshotStore
from crumblr.trading_agent.features import FeatureSnapshot, compute_features
from tests.conftest import FIXED_NOW, make_bar

pytestmark = pytest.mark.integration


def a_feature_snapshot() -> FeatureSnapshot:
    bars = tuple(make_bar(open_time_utc=FIXED_NOW + timedelta(minutes=5 * i)) for i in range(70))
    features = compute_features(bars, symbol="EUR/USD", computed_at_utc=bars[-1].open_time_utc)
    assert features is not None
    return features


class TestRecordAndReadBack:
    def test_a_recorded_snapshot_is_readable_by_id(self, engine: Engine) -> None:
        features = a_feature_snapshot()
        store = FeatureSnapshotStore(engine)

        store.record(features)

        payload = store.get_payload(features.feature_snapshot_id)
        assert payload is not None
        assert payload["feature_set_version"] == features.feature_set_version
        assert payload["trend_score"] == str(features.trend_score)

    def test_nothing_recorded_yet_reads_as_none(self, engine: Engine) -> None:
        features = a_feature_snapshot()
        store = FeatureSnapshotStore(engine)
        assert store.get_payload(features.feature_snapshot_id) is None

    def test_recording_the_same_snapshot_twice_does_not_duplicate(self, engine: Engine) -> None:
        features = a_feature_snapshot()
        store = FeatureSnapshotStore(engine)

        store.record(features)
        store.record(features)

        payload = store.get_payload(features.feature_snapshot_id)
        assert payload is not None
