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

**Deliberately simpler failure semantics than `RiskSessionStore`.** A risk
budget that cannot be read must halt (`SessionRecord.unreadable`) — losing
that record could buy back headroom, which is unsafe in the permissive
direction. Losing *this* record cannot: the worst consequence, today, is a
duplicate audit row, which is explicitly the safe direction. `load_latest`
therefore resolves an unreadable record to "nothing recorded" rather than a
third state a caller must handle, which would just be more code protecting
a risk this module's own docstring says does not exist yet. Revisit this
choice when an execution service is attached and the consequence of
"forgot" changes from an audit duplicate to a real one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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


class DecisionWindowStore(Protocol):
    """Where decision-window idempotence state is persisted between runs.

    `load_latest` must never raise — an implementation that cannot read its
    own record returns `None`, the same as "nothing recorded yet" (see the
    module docstring for why that collapse is safe here and would not be
    for `RiskSessionStore`).
    """

    def load_latest(
        self, *, canonical_symbol: str, strategy_id: str, config_version: str
    ) -> DecisionWindowState | None: ...

    def save(self, state: DecisionWindowState) -> None: ...


class InMemoryDecisionWindowStore:
    """For tests and for a decision worker that should not outlive its process."""

    def __init__(self, initial: DecisionWindowState | None = None) -> None:
        self._state = initial
        self.saves = 0

    def load_latest(
        self, *, canonical_symbol: str, strategy_id: str, config_version: str
    ) -> DecisionWindowState | None:
        state = self._state
        if (
            state is not None
            and state.canonical_symbol == canonical_symbol
            and state.strategy_id == strategy_id
            and state.config_version == config_version
        ):
            return state
        return None

    def save(self, state: DecisionWindowState) -> None:
        self._state = state
        self.saves += 1
