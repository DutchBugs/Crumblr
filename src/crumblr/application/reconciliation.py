"""Read-only reconciliation: broker truth (F-047) vs. platform expectation.

Review 1.15 §14, review 1.16 §7-8:

    Expected Platform State
             |
        Reconciliation
             |
    Observed Broker Snapshot

Before an execution path exists, "expected" is trivially flat: build.md's
M1/M2 scope never creates a position or a pending order, so the only correct
expectation today is zero of each — see `ExpectedState.flat()`. Once
execution exists, expected state must come from the platform's own durable
order/position history, never from the latest MT5 snapshot itself — that
would compare MT5 to MT5 and detect nothing (review 1.16 §8).

Fail-closed, not merely fail-cautious: `ReconciliationStatus.UNKNOWN` is the
result whenever the observed side cannot be trusted — missing, stale, or an
incomplete position/pending-order collection — never `MATCHED` by default.
Review F-002's original rule ("absence of evidence is not evidence of
safety") and review 1.16 §3's explicit "`UNKNOWN` must never be upgraded
into `MATCHED`" both apply directly here.

**Scope, deliberately.** This reads only PostgreSQL through a
`BrokerStateSource` — never MT5 directly, and never the live gateway.
`LiveReader`/`capture_broker_state` are what talk to the terminal; this
module's only job is to say whether what they last recorded still agrees
with what the platform expects. That is exactly the shape build.md's
five-stage pipeline gives this stage: "Agent proposes. Risk engine
constrains. Supervisor vetoes. Execution service executes. Reconciliation
verifies."

**The instrument spec has an expected value too, and it must be pinned,
not inferred (review 1.19 §4, F-055).** An earlier version of this module
compared the latest observed `InstrumentSpec` against the *first* one ever
durably recorded — which detects drift after that first row, but never
establishes that the first row itself was the approved contract. A fresh
or reset database could observe an already-wrong spec and call it its own
baseline, and reconciliation would report `MATCHED` for comparing the
broker to itself. `ExpectedState.expected_spec_version` is instead read
from `config.MarketConfig.expected_spec_version` — `None` until a human
explicitly approves a real observation (normally during F-051), at which
point it becomes a git-reviewable config value like any other pinned
expectation in this module (`expected_server`, `expected_currency`, …).
No pin yet means `UNKNOWN`, never `MATCHED`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol
from uuid import UUID

from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import ReconciliationStatus, SnapshotCompleteness
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import (
    BrokerAccountSnapshot,
    BrokerPendingOrderSnapshot,
    BrokerPositionSnapshot,
    InstrumentSpec,
)
from crumblr.domain.timeutils import UtcDatetime

DEFAULT_MAX_SNAPSHOT_AGE = timedelta(minutes=5)
"""How old the latest broker-state snapshot may be before reconciliation

