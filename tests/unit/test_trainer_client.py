"""trainer_bridge/trainer_client.py -- the outbound HTTP client for the
external Trainer's `POST /api/v1/campaigns/{campaign_id}/agent-data`.

Uses a small local `http.server`-based test double, the same pattern
`test_static_agent_client.py` already established for the symmetric
outbound call to the Static Agent -- no mocking library, and not the real
`DutchBugs/crumblr-trainer` service (that round trip was separately,
manually verified end-to-end against a real running Trainer API: a genuine
HTTP 200 with `data_provenance.origin=crumblr_agent`, a genuine 409 on
`GET .../candidate-artifact`, both recorded in `status.md`, not repeated
here as an automated dependency).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from crumblr.trainer_bridge.trainer_client import (
    TrainerClientConfig,
    TrainerInvalidResponseError,
    TrainerRedirectRefusedError,
    TrainerResponseTooLargeError,
    get_candidate_artifact,
    post_agent_data,
)

_REQUESTS: list[dict[str, Any]] = []


def _handler_factory(*, status: int, body: bytes) -> type:
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
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            _REQUESTS.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "content_type": None,
                    "body": b"",
                }
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
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

        def do_GET(self) -> None:
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


RESULT = {"returns_r": [0.5], "trade_ids": ["crumblr:abc"], "transaction_costs_included": False}


class TestSuccessfulResponses:
    def test_a_2xx_response_is_returned_not_raised(self, server_factory: Any) -> None:
        body = json.dumps({"status": "ACTIVE"}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = TrainerClientConfig(base_url=server.base_url)

        status, decoded = post_agent_data(
            config, campaign_id="CAM-1", agent_id="crumblr-fixture", result=RESULT
        )

        assert status == 200
        assert decoded == {"status": "ACTIVE"}

    def test_posts_to_the_exact_agent_data_path(self, server_factory: Any) -> None:
        _REQUESTS.clear()
        body = json.dumps({"ok": True}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = TrainerClientConfig(base_url=server.base_url)

        post_agent_data(config, campaign_id="CAM-DEMO", agent_id="agent-x", result=RESULT)

        assert _REQUESTS[-1]["path"] == "/api/v1/campaigns/CAM-DEMO/agent-data"

    def test_the_body_carries_agent_id_result_and_source_reference(
        self, server_factory: Any
    ) -> None:
        _REQUESTS.clear()
        body = json.dumps({"ok": True}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = TrainerClientConfig(base_url=server.base_url)

        post_agent_data(
            config,
            campaign_id="CAM-1",
            agent_id="agent-x",
            result=RESULT,
            source_reference='{"capsule_id":"abc"}',
        )

        sent = json.loads(_REQUESTS[-1]["body"])
        assert sent["agent_id"] == "agent-x"
        assert sent["result"] == RESULT
        assert sent["source_reference"] == '{"capsule_id":"abc"}'

    def test_source_reference_is_omitted_when_not_supplied(self, server_factory: Any) -> None:
        _REQUESTS.clear()
        body = json.dumps({"ok": True}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = TrainerClientConfig(base_url=server.base_url)

        post_agent_data(config, campaign_id="CAM-1", agent_id="agent-x", result=RESULT)

        sent = json.loads(_REQUESTS[-1]["body"])
        assert "source_reference" not in sent

    def test_the_api_key_becomes_a_bearer_header(self, server_factory: Any) -> None:
        _REQUESTS.clear()
        body = json.dumps({"ok": True}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = TrainerClientConfig(base_url=server.base_url, api_key="secret")

        post_agent_data(config, campaign_id="CAM-1", agent_id="agent-x", result=RESULT)

        assert _REQUESTS[-1]["authorization"] == "Bearer secret"

    def test_a_4xx_scope_violation_is_returned_not_raised(self, server_factory: Any) -> None:
        """Trainer's own `ScopeViolationError`/`ConflictError` responses are
        meaningful JSON a caller must see and translate, not a transport
        failure -- e.g. posting into a campaign not in MODE_2."""
        body = json.dumps(
            {"error": "ScopeViolationError", "message": "Crumblr-agentdata is alleen ..."}
        ).encode("utf-8")
        server = server_factory(_handler_factory(status=409, body=body))
        config = TrainerClientConfig(base_url=server.base_url)

        status, decoded = post_agent_data(
            config, campaign_id="CAM-1", agent_id="agent-x", result=RESULT
        )

        assert status == 409
        assert decoded["error"] == "ScopeViolationError"


class TestGetCandidateArtifact:
    def test_a_2xx_response_is_returned_not_raised(self, server_factory: Any) -> None:
        body = json.dumps({"artifact_state": "UNAPPROVED_CANDIDATE"}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = TrainerClientConfig(base_url=server.base_url)

        status, decoded = get_candidate_artifact(config, campaign_id="CAM-1")

        assert status == 200
        assert decoded == {"artifact_state": "UNAPPROVED_CANDIDATE"}

    def test_gets_the_exact_candidate_artifact_path(self, server_factory: Any) -> None:
        _REQUESTS.clear()
        body = json.dumps({"ok": True}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = TrainerClientConfig(base_url=server.base_url)

        get_candidate_artifact(config, campaign_id="CAM-DEMO")

        assert _REQUESTS[-1]["path"] == "/api/v1/campaigns/CAM-DEMO/candidate-artifact"

    def test_the_request_carries_no_body(self, server_factory: Any) -> None:
        _REQUESTS.clear()
        body = json.dumps({"ok": True}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = TrainerClientConfig(base_url=server.base_url)

        get_candidate_artifact(config, campaign_id="CAM-1")

        assert _REQUESTS[-1]["body"] == b""

    def test_the_api_key_becomes_a_bearer_header(self, server_factory: Any) -> None:
        _REQUESTS.clear()
        body = json.dumps({"ok": True}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=body))
        config = TrainerClientConfig(base_url=server.base_url, api_key="secret")

        get_candidate_artifact(config, campaign_id="CAM-1")

        assert _REQUESTS[-1]["authorization"] == "Bearer secret"

    def test_a_409_conflict_is_returned_not_raised(self, server_factory: Any) -> None:
        """Trainer's own `ConflictError` (no `RESEARCH_PROMISING` candidate
        exists yet, or the strategy has no executable `local_strategy`) is a
        real, meaningful answer here, not a transport failure."""
        body = json.dumps(
            {"error": "ConflictError", "message": "No RESEARCH_PROMISING candidate exists"}
        ).encode("utf-8")
        server = server_factory(_handler_factory(status=409, body=body))
        config = TrainerClientConfig(base_url=server.base_url)

        status, decoded = get_candidate_artifact(config, campaign_id="CAM-1")

        assert status == 409
        assert decoded["error"] == "ConflictError"

    def test_a_redirect_is_never_followed(self, server_factory: Any) -> None:
        server = server_factory(_redirect_handler_factory(location="http://evil.example/steal"))
        config = TrainerClientConfig(base_url=server.base_url)

        with pytest.raises(TrainerRedirectRefusedError):
            get_candidate_artifact(config, campaign_id="CAM-1")

    def test_a_response_over_the_size_limit_is_refused(self, server_factory: Any) -> None:
        oversized = json.dumps({"padding": "x" * 100}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=oversized))
        config = TrainerClientConfig(base_url=server.base_url, max_response_bytes=10)

        with pytest.raises(TrainerResponseTooLargeError):
            get_candidate_artifact(config, campaign_id="CAM-1")

    def test_a_non_json_response_is_refused(self, server_factory: Any) -> None:
        server = server_factory(_handler_factory(status=200, body=b"not json"))
        config = TrainerClientConfig(base_url=server.base_url)

        with pytest.raises(TrainerInvalidResponseError):
            get_candidate_artifact(config, campaign_id="CAM-1")


class TestRefusals:
    def test_a_redirect_is_never_followed(self, server_factory: Any) -> None:
        server = server_factory(_redirect_handler_factory(location="http://evil.example/steal"))
        config = TrainerClientConfig(base_url=server.base_url)

        with pytest.raises(TrainerRedirectRefusedError):
            post_agent_data(config, campaign_id="CAM-1", agent_id="agent-x", result=RESULT)

    def test_a_response_over_the_size_limit_is_refused(self, server_factory: Any) -> None:
        oversized = json.dumps({"padding": "x" * 100}).encode("utf-8")
        server = server_factory(_handler_factory(status=200, body=oversized))
        config = TrainerClientConfig(base_url=server.base_url, max_response_bytes=10)

        with pytest.raises(TrainerResponseTooLargeError):
            post_agent_data(config, campaign_id="CAM-1", agent_id="agent-x", result=RESULT)

    def test_a_non_json_response_is_refused(self, server_factory: Any) -> None:
        server = server_factory(_handler_factory(status=200, body=b"not json"))
        config = TrainerClientConfig(base_url=server.base_url)

        with pytest.raises(TrainerInvalidResponseError):
            post_agent_data(config, campaign_id="CAM-1", agent_id="agent-x", result=RESULT)
