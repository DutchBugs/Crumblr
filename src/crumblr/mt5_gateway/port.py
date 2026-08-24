"""The boundary the rest of the platform talks to (build.md §2.2).

Only implementations of this protocol may reach a broker. The real one wraps
the `MetaTrader5` package on a Windows host; the simulated one fills orders
against a replayed series. Everything upstream — strategy, risk, supervisor,
orchestrator — is written against this interface and cannot tell the two apart.

That is what makes the replay engine meaningful: it exercises production code
with only the final adapter swapped.
"""

from __future__ import annotations

from typing import Protocol

from crumblr.domain.events import OrderCheckCompleted
from crumblr.domain.models import (
    AccountState,
    ApprovedOrder,
    ExecutionResult,
    InstrumentSpec,
    PositionState,
)


class BrokerPort(Protocol):
    """Broker operations the platform depends on."""

    def account(self) -> AccountState:
        """Current account state, including whether it is a demo account."""
        ...

    def instrument(self, canonical_symbol: str) -> InstrumentSpec:
        """Symbol specification as the broker currently reports it."""
        ...

    def positions(self) -> tuple[PositionState, ...]:
        """Open positions according to the broker."""
        ...

    def order_check(self, order: ApprovedOrder) -> OrderCheckCompleted:
        """Pre-flight validation, before anything is submitted."""
        ...

    def order_send(self, order: ApprovedOrder) -> ExecutionResult:
        """Submit an order.

        Implementations must be idempotent on `order.order_request_id`:
        resubmitting the same request must never create a second position.
        """
        ...

    def cancel_pending_orders(self) -> tuple[int, ...]:
        """Cancel every resting order, returning the tickets cancelled.

        One of the three separately controlled operator actions in build.md
        §8.2. It must not close positions and must not halt the system.
        """
        ...

    def close_all_positions(self, *, reason: str) -> tuple[int, ...]:
        """Close every open position at market, returning the tickets closed.

        The second of the three controls. It must not halt: an operator who
        wants both has to ask for both.
        """
        ...
