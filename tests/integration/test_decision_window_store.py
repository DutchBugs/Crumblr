"""Durable decision-window idempotence storage (review 1.17 §8, F-054)

against real PostgreSQL. The fail-closed rule matrix and the orchestrator
wiring itself are unit-tested against `InMemoryDecisionWindowStore`
(`tests/unit/test_live_decision.py`); this file proves the real store's
`load_latest`/`save` round trip.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import Engine

from crumblr.application.decision_window import DecisionWindowState
from crumblr.persistence.decision_window import PostgresDecisionWindowStore
from tests.conftest import FIXED_NOW

pytestmark = pytest.mark.integration


def state(**overrides: object) -> DecisionWindowState:
    fields: dict[str, object] = {
        "canonical_symbol": "EUR/USD",
        "strategy_id": "baseline_v1",
        "config_version": "config-v1",
        "last_decided_open_time_utc": FIXED_NOW,
        "seen_decision_hashes": frozenset({"hash-a", "hash-b"}),
        "recorded_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return DecisionWindowState(**fields)  # type: ignore[arg-type]


class TestSaveAndLoadLatest:
    def test_nothing_recorded_yet_reads_as_none(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        assert (
            store.load_latest(
                canonical_symbol="EUR/USD", strategy_id="baseline_v1", config_version="config-v1"
            )
            is None
        )

    def test_a_saved_state_reads_back_intact(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state())

        loaded = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="baseline_v1", config_version="config-v1"
        )
        assert loaded is not None
        assert loaded.last_decided_open_time_utc == FIXED_NOW
        assert loaded.seen_decision_hashes == frozenset({"hash-a", "hash-b"})

    def test_a_later_save_becomes_the_new_latest(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state())
        store.save(
            state(
                last_decided_open_time_utc=FIXED_NOW + timedelta(minutes=5),
                seen_decision_hashes=frozenset({"hash-a", "hash-b", "hash-c"}),
            )
        )

        loaded = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="baseline_v1", config_version="config-v1"
        )
        assert loaded is not None
        assert loaded.last_decided_open_time_utc == FIXED_NOW + timedelta(minutes=5)
        assert loaded.seen_decision_hashes == frozenset({"hash-a", "hash-b", "hash-c"})

    def test_a_different_config_version_does_not_share_a_checkpoint(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state(config_version="config-v1"))

        loaded = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="baseline_v1", config_version="config-v2"
        )
        assert loaded is None

    def test_a_different_canonical_symbol_does_not_share_a_checkpoint(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state(canonical_symbol="EUR/USD"))

        loaded = store.load_latest(
            canonical_symbol="GBP/USD", strategy_id="baseline_v1", config_version="config-v1"
        )
        assert loaded is None

    def test_a_different_strategy_does_not_share_a_checkpoint(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state(strategy_id="baseline_v1"))

        loaded = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="ict_v1", config_version="config-v1"
        )
        assert loaded is None
