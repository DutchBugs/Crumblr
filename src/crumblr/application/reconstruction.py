"""Rebuilding a run from the journal alone (review 1.5 step 1).

The evidence M2 has to produce is not "the rows came back". It is:

    in-memory replay result == replay reconstructed from the persisted journal

so this module reads the `events` table and nothing else — not the capsule
store, not the run's own objects — and rebuilds what happened. If the journal
cannot support that, it is storage rather than an audit trail, and every
downstream guarantee about provenance is unbacked.

Equality is the existing evidence contract: `provenance_fingerprint` per
decision (build.md §25.2), folded into one digest over the sequence.

What the journal does **not** carry is stated as plainly as what it does. A
window that ended before the strategy had enough history produces no event, so
`windows` and `features_unavailable` cannot be reconstructed — see D-031. The
counters below are the ones the journal genuinely accounts for.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from crumblr.application.orchestration import RunTally
from crumblr.domain.enums import Environment, RiskVerdict, SupervisorVerdict
from crumblr.domain.events import EventType, SignalGenerated, SystemHalted
from crumblr.domain.hashing import fingerprint
from crumblr.domain.models import Contract, DecisionCapsule
from crumblr.persistence.journal import EventJournal, JournalIntegrityError


def decision_fingerprint(capsules: Sequence[DecisionCapsule]) -> str:
    """One digest over a decision sequence.

    Order matters: two runs that made the same decisions in a different order
    are not the same run, and a journal that reorders them is not reproducing
    anything.
    """
    payload: dict[str, Any] = {
        "decisions": [capsule.provenance_fingerprint for capsule in capsules]
    }
    return fingerprint(payload)


def tally_from_capsules(
    capsules: Sequence[DecisionCapsule],
    *,
    signals: Sequence[SignalGenerated] = (),
) -> RunTally:
    """Recount a run from its sealed decisions.

    Used on both sides of the comparison — the in-memory capsules and the ones
    read back — so that an equality between them is a statement about the data
    rather than about two different counting routines agreeing by luck.

    `signals` supplies the strategy's NO_TRADE reasons, which a capsule does
    not carry: a window with no intent records its reasoning in the
    `SignalGenerated` event, so the breakdown is only reconstructable from the
    journal proper.
    """
    tally = RunTally()
    intent_free: set[UUID] = set()

    for capsule in capsules:
        if capsule.trade_intent is None:
            tally.no_trade += 1
            intent_free.add(capsule.correlation_id)
            continue

        tally.intents += 1
        risk = capsule.risk_decision
        if risk is not None:
            for reason in risk.reason_codes:
                tally.count(tally.risk_reasons, reason.value)
            if risk.verdict is RiskVerdict.PASS:
                tally.risk_passed += 1
            elif risk.verdict is RiskVerdict.BLOCK:
                tally.risk_blocked += 1
            else:
                tally.risk_halted += 1

        supervisor = capsule.supervisor_decision
        if supervisor is None:
            continue
        for reason in supervisor.reason_codes:
            tally.count(tally.supervisor_reasons, reason.value)
        if supervisor.verdict is SupervisorVerdict.APPROVE:
            tally.supervisor_approved += 1
            # An approved decision with no execution result was refused by the
            # broker's pre-flight — the only remaining place it can be lost.
            if capsule.execution_result is None:
                tally.order_check_rejected += 1
            else:
                tally.orders_filled += 1
        elif supervisor.verdict is SupervisorVerdict.VETO:
            tally.supervisor_vetoed += 1
        else:
            tally.supervisor_halted += 1

    for signal in signals:
        if signal.snapshot_id in intent_free:
            for strategy_reason in signal.reason_codes:
                tally.count(tally.no_trade_reasons, strategy_reason)

    return tally


class ReconstructedRun:
    """What the journal says happened."""

    def __init__(
        self,
        capsules: tuple[DecisionCapsule, ...],
        signals: tuple[SignalGenerated, ...],
        halts: tuple[SystemHalted, ...],
    ) -> None:
        self.capsules = capsules
        self.signals = signals
        self.halts = halts

    @property
    def tally(self) -> RunTally:
        return tally_from_capsules(self.capsules, signals=self.signals)

    @property
    def fingerprint(self) -> str:
        return decision_fingerprint(self.capsules)

    @property
    def halted(self) -> bool:
        return bool(self.halts)


def reconstruct_from_journal(
    journal: EventJournal, *, environment: Environment | None = None
) -> ReconstructedRun:
    """Read the run back out of the `events` table.

    Ordering comes from the journal itself — market time, then insertion
    sequence — never from the order rows happened to be written in.
    """
    capsules: list[DecisionCapsule] = []
    signals: list[SignalGenerated] = []
    halts: list[SystemHalted] = []

    for event in journal.read_all():
        if environment is not None and event.environment is not environment:
            continue
        payload = event.payload
        if event.event_type is EventType.DECISION_CAPSULE_SEALED:
            capsules.append(_expect(payload, DecisionCapsule, event.event_id))
        elif event.event_type is EventType.SIGNAL_GENERATED:
            signals.append(_expect(payload, SignalGenerated, event.event_id))
        elif event.event_type is EventType.SYSTEM_HALTED:
            halts.append(_expect(payload, SystemHalted, event.event_id))

    return ReconstructedRun(tuple(capsules), tuple(signals), tuple(halts))


def _expect[T: Contract](payload: Contract, expected: type[T], event_id: UUID) -> T:
    """Narrow a decoded payload to the type its event type promises.

    `Event` validates this on construction, so reaching the raise means a row
    was written by something that bypassed the envelope. Not an `assert`: this
    is a decode-time integrity check, and `python -O` would delete it.
    """
    if not isinstance(payload, expected):
        raise JournalIntegrityError(
            f"event {event_id} is typed {expected.__name__} but carries a "
            f"{type(payload).__name__} payload"
        )
    return payload
