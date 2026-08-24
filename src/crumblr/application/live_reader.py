"""Continuous MT5 read: connect, read, persist, reconnect, revalidate.

HANDOVER.md §4.5 named this the part of M1 that first contact does not
finish: "the probe proves a connection and reads a snapshot" — a long-running
process that keeps reading, notices when it has stopped, and does not resume
on trust, is a different and larger claim. Review 1.9 F-034 is explicit about
why: a reconnect that simply reopens the socket and carries on is not enough,
because MT5 attaches to whatever account and terminal happen to be logged in
at that moment. Nothing here assumes a reconnect returns to the same account,
the same symbol, or even the same broker entity — every one of those is
re-read and re-checked, every time.

**What this is not.** This is not the orchestrator (`application/orchestration`
drives the decision loop against a `BrokerPort`). This reads ticks and bars
and stores them — build.md's Milestone 1 acceptance ("reads EUR/USD
ticks/bars", "restart/reconnect tested"), nothing more. No `TradeIntent` is
built here, no strategy runs, and the read-only gateway underneath cannot
reach an order interface even if this code tried.

Two failure classes are handled differently on purpose:

- **A stale feed is not a wrong account.** No new ticks for a while means the
  reader marks itself `STALE` and keeps trying — fresh data clears it, with no
  human in the loop, because nothing here contradicts what was already
  verified.
- **A revalidation mismatch is not a stale feed.** The server, currency,
  leverage, demo status, or (this session's addition) hedging/netting mode
  disagreeing with what was last confirmed means the reader marks itself
  `UNHEALTHY` and **stops reading** until `acknowledge()` is called with an
  operator and a note. Nothing here clears that by itself — the same
  no-automatic-reset discipline `risk/kill_switch.py` uses for the halt, kept
  a deliberately separate instance because "the data cannot be trusted" and
  "new orders are forbidden" are different claims that happen to currently
  have the same consequence.
"""

from __future__ import annotations

import time as _time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from typing import Any, Protocol

from crumblr.config import AccountGuardConfig
from crumblr.domain.models import InstrumentSpec, MarketBar, MarketTick
from crumblr.domain.timeutils import UtcDatetime, age_ms, utc_now
from crumblr.mt5_gateway.client import (
    Mt5CallFailedError,
    Mt5Client,
    Mt5Credentials,
    Mt5UnavailableError,
)
from crumblr.mt5_gateway.readonly import (
    AccountGuardError,
    ReadOnlyMt5Gateway,
    SymbolNotFoundError,
)
from crumblr.observability.logging import get_logger
from crumblr.persistence.journal import JournalIntegrityError

_log = get_logger("live_reader")


class MarketDataSink(Protocol):
    """The slice of `persistence.market_data.MarketDataStore` this reader uses.

    A real `MarketDataStore` needs PostgreSQL, and PostgreSQL-backed tests
    belong in `tests/integration` (skipping without a database). Typing
    against this narrower protocol instead of the concrete store lets the
    reconnect/revalidation logic — the part this session is actually adding —
    be unit-tested with a plain in-memory fake, the same way `Mt5Client` is
    tested against a fake `Mt5Module` rather than a real terminal.
    """

    def record_ticks(self, ticks: Sequence[MarketTick]) -> int: ...
    def record_bars(self, bars: Sequence[MarketBar]) -> int: ...


DEFAULT_TICK_LOOKBACK = timedelta(minutes=5)
"""How far back the very first tick read reaches. Every read after that uses
the timestamp of the newest tick actually stored as its cursor."""

DEFAULT_BAR_WINDOW = 12
"""How many of the most recent bars are re-requested on every poll.

Deliberately larger than one: `MarketDataStore.record_bars` is idempotent on
`bar_id` and raises on a genuine conflict, so re-fetching a small trailing
window is how a bar that was still forming on the last poll gets its final
values without needing separate "closed" vs. "forming" logic here.
"""


class ReaderStatus(StrEnum):
    """The reader's own health, kept separate from `KillSwitchState`.

    Not part of the persisted decision contract in `domain/enums.py` — this
    is operational state about *this process*, recomputed each run rather
    than restored across a restart, deliberately scoped smaller than the
    kill switch for the reasons in the module docstring.
    """

    HEALTHY = "HEALTHY"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    UNHEALTHY = "UNHEALTHY"
    """Revalidation disagreed, or could not be established. Sticky — see
    `LiveReader.acknowledge`."""


