"""Kill switch (build.md §8.2).

Three rules define this module:

- Anything may trip it.
- Nothing automatic may reset it.
- It survives the process that tripped it.

`reset` therefore demands an operator identity and an incident note, and there
is no code path that supplies those on the system's behalf. Every state change
is persisted before it takes effect, and startup begins closed until the
recorded state has been read and found explicitly clear.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from crumblr.domain.enums import KillSwitchState, ReasonCode
from crumblr.domain.money import ZERO
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.observability.logging import get_logger
from crumblr.risk.safety_state import (
    InMemorySafetyStateStore,
    SafetyState,
    SafetyStateStore,
)

_log = get_logger("kill_switch")


@dataclass(frozen=True)
class HaltRecord:
    """One entry in the kill-switch log (status.md §8)."""

    occurred_at_utc: UtcDatetime
    reason_codes: tuple[ReasonCode, ...]
    tripped_by: str
    detail: str | None = None


class KillSwitch:
    """Durable kill switch.

    Every state change is written to the store before it is considered made, so
    a halt survives the process that tripped it (review finding F-003).

    Construct with `KillSwitch.on_startup(...)` rather than directly whenever a
    previous run's state could exist: that path begins disabled and only
    releases once the recorded state has been read and found to be explicitly
    RUNNING.
    """

    def __init__(self, store: SafetyStateStore | None = None) -> None:
        self._store: SafetyStateStore = store or InMemorySafetyStateStore()
        self._state = KillSwitchState.RUNNING
        self._history: list[HaltRecord] = []
        self._startup_detail: str | None = None

    @classmethod
    def on_startup(cls, store: SafetyStateStore) -> KillSwitch:
        """Restore from the store, failing closed on anything unclear.

        build.md §7 invariant 9 wants a gateway that starts read-only until it
        knows where it stands. The same applies to the whole system: an
        unreadable, missing or halted record all leave new orders disabled, and
        only an explicit RUNNING record permits them.
        """
        switch = cls(store)
        recorded = store.load()

        if recorded.permits_new_orders:
            switch._state = KillSwitchState.RUNNING
            _log.info(
                "safety_state.recovered",
                state=recorded.state.value,
                new_orders="enabled",
                recorded_at=recorded.recorded_at_utc.isoformat(),
            )
            return switch

        switch._state = (
            KillSwitchState.HALTED
            if recorded.state is KillSwitchState.HALTED
            else KillSwitchState.UNKNOWN
        )
        switch._startup_detail = recorded.detail
        _log.warning(
            "safety_state.recovered_closed",
            state=switch._state.value,
            new_orders="disabled",
            reason_codes=[code.value for code in recorded.reason_codes],
            detail=recorded.detail,
        )
        switch._history.append(
            HaltRecord(
                occurred_at_utc=recorded.recorded_at_utc,
                reason_codes=recorded.reason_codes or (ReasonCode.SAFETY_STATE_UNKNOWN,),
                tripped_by=recorded.tripped_by or "startup_guard",
                detail=recorded.detail,
            )
        )
        return switch

    @property
    def state(self) -> KillSwitchState:
        return self._state

    @property
    def is_halted(self) -> bool:
        """True unless the switch is explicitly RUNNING.

        UNKNOWN counts as halted. Anything else would mean a system that has
        lost track of its own safety state carries on trading.
        """
        return self._state is not KillSwitchState.RUNNING

    @property
    def startup_detail(self) -> str | None:
        """Why startup found the system halted, when it did."""
        return self._startup_detail

    @property
    def history(self) -> tuple[HaltRecord, ...]:
        return tuple(self._history)

    @property
    def active_reasons(self) -> tuple[ReasonCode, ...]:
        return self._history[-1].reason_codes if self._history and self.is_halted else ()

    def trip(
        self,
        *,
        reason_codes: tuple[ReasonCode, ...],
        tripped_by: str,
        occurred_at_utc: UtcDatetime,
        detail: str | None = None,
    ) -> HaltRecord:
        """Halt new orders. Idempotent — re-tripping records the reason again."""
        if not reason_codes:
            raise ValueError("a halt must record why it happened")
        record = HaltRecord(
            occurred_at_utc=occurred_at_utc,
            reason_codes=reason_codes,
            tripped_by=tripped_by,
            detail=detail,
        )
        # Written before the in-memory state changes: if persisting fails, the
        # exception propagates and the caller sees a halt that did not take,
        # rather than a process that believes it halted and a record that
        # disagrees.
        self._store.save(
            SafetyState(
                state=KillSwitchState.HALTED,
                reason_codes=reason_codes,
                recorded_at_utc=occurred_at_utc,
                tripped_by=tripped_by,
                detail=detail,
            )
        )
        self._history.append(record)
        self._state = KillSwitchState.HALTED
        _log.error(
            "kill_switch.tripped",
            reason_codes=[code.value for code in reason_codes],
            tripped_by=tripped_by,
            detail=detail,
        )
        return record

    def reset(self, *, operator: str, incident_note: str) -> None:
        """Clear the halt. Operator-only, by design (build.md §8.2 reset rule).

        The halt stays in the log. Resetting clears the state, not the history.
        """
        if not operator.strip():
            raise ValueError("a kill-switch reset requires an identified operator")
        if not incident_note.strip():
            raise ValueError("a kill-switch reset requires an incident note")
        self._store.save(
            SafetyState(
                state=KillSwitchState.RUNNING,
                reason_codes=(),
                recorded_at_utc=utc_now(),
                tripped_by=operator,
                detail=f"reset by {operator}: {incident_note}",
            )
        )
        self._state = KillSwitchState.RUNNING
        self._startup_detail = None
        _log.warning("kill_switch.reset", operator=operator, incident_note=incident_note)


@dataclass
class EquityLedger:
    """Tracks the loss and drawdown the halt thresholds are measured against.

    Drawdown is measured from the high-water mark, not from the starting
    balance, so a profitable run that gives everything back still trips.
    """

    starting_equity: Decimal
    peak_equity: Decimal = field(init=False)
    current_equity: Decimal = field(init=False)
    session_start_equity: Decimal = field(init=False)
    max_drawdown_fraction: Decimal = field(init=False)
    max_session_loss_fraction: Decimal = field(init=False)

    def __post_init__(self) -> None:
        if self.starting_equity <= ZERO:
            raise ValueError("starting equity must be positive")
        self.peak_equity = self.starting_equity
        self.current_equity = self.starting_equity
        self.session_start_equity = self.starting_equity
        self.max_drawdown_fraction = ZERO
        self.max_session_loss_fraction = ZERO

    @classmethod
    def resumed(
        cls,
        *,
        starting_equity: Decimal,
        session_start_equity: Decimal,
        current_equity: Decimal,
        peak_equity: Decimal,
        max_drawdown_fraction: Decimal,
        max_session_loss_fraction: Decimal,
    ) -> EquityLedger:
        """Rebuild a ledger from a persisted session (review F-019).

        The recorded maxima are installed *before* the live equity is folded
        in, so `update` can only widen them. A resumed ledger is therefore
        never further from its limits than the record it came from — which is
        the whole point of persisting it.
        """
        ledger = cls(starting_equity=starting_equity)
        ledger.session_start_equity = session_start_equity
        ledger.peak_equity = peak_equity
        ledger.max_drawdown_fraction = max_drawdown_fraction
        ledger.max_session_loss_fraction = max_session_loss_fraction
        ledger.update(current_equity)
        return ledger

    def update(self, equity: Decimal) -> None:
        """Record a new equity reading and update the worst values seen.

        The running maxima are tracked here rather than derived at the end,
        because the deepest drawdown of a run is usually not the one it
        finishes on — and that is the number promotion is judged against.
        """
        self.current_equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        self.max_drawdown_fraction = max(self.max_drawdown_fraction, self.drawdown_fraction)
        self.max_session_loss_fraction = max(
            self.max_session_loss_fraction, self.session_loss_fraction
        )

    def start_new_session(self) -> None:
        """Roll the daily-loss baseline forward. Drawdown is unaffected."""
        self.session_start_equity = self.current_equity

    @property
    def session_loss_fraction(self) -> Decimal:
        """Realised + unrealised loss this session, as a fraction of its opening equity."""
        if self.session_start_equity <= ZERO:
            return ZERO
        loss = self.session_start_equity - self.current_equity
        return max(ZERO, loss / self.session_start_equity)

    @property
    def drawdown_fraction(self) -> Decimal:
        """Distance below the high-water mark, as a fraction of that mark."""
        if self.peak_equity <= ZERO:
            return ZERO
        return max(ZERO, (self.peak_equity - self.current_equity) / self.peak_equity)
