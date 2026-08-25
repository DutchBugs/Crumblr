"""Read-only reconciliation (review 1.15 §14, review 1.16 §7-8): the

fail-closed rule table checked directly, against an in-memory fake
`BrokerStateSource` — no PostgreSQL needed, the same reasoning `MarketDataSink`
and `BrokerStateSink` fakes use elsewhere.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

from crumblr.application.reconciliation import ExpectedState, reconcile
from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import ReconciliationStatus, SnapshotCompleteness
from crumblr.domain.models import (
    BrokerAccountSnapshot,
    BrokerPendingOrderSnapshot,
    BrokerPositionSnapshot,
)
from tests.conftest import (
    FIXED_NOW,
    make_broker_account_snapshot,
    make_broker_pending_order_snapshot,
    make_broker_position_snapshot,
)

# Matches `make_broker_account_snapshot`'s own defaults (`tests/conftest.py`)
# so `expectation()` and a plain `make_broker_account_snapshot()` agree
# without every test having to override `server`/`currency`/`leverage`.
GUARD = AccountGuardConfig.model_validate(
    {
        "expected_server": "DemoBroker-Demo",
        "expected_login": None,
        "require_demo_account": True,
        "expected_currency": "EUR",
        "expected_leverage": 30,
    }
)


class FakeBrokerStateSource:
    def __init__(
        self,
        account: BrokerAccountSnapshot | None,
        positions: tuple[BrokerPositionSnapshot, ...] = (),
        pending_orders: tuple[BrokerPendingOrderSnapshot, ...] = (),
    ) -> None:
        self._account = account
        self._positions = positions
        self._pending_orders = pending_orders

    def latest_account_snapshot(self) -> BrokerAccountSnapshot | None:
        return self._account

    def positions_for(self, snapshot_id: UUID) -> tuple[BrokerPositionSnapshot, ...]:
        return self._positions

    def pending_orders_for(self, snapshot_id: UUID) -> tuple[BrokerPendingOrderSnapshot, ...]:
        return self._pending_orders


def expectation(**overrides: object) -> ExpectedState:
    return replace(ExpectedState.flat(GUARD), **overrides)  # type: ignore[arg-type]


class TestUnknownWhenTheObservedSideCannotBeTrusted:
    """Review 1.16 §7's fail-closed rules: missing/stale/incomplete -> UNKNOWN."""

    def test_no_snapshot_ever_captured(self) -> None:
        result = reconcile(FakeBrokerStateSource(account=None), expectation(), now=FIXED_NOW)
        assert result.status is ReconciliationStatus.UNKNOWN
        assert "ever been captured" in result.reasons[0]
        assert result.snapshot_id is None

    def test_a_stale_snapshot(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile(
            FakeBrokerStateSource(account=account),
            expectation(),
            now=FIXED_NOW + timedelta(minutes=10),
            max_snapshot_age=timedelta(minutes=5),
        )
        assert result.status is ReconciliationStatus.UNKNOWN
        assert "old" in result.reasons[0]

    def test_a_failed_position_set(self) -> None:
        account = make_broker_account_snapshot(
            observed_at_utc=FIXED_NOW, position_set_state=SnapshotCompleteness.FAILED
        )
        result = reconcile(FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW)
        assert result.status is ReconciliationStatus.UNKNOWN
        assert "position set" in result.reasons[0]

    def test_an_unknown_pending_order_set(self) -> None:
        account = make_broker_account_snapshot(
            observed_at_utc=FIXED_NOW, pending_order_set_state=SnapshotCompleteness.UNKNOWN
        )
        result = reconcile(FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW)
        assert result.status is ReconciliationStatus.UNKNOWN
        assert "pending-order set" in result.reasons[0]


class TestMatchedOnAFlatCorrectAccount:
    def test_a_flat_complete_account_matches(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile(FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW)
        assert result.status is ReconciliationStatus.MATCHED
        assert result.reasons == ()
        assert result.snapshot_id == account.snapshot_id

    def test_matching_positions_and_orders_still_match(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        position = make_broker_position_snapshot(snapshot_id=account.snapshot_id, ticket=111)
        order = make_broker_pending_order_snapshot(snapshot_id=account.snapshot_id, order_id=222)
        result = reconcile(
            FakeBrokerStateSource(account=account, positions=(position,), pending_orders=(order,)),
            expectation(
                expected_position_tickets=frozenset({111}),
                expected_pending_order_ids=frozenset({222}),
            ),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MATCHED


class TestMismatched:
    """Review 1.16 §7's fail-closed rules: real disagreement -> MISMATCHED."""

    def test_wrong_server(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW, server="OtherBroker-Demo")
        result = reconcile(FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW)
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("server" in reason for reason in result.reasons)

    def test_wrong_currency(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW, currency="USD")
        result = reconcile(FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW)
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("currency" in reason for reason in result.reasons)

    def test_wrong_leverage(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW, leverage=100)
        result = reconcile(FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW)
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("leverage" in reason for reason in result.reasons)

    def test_an_unexpected_position(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        position = make_broker_position_snapshot(snapshot_id=account.snapshot_id, ticket=999)
        result = reconcile(
            FakeBrokerStateSource(account=account, positions=(position,)),
            expectation(),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("unexpected open position" in reason for reason in result.reasons)

    def test_a_missing_expected_position(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile(
            FakeBrokerStateSource(account=account),
            expectation(expected_position_tickets=frozenset({555})),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("missing" in reason for reason in result.reasons)

    def test_an_unexpected_pending_order(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        order = make_broker_pending_order_snapshot(snapshot_id=account.snapshot_id, order_id=777)
        result = reconcile(
            FakeBrokerStateSource(account=account, pending_orders=(order,)),
            expectation(),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("unexpected pending order" in reason for reason in result.reasons)

    def test_a_missing_expected_pending_order(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile(
            FakeBrokerStateSource(account=account),
            expectation(expected_pending_order_ids=frozenset({888})),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED

    def test_an_unexpected_symbol_on_an_otherwise_expected_position(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        position = make_broker_position_snapshot(
            snapshot_id=account.snapshot_id, ticket=111, canonical_symbol="GBP/USD"
        )
        result = reconcile(
            FakeBrokerStateSource(account=account, positions=(position,)),
            expectation(expected_position_tickets=frozenset({111})),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("symbol" in reason for reason in result.reasons)


class TestAccountIdentity:
    def test_expected_account_ref_none_when_login_not_configured(self) -> None:
        assert expectation().expected_account_ref is None

    def test_expected_account_ref_is_derived_when_login_is_configured(self) -> None:
        guard = GUARD.model_copy(update={"expected_login": 5_000_123})
        exp = ExpectedState.flat(guard)
        assert exp.expected_account_ref is not None

    def test_a_mismatched_account_ref_is_caught(self) -> None:
        guard = GUARD.model_copy(update={"expected_login": 5_000_123})
        exp = ExpectedState.flat(guard)
        account = make_broker_account_snapshot(
            observed_at_utc=FIXED_NOW, account_ref="wrongwrongwrong0"
        )
        result = reconcile(FakeBrokerStateSource(account=account), exp, now=FIXED_NOW)
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("account identity" in reason for reason in result.reasons)

    def test_a_matching_account_ref_does_not_by_itself_cause_a_mismatch(self) -> None:
        guard = GUARD.model_copy(update={"expected_login": 5_000_123})
        exp = ExpectedState.flat(guard)
        account = make_broker_account_snapshot(
            observed_at_utc=FIXED_NOW, account_ref=exp.expected_account_ref
        )
        result = reconcile(FakeBrokerStateSource(account=account), exp, now=FIXED_NOW)
        assert result.status is ReconciliationStatus.MATCHED


class TestResultPayload:
    def test_to_payload_is_json_safe(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile(FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW)
        payload = result.to_payload()
        assert payload["status"] == "MATCHED"
        assert payload["snapshot_id"] == str(account.snapshot_id)
        assert payload["reasons"] == []
