"""Risk-session state that survives a restart (review 1.5 step 2; F-019).

The kill switch already survives a restart. The *budget* did not. A run that
had spent 1.5% of a 2% daily-loss allowance and then restarted came back with
the allowance untouched — the halt would still be in force if it had tripped,
but short of tripping, a crash was a way to buy more room to lose money.

    loss consumed → restart → loss forgotten → the gate is further away

That is a reset in the permissive direction, which is the one direction a
safety limit may never move on its own.

The rule this module implements is that **recovery may only ever be more
conservative than the record**. Every worst-case value is seeded with the
worse of what was recorded and what the account currently shows, so a stale or
partial record cannot buy back headroom. Anything that cannot be established
at all — an unreadable record, a record from the future, a position book that
disagrees with the broker — resolves to `UNKNOWN` and halts, exactly as the
safety state does.

Broker history is not part of reconstruction yet, because there is no broker.
Review 1.5 requires it once MT5 exists; the seam is `live_open_positions`
below, which today is answered by the simulated broker and at M5 by
reconciliation. See D-032.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import Connection

from crumblr.domain.enums import ReasonCode
from crumblr.domain.money import ZERO
from crumblr.domain.timeutils import UtcDatetime
from crumblr.observability.logging import get_logger
from crumblr.risk.kill_switch import EquityLedger

_log = get_logger("risk_session")

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RiskSessionState:
    """What the risk engine knew about its budget when this was written.

    Every field is something a restart must not silently improve on.
    """

    trading_day: date
    session_start_equity: Decimal
    current_equity: Decimal
    peak_equity: Decimal
    realized_pnl: Decimal
    max_drawdown_fraction: Decimal
    max_session_loss_fraction: Decimal
    open_risk_fraction: Decimal | None
    """Owner risk policy v1 (D1.4): `None` means the platform could not

    establish it (an open position with untrustworthy stop geometry) —
    never a function of `open_position_count` alone any more, and never
    silently treated as zero. Recovery does not read this field
    (`recover_session()` below), so its only role today is audit."""
    open_position_count: int
    recorded_at_utc: UtcDatetime
    schema_version: int = SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "trading_day": self.trading_day.isoformat(),
            "session_start_equity": str(self.session_start_equity),
            "current_equity": str(self.current_equity),
            "peak_equity": str(self.peak_equity),
            "realized_pnl": str(self.realized_pnl),
            "max_drawdown_fraction": str(self.max_drawdown_fraction),
            "max_session_loss_fraction": str(self.max_session_loss_fraction),
            "open_risk_fraction": (
                None if self.open_risk_fraction is None else str(self.open_risk_fraction)
            ),
            "open_position_count": self.open_position_count,
            "recorded_at_utc": self.recorded_at_utc.isoformat(),
        }


@dataclass(frozen=True)
class SessionRecord:
    """The outcome of asking the store what it remembers.

    Three answers, not two. "Nothing recorded" and "I could not read what was
    recorded" are different situations and must not collapse into one — the
    first is a first start, the second is a reason to halt.
    """

    state: RiskSessionState | None = None
    unreadable: str | None = None

    @property
    def is_known(self) -> bool:
        return self.unreadable is None


class RiskSessionStore(Protocol):
    """Where risk-session state is persisted between runs.

    `load_latest` must never raise. An implementation that cannot read its
    own record returns `SessionRecord(unreadable=...)`, so the caller cannot
    mistake a failed read for an absent one.

    `connection` (ADR-021, AG-012/Phase C): optional, so every existing
    caller that omits it keeps today's exact behaviour (its own
    transaction). A caller that *does* pass one — `LiveDecisionOrchestrator`
    inside `RiskLedgerLock.held()`'s block, below — makes this call
    participate in that caller's already-open transaction instead of
    opening a second one, so the read/write and the advisory lock share
    one atomic scope.
    """

    def load_latest(self, *, connection: Connection | None = None) -> SessionRecord: ...

    def save(self, state: RiskSessionState, *, connection: Connection | None = None) -> None: ...


class InMemoryRiskSessionStore:
    """For tests and for replays that should not outlive their process.

    `connection` is accepted (Protocol conformance, ADR-021) and always
    ignored — there is no real transaction for an in-memory store to
    participate in.
    """

    def __init__(self, initial: RiskSessionState | None = None) -> None:
        self._state = initial
        self.saves = 0

    def load_latest(self, *, connection: Connection | None = None) -> SessionRecord:
        return SessionRecord(state=self._state)

    def save(self, state: RiskSessionState, *, connection: Connection | None = None) -> None:
        self._state = state
        self.saves += 1


class RiskLedgerLock(Protocol):
    """Serializes the recover→evaluate(→persist) critical section around

    `risk_session_states` for one canonical symbol, across every process
    that reads or writes it (ADR-021, AG-012/Phase C) —
    `LiveDecisionOrchestrator` (intent-time Risk, internal strategies),
    `agent_gateway/decision_path.py::evaluate_agent_trade_intent`
    (intent-time Risk, external-agent proposals) and
    `ExecutionOrchestrator._process()` (FINAL Risk, already race-free on
    its own but included for completeness — see ADR-021 §5).

    Opens its own transaction and yields the connection out, rather than
    requiring one in: none of the three call sites above owns a raw
    `Engine` directly (ADR-021 §2.1's "note on the first draft" records
    why a required-`connection` shape was tried first and reverted).
    `RiskSessionStore.load_latest()`/`.save()` calls made with the
    yielded `connection` run inside the same lock/transaction scope.
    """

    def held(self, canonical_symbol: str) -> AbstractContextManager[Connection | None]: ...

    """`with lock.held(canonical_symbol) as connection: ...` — every real

    implementation applies `@contextmanager` to its own `held()`, which
    satisfies this signature (a generator decorated with `@contextmanager`
    returns an `AbstractContextManager` when called). Yields `Connection | None`,
    not a bare `Connection`: `InMemoryRiskLedgerLock` (below) genuinely
    has no real connection to yield, and `RiskSessionStore.load_latest()`/
    `.save()`'s own `connection: Connection | None = None` parameter
    already accepts either."""


class InMemoryRiskLedgerLock:
    """For tests and for replays that should not outlive their process —

    `InMemoryRiskSessionStore`'s own pairing. A no-op: a single in-process
    caller needs no real mutual exclusion, and `InMemoryRiskSessionStore`
    ignores `connection` regardless, so the `None` this yields is never
    dereferenced by anything that matters. Real cross-process
    serialization is `persistence/risk_session.py::PostgresRiskLedgerLock`
    (ADR-021).
    """

    @contextmanager
    def held(self, canonical_symbol: str) -> Iterator[Connection | None]:
        yield None


@dataclass(frozen=True)
class SessionRecovery:
    """The ledger to start from, and whether starting is allowed at all."""

    ledger: EquityLedger
    trading_day: date | None
    resumed: bool
    reason_codes: tuple[ReasonCode, ...] = ()
    detail: str | None = None

    @property
    def must_halt(self) -> bool:
        return bool(self.reason_codes)


def recover_session(
    record: SessionRecord,
    *,
    live_equity: Decimal,
    live_open_positions: int,
    market_day: date,
    max_daily_loss: Decimal,
    max_drawdown: Decimal,
) -> SessionRecovery:
    """Rebuild the risk session from what was recorded, erring downwards.

    `live_equity` and `live_open_positions` are what the account says right
    now. They are the second opinion: where the record and the account
    disagree about how much has been lost, the worse number wins; where they
    disagree about whether a position exists, nothing wins and the system
    halts.

    `max_daily_loss`/`max_drawdown` are the *configured* thresholds (owner
    risk policy v1), not a cached historical value — PL-006. Reconstructing
    the recorded maxima correctly is not enough on its own: this function
    must also refuse to resume a session whose own recorded worst already
    breached policy, or a restart followed by a partial equity recovery
    could quietly resume trading a session that should stay halted. See
    `review/adr/ADR-013-restart-recovery-loss-drawdown-check.md`.
    """
    if not record.is_known:
        return _halt(
            live_equity,
            market_day,
            (ReasonCode.SAFETY_STATE_UNKNOWN,),
            f"risk-session state could not be read: {record.unreadable}",
        )

    recorded = record.state
    if recorded is None:
        # A genuinely first start. Nothing has been consumed because nothing
        # has happened yet, and refusing here would make the system
        # unstartable rather than safe.
        _log.info("risk_session.fresh", equity=str(live_equity), trading_day=market_day.isoformat())
        return SessionRecovery(
            ledger=EquityLedger(starting_equity=live_equity),
            trading_day=market_day,
            resumed=False,
        )

    if recorded.schema_version != SCHEMA_VERSION:
        return _halt(
            live_equity,
            market_day,
            (ReasonCode.SAFETY_STATE_UNKNOWN,),
            f"risk-session schema {recorded.schema_version!r} is not the expected {SCHEMA_VERSION}",
        )

    if recorded.trading_day > market_day:
        # The record is ahead of the market data being replayed into it. One
        # of the two is wrong about when it is, and neither can be trusted to
        # size a position.
        return _halt(
            live_equity,
            market_day,
            (ReasonCode.SAFETY_STATE_UNKNOWN,),
            f"recorded trading day {recorded.trading_day} is after the market day {market_day}",
        )

    if recorded.open_position_count != live_open_positions:
        # The reconciliation rule in miniature: local and account disagree
        # about exposure. Review 1.5 §4 step 8 makes this a halt in both
        # directions, and it is a halt here for the same reason.
        return _halt(
            live_equity,
            market_day,
            (ReasonCode.RECONCILIATION_MISMATCH,),
            f"recorded {recorded.open_position_count} open position(s), "
            f"account reports {live_open_positions}",
        )

    same_session = recorded.trading_day == market_day
    session_start_equity = (
        recorded.session_start_equity if same_session else recorded.current_equity
    )

    # Seed the worst-case values with the worse of the two accounts of them.
    # Recomputing from live equity matters when the record is stale: the
    # position may have moved further against us since it was written.
    peak_equity = max(recorded.peak_equity, live_equity)
    ledger = EquityLedger.resumed(
        # What this run started with, for its own reporting. The session
        # baseline the daily gate measures against is separate, below.
        starting_equity=live_equity,
        session_start_equity=session_start_equity,
        current_equity=live_equity,
        peak_equity=peak_equity,
        max_drawdown_fraction=recorded.max_drawdown_fraction,
        max_session_loss_fraction=(recorded.max_session_loss_fraction if same_session else ZERO),
    )

    # PL-006: a restart must not silently resume a session whose own
    # recorded worst already exhausted an owner-policy limit, just because
    # equity has since moved back under it. Checked against the *widened*
    # maxima above, not the live instantaneous fraction the per-tick gate
    # uses — that gate already catches a live breach; this catches one a
    # restart could otherwise forget.
    exhausted: list[ReasonCode] = []
    if ledger.max_drawdown_fraction >= max_drawdown:
        exhausted.append(ReasonCode.MAX_DRAWDOWN)
    if ledger.max_session_loss_fraction >= max_daily_loss:
        exhausted.append(ReasonCode.DAILY_LOSS_LIMIT)
    if exhausted:
        return _halt(
            live_equity,
            market_day,
            tuple(exhausted),
            "recovered risk session already exhausted an owner-policy limit: "
            f"max_session_loss_fraction={ledger.max_session_loss_fraction}, "
            f"max_drawdown_fraction={ledger.max_drawdown_fraction}",
        )

    _log.info(
        "risk_session.resumed",
        recorded_day=recorded.trading_day.isoformat(),
        market_day=market_day.isoformat(),
        same_session=same_session,
        session_loss_carried=str(ledger.max_session_loss_fraction),
        drawdown_carried=str(ledger.max_drawdown_fraction),
    )
    return SessionRecovery(ledger=ledger, trading_day=market_day, resumed=True)


def _halt(
    live_equity: Decimal,
    market_day: date,
    reasons: tuple[ReasonCode, ...],
    detail: str,
) -> SessionRecovery:
    """Refuse to start trading, but still produce a usable ledger.

    The run continues observing — a halted system that cannot even measure its
    own equity has nothing to report to the operator who has to clear it.
    """
    _log.error(
        "risk_session.unrecoverable", reasons=[reason.value for reason in reasons], detail=detail
    )
    return SessionRecovery(
        ledger=EquityLedger(starting_equity=live_equity),
        trading_day=market_day,
        resumed=False,
        reason_codes=reasons,
        detail=detail,
    )


def snapshot(
    ledger: EquityLedger,
    *,
    trading_day: date,
    realized_pnl: Decimal,
    open_risk_fraction: Decimal | None,
    open_position_count: int,
    recorded_at_utc: UtcDatetime,
) -> RiskSessionState:
    """Capture the ledger as a record that a later process can resume from."""
    return RiskSessionState(
        trading_day=trading_day,
        session_start_equity=ledger.session_start_equity,
        current_equity=ledger.current_equity,
        peak_equity=ledger.peak_equity,
        realized_pnl=realized_pnl,
        max_drawdown_fraction=ledger.max_drawdown_fraction,
        max_session_loss_fraction=ledger.max_session_loss_fraction,
        open_risk_fraction=open_risk_fraction,
        open_position_count=open_position_count,
        recorded_at_utc=recorded_at_utc,
    )
