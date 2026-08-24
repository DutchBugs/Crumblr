"""The read-only MT5 gateway (M1, build.md §26 milestone 1).

M1 is deliberately read-only, and that is enforced structurally rather than by
discipline: the execution methods of `BrokerPort` exist here only to refuse.
There is no code path through this class that can place, modify or cancel an
order, so "M1 cannot trade" is a property of the type rather than a promise
about how it will be used.

Two rules from owner decision O-001 shape the rest:

- **Nothing about the broker is assumed.** The EUR/USD symbol is discovered
  from the terminal, never hard-coded — Pepperstone and others use suffixes,
  and a hard-coded `"EURUSD"` is a bug that only shows up against a real
  account.
- **The account is verified, not trusted.** Server, currency, leverage and
  demo status are checked against configuration on every read, because a demo
  that has expired and been replaced (Pepperstone expires them at 60 days) is
  a normal event rather than an exceptional one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, NoReturn

from crumblr.config import AccountGuardConfig
from crumblr.domain.enums import BarOrigin, Side
from crumblr.domain.models import (
    AccountState,
    ApprovedOrder,
    Bar,
    InstrumentSpec,
    MarketTick,
    PositionState,
)
from crumblr.domain.timeutils import utc_now
from crumblr.market_data.pipeline import BarBuildResult, normalize_bars
from crumblr.mt5_gateway.client import Mt5CallFailedError, Mt5Client, Mt5Module, mask_login
from crumblr.mt5_gateway.enums import (
    SYMBOL_TRADE_MODES,
    decode_enum,
    decode_filling_modes,
)
from crumblr.observability.logging import get_logger
from crumblr.persistence.market_data import tick_identity

_log = get_logger("mt5_gateway")

MT5_POSITION_TYPE_BUY = 0
MT5_POSITION_TYPE_SELL = 1

MT5_ACCOUNT_TRADE_MODE_DEMO = 0
"""`ACCOUNT_TRADE_MODE_DEMO`. Contest is 1, real is 2."""


class ReadOnlyViolationError(RuntimeError):
    """Something asked the M1 gateway to change broker state."""


class AccountGuardError(RuntimeError):
    """The connected account is not the one the configuration expects."""


class SymbolNotFoundError(RuntimeError):
    """No broker symbol could be resolved for a canonical symbol."""


def _to_decimal(value: object, field: str) -> Decimal:
    """Convert an MT5 float to `Decimal` without going through binary float.

    MT5 hands back Python floats from `account_info`/`symbol_info`, but a
    numpy *scalar* — `numpy.float64` — from `copy_ticks_from` and
    `copy_rates_from_pos`. Both pass `isinstance(value, float)`, since numpy's
    float64 subclasses Python's `float`. They are not interchangeable for
    this purpose: `repr(1.1685)` is `"1.1685"`, but numpy 2.x's own `__repr__`
    for a scalar is `"np.float64(1.1685)"`, which `Decimal()` cannot parse.
    Found running the first real continuous-read soak, 2026-08-24 — every
    tick has this shape and every one crashed the reader. `float(value)`
    strips the numpy wrapper before `repr` ever sees it, and is a no-op for
    an already-plain float.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(float(value)))
    raise TypeError(f"{field} is {type(value).__name__}, expected a number")


def _field(row: Any, name: str) -> Any:
    """Read one field off a `copy_ticks_from` / `copy_rates_from_pos` row.

    The real calls return numpy structured arrays, whose rows answer to
    `row["field"]`; a hand-built fake in the test suite may use a plain
    object with attributes instead. Supporting both means the fakes do not
    have to reproduce numpy's dtype machinery to stand in for the real thing.
    """
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        return getattr(row, name)


_TIMEFRAME_ATTRS: dict[str, str] = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}
"""market_data.pipeline's timeframe names, mapped to the module attribute that
carries MT5's own constant for it — read at call time, never hardcoded."""


def _mt5_timeframe(module: Mt5Module, timeframe: str) -> int:
    attr = _TIMEFRAME_ATTRS.get(timeframe)
    if attr is None:
        known = ", ".join(sorted(_TIMEFRAME_ATTRS))
        raise KeyError(f"unsupported timeframe {timeframe!r}; known: {known}")
    return int(getattr(module, attr))


