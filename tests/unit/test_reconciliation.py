"""Read-only reconciliation (review 1.15 §14, review 1.16 §7-8): the

fail-closed rule table checked directly, against an in-memory fake
`BrokerStateSource` — no PostgreSQL needed, the same reasoning `MarketDataSink`
and `BrokerStateSink` fakes use elsewhere.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from crumblr.application.reconciliation import (
    DEFAULT_MAX_SNAPSHOT_AGE,
    ExpectedState,
    ReconciliationResult,
    reconcile,
)
from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import ReconciliationStatus, SnapshotCompleteness
from crumblr.domain.models import (
    BrokerAccountSnapshot,
    BrokerPendingOrderSnapshot,
    BrokerPositionSnapshot,
    InstrumentSpec,
)
from crumblr.domain.timeutils import UtcDatetime
from tests.conftest import (
    FIXED_NOW,
    make_broker_account_snapshot,
    make_broker_pending_order_snapshot,
    make_broker_position_snapshot,
    make_instrument_spec,
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


# The default fixture every test implicitly reconciles against unless it
# overrides `specs=`: one observed spec, matching `expectation()`'s own
# default pinned `expected_spec_version` below — the same "matches unless
# told otherwise" shape `FakeBrokerStateSource` already has for
# account/position/pending-order state.
DEFAULT_SPEC = make_instrument_spec()


class FakeInstrumentSpecSource:
    def __init__(self, *, latest: InstrumentSpec | None = DEFAULT_SPEC) -> None:
        self._latest = latest

    def latest(self, *, canonical_symbol: str) -> InstrumentSpec | None:
        return self._latest


def expectation(**overrides: object) -> ExpectedState:
    fields: dict[str, object] = {"expected_spec_version": DEFAULT_SPEC.spec_version}
    fields.update(overrides)
    return replace(ExpectedState.flat(GUARD), **fields)  # type: ignore[arg-type]


def reconcile_with(
    source: FakeBrokerStateSource,
    exp: ExpectedState | None = None,
    *,
    specs: FakeInstrumentSpecSource | None = None,
    now: UtcDatetime,
    max_snapshot_age: timedelta = DEFAULT_MAX_SNAPSHOT_AGE,
) -> ReconciliationResult:
    """`reconcile()` with the default matching instrument-spec fixture wired

    in, so tests about account/position/pending-order behaviour do not each
    have to know about F-053's instrument-spec check to stay green.
    """
    return reconcile(
        source,
        exp if exp is not None else expectation(),
        instrument_specs=specs if specs is not None else FakeInstrumentSpecSource(),
        now=now,
        max_snapshot_age=max_snapshot_age,
    )


class TestUnknownWhenTheObservedSideCannotBeTrusted:
    """Review 1.16 §7's fail-closed rules: missing/stale/incomplete -> UNKNOWN."""

    def test_no_snapshot_ever_captured(self) -> None:
        result = reconcile_with(FakeBrokerStateSource(account=None), expectation(), now=FIXED_NOW)
        assert result.status is ReconciliationStatus.UNKNOWN
        assert "ever been captured" in result.reasons[0]
        assert result.snapshot_id is None

    def test_a_stale_snapshot(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
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
        result = reconcile_with(
            FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW
        )
        assert result.status is ReconciliationStatus.UNKNOWN
        assert "position set" in result.reasons[0]

    def test_an_unknown_pending_order_set(self) -> None:
        account = make_broker_account_snapshot(
            observed_at_utc=FIXED_NOW, pending_order_set_state=SnapshotCompleteness.UNKNOWN
        )
        result = reconcile_with(
            FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW
        )
        assert result.status is ReconciliationStatus.UNKNOWN
        assert "pending-order set" in result.reasons[0]


class TestMatchedOnAFlatCorrectAccount:
    def test_a_flat_complete_account_matches(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
            FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW
        )
        assert result.status is ReconciliationStatus.MATCHED
        assert result.reasons == ()
        assert result.snapshot_id == account.snapshot_id

    def test_matching_positions_and_orders_still_match(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        position = make_broker_position_snapshot(snapshot_id=account.snapshot_id, ticket=111)
        order = make_broker_pending_order_snapshot(snapshot_id=account.snapshot_id, order_id=222)
        result = reconcile_with(
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
        result = reconcile_with(
            FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("server" in reason for reason in result.reasons)

    def test_wrong_currency(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW, currency="USD")
        result = reconcile_with(
            FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("currency" in reason for reason in result.reasons)

    def test_wrong_leverage(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW, leverage=100)
        result = reconcile_with(
            FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("leverage" in reason for reason in result.reasons)

    def test_an_unexpected_position(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        position = make_broker_position_snapshot(snapshot_id=account.snapshot_id, ticket=999)
        result = reconcile_with(
            FakeBrokerStateSource(account=account, positions=(position,)),
            expectation(),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("unexpected open position" in reason for reason in result.reasons)

    def test_a_missing_expected_position(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
            FakeBrokerStateSource(account=account),
            expectation(expected_position_tickets=frozenset({555})),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("missing" in reason for reason in result.reasons)

    def test_an_unexpected_pending_order(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        order = make_broker_pending_order_snapshot(snapshot_id=account.snapshot_id, order_id=777)
        result = reconcile_with(
            FakeBrokerStateSource(account=account, pending_orders=(order,)),
            expectation(),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("unexpected pending order" in reason for reason in result.reasons)

    def test_a_missing_expected_pending_order(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
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
        result = reconcile_with(
            FakeBrokerStateSource(account=account, positions=(position,)),
            expectation(expected_position_tickets=frozenset({111})),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("symbol" in reason for reason in result.reasons)


class TestInstrumentSpecReconciliation:
    """Review 1.17 §7 / F-053: the semantic contract spec is part of what

    reconciliation checks now that `instrument_specs` has a durable producer
    (F-048) — not only account/position/pending-order identity. Review 1.19
    §4 (F-055) then required the *expected* side to be an explicitly pinned
    baseline, not whichever spec happened to be observed first.
    """

    def test_no_pinned_baseline_is_unknown_even_with_a_matching_observation(self) -> None:
        """F-055's exact concern: an unpinned baseline must never read as

        MATCHED just because a spec has been observed — that would be
        trust-on-first-use, not authority.
        """
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
            FakeBrokerStateSource(account=account),
            expectation(expected_spec_version=None),
            specs=FakeInstrumentSpecSource(latest=DEFAULT_SPEC),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.UNKNOWN
        assert any("no approved instrument-spec baseline" in reason for reason in result.reasons)

    def test_no_spec_ever_observed_is_unknown_not_matched(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
            FakeBrokerStateSource(account=account),
            expectation(),
            specs=FakeInstrumentSpecSource(latest=None),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.UNKNOWN
        assert any("no instrument spec" in reason for reason in result.reasons)

    def test_the_pinned_baseline_matches_the_current_observation(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        spec = make_instrument_spec()
        result = reconcile_with(
            FakeBrokerStateSource(account=account),
            expectation(expected_spec_version=spec.spec_version),
            specs=FakeInstrumentSpecSource(latest=spec),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MATCHED

    def test_a_material_spec_change_from_the_pinned_baseline_is_a_mismatch(self) -> None:
        pinned = make_instrument_spec()
        current = make_instrument_spec(volume_step=Decimal("0.10"))
        assert pinned.spec_version != current.spec_version
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
            FakeBrokerStateSource(account=account),
            expectation(expected_spec_version=pinned.spec_version),
            specs=FakeInstrumentSpecSource(latest=current),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("does not match the approved baseline" in reason for reason in result.reasons)

    def test_tick_value_drift_alone_does_not_cause_a_mismatch(self) -> None:
        """F-039 already excludes `tick_value` from `spec_version`'s hash

        because it drifts live with the account/quote cross-currency rate,
        not with broker policy — review 1.17 §7 explicitly repeats that
        instruction for this check. Reusing `spec_version` here means it is
        structurally impossible to regress independently of that fix.
        """
        pinned = make_instrument_spec()
        current = make_instrument_spec(tick_value=Decimal("1.13"))
        assert pinned.spec_version == current.spec_version
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
            FakeBrokerStateSource(account=account),
            expectation(expected_spec_version=pinned.spec_version),
            specs=FakeInstrumentSpecSource(latest=current),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MATCHED

    def test_a_spec_mismatch_combines_with_other_mismatches(self) -> None:
        pinned = make_instrument_spec()
        current = make_instrument_spec(digits=3)
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW, currency="USD")
        result = reconcile_with(
            FakeBrokerStateSource(account=account),
            expectation(expected_spec_version=pinned.spec_version),
            specs=FakeInstrumentSpecSource(latest=current),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("currency" in reason for reason in result.reasons)
        assert any("does not match the approved baseline" in reason for reason in result.reasons)

    def test_resetting_the_observation_database_does_not_silently_repin(self) -> None:
        """F-055's motivating scenario: a fresh/reset database observing a

        materially different spec must not have that new observation
        quietly become the accepted baseline just because reconciliation
        has nothing else to compare against — the pinned config value is
        the only authority, and it does not move on its own.
        """
        pinned = make_instrument_spec()
        first_observation_after_reset = make_instrument_spec(digits=2)
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
            FakeBrokerStateSource(account=account),
            expectation(expected_spec_version=pinned.spec_version),
            specs=FakeInstrumentSpecSource(latest=first_observation_after_reset),
            now=FIXED_NOW,
        )
        assert result.status is ReconciliationStatus.MISMATCHED


class TestAccountIdentity:
    def test_expected_account_ref_none_when_login_not_configured(self) -> None:
        assert expectation().expected_account_ref is None

    def test_expected_account_ref_is_derived_when_login_is_configured(self) -> None:
        guard = GUARD.model_copy(update={"expected_login": 5_000_123})
        exp = ExpectedState.flat(guard)
        assert exp.expected_account_ref is not None

    def test_a_mismatched_account_ref_is_caught(self) -> None:
        guard = GUARD.model_copy(update={"expected_login": 5_000_123})
        exp = ExpectedState.flat(guard, expected_spec_version=DEFAULT_SPEC.spec_version)
        account = make_broker_account_snapshot(
            observed_at_utc=FIXED_NOW, account_ref="wrongwrongwrong0"
        )
        result = reconcile_with(FakeBrokerStateSource(account=account), exp, now=FIXED_NOW)
        assert result.status is ReconciliationStatus.MISMATCHED
        assert any("account identity" in reason for reason in result.reasons)

    def test_a_matching_account_ref_does_not_by_itself_cause_a_mismatch(self) -> None:
        guard = GUARD.model_copy(update={"expected_login": 5_000_123})
        exp = ExpectedState.flat(guard, expected_spec_version=DEFAULT_SPEC.spec_version)
        account = make_broker_account_snapshot(
            observed_at_utc=FIXED_NOW, account_ref=exp.expected_account_ref
        )
        result = reconcile_with(FakeBrokerStateSource(account=account), exp, now=FIXED_NOW)
        assert result.status is ReconciliationStatus.MATCHED


class TestResultPayload:
    def test_to_payload_is_json_safe(self) -> None:
        account = make_broker_account_snapshot(observed_at_utc=FIXED_NOW)
        result = reconcile_with(
            FakeBrokerStateSource(account=account), expectation(), now=FIXED_NOW
        )
        payload = result.to_payload()
        assert payload["status"] == "MATCHED"
        assert payload["snapshot_id"] == str(account.snapshot_id)
        assert payload["reasons"] == []
