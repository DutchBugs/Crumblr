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

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
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
from crumblr.domain.timeutils import UtcDatetime, utc_now
from crumblr.market_data.pipeline import BarBuildResult, interval_for, normalize_bars
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

_BAR_SETTLE_BUFFER = timedelta(seconds=30)
"""How long past its raw boundary a bar must sit before `bars()` returns it.

Fifth real soak, 2026-08-24: a bar's `tick_volume` was observed to change
between two polls five seconds apart, both after the bar's interval had
already closed by the raw boundary alone — MT5 kept attributing a few very
late ticks to it for a short window past the nominal close. 30 seconds is
six poll cycles at the default 5-second interval: generous margin over the
single observed revision, not tuned tight against it. Widen this from
further soak evidence if a revision is ever seen this far past close;
narrowing it needs the same kind of evidence, not a hunch."""

_CLOCK_OFFSET_TOLERANCE = timedelta(minutes=3)
"""How far a measured offset may sit from a clean half-hour multiple.

Review F-040: a fresh reference tick's timestamp, compared against this
platform's own clock, lands within call latency of a whole/half-hour GMT
offset (measured ~2:59:39-2:59:40 for a 180-minute offset in the real
2026-08-24 soak — a few seconds of slack, not minutes). A tick that is
stale — the terminal returning the last quote it ever saw, e.g. market
closed or feed frozen — no longer tracks wall-clock time at all, so the
elapsed time since it was captured shows up here as extra drift away from
that clean multiple. 3 minutes is generous over the observed latency while
still catching a tick that is materially old rather than merely delayed
by a network round trip."""

_MAX_PLAUSIBLE_CLOCK_OFFSET = timedelta(hours=15)
"""No real GMT offset exceeds UTC-12:00/UTC+14:00. A measurement outside
this band is not a broker in an unusual timezone; it is bad input."""


class ReadOnlyViolationError(RuntimeError):
    """Something asked the M1 gateway to change broker state."""


class AccountGuardError(RuntimeError):
    """The connected account is not the one the configuration expects."""


class SymbolNotFoundError(RuntimeError):
    """No broker symbol could be resolved for a canonical symbol."""