class ReadOnlyMt5Gateway:
    """Reads account, instrument and position state from a live terminal.

    Implements the read half of `BrokerPort`. The write half raises.
    """

    def __init__(
        self,
        client: Mt5Client,
        guard: AccountGuardConfig,
        *,
        canonical_symbol: str = "EUR/USD",
    ) -> None:
        self._client = client
        self._guard = guard
        self._canonical_symbol = canonical_symbol
        self._resolved_symbol: str | None = None

    # ------------------------------------------------------------------ #
    # Read operations
    # ------------------------------------------------------------------ #

    def account(self) -> AccountState:
        """Read the account, then verify it is the expected one."""
        info = self._client.checked("account_info", self._client.module.account_info())
        state = AccountState(
            login=int(info.login),
            server=str(info.server),
            currency=str(info.currency),
            is_demo=int(info.trade_mode) == MT5_ACCOUNT_TRADE_MODE_DEMO,
            trade_allowed=bool(info.trade_allowed),
            expert_allowed=bool(getattr(info, "trade_expert", info.trade_allowed)),
            connected=self._client.is_connected,
            balance=_to_decimal(info.balance, "balance"),
            equity=_to_decimal(info.equity, "equity"),
            margin=_to_decimal(info.margin, "margin"),
            margin_free=_to_decimal(info.margin_free, "margin_free"),
            margin_level=(
                _to_decimal(info.margin_level, "margin_level")
                if getattr(info, "margin_level", 0)
                else None
            ),
            leverage=int(info.leverage),
            observed_at_utc=utc_now(),
        )
        self._verify_account(state)
        return state

    def _verify_account(self, state: AccountState) -> None:
        """Refuse an account that does not match configuration.

        Raising rather than returning a flag: the risk engine has its own
        account checks, but a gateway that hands back state from the wrong
        account has already failed at its one job.
        """
        mismatches: list[str] = []
        if self._guard.require_demo_account and not state.is_demo:
            mismatches.append("account is not a demo account")
        if state.server != self._guard.expected_server:
            mismatches.append(
                f"server {state.server!r} != expected {self._guard.expected_server!r}"
            )
        if self._guard.expected_login is not None and state.login != self._guard.expected_login:
            mismatches.append(
                f"login {mask_login(state.login)} != expected "
                f"{mask_login(self._guard.expected_login)}"
            )

        expected_currency = getattr(self._guard, "expected_currency", None)
        if expected_currency and state.currency != expected_currency:
            mismatches.append(f"currency {state.currency!r} != expected {expected_currency!r}")

        expected_leverage = getattr(self._guard, "expected_leverage", None)
        if expected_leverage and state.leverage != expected_leverage:
            mismatches.append(f"leverage {state.leverage} != expected {expected_leverage}")

        if mismatches:
            _log.error(
                "mt5.account_guard_failed",
                account_ref=mask_login(state.login),
                server=state.server,
                mismatches=mismatches,
            )
            raise AccountGuardError("; ".join(mismatches))

    def resolve_symbol(self) -> str:
        """Find the broker's name for the canonical symbol.

        Pepperstone and others append suffixes, so the name is discovered from
        `symbols_get` rather than assumed. An exact match wins; otherwise the
        shortest candidate whose base name matches, because `EURUSD.a` is the
        instrument and `EURUSD.a.cfd` would be something else.
        """
        if self._resolved_symbol is not None:
            return self._resolved_symbol

        wanted = self._canonical_symbol.replace("/", "").upper()
        symbols = self._client.checked("symbols_get", self._client.module.symbols_get())

        candidates = [
            str(symbol.name)
            for symbol in symbols
            if str(symbol.name).upper().replace(".", "").startswith(wanted)
        ]
        if not candidates:
            raise SymbolNotFoundError(
                f"no broker symbol found for {self._canonical_symbol!r}; "
                f"the terminal reported {len(symbols)} symbols"
            )

        exact = [name for name in candidates if name.upper() == wanted]
        chosen = exact[0] if exact else min(candidates, key=len)

        if not self._client.module.symbol_select(chosen, True):
            code, message = self._client.module.last_error()
            raise Mt5CallFailedError("symbol_select", code, message)

        self._resolved_symbol = chosen
        _log.info(
            "mt5.symbol_resolved",
            canonical=self._canonical_symbol,
            broker_symbol=chosen,
            candidates=candidates,
        )
        return chosen

    def instrument(self, canonical_symbol: str) -> InstrumentSpec:
        """Read the symbol specification as the broker currently reports it."""
        if canonical_symbol != self._canonical_symbol:
            raise SymbolNotFoundError(
                f"this gateway serves {self._canonical_symbol!r}, not {canonical_symbol!r}"
            )
        broker_symbol = self.resolve_symbol()
        info = self._client.checked("symbol_info", self._client.module.symbol_info(broker_symbol))

        return InstrumentSpec(
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            currency_base=str(info.currency_base),
            currency_profit=str(info.currency_profit),
            contract_size=_to_decimal(info.trade_contract_size, "contract_size"),
            digits=int(info.digits),
            point=_to_decimal(info.point, "point"),
            tick_size=_to_decimal(info.trade_tick_size, "tick_size"),
            tick_value=_to_decimal(info.trade_tick_value, "tick_value"),
            volume_min=_to_decimal(info.volume_min, "volume_min"),
            volume_max=_to_decimal(info.volume_max, "volume_max"),
            volume_step=_to_decimal(info.volume_step, "volume_step"),
            stops_level=int(info.trade_stops_level),
            freeze_level=int(info.trade_freeze_level),
            # D-037, resolved: these were `str(int)` — the digit, not the name.
            # Confirmed against a real terminal 2026-08-24 (status.md §13).
            filling_modes=decode_filling_modes(int(info.filling_mode)),
            trade_mode=decode_enum(int(info.trade_mode), SYMBOL_TRADE_MODES),
            captured_at_utc=utc_now(),
        )

    def positions(self) -> tuple[PositionState, ...]:
        """Read open positions.

        An empty result from MT5 is ambiguous — no positions, or a failed call —
        so `positions_get` returning `None` raises while an empty tuple is
        reported as genuinely flat.
        """
        raw = self._client.module.positions_get()
        if raw is None:
            code, message = self._client.module.last_error()
            # Code 1 is RES_S_OK: an empty book, not a failure.
            if code != 1:
                raise Mt5CallFailedError("positions_get", code, message)
            return ()

        observed = utc_now()
        return tuple(
            PositionState(
                ticket=int(position.ticket),
                broker_symbol=str(position.symbol),
                side=(Side.BUY if int(position.type) == MT5_POSITION_TYPE_BUY else Side.SELL),
                volume=_to_decimal(position.volume, "volume"),
                open_price=_to_decimal(position.price_open, "price_open"),
                stop_loss_price=(_to_decimal(position.sl, "sl") if position.sl else None),
                take_profit_price=(_to_decimal(position.tp, "tp") if position.tp else None),
                opened_at_utc=datetime.fromtimestamp(int(position.time), tz=UTC),
                profit=_to_decimal(position.profit, "profit"),
                swap=_to_decimal(position.swap, "swap"),
                magic=int(position.magic) if getattr(position, "magic", None) else None,
                observed_at_utc=observed,
            )
            for position in raw
        )

    def ticks(
        self, canonical_symbol: str, *, since: datetime, count: int, source: str
    ) -> tuple[MarketTick, ...]:
        """Read raw ticks from the terminal, forward from `since`.

        HANDOVER.md §4.5 — the continuous-read half of M1 that `readonly.py`
        did not yet implement. No dedup, gap or ordering logic here; that is
        `market_data.pipeline`'s job, and it runs on whatever a caller stores.
        This method only reads and converts what the terminal handed back.

        **Unverified against a real feed**: MT5 tick timestamps are assumed
        UTC, matching the same assumption `positions()` already makes for
        `position.time`. The first continuous-read soak test is what actually
        checks this — see status.md §13.
        """
        broker_symbol = self.resolve_symbol()
        module = self._client.module
        raw = self._client.checked(
            "copy_ticks_from",
            module.copy_ticks_from(broker_symbol, since, count, module.COPY_TICKS_ALL),
        )
        return tuple(
            self._tick_from_raw(row, canonical_symbol, broker_symbol, source) for row in raw
        )

    def _tick_from_raw(
        self, row: Any, canonical_symbol: str, broker_symbol: str, source: str
    ) -> MarketTick:
        bid = _to_decimal(_field(row, "bid"), "bid")
        ask = _to_decimal(_field(row, "ask"), "ask")
        last_raw = _field(row, "last")
        time_msc = _field(row, "time_msc")
        event_time = (
            datetime.fromtimestamp(int(time_msc) / 1000, tz=UTC)
            if time_msc
            else datetime.fromtimestamp(int(_field(row, "time")), tz=UTC)
        )
        volume = _field(row, "volume")
        flags = _field(row, "flags")
        return MarketTick(
            tick_id=tick_identity(
                source=source,
                canonical_symbol=canonical_symbol,
                event_time_utc=event_time,
                bid=bid,
                ask=ask,
            ),
            source=source,
            canonical_symbol=canonical_symbol,
            broker_symbol=broker_symbol,
            event_time_utc=event_time,
            received_time_utc=utc_now(),
            bid=bid,
            ask=ask,
            last=(_to_decimal(last_raw, "last") if last_raw else None),
            volume=(int(volume) if volume else None),
            flags=(int(flags) if flags is not None else None),
        )

    def bars(
        self, canonical_symbol: str, *, timeframe: str, count: int, source: str
    ) -> BarBuildResult:
        """Read the broker's own bars for `timeframe`, most recent `count`.

        Delivered pre-formed by MT5 (`copy_rates_from_pos`), not aggregated
        from ticks — `origin=BarOrigin.BROKER`. Still passed through
        `market_data.pipeline.normalize_bars` for the same gap/order/duplicate
        checks a tick-built series gets: a delivered series is not more
        trustworthy than a derived one, only differently sourced.
        """
        broker_symbol = self.resolve_symbol()
        spec = self.instrument(canonical_symbol)
        module = self._client.module
        raw = self._client.checked(
            "copy_rates_from_pos",
            module.copy_rates_from_pos(broker_symbol, _mt5_timeframe(module, timeframe), 0, count),
        )
        bars = sorted((self._bar_from_raw(row) for row in raw), key=lambda bar: bar.open_time_utc)
        return normalize_bars(
            bars,
            timeframe=timeframe,
            spec=spec,
            source=source,
            origin=BarOrigin.BROKER,
            received_time_utc=utc_now(),
        )

    def _bar_from_raw(self, row: Any) -> Bar:
        real_volume = _field(row, "real_volume")
        spread = _field(row, "spread")
        return Bar(
            open_time_utc=datetime.fromtimestamp(int(_field(row, "time")), tz=UTC),
            open=_to_decimal(_field(row, "open"), "open"),
            high=_to_decimal(_field(row, "high"), "high"),
            low=_to_decimal(_field(row, "low"), "low"),
            close=_to_decimal(_field(row, "close"), "close"),
            tick_volume=int(_field(row, "tick_volume")),
            real_volume=(int(real_volume) if real_volume else None),
            spread_points=(int(spread) if spread is not None else None),
        )

    def terminal_health(self) -> dict[str, Any]:
        """Facts about the terminal, for the health panel (build.md §22)."""
        info = self._client.checked("terminal_info", self._client.module.terminal_info())
        version = self._client.module.version()
        return {
            "connected": bool(getattr(info, "connected", False)),
            "trade_allowed": bool(getattr(info, "trade_allowed", False)),
            "build": version[1] if version and len(version) > 1 else None,
            "ping_last_ms": getattr(info, "ping_last", None),
            "observed_at_utc": utc_now().isoformat(),
        }

    # ------------------------------------------------------------------ #
    # Execution — refused, by design
    # ------------------------------------------------------------------ #

    def _refuse(self, operation: str) -> NoReturn:
        _log.error("mt5.readonly_violation", operation=operation)
        raise ReadOnlyViolationError(
            f"{operation} is not available on the M1 read-only gateway. "
            "Execution arrives at M5, behind ADR-001's execution-time risk "
            "revalidation and broker reconciliation."
        )

    def order_check(self, order: ApprovedOrder) -> NoReturn:
        self._refuse("order_check")

    def order_send(self, order: ApprovedOrder) -> NoReturn:
        self._refuse("order_send")

    def cancel_pending_orders(self) -> NoReturn:
        self._refuse("cancel_pending_orders")

    def close_all_positions(self, *, reason: str) -> NoReturn:
        self._refuse("close_all_positions")
