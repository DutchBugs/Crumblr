"""Durable decision-window idempotence for the live/shadow pipeline (F-054).

Review 1.17 §8 / review 1.18 §7: `LiveDecisionOrchestrator`'s two in-process
guards — "which bar window did I last decide" and "which `TradeIntent`
hashes has the risk engine's duplicate-protection check already seen" — both
reset to empty on every restart. That is harmless today only because no
execution path exists: re-deciding an already-decided window produces at
worst a duplicate *audit* row, and both `CapsuleStore.seal()` and
`EventJournal.append()` are already idempotent on content-derived identity,
so the duplicate write is a silent no-op. The instant an execution service
is attached, "a harmless duplicate audit row" and "a second independently
executable order request" stop being the same risk.

This module makes the two facts durable, the same shape `risk/session.py`
already uses for the daily-loss budget (F-019): a frozen state, a narrow
store `Protocol`, an in-memory implementation for tests, and a PostgreSQL
implementation in `persistence/decision_window.py`.

Keyed by `(canonical_symbol, strategy_id, config_version)` — review 1.17
§8's own invariant is "same strategy + same config + same canonical symbol
+ same closed M5 decision window + same feature/input identity -> same
logical decision identity". A config change is therefore a genuinely new
logical-decision space, not a continuation of the old one: starting fresh
when `config_version` changes is correct, not a gap.

**Review 1.19 §5: "nothing recorded" and "recorded but unreadable" must not**
**collapse into the same outcome.** The first version of this module made
exactly that collapse, on the reasoning that the only consequence today is a
duplicate audit row — true, but the review's point stands: a corrupted or
unreadable record would look identical to a legitimate fresh start, and that
distinction is precisely the one F-054 exists to preserve, not something to
skip while it is still cheap. `DecisionWindowRecord`/`recover_decision_window`
now mirror `risk/session.py`'s `SessionRecord`/`recover_session` shape
exactly: three answers, not two — "no prior record" recovers cleanly, while
"a record exists but could not be read" fails closed and trips the kill
switch, the same way a corrupted risk-session record does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from crumblr.domain.enums import ReasonCode
from crumblr.domain.timeutils import UtcDatetime

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DecisionWindowState:
    """What a live decision worker had already decided when this was written."""

    canonical_symbol: str
    strategy_id: str
    config_version: str
    last_decided_open_time_utc: UtcDatetime
    seen_decision_hashes: frozenset[str]
    recorded_at_utc: UtcDatetime
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class DecisionWindowRecord:
    """The outcome of asking the store what it remembers.

    Three answers, not two — the same shape `risk/session.py::SessionRecord`
    uses, for the same reason (review 1.19 §5): "nothing recorded" (a
    genuine first start) and "I could not read what was recorded" (the
    store is corrupted, unavailable, or on a schema this code no longer
    understands) must never collapse into one, because a caller that cannot
    tell them apart cannot tell a legitimate fresh start from a corrupted
    idempotence record that happens to look empty.
    """

    state: DecisionWindowState | None = None
    unreadable: str | None = None

    @property
    def is_known(self) -> bool:
        return self.unreadable is None


class DecisionWindowStore(Protocol):
    """Where decision-window idempotence state is persisted between runs.

    `load_latest` must never raise — an implementation that cannot read its
    own record returns `DecisionWindowRecord(unreadable=...)`, never a bare
    `None`, so the caller cannot mistake a failed read for an absent one.
    """

    def load_latest(
        self, *, canonical_symbol: str, strategy_id: str, config_version: str
    ) -> DecisionWindowRecord: ...

    def save(self, state: DecisionWindowState) -> None: ...


class InMemoryDecisionWindowStore:
    """For tests and for a decision worker that should not outlive its process."""

    def __init__(
        self, initial: DecisionWindowState | None = None, *, unreadable: str | None = None
    ) -> None:
        self._state = initial
        self._unreadable = unreadable
        self.saves = 0

    def load_latest(
        self, *, canonical_symbol: str, strategy_id: str, config_version: str
    ) -> DecisionWindowRecord:
        if self._unreadable is not None:
            return DecisionWindowRecord(unreadable=self._unreadable)
        state = self._state
        if (
            state is not None
            and state.canonical_symbol == canonical_symbol
            and state.strategy_id == strategy_id
            and state.config_version == config_version
        ):
            return DecisionWindowRecord(state=state)
        return DecisionWindowRecord()

    def save(self, state: DecisionWindowState) -> None:
        self._state = state
        self.saves += 1


@dataclass(frozen=True)
class DecisionWindowRecovery:
    """What a `LiveDecisionOrchestrator` should restore, and whether it must halt first."""

    last_decided_open_time_utc: UtcDatetime | None
    seen_decision_hashes: frozenset[str]
    must_halt: bool
    reason_codes: tuple[ReasonCode, ...] = ()
    detail: str | None = None


def recover_decision_window(record: DecisionWindowRecord) -> DecisionWindowRecovery:
    """Restore decision-window state from a record, failing closed on corruption.

    Mirrors `risk/session.py::recover_session`'s shape. A genuinely absent
    record recovers to an empty, un-halted starting point — nothing has
    been decided yet, so there is nothing to protect. An unreadable record
    halts instead of silently starting empty, because "empty" and
    "corrupted" must never look the same to a caller deciding whether it is
    safe to proceed (review 1.19 §5).
    """
    if not record.is_known:
        return DecisionWindowRecovery(
            last_decided_open_time_utc=None,
            seen_decision_hashes=frozenset(),
            must_halt=True,
            reason_codes=(ReasonCode.DECISION_STATE_UNKNOWN,),
            detail=f"decision-window state could not be read: {record.unreadable}",
        )
    if record.state is None:
        return DecisionWindowRecovery(
            last_decided_open_time_utc=None,
            seen_decision_hashes=frozenset(),
            must_halt=False,
        )
    return DecisionWindowRecovery(
        last_decided_open_time_utc=record.state.last_decided_open_time_utc,
        seen_decision_hashes=record.state.seen_decision_hashes,
        must_halt=False,
    )
