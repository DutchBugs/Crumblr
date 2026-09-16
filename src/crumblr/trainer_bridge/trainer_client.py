"""HTTP client for the external Trainer's inbound MODE_2 ingestion endpoint
(`POST /api/v1/campaigns/{campaign_id}/agent-data`).

Deliberately stdlib-only (`urllib.request`), mirroring
`agent_gateway/static_agent_client.py`'s exact pattern for the same reason:
one narrow outbound POST needs nothing a third-party HTTP client would add,
and this project already has one proven, reviewed template for talking to
an external agent-shaped service over HTTP.

**No redirect is ever followed** -- same reasoning as the Static Agent
client: there is no legitimate reason this endpoint would ever redirect.

**A non-2xx status is still a real answer, not a transport failure.** The
Trainer's own `ScopeViolationError`/`ValidationError`/`ConflictError`
responses are meaningful JSON a caller must see, not something to hide
behind a raised exception. `TrainerTransportError` subclasses are raised
only when the call itself failed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 1024 * 1024


class TrainerTransportError(Exception):
    """Base for every failure calling the Trainer -- a caller that only
    cares "did this call itself fail" can catch this base class."""


class TrainerTimeoutError(TrainerTransportError):
    """The connection or a read exceeded `timeout_seconds`."""


class TrainerConnectionError(TrainerTransportError):
    """The request could not reach the server at all."""


class TrainerRedirectRefusedError(TrainerTransportError):
    """The server tried to redirect the request -- never followed."""


class TrainerResponseTooLargeError(TrainerTransportError):
    """The response body exceeded `max_response_bytes`."""


class TrainerInvalidResponseError(TrainerTransportError):
    """The response was not valid JSON, or not a JSON object."""


@dataclass(frozen=True)
class TrainerClientConfig:
    """Everything one call to the Trainer needs to know about how to reach
    it. `base_url` names one specific, operator-configured host -- never
    derived from anything in the request/response themselves."""

    base_url: str
    api_key: str | None = None
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
        raise TrainerRedirectRefusedError(f"refused redirect to {newurl!r} (status {code})")


def _send(
    config: TrainerClientConfig, *, method: str, url: str, body: bytes | None
) -> tuple[int, dict[str, Any]]:
    """Shared request/response handling for every call in this module.

    Returns `(status_code, decoded_json_body)` for any response the server
    actually sent. Raises a `TrainerTransportError` subclass only when the
    call itself failed -- see the module docstring.
    """
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if config.api_key is not None:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_RefuseRedirects())

    try:
        response: Any = opener.open(request, timeout=config.timeout_seconds)
    except TrainerRedirectRefusedError:
        raise
    except urllib.error.HTTPError as error:
        response = error
    except TimeoutError as error:
        raise TrainerTimeoutError(str(error)) from error
    except urllib.error.URLError as error:
        raise TrainerConnectionError(str(error)) from error

    try:
        with response:
            raw = response.read(config.max_response_bytes + 1)
            status_code = response.status
    except TimeoutError as error:
        raise TrainerTimeoutError(str(error)) from error
    except OSError as error:
        raise TrainerConnectionError(str(error)) from error

    if len(raw) > config.max_response_bytes:
        raise TrainerResponseTooLargeError(f"response exceeded {config.max_response_bytes} bytes")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrainerInvalidResponseError(f"response was not valid JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise TrainerInvalidResponseError("response JSON was not an object")
    return status_code, decoded


def post_agent_data(
    config: TrainerClientConfig,
    *,
    campaign_id: str,
    agent_id: str,
    result: dict[str, Any],
    source_reference: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """POST to `{base_url}/api/v1/campaigns/{campaign_id}/agent-data`.

    Returns `(status_code, decoded_json_body)` for any response the server
    actually sent. Raises a `TrainerTransportError` subclass only when the
    call itself failed -- see the module docstring.
    """
    body_dict: dict[str, Any] = {"agent_id": agent_id, "result": result}
    if source_reference is not None:
        body_dict["source_reference"] = source_reference
    body = json.dumps(body_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
    url = config.base_url.rstrip("/") + f"/api/v1/campaigns/{campaign_id}/agent-data"
    return _send(config, method="POST", url=url, body=body)


def get_candidate_artifact(
    config: TrainerClientConfig, *, campaign_id: str
) -> tuple[int, dict[str, Any]]:
    """GET `{base_url}/api/v1/campaigns/{campaign_id}/candidate-artifact`.

    Read-only. Returns `(status_code, decoded_json_body)` for any response
    the server actually sent -- Trainer's own `ConflictError` (no
    `RESEARCH_PROMISING` candidate exists yet, or the strategy has no
    executable `local_strategy`) is a real, meaningful answer here, not a
    transport failure. Raises a `TrainerTransportError` subclass only when
    the call itself failed -- see the module docstring.
    """
    url = config.base_url.rstrip("/") + f"/api/v1/campaigns/{campaign_id}/candidate-artifact"
    return _send(config, method="GET", url=url, body=None)
