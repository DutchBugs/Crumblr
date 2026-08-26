"""`reconcile()` against the real `BrokerStateStore` (review 1.15 §14, 1.16

§7-8) — the fail-closed rule matrix itself is unit-tested against an
in-memory fake (`tests/unit/test_reconciliation.py`); this file proves the
`BrokerStateSource`/`InstrumentSpecSource` Protocols the real stores
implement actually match what `reconcile()` calls, against real PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import Engine

from crumblr.application.broker_state import BrokerStateObservation
from crumblr.application.reconciliation import ExpectedState, reconcile
from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import ReconciliationStatus
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from tests.conftest import (
    make_broker_account_snapshot,
    make_broker_position_snapshot,
    make_instrument_spec,
)

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

PINNED_SPEC = make_instrument_spec()


class TestReconcileAgainstTheRealStore:
    def test_a_flat_account_persisted_via_the_real_store_matches(self, engine: Engine) -> None:
        store = BrokerStateStore(engine)
        specs = InstrumentSpecStore(engine)
        specs.record(PINNED_SPEC)
        account = make_broker_account_snapshot(observed_at_utc=NOW)
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        expectation = ExpectedState.flat(GUARD, expected_spec_version=PINNED_SPEC.spec_version)
        result = reconcile(store, expectation, instrument_specs=specs, now=NOW)

        assert result.status is ReconciliationStatus.MATCHED

    def test_an_unexpected_position_persisted_via_the_real_store_mismatches(
        self, engine: Engine
    ) -> None:
        store = BrokerStateStore(engine)
        specs = InstrumentSpecStore(engine)
        specs.record(PINNED_SPEC)
        account = make_broker_account_snapshot(observed_at_utc=NOW)
        position = make_broker_position_snapshot(snapshot_id=account.snapshot_id, ticket=42)
        store.record(
            BrokerStateObservation(account=account, positions=(position,), pending_orders=())
        )

        expectation = ExpectedState.flat(GUARD, expected_spec_version=PINNED_SPEC.spec_version)
        result = reconcile(store, expectation, instrument_specs=specs, now=NOW)

        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("unexpected open position" in reason for reason in result.reasons)

    def test_no_snapshot_at_all_is_unknown(self, engine: Engine) -> None:
        store = BrokerStateStore(engine)
        specs = InstrumentSpecStore(engine)

        expectation = ExpectedState.flat(GUARD, expected_spec_version=PINNED_SPEC.spec_version)
        result = reconcile(store, expectation, instrument_specs=specs, now=NOW)

        assert result.status is ReconciliationStatus.UNKNOWN

    def test_no_pinned_baseline_is_unknown_even_with_a_matching_flat_account(
        self, engine: Engine
    ) -> None:
        """Review 1.19 §4 (F-055): no config-approved baseline means

        `UNKNOWN`, never `MATCHED` — regardless of what the database
        happens to hold, including a perfectly ordinary observation.
        """
        store = BrokerStateStore(engine)
        specs = InstrumentSpecStore(engine)
        specs.record(PINNED_SPEC)
        account = make_broker_account_snapshot(observed_at_utc=NOW)
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        expectation = ExpectedState.flat(GUARD)  # expected_spec_version left unset
        result = reconcile(store, expectation, instrument_specs=specs, now=NOW)

        assert result.status is ReconciliationStatus.UNKNOWN
        assert any("no approved instrument-spec baseline" in reason for reason in result.reasons)

    def test_no_instrument_spec_ever_persisted_is_unknown_even_with_a_flat_account(
        self, engine: Engine
    ) -> None:
        store = BrokerStateStore(engine)
        specs = InstrumentSpecStore(engine)
        account = make_broker_account_snapshot(observed_at_utc=NOW)
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        expectation = ExpectedState.flat(GUARD, expected_spec_version=PINNED_SPEC.spec_version)
        result = reconcile(store, expectation, instrument_specs=specs, now=NOW)

        assert result.status is ReconciliationStatus.UNKNOWN
        assert any("no instrument spec" in reason for reason in result.reasons)

    def test_a_real_persisted_spec_change_from_the_pinned_baseline_mismatches(
        self, engine: Engine
    ) -> None:
        store = BrokerStateStore(engine)
        specs = InstrumentSpecStore(engine)
        specs.record(make_instrument_spec(volume_step=Decimal("0.10"), captured_at_utc=NOW))
        account = make_broker_account_snapshot(observed_at_utc=NOW)
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        # Pinned to a spec that is NOT the one now observed — exactly the
        # broker-side drift reconciliation exists to catch, and exactly the
        # comparison F-055 requires (against the pin, not "the first row").
        expectation = ExpectedState.flat(GUARD, expected_spec_version=PINNED_SPEC.spec_version)
        result = reconcile(store, expectation, instrument_specs=specs, now=NOW)

        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("does not match the approved baseline" in reason for reason in result.reasons)

    def test_a_database_reset_does_not_silently_repin_the_baseline(self, engine: Engine) -> None:
        """F-055's motivating scenario, proven against a real store: even

        though this is the *only* row `instrument_specs` has ever held (as
        a reset/fresh database would produce), it does not become the
        accepted baseline just by being first and only — the pin comes
        from config, and config says nothing was ever approved here.
        """
        store = BrokerStateStore(engine)
        specs = InstrumentSpecStore(engine)
        specs.record(make_instrument_spec(digits=2, captured_at_utc=NOW - timedelta(minutes=1)))
        account = make_broker_account_snapshot(observed_at_utc=NOW)
        store.record(BrokerStateObservation(account=account, positions=(), pending_orders=()))

        expectation = ExpectedState.flat(GUARD)  # no pin configured
        result = reconcile(store, expectation, instrument_specs=specs, now=NOW)

        assert result.status is ReconciliationStatus.UNKNOWN
