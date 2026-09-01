"""agent_gateway/static_agent_client.py -- the outbound HTTP client for
the Static Agent fork's `POST /v1/trader/evaluate`.

Uses a small local `http.server`-based test double, not a mocking
library (none is a dev dependency) and not the actual
`DutchBugs/crumblr-static-agent-host` fork -- that round trip was
separately, manually verified end-to-end (real bearer auth, a real
running server, a genuine 200 with the expected `NO_TRADE` decision and a
genuine 401 on a wrong token) and is recorded in `review/AGENT_STATUS.md`,
not repeated here as an automated dependency.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from crumblr.agent_gateway.static_agent_client import (
    StaticAgentClientConfig,
    StaticAgentInvalidResponseError,
    StaticAgentRedirectRefusedError,
    StaticAgentResponseTooLargeError,
    StaticAgentTimeoutError,
    evaluate,
)

_REQUESTS: list[dict[str, Any]] = []


def _handler_factory(*, status: int, body: bytes, headers: dict[str, str] | None = None) -> type:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, log_format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            _REQUESTS.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "body": raw,
                }
            )
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _slow_handler_factory(*, delay_seconds: float) -> type:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, log_format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            time.sleep(delay_seconds)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _slow_body_handler_factory(*, delay_seconds: float) -> type:
    """Unlike `_slow_handler_factory`, headers/status are sent *promptly*
    and only the body write stalls -- reproduces the self-review finding
    that a stall during `response.read()` (not `opener.open()`) escaped as
    a bare `TimeoutError` instead of `StaticAgentTimeoutError`."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, log_format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.flush()
            time.sleep(delay_seconds)
            self.wfile.write(body)

    return Handler


def _redirect_handler_factory(*, location: str) -> type:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, log_format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


class _RunningServer:
    def __init__(self, handler_class: type) -> None:
        self.server = HTTPServer(("127.0.0.1", 0), handler_class)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[0], self.server.server_address[1]
        return f"http://{host!s}:{port}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def server_factory() -> Any:
    servers: list[_RunningServer] = []

    def _make(handler_class: type) -> _RunningServer:
        server = _RunningServer(handler_class)
        servers.append(server)
        return server

    yield _make
    for server in servers:
        server.stop()


PAYLOAD = {"schema_version": "1.0", "example": "payload"}


class TestSuccessfulResponses:
    def test_a_2xx_response_is_returned_not_raised(self, server_factory: Any) -> None:
        body = json.dumps({"decision_type": "NO_TRADE"}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = StaticAgentClientConfig(base_url=server.base_url, bearer_token="secret-token")

        status, decoded = evaluate(config, PAYLOAD)

        assert status == 200
        assert decoded == {"decision_type": "NO_TRADE"}

    def test_the_bearer_token_and_content_type_are_sent(self, server_factory: Any) -> None:
        _REQUESTS.clear()
        body = json.dumps({"ok": True}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = StaticAgentClientConfig(base_url=server.base_url, bearer_token="secret-token")

        evaluate(config, PAYLOAD)

        assert _REQUESTS[-1]["authorization"] == "Bearer secret-token"
        assert _REQUESTS[-1]["content_type"] == "application/json"
        assert json.loads(_REQUESTS[-1]["body"]) == PAYLOAD

    def test_a_4xx_rejection_envelope_is_returned_not_raised(self, server_factory: Any) -> None:
        """The fork's own rejection envelope (`api.py::rejection()`) is
        meaningful JSON a caller must see and translate -- not a transport
        failure."""
        body = json.dumps({"result": "REJECTED", "error_code": "TRADER_CONTEXT_INVALID"}).encode(
            "utf-8"
        )
        server = server_factory(_handler_factory(status=422, body=body))
        config = StaticAgentClientConfig(base_url=server.base_url, bearer_token=None)

        status, decoded = evaluate(config, PAYLOAD)

        assert status == 422
        assert decoded["error_code"] == "TRADER_CONTEXT_INVALID"


class TestRefusals:
    def test_a_redirect_is_never_followed(self, server_factory: Any) -> None:
        server = server_factory(_redirect_handler_factory(location="http://evil.example/steal"))
        config = StaticAgentClientConfig(base_url=server.base_url, bearer_token=None)

        with pytest.raises(StaticAgentRedirectRefusedError):
            evaluate(config, PAYLOAD)

    def test_a_response_over_the_size_limit_is_refused(self, server_factory: Any) -> None:
        oversized = json.dumps({"padding": "x" * 100}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=oversized))
        config = StaticAgentClientConfig(
            base_url=server.base_url, bearer_token=None, max_response_bytes=10
        )

        with pytest.raises(StaticAgentResponseTooLargeError):
            evaluate(config, PAYLOAD)

    def test_a_non_json_response_is_refused(self, server_factory: Any) -> None:
        server = server_factory(_handler_factory(status=200, body=b"not json at all"))
        config = StaticAgentClientConfig(base_url=server.base_url, bearer_token=None)

        with pytest.raises(StaticAgentInvalidResponseError):
            evaluate(config, PAYLOAD)

    def test_a_json_array_response_is_refused(self, server_factory: Any) -> None:
        server = server_factory(_handler_factory(status=200, body=b"[1, 2, 3]"))
        config = StaticAgentClientConfig(base_url=server.base_url, bearer_token=None)

        with pytest.raises(StaticAgentInvalidResponseError):
            evaluate(config, PAYLOAD)

    def test_a_slow_server_times_out(self, server_factory: Any) -> None:
        server = server_factory(_slow_handler_factory(delay_seconds=1.0))
        config = StaticAgentClientConfig(
            base_url=server.base_url, bearer_token=None, timeout_seconds=0.1
        )

        with pytest.raises(StaticAgentTimeoutError):
            evaluate(config, PAYLOAD)

    def test_a_stall_mid_body_also_raises_the_typed_timeout(self, server_factory: Any) -> None:
        server = server_factory(_slow_body_handler_factory(delay_seconds=1.0))
        config = StaticAgentClientConfig(
            base_url=server.base_url, bearer_token=None, timeout_seconds=0.1
        )

        with pytest.raises(StaticAgentTimeoutError):
            evaluate(config, PAYLOAD)