class ClockOffsetUnavailableError(RuntimeError):
    """The broker-clock offset could not be established from a trustworthy tick."""


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
        clock: Callable[[], UtcDatetime] = utc_now,
    ) -> None:
        self._client = client
        self._guard = guard
        self._canonical_symbol = canonical_symbol
        self._clock = clock
        self._resolved_symbol: str | None = None
        self._broker_clock_offset: timedelta | None = None

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
            observed_at_utc=self._clock(),
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

    def _clock_offset(self) -> timedelta:
        """How far ahead of true UTC the terminal's own clock runs (D-039).

        MT5's raw `time`/`time_msc` fields are the *server's* clock, not
        necessarily UTC — many brokers run servers at UTC+2/+3. Discovered
        by observation the first real continuous-read soak, 2026-08-24: a
        30-minute run against Pepperstone's demo showed a stable +2:59:39 to
        +2:59:40 gap between broker-reported tick time and this platform's
        own UTC clock, once the reader had caught up to live data — not a
        one-off latency artefact, and consistent with the same D-039 gap
        `positions()` already carried, unremarked, before this fix.

        Rather than hard-code that observation, this asks the terminal for
        its current tick and measures the gap live, every time a gateway
        instance connects — a `LiveReader` reconnect builds a fresh gateway,
        so this re-detects on every reconnect rather than assuming a value
        detected once stays true across a restart, a DST change, or a
        different broker/server entirely. Rounded to the nearest 30 minutes,
        since GMT offsets are always whole or half-hour multiples and a raw
        measurement carries a little call latency.
        """
        if self._broker_clock_offset is None:
            broker_symbol = self.resolve_symbol()
            module = self._client.module
            tick = self._client.checked("symbol_info_tick", module.symbol_info_tick(broker_symbol))
            broker_now = datetime.fromtimestamp(int(_field(tick, "time")), tz=UTC)
            raw_offset = broker_now - self._clock()
            half_hours = round(raw_offset / timedelta(minutes=30))
            candidate = timedelta(minutes=30 * half_hours)
            residual = raw_offset - candidate

            implausible = abs(candidate) > _MAX_PLAUSIBLE_CLOCK_OFFSET
            if abs(residual) > _CLOCK_OFFSET_TOLERANCE or implausible:
                _log.warning(
                    "mt5.broker_clock_offset_unreliable",
                    raw_offset_seconds=raw_offset.total_seconds(),
                    nearest_half_hour_minutes=int(candidate.total_seconds() // 60),
                    residual_seconds=residual.total_seconds(),
                )
                raise ClockOffsetUnavailableError(
                    "reference tick does not support a stable broker-clock offset "
                    f"(raw={raw_offset}, nearest clean offset={candidate}, "
                    f"residual={residual}) — the tick may be stale"
                )

            self._broker_clock_offset = candidate
            _log.info(
                "mt5.broker_clock_offset_detected",
                offset_minutes=int(self._broker_clock_offset.total_seconds() // 60),
                raw_offset_seconds=raw_offset.total_seconds(),
            )
        return self._broker_clock_offset

    def _to_utc(self, raw_seconds: float) -> datetime:
        """A raw MT5 timestamp (server clock), corrected to true UTC.

        Takes seconds rather than an `int` so a caller with `time_msc`
        (millisecond precision) can pass `time_msc / 1000` straight through
        without losing sub-second resolution to a premature truncation.
        """
        return datetime.fromtimestamp(raw_seconds, tz=UTC) - self._clock_offset()

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
            captured_at_utc=self._clock(),
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

        observed = self._clock()
        return tuple(
            PositionState(
                ticket=int(position.ticket),
                broker_symbol=str(position.symbol),
                side=(Side.BUY if int(position.type) == MT5_POSITION_TYPE_BUY else Side.SELL),
                volume=_to_decimal(position.volume, "volume"),
                open_price=_to_decimal(position.price_open, "price_open"),
                stop_loss_price=(_to_decimal(position.sl, "sl") if position.sl else None),
                take_profit_price=(_to_decimal(position.tp, "tp") if position.tp else None),
                opened_at_utc=self._to_utc(int(position.time)),
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

        **D-039, resolved by observation 2026-08-24**: MT5 tick timestamps are
        the server's own clock, not necessarily UTC — see `_clock_offset`.
        `since` is supplied in true UTC by the caller, so it is shifted into
        the terminal's own clock before being sent, the same correction run
        in reverse from what `_tick_from_raw` applies to what comes back.
        Skipping this half would ask the terminal for ticks "since" a moment
        that, on its own clock, is really `_clock_offset()` in the past —
        which is exactly what produced a several-hour backlog on the first
        real soak that used this method.
        """
        broker_symbol = self.resolve_symbol()
        module = self._client.module
        since_broker = since + self._clock_offset()
        raw = self._client.checked(
            "copy_ticks_from",
            module.copy_ticks_from(broker_symbol, since_broker, count, module.COPY_TICKS_ALL),
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
            self._to_utc(int(time_msc) / 1000)
            if time_msc
            else self._to_utc(int(_field(row, "time")))
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
            received_time_utc=self._clock(),
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

        `copy_rates_from_pos(..., 0, count)` position 0 is MT5's *current*,
        still-forming bar, not a closed one — its OHLC keeps changing every
        poll until the interval ends. Found on the third real soak: a live
        `LiveReader` polling every few seconds fetched that bar, whose close
        had moved by the next poll, and `record_bars` correctly read the
        second value as a contradiction of the first and raised — because
        "raw data is immutable" is true of a *closed* bar and not yet true of
        one still being formed. Dropped here rather than downstream: a bar
        only becomes the kind of fact this platform persists once its own
        interval has actually ended.

        `_BAR_SETTLE_BUFFER` exists because "the interval has ended" was not
        quite enough on its own. Fifth real soak, 2026-08-24: a bar was first
        read and stored the moment its interval closed, then re-read one poll
        (5 seconds) later with a different `tick_volume` for the *same*
        interval — MT5 kept attributing a few very-late ticks to a bar for a
        short window after its nominal close, and `record_bars` correctly
        raised on the second, revised value. The buffer holds a bar back a
        little past its raw boundary so the first read already carries MT5's
        settled figures, rather than trying to tolerate or merge a revision
        after the fact.
        """
        broker_symbol = self.resolve_symbol()
        spec = self.instrument(canonical_symbol)
        module = self._client.module
        raw = self._client.checked(
            "copy_rates_from_pos",
            module.copy_rates_from_pos(broker_symbol, _mt5_timeframe(module, timeframe), 0, count),
        )
        received_time_utc = self._clock()
        interval = interval_for(timeframe)
        bars = sorted(
            (
                bar
                for bar in (self._bar_from_raw(row) for row in raw)
                if bar.open_time_utc + interval + _BAR_SETTLE_BUFFER <= received_time_utc
            ),
            key=lambda bar: bar.open_time_utc,
        )
        return normalize_bars(
            bars,
            timeframe=timeframe,
            spec=spec,
            source=source,
            origin=BarOrigin.BROKER,
            received_time_utc=received_time_utc,
        )

    def _bar_from_raw(self, row: Any) -> Bar:
        real_volume = _field(row, "real_volume")
        spread = _field(row, "spread")
        return Bar(
            open_time_utc=self._to_utc(int(_field(row, "time"))),
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
            "observed_at_utc": self._clock().isoformat(),
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
