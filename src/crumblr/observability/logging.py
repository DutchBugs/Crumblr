"""Structured logging baseline (build.md §26 deliverable, review finding F-013).

Three rules shape this module.

**Logs are observability, never state.** The event journal is the authoritative
record of what the system decided; these logs exist so a person can watch it
work. A log line that fails to be written must never change a trading outcome,
so every emission is wrapped and cannot raise into the caller.

**Logs go to stderr, reports go to stdout.** The replay report is piped and
hashed as a determinism gate. Interleaving log lines into that stream would
break the gate, and worse, would break it non-deterministically.

**Secrets cannot be logged.** Credential-shaped keys are redacted in a
processor rather than at each call site, because relying on every future caller
to remember is not a control.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, MutableMapping
from contextlib import suppress
from typing import IO, Any, Final

import structlog

SERVICE_NAME: Final = "crumblr"

REDACTED: Final = "[redacted]"

_SECRET_KEY_MARKERS: Final = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "passwd",
    "private_key",
)

_configured = False


def _redact_secrets(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> Mapping[str, Any]:
    """Replace credential-shaped values anywhere in the record.

    Matching is on the key, not the value: a value that looks like a password
    is indistinguishable from one that is not, but a field *named* password is
    unambiguous. build.md §21 keeps secrets out of logs entirely.
    """
    return {key: _redact_value(key, value) for key, value in event_dict.items()}


def _redact_value(key: str, value: Any) -> Any:
    if _is_secret_key(key):
        return REDACTED
    if isinstance(value, Mapping):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(key, item) for item in value)
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def configure_logging(
    *,
    level: str = "INFO",
    stream: IO[str] | None = None,
    json_output: bool = True,
    service: str = SERVICE_NAME,
) -> None:
    """Set up structured logging. Safe to call more than once.

    Needs no external infrastructure: it writes to a stream. Prometheus, Loki
    and OpenTelemetry are production concerns and are explicitly not required
    at M0.
    """
    global _configured

    destination = stream if stream is not None else sys.stderr
    logging.basicConfig(
        format="%(message)s",
        stream=destination,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )

    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer(sort_keys=True)
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            # UTC, ISO-8601, and named `timestamp` in every record.
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_secrets,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    structlog.contextvars.bind_contextvars(service=service)
    _configured = True


def is_configured() -> bool:
    return _configured


class ComponentLogger:
    """A logger that cannot break the thing it is observing.

    Every method swallows its own exceptions. A full disk, a closed stream or a
    value that will not serialise must not propagate into the risk engine — the
    cost of a lost log line is far below the cost of an exception thrown from
    inside a halt.
    """

    def __init__(self, component: str) -> None:
        self._component = component
        self._logger = structlog.get_logger().bind(component=component)

    @property
    def component(self) -> str:
        return self._component

    def bind(self, **values: Any) -> ComponentLogger:
        """Return a logger carrying extra fields, e.g. a correlation id."""
        bound = ComponentLogger(self._component)
        # Observability must never raise into the thing it observes.
        with suppress(Exception):
            bound._logger = self._logger.bind(**values)
        return bound

    def debug(self, event: str, **values: Any) -> None:
        self._emit("debug", event, values)

    def info(self, event: str, **values: Any) -> None:
        self._emit("info", event, values)

    def warning(self, event: str, **values: Any) -> None:
        self._emit("warning", event, values)

    def error(self, event: str, **values: Any) -> None:
        self._emit("error", event, values)

    def exception(self, event: str, **values: Any) -> None:
        self._emit("exception", event, values)

    def _emit(self, method: str, event: str, values: dict[str, Any]) -> None:
        # Deliberately broad: see the class docstring. A lost log line costs
        # far less than an exception thrown from inside a halt.
        with suppress(Exception):
            getattr(self._logger, method)(event, **values)


def get_logger(component: str) -> ComponentLogger:
    """A logger bound to one component of the platform."""
    if not _configured:
        configure_logging()
    return ComponentLogger(component)
