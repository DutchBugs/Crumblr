"""The thin wrapper around the official `MetaTrader5` package (build.md §7).

This is the only module in the platform permitted to import `MetaTrader5`, and
the import is deferred to call time so the rest of the codebase — and the whole
test suite — runs on macOS and Linux where the package does not exist.

Every call goes through `_checked`, which turns MT5's convention of returning
`None`/`False` and leaving the reason in `last_error()` into an exception that
names what failed. Silently continuing past a failed call is how a gateway ends
up reporting an account it never actually read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol, cast

from crumblr.observability.logging import get_logger

_log = get_logger("mt5_client")


class Mt5Module(Protocol):
    """The subset of the `MetaTrader5` package this gateway uses.

    Declared as a protocol so tests can supply a fake. The real package is a C
    extension with no type information; pinning the surface we depend on here
    means a change in that surface fails at our boundary rather than deep in a
    call chain.
    """

    def initialize(self, *args: Any, **kwargs: Any) -> bool: ...
    def login(self, *args: Any, **kwargs: Any) -> bool: ...
    def shutdown(self) -> None: ...
    def last_error(self) -> tuple[int, str]: ...
    def version(self) -> tuple[Any, ...] | None: ...
    def terminal_info(self) -> Any: ...
    def account_info(self) -> Any: ...
    def symbols_get(self, *args: Any, **kwargs: Any) -> tuple[Any, ...] | None: ...
    def symbol_select(self, symbol: str, enable: bool) -> bool: ...
    def symbol_info(self, symbol: str) -> Any: ...
    def symbol_info_tick(self, symbol: str) -> Any: ...
    def copy_rates_from_pos(self, *args: Any, **kwargs: Any) -> Any: ...
    def copy_ticks_from(self, *args: Any, **kwargs: Any) -> Any: ...
    def positions_get(self, *args: Any, **kwargs: Any) -> tuple[Any, ...] | None: ...
    def orders_get(self, *args: Any, **kwargs: Any) -> tuple[Any, ...] | None: ...
    # Server-side dry run: validates a request without creating a ticket or
    # any market exposure. Phase 4 (`mt5_gateway/execution.py`) is the only
    # caller — `order_send` is not part of this protocol, deliberately, so
    # there is nothing here for a future caller to reach for by accident.
    def order_check(self, request: dict[str, Any]) -> Any: ...

    # Request-parameter constants for copy_ticks_from / copy_rates_from_pos.
    # Declared here rather than hardcoded at the call site: these are read off
    # the real package at run time, not guessed from documentation — the same
    # discipline D-037 exists to enforce for values read *from* MT5 applies
    # just as much to values passed *to* it.
    COPY_TICKS_ALL: int
    TIMEFRAME_M1: int
    TIMEFRAME_M5: int
    TIMEFRAME_M15: int
    TIMEFRAME_M30: int
    TIMEFRAME_H1: int
    TIMEFRAME_H4: int
    TIMEFRAME_D1: int

    # order_check request-parameter constants (Phase 4).
    TRADE_ACTION_DEAL: int
    ORDER_TYPE_BUY: int
    ORDER_TYPE_SELL: int
    ORDER_TIME_GTC: int
    ORDER_FILLING_IOC: int
    TRADE_RETCODE_DONE: int


class Mt5UnavailableError(RuntimeError):
    """The MetaTrader5 package is not importable on this host."""


class Mt5CallFailedError(RuntimeError):
    """An MT5 call failed. Carries the terminal's own error code and message."""

    def __init__(self, operation: str, code: int, message: str) -> None:
        super().__init__(f"MT5 {operation} failed: [{code}] {message}")
        self.operation = operation
        self.code = code
        self.message = message


def load_mt5_module() -> Mt5Module:
    """Import the real package, or explain precisely why it is unavailable.

    The official distribution ships Windows x86-64 wheels only, so this raising
    on macOS or Linux is expected rather than a fault — see `HANDOVER.md` §8.
    """
    try:
        # Deferred to call time so the rest of the platform runs off-Windows.
        import MetaTrader5
    except ImportError as error:
        raise Mt5UnavailableError(
            "the MetaTrader5 package is not importable here. It ships Windows "
            "x86-64 wheels only; the gateway must run on the Windows host "
            f"(original error: {error})"
        ) from error
    # The package is a C extension with no type information; the protocol
    # above is our declaration of the surface we depend on.
    return cast("Mt5Module", MetaTrader5)


