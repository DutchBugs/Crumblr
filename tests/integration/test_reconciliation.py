"""`reconcile()` against the real `BrokerStateStore` (review 1.15 §14, 1.16

§7-8) — the fail-closed rule matrix itself is unit-tested against an
in-memory fake (`tests/unit/test_reconciliation.py`); this file proves the
`BrokerStateSource` Protocol the real store implements actually matches
what `reconcile()` calls, against real PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from crumblr.application.broker_state import BrokerStateObservation
from crumblr.application.reconciliation import ExpectedState, reconcile
from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import ReconciliationStatus
from crumblr.persistence.broker_state import BrokerStateStore
from tests.conftest import make_broker_account_snapshot, make_broker_position_snapshot

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)

GUARD = AccountGuardConfig.model_validate(
    {
        "expected_server": "DemoBroker-Demo",
        "expected_login": None,
        "require_demo_account": True,
        "expected_currency": "EUR",
        "expected_leverage": 30,
    }
)


class TestReconcileAgainstTheRealStore:
    def test_a_flat_account_persisted_via_the_real_store_matches(self, engine: Engine) -> None:
        store = BrokerStateStore(engine)
        account = make_broker_account_snapshot(observed_at_utc=NOW)
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        result = reconcile(store, ExpectedState.flat(GUARD), now=NOW)

        assert result.status is ReconciliationStatus.MATCHED

    def test_an_unexpected_position_persisted_via_the_real_store_mismatches(
        self, engine: Engine
    ) -> None:
        store = BrokerStateStore(engine)
        account = make_broker_account_snapshot(observed_at_utc=NOW)
        position = make_broker_position_snapshot(snapshot_id=account.snapshot_id, ticket=42)
        store.record(
            BrokerStateObservation(account=account, positions=(position,), pending_orders=())
        )

        result = reconcile(store, ExpectedState.flat(GUARD), now=NOW)

        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("unexpected open position" in reason for reason in result.reasons)

    def test_no_snapshot_at_all_is_unknown(self, engine: Engine) -> None:
        store = BrokerStateStore(engine)

        result = reconcile(store, ExpectedState.flat(GUARD), now=NOW)

        assert result.status is ReconciliationStatus.UNKNOWN
