"""Durable broker-state observations (review 1.15 F-047), against real

PostgreSQL. Three claims are under test: the account snapshot and its
children are written together and read back verbatim; a snapshot with a
`FAILED` position/pending-order set genuinely stores no child rows rather
than an empty-looking success; and re-recording the same observation
collapses rather than duplicating, the same content-derived-identity
discipline every other store in this schema follows.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from crumblr.application.broker_state import BrokerStateObservation
from crumblr.domain.enums import SnapshotCompleteness
from crumblr.persistence.broker_state import BrokerStateStore
from tests.conftest import (
    make_broker_account_snapshot,
    make_broker_pending_order_snapshot,
    make_broker_position_snapshot,
)

pytestmark = pytest.mark.integration


class TestRecordAndReadBack:
    def test_an_account_snapshot_with_no_children_round_trips(self, engine: Engine) -> None:
        account = make_broker_account_snapshot()
        store = BrokerStateStore(engine)

        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        assert store.latest_account_snapshot() == account
        assert store.positions_for(account.snapshot_id) == ()
        assert store.pending_orders_for(account.snapshot_id) == ()

    def test_positions_and_pending_orders_are_tied_to_their_snapshot(self, engine: Engine) -> None:
        account = make_broker_account_snapshot()
        position = make_broker_position_snapshot(snapshot_id=account.snapshot_id)
        order = make_broker_pending_order_snapshot(snapshot_id=account.snapshot_id)
        store = BrokerStateStore(engine)

        store.record(
            BrokerStateObservation(account=account, positions=(position,), pending_orders=(order,))
        )

        assert store.positions_for(account.snapshot_id) == (position,)
        assert store.pending_orders_for(account.snapshot_id) == (order,)

    def test_the_latest_snapshot_is_the_most_recently_observed_one(self, engine: Engine) -> None:
        older = make_broker_account_snapshot()
        newer = make_broker_account_snapshot(
            observed_at_utc=older.observed_at_utc + timedelta(minutes=1)
        )
        store = BrokerStateStore(engine)

        store.record(BrokerStateObservation(account=older, positions=(), pending_orders=()))
        store.record(BrokerStateObservation(account=newer, positions=(), pending_orders=()))

        assert store.latest_account_snapshot() == newer


class TestCompleteSetSemantics:
    """F-047 §5: a `FAILED`/`UNKNOWN` set must store no child rows, so it can

    never be misread as a confirmed-empty book by anything reading these
    tables later (reconciliation, the dashboard).
    """

    def test_a_failed_position_set_stores_no_position_rows(self, engine: Engine) -> None:
        account = make_broker_account_snapshot(position_set_state=SnapshotCompleteness.FAILED)
        store = BrokerStateStore(engine)

        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        stored = store.latest_account_snapshot()
        assert stored is not None
        assert stored.position_set_state is SnapshotCompleteness.FAILED
        assert store.positions_for(account.snapshot_id) == ()


class TestIdempotency:
    def test_recording_the_same_observation_twice_does_not_duplicate(self, engine: Engine) -> None:
        account = make_broker_account_snapshot()
        position = make_broker_position_snapshot(snapshot_id=account.snapshot_id)
        order = make_broker_pending_order_snapshot(snapshot_id=account.snapshot_id)
        observation = BrokerStateObservation(
            account=account, positions=(position,), pending_orders=(order,)
        )
        store = BrokerStateStore(engine)

        store.record(observation)
        store.record(observation)

        assert store.positions_for(account.snapshot_id) == (position,)
        assert store.pending_orders_for(account.snapshot_id) == (order,)
        assert store.record_count() == 1

    def test_two_different_snapshots_both_count(self, engine: Engine) -> None:
        store = BrokerStateStore(engine)
        store.record(
            BrokerStateObservation(
                account=make_broker_account_snapshot(snapshot_id=uuid4()),
                positions=(),
                pending_orders=(),
            )
        )
        store.record(
            BrokerStateObservation(
                account=make_broker_account_snapshot(snapshot_id=uuid4()),
                positions=(),
                pending_orders=(),
            )
        )

        assert store.record_count() == 2
