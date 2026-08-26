"""Durable decision-window idempotence storage (review 1.17 §8, F-054)

against real PostgreSQL. The fail-closed rule matrix and the orchestrator
wiring itself are unit-tested against `InMemoryDecisionWindowStore`
(`tests/unit/test_live_decision.py`); this file proves the real store's
`load_latest`/`save` round trip, including the three-state
`DecisionWindowRecord` shape review 1.19 §5 requires.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.application.decision_window import DecisionWindowState
from crumblr.persistence.decision_window import PostgresDecisionWindowStore
from crumblr.persistence.schema import decision_window_states
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
    def test_nothing_recorded_yet_reads_as_known_and_empty(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        record = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="baseline_v1", config_version="config-v1"
        )
        assert record.is_known
        assert record.state is None

    def test_a_saved_state_reads_back_intact(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state())

        record = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="baseline_v1", config_version="config-v1"
        )
        assert record.is_known
        assert record.state is not None
        assert record.state.last_decided_open_time_utc == FIXED_NOW
        assert record.state.seen_decision_hashes == frozenset({"hash-a", "hash-b"})

    def test_a_later_save_becomes_the_new_latest(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state())
        store.save(
            state(
                last_decided_open_time_utc=FIXED_NOW + timedelta(minutes=5),
                seen_decision_hashes=frozenset({"hash-a", "hash-b", "hash-c"}),
            )
        )

        record = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="baseline_v1", config_version="config-v1"
        )
        assert record.state is not None
        assert record.state.last_decided_open_time_utc == FIXED_NOW + timedelta(minutes=5)
        assert record.state.seen_decision_hashes == frozenset({"hash-a", "hash-b", "hash-c"})

    def test_a_different_config_version_does_not_share_a_checkpoint(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state(config_version="config-v1"))

        record = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="baseline_v1", config_version="config-v2"
        )
        assert record.is_known
        assert record.state is None

    def test_a_different_canonical_symbol_does_not_share_a_checkpoint(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state(canonical_symbol="EUR/USD"))

        record = store.load_latest(
            canonical_symbol="GBP/USD", strategy_id="baseline_v1", config_version="config-v1"
        )
        assert record.is_known
        assert record.state is None

    def test_a_different_strategy_does_not_share_a_checkpoint(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        store.save(state(strategy_id="baseline_v1"))

        record = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="ict_v1", config_version="config-v1"
        )
        assert record.is_known
        assert record.state is None


class TestUnreadableIsNeverConfusedWithEmpty:
    """Review 1.19 §5: a store that holds a row it cannot trust must say so,

    not silently read back as "nothing recorded yet" — the two must never
    look the same to a caller deciding whether it is safe to proceed.
    """

    def test_a_schema_version_mismatch_reads_as_unreadable_not_empty(self, engine: Engine) -> None:
        store = PostgresDecisionWindowStore(engine)
        # Bypass `save()` (which always writes the current SCHEMA_VERSION)
        # to plant a row this code no longer understands how to decode —
        # the same "a future migration changed the shape" scenario
        # `PostgresRiskSessionStore` guards against.
        with engine.begin() as connection:
            connection.execute(
                decision_window_states.insert().values(
                    event_id=uuid4(),
                    canonical_symbol="EUR/USD",
                    strategy_id="baseline_v1",
                    config_version="config-v1",
                    last_decided_open_time_utc=FIXED_NOW,
                    seen_decision_hashes=["hash-a"],
                    schema_version=99,
                )
            )

        record = store.load_latest(
            canonical_symbol="EUR/USD", strategy_id="baseline_v1", config_version="config-v1"
        )

        assert not record.is_known
        assert record.unreadable is not None
        assert record.state is None
