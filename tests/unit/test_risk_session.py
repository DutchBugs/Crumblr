"""A restart may not refill the risk budget (review 1.5 step 2; F-019).

The kill switch already survived a restart. The budget did not, and the two
failures are not the same size: a tripped halt is loud and durable, while a
half-spent daily-loss allowance quietly returning to full is invisible and
happens on the ordinary path.

    1.5% of a 2% allowance spent → crash → restart → 2% available again

Every test below is a statement about direction. Recovery may come back with
the same headroom or less. It may never come back with more, and where it
cannot tell what the headroom was, it halts.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from crumblr.domain.enums import ReasonCode
from crumblr.risk.session import (
    InMemoryRiskSessionStore,
    RiskSessionState,
    SessionRecord,
    recover_session,
    snapshot,
)

TODAY = date(2026, 8, 17)
TOMORROW = date(2026, 8, 18)
RECORDED_AT = datetime(2026, 8, 17, 15, 30, tzinfo=UTC)


def make_state(**overrides: Any) -> RiskSessionState:
    """A session that has spent 1.5% of its day: 10,000 down to 9,850."""
    fields: dict[str, Any] = {
        "trading_day": TODAY,
        "session_start_equity": Decimal("10000"),
        "current_equity": Decimal("9850"),
        "peak_equity": Decimal("10200"),
        "realized_pnl": Decimal("-150"),
        "max_drawdown_fraction": Decimal("0.0343"),
        "max_session_loss_fraction": Decimal("0.015"),
        "open_risk_fraction": Decimal("0"),
        "open_position_count": 0,
        "recorded_at_utc": RECORDED_AT,
    }
    fields.update(overrides)
    return RiskSessionState(**fields)


def recover(record: SessionRecord, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "live_equity": Decimal("9850"),
        "live_open_positions": 0,
        "market_day": TODAY,
    }
    fields.update(overrides)
    return recover_session(record, **fields)


class TestTheBudgetSurvives:
    """The finding itself."""

    def test_a_resumed_session_still_knows_what_it_has_spent(self) -> None:
        recovery = recover(SessionRecord(state=make_state()))

        assert recovery.resumed
        assert not recovery.must_halt
        # 10,000 → 9,850 is 1.5% of the session, and it stays 1.5% after the
        # restart. Before F-019 this read 0.
        assert recovery.ledger.session_loss_fraction == Decimal("0.015")

    def test_the_session_baseline_is_the_recorded_one_not_the_current_equity(self) -> None:
        """The bug in one assertion.

        Rebuilding the ledger from live equity would make the session start at
        9,850 — and 9,850 of 9,850 is a loss of nothing at all.
        """
        recovery = recover(SessionRecord(state=make_state()))

        assert recovery.ledger.session_start_equity == Decimal("10000")

    def test_the_high_water_mark_survives_so_drawdown_does_too(self) -> None:
        """Drawdown is measured from the peak, which is not a daily figure."""
        recovery = recover(SessionRecord(state=make_state()))

        assert recovery.ledger.peak_equity == Decimal("10200")
        assert recovery.ledger.drawdown_fraction > Decimal("0.034")

    def test_the_worst_values_seen_are_carried_not_recomputed(self) -> None:
        """A run that recovered before it restarted still records the trough."""
        recovery = recover(
            SessionRecord(state=make_state(max_drawdown_fraction=Decimal("0.08"))),
            live_equity=Decimal("10190"),
        )

        assert recovery.ledger.max_drawdown_fraction == Decimal("0.08")


class TestRecoveryOnlyEverTightens:
    """Where the record and the account disagree, the worse number wins."""

    def test_a_stale_record_does_not_hide_a_worse_position(self) -> None:
        """Equity fell further after the last snapshot was written."""
        recovery = recover(
            SessionRecord(state=make_state()),
            live_equity=Decimal("9700"),  # 3% down on the session, not 1.5%
        )

        assert recovery.ledger.session_loss_fraction == Decimal("0.03")
        assert recovery.ledger.max_session_loss_fraction >= Decimal("0.03")

    def test_a_recovered_position_never_has_more_headroom_than_the_record(self) -> None:
        for live_equity in ("10300", "10000", "9850", "9700", "9000"):
            recovery = recover(SessionRecord(state=make_state()), live_equity=Decimal(live_equity))
            assert recovery.ledger.max_session_loss_fraction >= Decimal("0.015"), live_equity
            assert recovery.ledger.max_drawdown_fraction >= Decimal("0.0343"), live_equity

    def test_a_new_trading_day_resets_the_daily_gate_but_not_the_drawdown(self) -> None:
        """The daily allowance is meant to renew. The drawdown limit is not."""
        recovery = recover(
            SessionRecord(state=make_state()),
            market_day=TOMORROW,
            live_equity=Decimal("9850"),
        )

        assert recovery.ledger.session_start_equity == Decimal("9850")
        assert recovery.ledger.session_loss_fraction == Decimal("0")
        assert recovery.ledger.peak_equity == Decimal("10200")
        assert recovery.ledger.max_drawdown_fraction >= Decimal("0.0343")


class TestWhatCannotBeEstablishedHalts:
    """`UNKNOWN → HALT → reconcile`, as review 1.5 §4 step 2 requires."""

    def test_an_unreadable_record_halts(self) -> None:
        recovery = recover(SessionRecord(unreadable="connection refused"))

        assert recovery.must_halt
        assert recovery.reason_codes == (ReasonCode.SAFETY_STATE_UNKNOWN,)
        assert "connection refused" in (recovery.detail or "")

    def test_an_unsupported_schema_version_halts(self) -> None:
        """A record written by another version may not mean what it says."""
        recovery = recover(SessionRecord(state=make_state(schema_version=99)))

        assert recovery.must_halt
        assert recovery.reason_codes == (ReasonCode.SAFETY_STATE_UNKNOWN,)

    def test_a_record_from_the_future_halts(self) -> None:
        """Either the clock or the record is wrong; neither can size a trade."""
        recovery = recover(SessionRecord(state=make_state(trading_day=TOMORROW)))

        assert recovery.must_halt
        assert recovery.reason_codes == (ReasonCode.SAFETY_STATE_UNKNOWN,)

    def test_a_position_the_account_does_not_have_halts(self) -> None:
        recovery = recover(
            SessionRecord(state=make_state(open_position_count=1)), live_open_positions=0
        )

        assert recovery.must_halt
        assert recovery.reason_codes == (ReasonCode.RECONCILIATION_MISMATCH,)

    def test_a_position_the_record_does_not_have_also_halts(self) -> None:
        """Both directions. An unexpected position is the worse of the two."""
        recovery = recover(
            SessionRecord(state=make_state(open_position_count=0)), live_open_positions=1
        )

        assert recovery.must_halt
        assert recovery.reason_codes == (ReasonCode.RECONCILIATION_MISMATCH,)

    def test_a_halted_recovery_still_produces_a_usable_ledger(self) -> None:
        """An operator clearing a halt needs to see where the account stands."""
        recovery = recover(SessionRecord(unreadable="disk error"))

        assert recovery.ledger.current_equity == Decimal("9850")


class TestAFirstStart:
    def test_no_record_at_all_starts_fresh(self) -> None:
        """The only permissive path, and it is conditioned on an empty store.

        Refusing here would make a system that has never run unstartable,
        which is a different failure rather than a safer one.
        """
        recovery = recover(SessionRecord())

        assert not recovery.must_halt
        assert not recovery.resumed
        assert recovery.ledger.session_start_equity == Decimal("9850")
        assert recovery.ledger.session_loss_fraction == Decimal("0")


class TestRoundTrip:
    def test_a_snapshot_of_a_resumed_ledger_recovers_to_the_same_place(self) -> None:
        """Restarting twice must not erode the record, the way it must not
        erode a halt (the property review 1.1 held F-003 open for)."""
        store = InMemoryRiskSessionStore(make_state())

        first = recover_session(
            store.load_latest(),
            live_equity=Decimal("9850"),
            live_open_positions=0,
            market_day=TODAY,
        )
        store.save(
            snapshot(
                first.ledger,
                trading_day=TODAY,
                realized_pnl=Decimal("-150"),
                open_risk_fraction=Decimal("0"),
                open_position_count=0,
                recorded_at_utc=RECORDED_AT,
            )
        )
        second = recover_session(
            store.load_latest(),
            live_equity=Decimal("9850"),
            live_open_positions=0,
            market_day=TODAY,
        )

        assert second.ledger.session_start_equity == first.ledger.session_start_equity
        assert second.ledger.session_loss_fraction == first.ledger.session_loss_fraction
        assert second.ledger.peak_equity == first.ledger.peak_equity

    @pytest.mark.parametrize("restarts", [1, 2, 5])
    def test_repeated_restarts_do_not_erode_the_budget(self, restarts: int) -> None:
        store = InMemoryRiskSessionStore(make_state())
        loss = Decimal("0.015")

        for _ in range(restarts):
            recovery = recover_session(
                store.load_latest(),
                live_equity=Decimal("9850"),
                live_open_positions=0,
                market_day=TODAY,
            )
            assert recovery.ledger.session_loss_fraction == loss
            store.save(
                snapshot(
                    recovery.ledger,
                    trading_day=TODAY,
                    realized_pnl=Decimal("-150"),
                    open_risk_fraction=Decimal("0"),
                    open_position_count=0,
                    recorded_at_utc=RECORDED_AT,
                )
            )
