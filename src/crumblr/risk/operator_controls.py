"""The three operator controls (build.md §8.2, review finding F-008).

    HALT NEW ORDERS
    CANCEL PENDING ORDERS
    FLATTEN POSITIONS

They are deliberately not wired to each other. build.md is explicit that
combining them into one ambiguous button is the wrong design, and the reason is
operational: under pressure an operator who means "stop opening new trades"
must not discover they have also liquidated the book, and an operator who means
"get me out now" must not find that only new orders were stopped.

Each action names its operator, states a reason, and is logged separately.
None of them can be invoked by the system on its own behalf — the automatic
path can trip the kill switch, and that is all it can do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from crumblr.domain.enums import ReasonCode
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.mt5_gateway.port import BrokerPort
from crumblr.risk.kill_switch import KillSwitch


class OperatorAction(StrEnum):
    """What an operator asked for. Each is a separate, separately audited act."""

    HALT_NEW_ORDERS = "HALT_NEW_ORDERS"
    CANCEL_PENDING_ORDERS = "CANCEL_PENDING_ORDERS"
    FLATTEN_POSITIONS = "FLATTEN_POSITIONS"
    RESET_HALT = "RESET_HALT"


@dataclass(frozen=True)
class OperatorActionRecord:
    """One entry in the operator audit log."""

    action: OperatorAction
    operator: str
    reason: str
    occurred_at_utc: UtcDatetime
    affected_tickets: tuple[int, ...] = ()
    detail: str | None = None


class OperatorControls:
    """Operator-facing actions over the kill switch and the broker.

    Holds no trading judgement of its own. It performs exactly what it is asked
    to perform, records who asked and why, and does nothing further.
    """

    def __init__(self, kill_switch: KillSwitch, broker: BrokerPort) -> None:
        self._kill_switch = kill_switch
        self._broker = broker
        self._log: list[OperatorActionRecord] = []

    @property
    def audit_log(self) -> tuple[OperatorActionRecord, ...]:
        return tuple(self._log)

    def halt_new_orders(self, *, operator: str, reason: str) -> OperatorActionRecord:
        """Stop new orders. Leaves pending orders and open positions untouched."""
        self._require_authorisation(operator, reason)
        now = utc_now()
        self._kill_switch.trip(
            reason_codes=(ReasonCode.MANUAL_HALT,),
            tripped_by=operator,
            occurred_at_utc=now,
            detail=reason,
        )
        return self._record(OperatorAction.HALT_NEW_ORDERS, operator, reason, now)

    def cancel_pending_orders(self, *, operator: str, reason: str) -> OperatorActionRecord:
        """Cancel resting orders. Does not halt, and does not close positions."""
        self._require_authorisation(operator, reason)
        cancelled = self._broker.cancel_pending_orders()
        return self._record(
            OperatorAction.CANCEL_PENDING_ORDERS,
            operator,
            reason,
            utc_now(),
            affected_tickets=cancelled,
        )

    def flatten_positions(self, *, operator: str, reason: str) -> OperatorActionRecord:
        """Close every open position at market.

        Deliberately does **not** halt. An operator who wants both must ask for
        both — otherwise flattening would silently stop trading, and someone
        would eventually flatten expecting exactly that and be wrong.
        """
        self._require_authorisation(operator, reason)
        closed = self._broker.close_all_positions(reason=f"operator flatten: {reason}")
        return self._record(
            OperatorAction.FLATTEN_POSITIONS,
            operator,
            reason,
            utc_now(),
            affected_tickets=closed,
        )

    def reset_halt(self, *, operator: str, incident_note: str) -> OperatorActionRecord:
        """Clear a halt. Requires an incident note; agents cannot call this."""
        self._require_authorisation(operator, incident_note)
        self._kill_switch.reset(operator=operator, incident_note=incident_note)
        return self._record(OperatorAction.RESET_HALT, operator, incident_note, utc_now())

    @staticmethod
    def _require_authorisation(operator: str, reason: str) -> None:
        """Every action is attributable. An unattributed action is refused."""
        if not operator.strip():
            raise ValueError("an operator action requires an identified operator")
        if not reason.strip():
            raise ValueError("an operator action requires a stated reason")

    def _record(
        self,
        action: OperatorAction,
        operator: str,
        reason: str,
        now: UtcDatetime,
        *,
        affected_tickets: tuple[int, ...] = (),
        detail: str | None = None,
    ) -> OperatorActionRecord:
        record = OperatorActionRecord(
            action=action,
            operator=operator,
            reason=reason,
            occurred_at_utc=now,
            affected_tickets=affected_tickets,
            detail=detail,
        )
        self._log.append(record)
        return record
