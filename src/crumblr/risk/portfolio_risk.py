"""Real portfolio open-risk accounting (owner risk policy v1, D1.4,

`review/adr/ADR-011-owner-risk-policy-v1.md`).

`risk/sizing.py`'s own module docstring scopes it to single-position math
over `(Decimal, InstrumentSpec)` — sizing a *new* position from a target
risk fraction. This module runs the same arithmetic in the opposite
direction, over a whole book: given the positions the platform actually
holds, how much of the account's equity is genuinely committed. It
reuses `sizing.py::realised_risk`/`loss_per_lot` directly — one
arithmetic authority, not two that could disagree.

**This is an allocation figure, not a mark-to-market exposure report.**
It answers "how much of the 3% budget is committed", using each
position's *entry* geometry (`open_price` vs `stop_loss_price`), never
its current unrealized P&L. See ADR-011 §2.3 for the full reasoning —
in short: `current_price` is unavailable for every position this
platform can currently hold (confirmed: `mt5_gateway/simulated.py` never
sets it), and mark-to-market geometry would free budget on a losing
position exactly as the book becomes most impaired, and over-report risk
on a winner that can no longer lose its authorized amount. Entry
geometry has neither failure mode, and still responds correctly to a
genuine protective change: a stop moved to breakeven reports exactly
zero risk for that position.

**Fails closed to "could not be established", never to a fabricated
maximum.** A position with no trustworthy protective-stop geometry (no
instrument spec, or no recorded stop) makes the *whole* assessment
`fraction=None` — a partial number would misrepresent a partial book as
a complete one. `None` is not "assume zero" and not "assume worst
case"; it is an honest absence of evidence, the same discipline this
codebase already applies to `ReconciliationStatus.UNKNOWN`,
`SafetyState`'s three-answer shape, and ADR-010's `ExpectedState
.undetermined_reasons`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from crumblr.domain.enums import Side
from crumblr.domain.models import InstrumentSpec, PositionState
from crumblr.domain.money import ZERO
from crumblr.risk.sizing import realised_risk

NO_PROTECTIVE_STOP = "no_protective_stop"
NO_INSTRUMENT_SPEC = "no_instrument_spec"


@dataclass(frozen=True)
class UntrustedPosition:
    """One position `assess_open_risk` could not honestly value, and why."""

    ticket: int
    broker_symbol: str
    reason: str


@dataclass(frozen=True)
class OpenRiskAssessment:
    """`fraction`/`risk_amount` are `None` together, meaning "could not be

    established" — never a partial or fabricated number. `untrusted`
    names exactly which positions blocked the assessment and why, for
    operator visibility, even though the numeric result they contributed
    to is withheld."""

    fraction: Decimal | None
    risk_amount: Decimal | None
    valued_tickets: tuple[int, ...] = ()
    untrusted: tuple[UntrustedPosition, ...] = ()
    detail: str | None = None

    @property
    def is_established(self) -> bool:
        return self.fraction is not None


def _adverse_distance(position: PositionState) -> Decimal:
    """Signed distance from entry to the stop, on the losing side only.

    Deliberately not `abs()`: `TradeIntent`'s own `_stop_distance`
    (`risk/policies.py`) may use `abs()` safely because that contract
    validates the stop sits on the protective side. `PositionState` has
    no equivalent validator — a broker can and does report a stop past
    breakeven on a winning position — so `abs()` here would report a
    locked-in profit as risk. A non-positive result means the position
    cannot lose more than nothing from here and is the caller's signal
    to treat it as established, zero-risk, without ever calling
    `realised_risk` (which raises on a non-positive distance).
    """
    assert position.stop_loss_price is not None
    if position.side is Side.BUY:
        return position.open_price - position.stop_loss_price
    return position.stop_loss_price - position.open_price


def assess_open_risk(
    positions: Sequence[PositionState],
    *,
    specs: Mapping[str, InstrumentSpec],
    equity: Decimal,
) -> OpenRiskAssessment:
    """The real, whole-book replacement for `max_risk_per_trade *

    len(positions)` (owner risk policy v1, D1.4). `specs` is keyed by
    `broker_symbol` rather than a single spec: a position in a symbol
    with no known spec must be untrusted, never silently skipped or
    valued against the wrong instrument.
    """
    if not positions:
        # A flat book is genuinely zero risk, not unknown — collapsing
        # this into "unestablished" would make the common case fail
        # closed and make the platform unable to ever open a first
        # position.
        return OpenRiskAssessment(fraction=ZERO, risk_amount=ZERO)

    if equity <= ZERO:
        return OpenRiskAssessment(fraction=None, risk_amount=None, detail="non_positive_equity")

    total_risk = ZERO
    valued: list[int] = []
    untrusted: list[UntrustedPosition] = []

    for position in positions:
        spec = specs.get(position.broker_symbol)
        if spec is None:
            untrusted.append(
                UntrustedPosition(position.ticket, position.broker_symbol, NO_INSTRUMENT_SPEC)
            )
            continue
        if position.stop_loss_price is None:
            untrusted.append(
                UntrustedPosition(position.ticket, position.broker_symbol, NO_PROTECTIVE_STOP)
            )
            continue

        adverse = _adverse_distance(position)
        if adverse > ZERO:
            total_risk += realised_risk(position.volume, adverse, spec)
        # adverse <= ZERO: the stop is at or past breakeven — established,
        # zero contribution, `loss_per_lot` never called (it raises on a
        # non-positive distance; that would be a fail-open by crash).
        valued.append(position.ticket)

    if untrusted:
        # Any one untrusted position withholds the whole figure — a
        # partial sum would misrepresent a partial book as a complete
        # measurement of it.
        return OpenRiskAssessment(
            fraction=None,
            risk_amount=None,
            valued_tickets=tuple(valued),
            untrusted=tuple(untrusted),
            detail="untrusted_position_geometry",
        )

    return OpenRiskAssessment(
        fraction=total_risk / equity,
        risk_amount=total_risk,
        valued_tickets=tuple(valued),
    )
