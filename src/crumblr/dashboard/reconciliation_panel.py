"""Read-only reconciliation status for the dashboard.

`application/reconciliation.py` has no durable "latest status" row — it is
always computed fresh from the latest broker-state snapshot. This wraps that
same real `reconcile()` function exactly the way `live_decision.py`/
`execution.py` already call it (an unpinned-flat expectation, real broker
state, real instrument specs), read-only, for display only.
"""

from __future__ import annotations

from dataclasses import dataclass

from crumblr.application.reconciliation import ExpectedState, reconcile
from crumblr.config import AccountGuardConfig
from crumblr.domain.timeutils import UtcDatetime
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore


@dataclass(frozen=True)
class ReconciliationPanelState:
    status: str
    reasons: tuple[str, ...]
    checked_at_utc: UtcDatetime


def build_reconciliation_panel(
    *,
    broker_state: BrokerStateStore,
    instrument_specs: InstrumentSpecStore,
    guard: AccountGuardConfig,
    canonical_symbol: str,
    expected_spec_version: str | None,
    now: UtcDatetime,
) -> ReconciliationPanelState:
    expectation = ExpectedState.flat(
        guard,
        canonical_symbol=canonical_symbol,
        expected_spec_version=expected_spec_version,
    )
    result = reconcile(
        broker_state,
        expectation,
        instrument_specs=instrument_specs,
        now=now,
    )
    return ReconciliationPanelState(
        status=result.status.value,
        reasons=result.reasons,
        checked_at_utc=result.checked_at_utc,
    )
