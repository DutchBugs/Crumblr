"""A simulated broker for replay (build.md §13).

Honest about what it is. The fill model here is a documented approximation, and
build.md §13.1 is explicit that close-only backtesting is not good enough for an
executable FX strategy, so this models bid/ask, spread and slippage — but it
still resolves intrabar stop/target ordering by assumption rather than by tick
data.

The assumptions, all deliberately pessimistic:

- Entry fills at the far side of the book (buy at ask, sell at bid), plus
  slippage proportional to the prevailing spread.
- If a bar touches both the stop and the target, the **stop** is taken. Without
  tick data the true order is unknown, and the optimistic choice is how
  backtests come to look profitable.
- Stops fill at the stop price plus adverse slippage, never better.

Swap and commission are not modelled yet; they belong to the cost model in M3.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.domain.enums import OrderState, Side
from crumblr.domain.events import OrderCheckCompleted
from crumblr.domain.models import (
    AccountState,
    ApprovedOrder,
    ExecutionResult,
    InstrumentSpec,
    PositionState,
)
from crumblr.domain.money import ZERO, price_to_points
from crumblr.domain.timeutils import UtcDatetime
from crumblr.market_data.synthetic import GeneratedTick

SLIPPAGE_SPREAD_FRACTION = Decimal("0.25")
"""Adverse slippage applied on entry and stop fills, as a fraction of the spread."""


@dataclass(frozen=True)
class ClosedTrade:
    """A round trip, kept for the post-trade scorecard."""

    ticket: int
    side: Side
    volume: Decimal
    entry_price: Decimal
    exit_price: Decimal
    opened_at_utc: UtcDatetime
    closed_at_utc: UtcDatetime
    profit: Decimal
    exit_reason: str
    entry_slippage_points: int
    max_adverse_excursion: Decimal
    max_favourable_excursion: Decimal


@dataclass
class _OpenPosition:
    ticket: int
    order_request_id: UUID
    intent_id: UUID
    side: Side
    volume: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal | None
    opened_at_utc: UtcDatetime
    entry_slippage_points: int
    worst_price: Decimal
    best_price: Decimal


class SimulatedBroker:
    """In-memory broker implementing `BrokerPort` against a replayed series."""

    def __init__(
        self,
        spec: InstrumentSpec,
        *,
        starting_balance: Decimal,
        account_currency: str = "EUR",
        server: str = "SimulatedBroker-Demo",
        login: int = 1000001,
        leverage: int = 30,
    ) -> None:
        self._spec = spec
        self._balance = starting_balance
        self._currency = account_currency
        self._server = server
        self._login = login
        self._leverage = leverage

        self._open: dict[int, _OpenPosition] = {}
        self._closed: list[ClosedTrade] = []
        self._handled_requests: dict[UUID, ExecutionResult] = {}
        self._next_ticket = 500_000
        self._tick: GeneratedTick | None = None
        self._now: UtcDatetime | None = None

    # ------------------------------------------------------------------ #
    # Replay driving
    # ------------------------------------------------------------------ #

    def advance(self, tick: GeneratedTick) -> tuple[ClosedTrade, ...]:
        """Move the simulation to `tick`, closing any position it triggers."""
        self._tick = tick
        self._now = tick.received_time_utc
        return self._resolve_exits(tick)

    def _resolve_exits(self, tick: GeneratedTick) -> tuple[ClosedTrade, ...]:
        closed: list[ClosedTrade] = []
        half_spread = (Decimal(tick.spread_points) * self._spec.point) / Decimal(2)

        for ticket, position in list(self._open.items()):
            bar = tick.bar
            if position.side is Side.BUY:
                # A long exits on the bid, which sits below the traded price.
                low = bar.low - half_spread
                high = bar.high - half_spread
                position.worst_price = min(position.worst_price, low)
                position.best_price = max(position.best_price, high)
                hit_stop = low <= position.stop_loss_price
                hit_target = (
                    position.take_profit_price is not None and high >= position.take_profit_price
                )
            else:
                # A short exits on the ask, above the traded price.
                low = bar.low + half_spread
                high = bar.high + half_spread
                position.worst_price = max(position.worst_price, high)
                position.best_price = min(position.best_price, low)
                hit_stop = high >= position.stop_loss_price
                hit_target = (
                    position.take_profit_price is not None and low <= position.take_profit_price
                )

            if not hit_stop and not hit_target:
                continue

            # Pessimistic: a bar touching both is resolved as a stop.
            if hit_stop:
                slip = half_spread * SLIPPAGE_SPREAD_FRACTION
                exit_price = (
                    position.stop_loss_price - slip
                    if position.side is Side.BUY
                    else position.stop_loss_price + slip
                )
                exit_reason = "stop_loss"
            else:
                assert position.take_profit_price is not None
                exit_price = position.take_profit_price
                exit_reason = "take_profit"

            closed.append(self._close(ticket, exit_price, exit_reason, tick.event_time_utc))

        return tuple(closed)

    def _close(self, ticket: int, exit_price: Decimal, reason: str, at: UtcDatetime) -> ClosedTrade:
        position = self._open.pop(ticket)
        profit = self._profit(position.side, position.volume, position.entry_price, exit_price)
        self._balance += profit

        trade = ClosedTrade(
            ticket=ticket,
            side=position.side,
            volume=position.volume,
            entry_price=position.entry_price,
            exit_price=exit_price,
            opened_at_utc=position.opened_at_utc,
            closed_at_utc=at,
            profit=profit,
            exit_reason=reason,
            entry_slippage_points=position.entry_slippage_points,
            max_adverse_excursion=self._profit(
                position.side, position.volume, position.entry_price, position.worst_price
            ),
            max_favourable_excursion=self._profit(
                position.side, position.volume, position.entry_price, position.best_price
            ),
        )
        self._closed.append(trade)
        return trade

    def _profit(self, side: Side, volume: Decimal, entry: Decimal, exit_price: Decimal) -> Decimal:
        move = exit_price - entry if side is Side.BUY else entry - exit_price
        return (move / self._spec.tick_size) * self._spec.tick_value * volume

    # ------------------------------------------------------------------ #
    # BrokerPort
    # ------------------------------------------------------------------ #

    def account(self) -> AccountState:
        equity = self._balance + self.unrealised_profit
        margin = self._used_margin()
        return AccountState(
            login=self._login,
            server=self._server,
            currency=self._currency,
            is_demo=True,
            trade_allowed=True,
            expert_allowed=True,
            connected=True,
            balance=self._balance,
            equity=equity,
            margin=margin,
            margin_free=equity - margin,
            margin_level=(equity / margin * 100) if margin > ZERO else None,
            leverage=self._leverage,
            observed_at_utc=self._require_now(),
        )

    def instrument(self, canonical_symbol: str) -> InstrumentSpec:
        if canonical_symbol != self._spec.canonical_symbol:
            raise KeyError(f"simulated broker does not carry {canonical_symbol!r}")
        return self._spec

    def positions(self) -> tuple[PositionState, ...]:
        now = self._require_now()
        return tuple(
            PositionState(
                ticket=position.ticket,
                broker_symbol=self._spec.broker_symbol,
                side=position.side,
                volume=position.volume,
                open_price=position.entry_price,
                stop_loss_price=position.stop_loss_price,
                take_profit_price=position.take_profit_price,
                opened_at_utc=position.opened_at_utc,
                profit=self._profit(
                    position.side, position.volume, position.entry_price, self._exit_price(position)
                ),
                swap=ZERO,
                observed_at_utc=now,
            )
            for position in self._open.values()
        )

    def order_check(self, order: ApprovedOrder) -> OrderCheckCompleted:
        """Margin and volume validation, mirroring MT5's `order_check`."""
        margin_required = self._margin_for(order.volume)
        account = self.account()
        accepted = True
        comment = "ok"

        if order.volume < self._spec.volume_min or order.volume > self._spec.volume_max:
            accepted, comment = False, "volume outside broker limits"
        elif margin_required > account.margin_free:
            accepted, comment = False, "insufficient free margin"

        return OrderCheckCompleted(
            order_request_id=order.order_request_id,
            intent_id=order.intent_id,
            accepted=accepted,
            retcode=0 if accepted else 10019,
            comment=comment,
            margin_required=margin_required,
        )

    def order_send(self, order: ApprovedOrder) -> ExecutionResult:
        """Open a position. Idempotent on `order_request_id` (build.md §7 inv. 2)."""
        if order.order_request_id in self._handled_requests:
            # A retry after a reconnect must not double the exposure.
            return self._handled_requests[order.order_request_id]

        tick = self._require_tick()
        now = self._require_now()
        half_spread = (Decimal(tick.spread_points) * self._spec.point) / Decimal(2)
        slip = (half_spread * SLIPPAGE_SPREAD_FRACTION).quantize(
            self._spec.tick_size, rounding=ROUND_DOWN
        )

        requested = tick.ask if order.side is Side.BUY else tick.bid
        fill = requested + slip if order.side is Side.BUY else requested - slip
        slippage_points = price_to_points(
            fill - requested if order.side is Side.BUY else requested - fill,
            self._spec.point,
        )

        ticket = self._next_ticket
        self._next_ticket += 1
        self._open[ticket] = _OpenPosition(
            ticket=ticket,
            order_request_id=order.order_request_id,
            intent_id=order.intent_id,
            side=order.side,
            volume=order.volume,
            entry_price=fill,
            stop_loss_price=order.stop_loss_price,
            take_profit_price=order.take_profit_price,
            opened_at_utc=tick.event_time_utc,
            entry_slippage_points=slippage_points,
            worst_price=fill,
            best_price=fill,
        )

        result = ExecutionResult(
            execution_id=uuid5(NAMESPACE_URL, f"crumblr:exec:{order.order_request_id}"),
            order_request_id=order.order_request_id,
            intent_id=order.intent_id,
            state=OrderState.FILLED,
            mt5_order_ticket=ticket,
            mt5_deal_ticket=ticket,
            mt5_position_ticket=ticket,
            retcode=10009,
            retcode_comment="simulated fill",
            requested_price=requested,
            executed_price=fill,
            requested_volume=order.volume,
            executed_volume=order.volume,
            slippage_points=slippage_points,
            submitted_at_utc=now,
            broker_time_utc=tick.event_time_utc,
            completed_at_utc=now,
            latency_ms=0,
        )
        self._handled_requests[order.order_request_id] = result
        return result

    def cancel_pending_orders(self) -> tuple[int, ...]:
        """No resting orders exist yet — this broker fills at market only.

        Returning an empty tuple is the honest answer, not a stub: there is
        genuinely nothing pending to cancel. When limit and stop entries are
        added the control is already wired through to here.
        """
        return ()

    def close_all_positions(self, *, reason: str) -> tuple[int, ...]:
        """Close every open position at the current bid/ask."""
        tick = self._require_tick()
        closed: list[int] = []
        for ticket, position in list(self._open.items()):
            exit_price = tick.bid if position.side is Side.BUY else tick.ask
            self._close(ticket, exit_price, reason, tick.event_time_utc)
            closed.append(ticket)
        return tuple(closed)

    # ------------------------------------------------------------------ #
    # Simulation state
    # ------------------------------------------------------------------ #

    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def unrealised_profit(self) -> Decimal:
        return sum(
            (
                self._profit(p.side, p.volume, p.entry_price, self._exit_price(p))
                for p in self._open.values()
            ),
            ZERO,
        )

    @property
    def equity(self) -> Decimal:
        return self._balance + self.unrealised_profit

    @property
    def closed_trades(self) -> tuple[ClosedTrade, ...]:
        return tuple(self._closed)

    def _exit_price(self, position: _OpenPosition) -> Decimal:
        tick = self._require_tick()
        return tick.bid if position.side is Side.BUY else tick.ask

    def _margin_for(self, volume: Decimal) -> Decimal:
        tick = self._require_tick()
        notional = volume * self._spec.contract_size * tick.ask
        return notional / Decimal(self._leverage)

    def _used_margin(self) -> Decimal:
        return sum((self._margin_for(p.volume) for p in self._open.values()), ZERO)

    def _require_tick(self) -> GeneratedTick:
        if self._tick is None:
            raise RuntimeError("simulated broker has no market data; call advance() first")
        return self._tick

    def _require_now(self) -> UtcDatetime:
        if self._now is None:
            raise RuntimeError("simulated broker has no clock; call advance() first")
        return self._now