@dataclass(frozen=True)
class ReaderHealth:
    """A snapshot of the reader's state, for logging, tests and a dashboard."""

    status: ReaderStatus
    connected: bool
    last_tick_at_utc: UtcDatetime | None = None
    last_bar_at_utc: UtcDatetime | None = None
    last_reconnect_at_utc: UtcDatetime | None = None
    reconnect_count: int = 0
    consecutive_failures: int = 0
    spec_version: str | None = None
    spec_changes: int = 0
    last_error: str | None = None
    detail: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """A dashboard-safe rendering — no credential-shaped field exists here."""
        return {
            "status": self.status.value,
            "connected": self.connected,
            "last_tick_at_utc": (
                self.last_tick_at_utc.isoformat() if self.last_tick_at_utc else None
            ),
            "last_bar_at_utc": (self.last_bar_at_utc.isoformat() if self.last_bar_at_utc else None),
            "last_reconnect_at_utc": (
                self.last_reconnect_at_utc.isoformat() if self.last_reconnect_at_utc else None
            ),
            "reconnect_count": self.reconnect_count,
            "consecutive_failures": self.consecutive_failures,
            "spec_version": self.spec_version,
            "spec_changes": self.spec_changes,
            "last_error": self.last_error,
            "detail": self.detail,
        }