refuses to trust it (-> UNKNOWN). Deliberately independent of
`LiveReader.broker_state_interval` (default 60s): this is the safety
ceiling reconciliation enforces, not the capture cadence LiveReader happens
to run at — several missed capture cycles in a row should read as
"unknown", not merely as "a bit behind"."""


@dataclass(frozen=True)
class ExpectedState:
    """What the platform currently expects to be true at the broker."""

    expected_server: str
    expected_currency: str | None
    expected_leverage: int | None
    canonical_symbol: str
    expected_account_ref: str | None = None
    expected_position_tickets: frozenset[int] = frozenset()
    expected_pending_order_ids: frozenset[int] = frozenset()
    expected_spec_version: str | None = None
    """The pinned, approved `InstrumentSpec.spec_version` (review 1.19 §4,

    F-055). `None` means no human has approved a baseline yet — reconciled
    as `UNKNOWN` for the instrument-spec dimension, never inferred from
    whichever observation happened to be recorded first. See
    `config.MarketConfig.expected_spec_version` for how this is set."""

    @classmethod
    def flat(
        cls,
        guard: AccountGuardConfig,
        *,
        canonical_symbol: str = "EUR/USD",
        expected_spec_version: str | None = None,
    ) -> ExpectedState:
        """The only correct expectation before an execution path exists

        (review 1.16 §8): no open positions, no pending orders. Once
        `order_send` exists, build the expectation from the platform's own
        durable order/position history instead — never from the latest MT5
        snapshot, which is the observed side, not the expected one.
        """
        expected_account_ref = (
            fingerprint({"login": guard.expected_login, "server": guard.expected_server})[:16]
            if guard.expected_login is not None
            else None
        )
        return cls(
            expected_server=guard.expected_server,
            expected_currency=guard.expected_currency,
            expected_leverage=guard.expected_leverage,
            canonical_symbol=canonical_symbol,
            expected_account_ref=expected_account_ref,
            expected_spec_version=expected_spec_version,
        )


@dataclass(frozen=True)
class ReconciliationResult:
    """One reconciliation run's verdict, with the reasons that produced it —

    never just the verdict alone, so a `MISMATCHED`/`UNKNOWN` is actionable
    rather than a bare status a human has to re-derive by hand.
    """

    status: ReconciliationStatus
    reasons: tuple[str, ...]
    checked_at_utc: UtcDatetime
    snapshot_id: UUID | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reasons": list(self.reasons),
            "checked_at_utc": self.checked_at_utc.isoformat(),
            "snapshot_id": str(self.snapshot_id) if self.snapshot_id is not None else None,
        }


class BrokerStateSource(Protocol):
    """The slice of `persistence.broker_state.BrokerStateStore` this reads —

    a narrow Protocol, the same testability reasoning as `MarketDataSink`.
    """

    def latest_account_snapshot(self) -> BrokerAccountSnapshot | None: ...
    def positions_for(self, snapshot_id: UUID) -> tuple[BrokerPositionSnapshot, ...]: ...
    def pending_orders_for(self, snapshot_id: UUID) -> tuple[BrokerPendingOrderSnapshot, ...]: ...


class InstrumentSpecSource(Protocol):
    """The slice of `persistence.instrument_specs.InstrumentSpecStore` this reads.

    Only `latest`: review 1.19 §4 (F-055) replaced the original design —
    comparing against `earliest()`, the first spec ever durably observed —
    with comparing against an explicitly pinned `expected_spec_version`
    (`ExpectedState.expected_spec_version`). `earliest()` remains on
    `InstrumentSpecStore` itself as a discovery/diagnostic tool, just no
    longer part of what reconciliation reads.
    """

    def latest(self, *, canonical_symbol: str) -> InstrumentSpec | None: ...


def _unknown(reason: str, *, now: UtcDatetime, snapshot_id: UUID | None) -> ReconciliationResult:
    return ReconciliationResult(
        status=ReconciliationStatus.UNKNOWN,
        reasons=(reason,),
        checked_at_utc=now,
        snapshot_id=snapshot_id,
    )


def reconcile(
    source: BrokerStateSource,
    expectation: ExpectedState,
    *,
    instrument_specs: InstrumentSpecSource,
    now: UtcDatetime,
    max_snapshot_age: timedelta = DEFAULT_MAX_SNAPSHOT_AGE,
) -> ReconciliationResult:
    """Compare the latest durable broker-state observation against `expectation`,

    including the semantic instrument specification (review 1.17 §7, F-053):
    a broker-side change to the contract (digits, volume steps, stops/freeze
    level, trade mode, filling capability) is exactly the kind of fact this
    stage exists to catch, and `instrument_specs` has had a durable producer
    since F-048 — there is no remaining reason to reconcile only account and
    position identity. Missing/unreadable is `UNKNOWN`, not `MATCHED` by
    default, the same rule every other check in this function follows.

    The instrument-spec comparison is against `expectation.expected_spec_version`
    — an explicitly pinned, human-approved baseline — never against whichever
    spec happened to be recorded first (review 1.19 §4, F-055). No pin yet
    means `UNKNOWN`, not "trust the first observation".
    """
    snapshot = source.latest_account_snapshot()
    if snapshot is None:
        return _unknown(
            "no broker-state snapshot has ever been captured", now=now, snapshot_id=None
        )

    age = now - snapshot.observed_at_utc
    if age > max_snapshot_age:
        return _unknown(
            f"latest snapshot is {age} old, older than the {max_snapshot_age} "
            "reconciliation trusts",
            now=now,
            snapshot_id=snapshot.snapshot_id,
        )
    if snapshot.position_set_state is not SnapshotCompleteness.COMPLETE:
        return _unknown(
            f"position set is {snapshot.position_set_state.value}, not COMPLETE",
            now=now,
            snapshot_id=snapshot.snapshot_id,
        )
    if snapshot.pending_order_set_state is not SnapshotCompleteness.COMPLETE:
        return _unknown(
            f"pending-order set is {snapshot.pending_order_set_state.value}, not COMPLETE",
            now=now,
            snapshot_id=snapshot.snapshot_id,
        )

    if expectation.expected_spec_version is None:
        return _unknown(
            f"no approved instrument-spec baseline has been pinned for "
            f"{expectation.canonical_symbol!r} — see config.MarketConfig.expected_spec_version",
            now=now,
            snapshot_id=snapshot.snapshot_id,
        )
    current_spec = instrument_specs.latest(canonical_symbol=expectation.canonical_symbol)
    if current_spec is None:
        return _unknown(
            f"no instrument spec has ever been observed for {expectation.canonical_symbol!r}",
            now=now,
            snapshot_id=snapshot.snapshot_id,
        )

    mismatches = _account_mismatches(snapshot, expectation)
    mismatches += _position_mismatches(source.positions_for(snapshot.snapshot_id), expectation)
    mismatches += _pending_order_mismatches(
        source.pending_orders_for(snapshot.snapshot_id), expectation
    )
    mismatches += _instrument_spec_mismatches(expectation.expected_spec_version, current_spec)

    if mismatches:
        return ReconciliationResult(
            status=ReconciliationStatus.MISMATCHED,
            reasons=tuple(mismatches),
            checked_at_utc=now,
            snapshot_id=snapshot.snapshot_id,
        )
    return ReconciliationResult(
        status=ReconciliationStatus.MATCHED,
        reasons=(),
        checked_at_utc=now,
        snapshot_id=snapshot.snapshot_id,
    )


def _account_mismatches(snapshot: BrokerAccountSnapshot, expectation: ExpectedState) -> list[str]:
    mismatches: list[str] = []
    if snapshot.server != expectation.expected_server:
        mismatches.append(f"server {snapshot.server!r} != expected {expectation.expected_server!r}")
    if expectation.expected_account_ref is not None and (
        snapshot.account_ref != expectation.expected_account_ref
    ):
        mismatches.append("account identity does not match the expected account")
    if (
        expectation.expected_currency is not None
        and snapshot.currency != expectation.expected_currency
    ):
        mismatches.append(
            f"currency {snapshot.currency!r} != expected {expectation.expected_currency!r}"
        )
    if (
        expectation.expected_leverage is not None
        and snapshot.leverage != expectation.expected_leverage
    ):
        mismatches.append(
            f"leverage {snapshot.leverage} != expected {expectation.expected_leverage}"
        )
    return mismatches


def _position_mismatches(
    positions: tuple[BrokerPositionSnapshot, ...], expectation: ExpectedState
) -> list[str]:
    mismatches: list[str] = []
    observed_tickets = frozenset(position.ticket for position in positions)
    for ticket in sorted(observed_tickets - expectation.expected_position_tickets):
        mismatches.append(f"unexpected open position, ticket={ticket}")
    for ticket in sorted(expectation.expected_position_tickets - observed_tickets):
        mismatches.append(f"expected open position missing, ticket={ticket}")
    for position in positions:
        if position.canonical_symbol != expectation.canonical_symbol:
            mismatches.append(
                f"unexpected symbol on position ticket={position.ticket}: "
                f"{position.canonical_symbol!r} != expected {expectation.canonical_symbol!r}"
            )
    return mismatches


def _instrument_spec_mismatches(expected_spec_version: str, current: InstrumentSpec) -> list[str]:
    """Compare the pinned baseline against the current observation by

    semantic identity, never field by field. `InstrumentSpec.spec_version`
    already excludes `captured_at_utc` and `tick_value` (review F-039) — the
    latter drifts live with the account/quote cross-currency rate, not with
    broker policy, so hashing it would manufacture a false mismatch on
    every reconnect. Reusing that same hash here means this check cannot
    regress independently of the one F-039 already fixed.
    """
    if expected_spec_version == current.spec_version:
        return []
    return [
        "instrument spec does not match the approved baseline: "
        f"expected spec_version={expected_spec_version[:12]}... "
        f"!= current spec_version={current.spec_version[:12]}... "
        f"(broker_symbol {current.broker_symbol!r}, observed "
        f"{current.captured_at_utc.isoformat()})"
    ]


def _pending_order_mismatches(
    orders: tuple[BrokerPendingOrderSnapshot, ...], expectation: ExpectedState
) -> list[str]:
    mismatches: list[str] = []
    observed_order_ids = frozenset(order.order_id for order in orders)
    for order_id in sorted(observed_order_ids - expectation.expected_pending_order_ids):
        mismatches.append(f"unexpected pending order, order_id={order_id}")
    for order_id in sorted(expectation.expected_pending_order_ids - observed_order_ids):
        mismatches.append(f"expected pending order missing, order_id={order_id}")
    for order in orders:
        if order.canonical_symbol != expectation.canonical_symbol:
            mismatches.append(
                f"unexpected symbol on pending order order_id={order.order_id}: "
                f"{order.canonical_symbol!r} != expected {expectation.canonical_symbol!r}"
            )
    return mismatches
