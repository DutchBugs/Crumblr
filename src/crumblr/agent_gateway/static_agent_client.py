"""HTTP client for the Static Agent fork's inbound API
(`POST /v1/trader/evaluate`) -- feedback.1.27 section 6 item C: "a Static
Agent HTTP client with strict timeout, response-size limit, schema
validation, no redirects to arbitrary hosts, and fail-closed handling."

Deliberately stdlib-only (`urllib.request`), not a new project dependency
-- `httpx2` is a dev/test-only dependency (`pyproject.toml`
`[dependency-groups] dev`, not `[project] dependencies`), unavailable in
production, and this is one narrow outbound POST that needs nothing a
third-party client would add. Mirrors the fork's own zero-dependency
philosophy (`crumblr-static-agent-host`'s `pyproject.toml`:
`dependencies = []`).

**No redirect is ever followed.** There is no legitimate reason
`/v1/trader/evaluate` would ever redirect, and silently following one
could send Crumblr's context payload somewhere nobody authorized --
refused entirely via a custom `HTTPRedirectHandler`, never treated as an
ordinary response.

**A non-2xx status is still a real answer, not a transport failure.**
`evaluate()` returns `(status_code, decoded_json_body)` for anything the
server actually answered with, 2xx or 4xx alike -- the fork's own
rejection envelope (`422`/`400`/`401`/`413`/`415`, `api.py::rejection()`)
is meaningful JSON a caller must see and translate, not something to hide
behind a raised exception. `StaticAgentTransportError` subclasses are
raised only when the call itself failed: timeout, connection refused, a
refused redirect, an oversized response, or a response that was not valid
JSON.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 1024 * 1024
"""Matches the fork's own inbound cap (`api.py::MAX_REQUEST_BYTES`) --
there is no reason a `TraderDecision` response would ever need to be
larger than the request the fork itself refuses past this size."""


class StaticAgentTransportError(Exception):
    """Base for every failure calling the Static Agent -- a caller that
    only cares "did this call itself fail" can catch this base class."""


class StaticAgentTimeoutError(StaticAgentTransportError):
    """The connection or a read exceeded `timeout_seconds`."""


class StaticAgentConnectionError(StaticAgentTransportError):
    """The request could not reach the server at all (DNS, refused, TLS)."""


class StaticAgentRedirectRefusedError(StaticAgentTransportError):
    """The server tried to redirect the request -- never followed."""


class StaticAgentResponseTooLargeError(StaticAgentTransportError):
    """The response body exceeded `max_response_bytes`."""


class StaticAgentInvalidResponseError(StaticAgentTransportError):
    """The response was not valid JSON, or not a JSON object."""


@dataclass(frozen=True)
class StaticAgentClientConfig:
    """Everything one call to the Static Agent needs to know about how to
    reach it. `base_url` names one specific, operator-configured host --
    never derived from anything in the request/response themselves."""

    base_url: str
    bearer_token: str | None
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = MAX_RESPONSE_BYTES


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise StaticAgentRedirectRefusedError(f"refused redirect to {newurl!r} (status {code})")


def evaluate(
    config: StaticAgentClientConfig, context_payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """POST `context_payload` to `{base_url}/v1/trader/evaluate`.

    Returns `(status_code, decoded_json_body)` for any response the server
    actually sent. Raises a `StaticAgentTransportError` subclass only when
    the call itself failed -- see the module docstring.
    """
    body = json.dumps(context_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    url = config.base_url.rstrip("/") + "/v1/trader/evaluate"
    headers = {"Content-Type": "application/json"}
    if config.bearer_token is not None:
        headers["Authorization"] = f"Bearer {config.bearer_token}"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    opener = urllib.request.build_opener(_RefuseRedirects())

    try:
        response: Any = opener.open(request, timeout=config.timeout_seconds)
    except StaticAgentRedirectRefusedError:
        raise
    except urllib.error.HTTPError as error:
        # A non-2xx status IS a real answer in urllib's model -- still a
        # response to decode, not a transport failure.
        response = error
    except TimeoutError as error:
        raise StaticAgentTimeoutError(str(error)) from error
    except urllib.error.URLError as error:
        raise StaticAgentConnectionError(str(error)) from error

    try:
        with response:
            raw = response.read(config.max_response_bytes + 1)
            status_code = response.status
    except TimeoutError as error:
        # `opener.open()` above only covers connect/headers -- a server
        # that answers promptly but stalls partway through streaming the
        # body raises here instead (self-review finding).
        raise StaticAgentTimeoutError(str(error)) from error
    except OSError as error:
        raise StaticAgentConnectionError(str(error)) from error

    if len(raw) > config.max_response_bytes:
        raise StaticAgentResponseTooLargeError(
            f"response exceeded {config.max_response_bytes} bytes"
        )
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StaticAgentInvalidResponseError(f"response was not valid JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise StaticAgentInvalidResponseError("response JSON was not an object")
    return status_code, decoded
