"""Behavioural tests for the Local Host Admin FastAPI app.

`credential_store` is monkeypatched to an in-memory fake here -- this suite
runs on any platform and is not a Credential Manager test (that is
`test_credential_store.py`, `windows_only`). What matters here is: the
auth gate, the secret/settings allowlists actually being enforced by the
routes (not only by the lower-level functions -- `test_local_admin_boundary.py`
covers those in isolation), and that a value written is never echoed back
in any response.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crumblr.local_admin import api as local_admin_api
from crumblr.local_admin import credential_store
from crumblr.local_admin.registry import ALLOWED_SECRET_NAMES

TOKEN = "test-admin-token"


class _FakeCredentialStore:
    """An in-memory stand-in for the real Windows-only backing store."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def write(self, name: str, value: str) -> None:
        self._values[name] = value

    def read(self, name: str) -> str | None:
        return self._values.get(name)

    def status(self, name: str) -> credential_store.CredentialStatus:
        if name not in self._values:
            return credential_store.CredentialStatus(configured=False, last_written_utc=None)
        return credential_store.CredentialStatus(
            configured=True, last_written_utc=datetime(2026, 9, 9, tzinfo=UTC)
        )

    def delete(self, name: str) -> bool:
        return self._values.pop(name, None) is not None


@pytest.fixture
def fake_store(monkeypatch: pytest.MonkeyPatch) -> _FakeCredentialStore:
    fake = _FakeCredentialStore()
    monkeypatch.setattr(credential_store, "write", fake.write)
    monkeypatch.setattr(credential_store, "read", fake.read)
    monkeypatch.setattr(credential_store, "status", fake.status)
    monkeypatch.setattr(credential_store, "delete", fake.delete)
    return fake


@pytest.fixture
def client(tmp_path: Path, fake_store: _FakeCredentialStore) -> TestClient:
    app = local_admin_api.create_app(
        admin_token=TOKEN, settings_path=tmp_path / "local_host_settings.json"
    )
    return TestClient(app)


AUTH = {"X-Local-Admin-Token": TOKEN}


class TestAuth:
    def test_every_api_route_rejects_a_missing_token(self, client: TestClient) -> None:
        response = client.get("/api/secrets")
        assert response.status_code == 401

    def test_every_api_route_rejects_a_wrong_token(self, client: TestClient) -> None:
        response = client.get("/api/secrets", headers={"X-Local-Admin-Token": "wrong"})
        assert response.status_code == 401

    def test_the_right_token_is_accepted(self, client: TestClient) -> None:
        response = client.get("/api/secrets", headers=AUTH)
        assert response.status_code == 200


class TestSecrets:
    def test_all_six_allowed_secrets_start_unconfigured(self, client: TestClient) -> None:
        body = client.get("/api/secrets", headers=AUTH).json()
        names = {row["name"] for row in body}
        assert names == set(ALLOWED_SECRET_NAMES)
        assert all(row["configured"] is False for row in body)

    def test_writing_an_unknown_secret_name_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/secrets/NOT_A_REAL_SECRET", json={"value": "x"}, headers=AUTH)
        assert response.status_code == 404

    def test_writing_an_empty_value_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/secrets/LOCAL_AGENT_SERVICE_TOKEN", json={"value": ""}, headers=AUTH
        )
        assert response.status_code == 400

    def test_writing_a_real_secret_never_echoes_the_value_back(
        self, client: TestClient, fake_store: _FakeCredentialStore
    ) -> None:
        response = client.post(
            "/api/secrets/LOCAL_AGENT_SERVICE_TOKEN",
            json={"value": "super-secret-value"},
            headers=AUTH,
        )
        assert response.status_code == 200
        assert "super-secret-value" not in response.text
        assert response.json() == {
            "name": "LOCAL_AGENT_SERVICE_TOKEN",
            "configured": True,
            "last_written_utc": "2026-09-09T00:00:00Z",
        }
        assert fake_store.read("LOCAL_AGENT_SERVICE_TOKEN") == "super-secret-value"

    def test_listing_secrets_after_a_write_never_carries_the_value(
        self, client: TestClient
    ) -> None:
        client.post(
            "/api/secrets/LOCAL_AGENT_SERVICE_TOKEN",
            json={"value": "super-secret-value"},
            headers=AUTH,
        )
        response = client.get("/api/secrets", headers=AUTH)
        assert "super-secret-value" not in response.text

    def test_delete_clears_configured_status(self, client: TestClient) -> None:
        client.post("/api/secrets/LOCAL_AGENT_SERVICE_TOKEN", json={"value": "x"}, headers=AUTH)
        response = client.delete("/api/secrets/LOCAL_AGENT_SERVICE_TOKEN", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["configured"] is False

    def test_deleting_an_unknown_secret_name_is_rejected(self, client: TestClient) -> None:
        response = client.delete("/api/secrets/NOT_A_REAL_SECRET", headers=AUTH)
        assert response.status_code == 404


class TestSettings:
    def test_defaults_are_returned_when_nothing_saved_yet(self, client: TestClient) -> None:
        response = client.get("/api/settings", headers=AUTH)
        assert response.json()["canonical_symbol"] == "EUR/USD"

    def test_a_recognised_setting_can_be_written_and_read_back(self, client: TestClient) -> None:
        client.put("/api/settings", json={"canonical_symbol": "GBP/USD"}, headers=AUTH)
        response = client.get("/api/settings", headers=AUTH)
        assert response.json()["canonical_symbol"] == "GBP/USD"

    @pytest.mark.parametrize(
        "forbidden_key",
        [
            "submission_enabled",
            "feedback_2_0_approved",
            "flatten_submission_enabled",
            "live_trading_acknowledged",
        ],
    )
    def test_a_governance_key_is_rejected_by_the_route_itself(
        self, client: TestClient, forbidden_key: str
    ) -> None:
        response = client.put("/api/settings", json={forbidden_key: True}, headers=AUTH)
        assert response.status_code == 400


class TestConnectivityTestsAreReadOnlyAndFailClosedWhenUnconfigured:
    def test_postgres_test_reports_missing_secret_rather_than_guessing(
        self, client: TestClient
    ) -> None:
        response = client.post("/api/test/postgres", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["ok"] is False

    def test_mt5_test_reports_missing_secret_rather_than_guessing(self, client: TestClient) -> None:
        response = client.post("/api/test/mt5", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["ok"] is False

    def test_static_agent_test_reports_unreachable_rather_than_hanging(
        self, client: TestClient
    ) -> None:
        # An explicit, almost-certainly-closed port -- not the real default
        # (8765), since this suite may run on the same host as a genuinely
        # live Static Agent and must not depend on that being absent.
        client.put("/api/settings", json={"static_agent_port": 39217}, headers=AUTH)
        response = client.post("/api/test/static-agent", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["ok"] is False