@dataclass(frozen=True)
class Mt5Credentials:
    """Login details. Held only inside the gateway process (build.md §21).

    `__repr__` is overridden because a dataclass would otherwise print the
    password into any log line, traceback or debugger that touched it.
    """

    login: int
    password: str
    server: str

    def __repr__(self) -> str:
        return (
            f"Mt5Credentials(login={mask_login(self.login)}, server={self.server!r}, "
            "password=<redacted>)"
        )


def mask_login(login: int) -> str:
    """The account number as it may appear in ordinary logs (review 1.11 F-031).

    Not a password, but not useful to broadcast either: review 1.10 accepted
    that the login is "an identifier, not a secret" for `Mt5Credentials`, but
    review 1.11 reopened F-031 because that same identifier showed up whole in
    `mt5.connected` and `mt5.account_guard_failed` — exactly the shared/review
    logs the project already agreed it must not enter. Keeping the last three
    digits is enough to eyeball-match against the real number during an
    incident without the full value sitting in a routine log line.
    """
    digits = str(login)
    return f"***{digits[-3:]}" if len(digits) > 3 else "***"


class MissingCredentialsError(RuntimeError):
    """The environment does not carry a full set of MT5 credentials."""


def read_credentials(environ: dict[str, str] | None = None) -> Mt5Credentials:
    """Assemble credentials from the environment, or say exactly what is absent.

    build.md §21 keeps these out of the repository. On a workstation they come
    from `.env`; in production from the Windows Credential Manager or a secret
    manager. Either way this process is the only one that sees them.

    Shared by `scripts/mt5_probe.py` and `scripts/mt5_live_reader.py` rather
    than duplicated — the same lesson D-037 taught about decode tables applies
    to a credential-shaped helper: two copies are two chances to drift.
    """
    env = os.environ if environ is None else environ
    missing = [
        name
        for name in ("CRUMBLR_MT5_LOGIN", "CRUMBLR_MT5_PASSWORD", "CRUMBLR_MT5_SERVER")
        if not env.get(name)
    ]
    if missing:
        raise MissingCredentialsError(
            "missing " + ", ".join(missing) + ". Copy .env.example to .env and fill it in, "
            "or export them from the secret store. They never belong in config/."
        )
    return Mt5Credentials(
        login=int(env["CRUMBLR_MT5_LOGIN"]),
        password=env["CRUMBLR_MT5_PASSWORD"],
        server=env["CRUMBLR_MT5_SERVER"],
    )


class Mt5Client:
    """Connection management for one terminal.

    Holds the credentials and nothing else of consequence. Everything that
    interprets MT5 data lives a layer up, in the gateway, so this stays small
    enough to reason about without a Windows machine in front of you.
    """

    def __init__(self, module: Mt5Module | None = None) -> None:
        self._module = module
        self._connected = False

    @property
    def module(self) -> Mt5Module:
        if self._module is None:
            self._module = load_mt5_module()
        return self._module

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self, credentials: Mt5Credentials, *, terminal_path: str | None = None) -> None:
        """Initialise the terminal and log in.

        Both steps are checked. `initialize` succeeding does not imply the login
        did, and a gateway that assumed it would happily read an account that
        belongs to somebody else.
        """
        module = self.module
        initialise_args: dict[str, Any] = {}
        if terminal_path:
            initialise_args["path"] = terminal_path

        if not module.initialize(**initialise_args):
            code, message = module.last_error()
            raise Mt5CallFailedError("initialize", code, message)

        if not module.login(
            credentials.login, password=credentials.password, server=credentials.server
        ):
            code, message = module.last_error()
            module.shutdown()
            raise Mt5CallFailedError("login", code, message)

        self._connected = True
        # review 1.11 F-031: the full login does not belong in a routine log
        # line even though it is an identifier rather than a secret - this is
        # exactly the "shared logs" case the project already ruled out.
        _log.info(
            "mt5.connected",
            account_ref=mask_login(credentials.login),
            server=credentials.server,
        )

    def disconnect(self) -> None:
        """Shut the terminal connection down. Safe to call when not connected.

        build.md §7 invariant 10: shutdown does not imply liquidation. This
        closes a connection and nothing else.
        """
        if self._connected:
            self.module.shutdown()
            self._connected = False
            _log.info("mt5.disconnected")

    def checked(self, operation: str, value: Any) -> Any:
        """Return `value`, or raise with the terminal's reason if it is empty.

        MT5 signals failure by returning `None` or `False` and leaving the
        reason in `last_error()`. Treating that as data is how a caller ends up
        with an empty tuple of positions and no idea whether the account is
        genuinely flat or the call simply failed.
        """
        if value is None or value is False:
            code, message = self.module.last_error()
            raise Mt5CallFailedError(operation, code, message)
        return value

    def __enter__(self) -> Mt5Client:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.disconnect()