class LiveReader:
    """Reads real ticks and M5 bars from MT5, forever, until told to stop.

    Construct once per process. `poll_once()` is the whole unit of work and
    takes no arguments other than `self`, so a test can drive it directly
    without a real clock or a real sleep; `run_forever()` is the thin loop
    around it that an operator or a script actually runs.
    """

    def __init__(
        self,
        client: Mt5Client,
        credentials: Mt5Credentials,
        guard: AccountGuardConfig,
        store: MarketDataSink,
        *,
        canonical_symbol: str = "EUR/USD",
        timeframe: str = "M5",
        source_prefix: str = "mt5",
        terminal_path: str | None = None,
        tick_batch: int = 5_000,
        bar_count: int = DEFAULT_BAR_WINDOW,
        tick_lookback: timedelta = DEFAULT_TICK_LOOKBACK,
        poll_interval: timedelta = timedelta(seconds=5),
        stale_after: timedelta = timedelta(seconds=60),
        reconnect_backoff: timedelta = timedelta(seconds=5),
        max_reconnect_backoff: timedelta = timedelta(minutes=5),
        clock: Callable[[], UtcDatetime] = utc_now,
        sleep: Callable[[float], None] = _time.sleep,
    ) -> None:
        self._client = client
        self._credentials = credentials
        self._guard = guard
        self._store = store
        self._canonical_symbol = canonical_symbol
        self._timeframe = timeframe
        self._source_prefix = source_prefix
        self._terminal_path = terminal_path
        self._tick_batch = tick_batch
        self._bar_count = bar_count
        self._tick_lookback = tick_lookback
        self._poll_interval = poll_interval
        self._stale_after = stale_after
        self._reconnect_backoff = reconnect_backoff
        self._max_reconnect_backoff = max_reconnect_backoff
        self._clock = clock
        self._sleep = sleep

        self._gateway: ReadOnlyMt5Gateway | None = None
        self._spec: InstrumentSpec | None = None
        self._expected_margin_mode: int | None = None
        self._last_tick_at: UtcDatetime | None = None
        self._health = ReaderHealth(status=ReaderStatus.DISCONNECTED, connected=False)

    @property
    def health(self) -> ReaderHealth:
        return self._health

    # ------------------------------------------------------------------ #
    # The loop
    # ------------------------------------------------------------------ #

    def run_forever(self, *, max_iterations: int | None = None) -> None:
        """Poll until stopped. `max_iterations` exists for tests and soak runs.

        Backoff between reconnect attempts grows on repeated failure and
        resets the moment a poll succeeds — a terminal that is down for an
        hour should not be hammered every five seconds for that hour.
        """
        backoff = self._reconnect_backoff
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            health = self.poll_once()
            if health.status is ReaderStatus.DISCONNECTED:
                self._sleep(backoff.total_seconds())
                backoff = min(backoff * 2, self._max_reconnect_backoff)
            else:
                backoff = self._reconnect_backoff
                self._sleep(self._poll_interval.total_seconds())
            iteration += 1

    def poll_once(self) -> ReaderHealth:
        """One unit of work: ensure connected, then read and persist.

        Never raises for an ordinary MT5 failure — those become health
        transitions, because a long-running reader that dies on the first
        dropped connection has not implemented reconnect at all.
        """
        if self._health.status is ReaderStatus.UNHEALTHY:
            # Sticky by design. See `acknowledge`.
            return self._health

        if self._gateway is None:
            health = self._reconnect()
            if health.status is not ReaderStatus.HEALTHY:
                # Connect or revalidate failed; nothing to read yet.
                return health
            # Falls through to read on the same poll a connect succeeded —
            # waiting a full cycle to read data that is already available
            # would just be added reconnect latency for no reason.

        assert self._gateway is not None
        try:
            self._read_and_persist(self._gateway)
        except (Mt5CallFailedError, Mt5UnavailableError) as error:
            _log.warning("live_reader.read_failed", error=str(error))
            self._gateway = None
            self._health = replace(
                self._health,
                status=ReaderStatus.DISCONNECTED,
                connected=False,
                consecutive_failures=self._health.consecutive_failures + 1,
                last_error=str(error),
            )
            return self._health
        except JournalIntegrityError as error:
            # Raw-data immutability contradicted itself — the broker sent a
            # different bar for an interval already stored. build.md §26
            # treats that as a fact worth stopping for, not overwriting past.
            _log.error("live_reader.data_conflict", error=str(error))
            self._health = replace(
                self._health,
                status=ReaderStatus.UNHEALTHY,
                last_error=str(error),
                detail=f"data conflict, not a connection problem: {error}",
            )
            return self._health

        return self._health

    # ------------------------------------------------------------------ #
    # Connect and revalidate
    # ------------------------------------------------------------------ #

    def _reconnect(self) -> ReaderHealth:
        now = self._clock()
        try:
            self._client.disconnect()
            self._client.connect(self._credentials, terminal_path=self._terminal_path)
            gateway = ReadOnlyMt5Gateway(
                self._client, self._guard, canonical_symbol=self._canonical_symbol
            )
            # Raises AccountGuardError on server/login/currency/leverage/demo
            # mismatch — reconnect -> wrong account -> fail closed, for free,
            # by reusing the same check the first-contact probe exercises.
            account = gateway.account()
            spec = gateway.instrument(self._canonical_symbol)
        except (Mt5CallFailedError, Mt5UnavailableError) as error:
            _log.warning("live_reader.reconnect_failed", error=str(error))
            self._health = replace(
                self._health,
                status=ReaderStatus.DISCONNECTED,
                connected=False,
                consecutive_failures=self._health.consecutive_failures + 1,
                last_reconnect_at_utc=now,
                last_error=str(error),
            )
            return self._health
        except AccountGuardError as error:
            _log.error("live_reader.account_mismatch", error=str(error))
            self._gateway = None
            self._health = replace(
                self._health,
                status=ReaderStatus.UNHEALTHY,
                connected=False,
                last_reconnect_at_utc=now,
                last_error=str(error),
                detail=f"account guard failed on reconnect: {error}",
            )
            return self._health
        except SymbolNotFoundError as error:
            # review 1.10 F-036: the instrument could not be established at
            # all — not a transient call failure, and not merely a changed
            # spec. This account cannot even see the expected symbol, which
            # is a safety-relevant fact that could not be confirmed, and
            # review 1.9's own rule for that is UNKNOWN -> HALT, not retry.
            _log.error("live_reader.symbol_unresolved", error=str(error))
            self._gateway = None
            self._health = replace(
                self._health,
                status=ReaderStatus.UNHEALTHY,
                connected=False,
                last_reconnect_at_utc=now,
                last_error=str(error),
                detail=f"instrument could not be established on reconnect: {error}",
            )
            return self._health

        margin_mode = self._read_margin_mode()
        if self._expected_margin_mode is None:
            self._expected_margin_mode = margin_mode
        elif margin_mode is not None and margin_mode != self._expected_margin_mode:
            _log.error(
                "live_reader.margin_mode_changed",
                expected=self._expected_margin_mode,
                observed=margin_mode,
            )
            self._gateway = None
            self._health = replace(
                self._health,
                status=ReaderStatus.UNHEALTHY,
                connected=False,
                last_reconnect_at_utc=now,
                detail=(
                    f"account margin mode changed from {self._expected_margin_mode} to "
                    f"{margin_mode} — hedging/netting must not change under this reader"
                ),
            )
            return self._health

        previous_spec_version = self._spec.spec_version if self._spec is not None else None
        spec_changed = (
            previous_spec_version is not None and spec.spec_version != previous_spec_version
        )
        spec_changes = self._health.spec_changes + (1 if spec_changed else 0)
        if spec_changed:
            _log.warning(
                "live_reader.spec_changed",
                previous=previous_spec_version,
                current=spec.spec_version,
            )

        self._gateway = gateway
        self._spec = spec
        self._health = ReaderHealth(
            status=ReaderStatus.HEALTHY,
            connected=True,
            last_tick_at_utc=self._health.last_tick_at_utc,
            last_bar_at_utc=self._health.last_bar_at_utc,
            last_reconnect_at_utc=now,
            reconnect_count=self._health.reconnect_count + 1,
            consecutive_failures=0,
            spec_version=spec.spec_version,
            spec_changes=spec_changes,
            last_error=None,
            detail=(
                f"symbol spec changed on reconnect: {previous_spec_version} -> {spec.spec_version}"
                if spec_changed
                else None
            ),
        )
        _log.info(
            "live_reader.connected",
            server=account.server,
            currency=account.currency,
            resolved_symbol=spec.broker_symbol,
            spec_version=spec.spec_version,
            reconnect_count=self._health.reconnect_count,
        )
        return self._health

    def _read_margin_mode(self) -> int | None:
        """Read `account_info().margin_mode` directly.

        Not part of `AccountState` — that contract carries what the risk
        engine needs, and margin mode is not (yet) one of those fields. Read
        here, ad hoc, the same way the first-contact probe reads it, because
        adding a field to a persisted domain contract for one reader's
        revalidation check is a larger change than this session decided to
        make. See `review/DEVIATIONS.md`.
        """
        try:
            info = self._client.checked("account_info", self._client.module.account_info())
        except Mt5CallFailedError:
            return None
        value = getattr(info, "margin_mode", None)
        return int(value) if value is not None else None

    # ------------------------------------------------------------------ #
    # Read and persist
    # ------------------------------------------------------------------ #

    def _read_and_persist(self, gateway: ReadOnlyMt5Gateway) -> None:
        now = self._clock()
        source = f"{self._source_prefix}:{self._guard.expected_server}"
        since = self._last_tick_at if self._last_tick_at is not None else now - self._tick_lookback

        ticks = gateway.ticks(
            self._canonical_symbol, since=since, count=self._tick_batch, source=source
        )
        if ticks:
            self._store.record_ticks(ticks)
            newest = max(tick.event_time_utc for tick in ticks)
            self._last_tick_at = max(self._last_tick_at, newest) if self._last_tick_at else newest

        bar_result = gateway.bars(
            self._canonical_symbol, timeframe=self._timeframe, count=self._bar_count, source=source
        )
        if bar_result.bars:
            self._store.record_bars(bar_result.bars)
        for observation in bar_result.observations:
            _log.warning(
                "live_reader.bar_anomaly",
                anomaly=observation.anomaly.value,
                at_utc=observation.at_utc.isoformat(),
                detail=observation.detail,
            )

        last_tick_at = self._last_tick_at
        last_bar_at = (
            max(bar.bar.open_time_utc for bar in bar_result.bars)
            if bar_result.bars
            else self._health.last_bar_at_utc
        )

        # Falls back to the last (re)connect time when nothing has ever been
        # read: silence since a successful connect is exactly as stale as
        # silence since the last tick — it should not read as perpetually
        # fresh just because nothing has arrived yet to be stale relative to.
        reference = last_tick_at or last_bar_at or self._health.last_reconnect_at_utc
        stale_after_ms = self._stale_after.total_seconds() * 1000
        stale = reference is not None and age_ms(reference, now=now) > stale_after_ms
        status = ReaderStatus.STALE if stale else ReaderStatus.HEALTHY

        self._health = replace(
            self._health,
            status=status,
            connected=True,
            last_tick_at_utc=last_tick_at,
            last_bar_at_utc=last_bar_at,
            consecutive_failures=0,
            last_error=None,
        )

    # ------------------------------------------------------------------ #
    # Recovery from UNHEALTHY
    # ------------------------------------------------------------------ #

    def acknowledge(self, *, operator: str, note: str) -> None:
        """Clear a sticky `UNHEALTHY` state. Operator-only, by design.

        Mirrors `risk/kill_switch.py`'s reset discipline: this does not
        pretend the last problem is resolved, only that a human has seen it
        and wants the reader to try connecting again. The next `poll_once`
        reconnects and revalidates from scratch — it does not resume as if
        nothing happened.
        """
        if not operator.strip():
            raise ValueError("acknowledging a data-service halt requires an identified operator")
        if not note.strip():
            raise ValueError("acknowledging a data-service halt requires a note")
        if self._health.status is not ReaderStatus.UNHEALTHY:
            return
        _log.warning("live_reader.acknowledged", operator=operator, note=note)
        self._gateway = None
        self._health = replace(
            self._health,
            status=ReaderStatus.DISCONNECTED,
            connected=False,
            detail=f"acknowledged by {operator}: {note}",
        )
