"""`scripts/agent_canary_execution.py`'s gateway-credential/agent-token

env-var *name* selection (Dev 1 review BLOCK fix, FEEDBACK.2.0 DEMO
EXECUTION): a run against a different registered AgentIdentity (the
deterministic canary fixture) must never silently read the existing
Stage C/ICT Agent's own credential/token. Pure env-var-name-to-value
resolution only -- no MT5, no DB, no HTTP.
"""

from __future__ import annotations

import pytest
from scripts.agent_canary_execution import (
    AGENT_TOKEN_ENV,
    GATEWAY_CREDENTIAL_ENV,
    _resolve_gateway_secrets,
    parse_args,
)

_REQUIRED_ARGV = [
    "--agent-id",
    "760e93be-117c-48a3-b997-f258055ec29b",
    "--assignment-id",
    "f98c0396-dd13-4a99-b1e9-b83ea0f15ed7",
    "--agent-url",
    "http://127.0.0.1:8766",
    "--code-commit",
    "deadbeef",
]


class TestParseArgsDefaultsMatchExistingIctVars:
    def test_omitting_both_flags_yields_the_existing_ict_env_var_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("sys.argv", ["agent_canary_execution.py", *_REQUIRED_ARGV])
        args = parse_args()
        assert args.gateway_credential_env == GATEWAY_CREDENTIAL_ENV
        assert args.agent_token_env == AGENT_TOKEN_ENV
        assert args.gateway_credential_env == "CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL"
        assert args.agent_token_env == "CRUMBLR_PAPER_LITE_AGENT_TOKEN"

    def test_explicit_flags_override_the_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "sys.argv",
            [
                "agent_canary_execution.py",
                *_REQUIRED_ARGV,
                "--gateway-credential-env",
                "CRUMBLR_DEMO_CANARY_GATEWAY_CREDENTIAL",
                "--agent-token-env",
                "LOCAL_AGENT_SERVICE_TOKEN",
            ],
        )
        args = parse_args()
        assert args.gateway_credential_env == "CRUMBLR_DEMO_CANARY_GATEWAY_CREDENTIAL"
        assert args.agent_token_env == "LOCAL_AGENT_SERVICE_TOKEN"


class TestResolveGatewaySecrets:
    def test_defaults_preserve_existing_ict_paper_lite_behaviour(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(GATEWAY_CREDENTIAL_ENV, "ict-credential-value")
        monkeypatch.setenv(AGENT_TOKEN_ENV, "ict-token-value")
        resolved = _resolve_gateway_secrets(
            gateway_credential_env=GATEWAY_CREDENTIAL_ENV,
            agent_token_env=AGENT_TOKEN_ENV,
        )
        assert resolved == ("ict-credential-value", "ict-token-value")

    def test_custom_gateway_credential_env_name_is_honored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(GATEWAY_CREDENTIAL_ENV, raising=False)
        monkeypatch.setenv("CRUMBLR_DEMO_CANARY_GATEWAY_CREDENTIAL", "fixture-credential-value")
        monkeypatch.setenv(AGENT_TOKEN_ENV, "ict-token-value")
        resolved = _resolve_gateway_secrets(
            gateway_credential_env="CRUMBLR_DEMO_CANARY_GATEWAY_CREDENTIAL",
            agent_token_env=AGENT_TOKEN_ENV,
        )
        assert resolved == ("fixture-credential-value", "ict-token-value")

    def test_custom_agent_token_env_name_is_honored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(GATEWAY_CREDENTIAL_ENV, "ict-credential-value")
        monkeypatch.delenv(AGENT_TOKEN_ENV, raising=False)
        monkeypatch.setenv("LOCAL_AGENT_SERVICE_TOKEN", "fixture-token-value")
        resolved = _resolve_gateway_secrets(
            gateway_credential_env=GATEWAY_CREDENTIAL_ENV,
            agent_token_env="LOCAL_AGENT_SERVICE_TOKEN",
        )
        assert resolved == ("ict-credential-value", "fixture-token-value")

    def test_missing_selected_gateway_credential_env_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CRUMBLR_DEMO_CANARY_GATEWAY_CREDENTIAL", raising=False)
        monkeypatch.setenv(AGENT_TOKEN_ENV, "ict-token-value")
        resolved = _resolve_gateway_secrets(
            gateway_credential_env="CRUMBLR_DEMO_CANARY_GATEWAY_CREDENTIAL",
            agent_token_env=AGENT_TOKEN_ENV,
        )
        assert resolved is None

    def test_missing_selected_agent_token_env_fails_closed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(GATEWAY_CREDENTIAL_ENV, "ict-credential-value")
        monkeypatch.delenv("LOCAL_AGENT_SERVICE_TOKEN", raising=False)
        resolved = _resolve_gateway_secrets(
            gateway_credential_env=GATEWAY_CREDENTIAL_ENV,
            agent_token_env="LOCAL_AGENT_SERVICE_TOKEN",
        )
        assert resolved is None

    def test_empty_string_env_value_fails_closed_same_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(GATEWAY_CREDENTIAL_ENV, "")
        monkeypatch.setenv(AGENT_TOKEN_ENV, "ict-token-value")
        resolved = _resolve_gateway_secrets(
            gateway_credential_env=GATEWAY_CREDENTIAL_ENV,
            agent_token_env=AGENT_TOKEN_ENV,
        )
        assert resolved is None

    def test_selected_fixture_credential_is_not_replaced_by_the_default_ict_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both the existing ICT credential and the fixture's own,

        differently-named credential are present at once (the real
        operational scenario: the ICT Stage C process's env still holds
        `CRUMBLR_PAPER_LITE_GATEWAY_CREDENTIAL`) -- selecting the fixture's
        var name must return the fixture's own value, never silently fall
        back to or get confused with the ICT one.
        """
        monkeypatch.setenv(GATEWAY_CREDENTIAL_ENV, "ict-credential-value")
        monkeypatch.setenv("CRUMBLR_DEMO_CANARY_GATEWAY_CREDENTIAL", "fixture-credential-value")
        monkeypatch.setenv(AGENT_TOKEN_ENV, "ict-token-value")
        monkeypatch.setenv("LOCAL_AGENT_SERVICE_TOKEN", "fixture-token-value")

        resolved = _resolve_gateway_secrets(
            gateway_credential_env="CRUMBLR_DEMO_CANARY_GATEWAY_CREDENTIAL",
            agent_token_env="LOCAL_AGENT_SERVICE_TOKEN",
        )

        assert resolved is not None
        credential, agent_token = resolved
        assert credential == "fixture-credential-value"
        assert credential != "ict-credential-value"
        assert agent_token == "fixture-token-value"
        assert agent_token != "ict-token-value"

        # And the ICT/default names, if resolved instead, still yield the
        # ICT values unchanged -- selecting one identity's secret never
        # mutates or clears the other's.
        default_resolved = _resolve_gateway_secrets(
            gateway_credential_env=GATEWAY_CREDENTIAL_ENV,
            agent_token_env=AGENT_TOKEN_ENV,
        )
        assert default_resolved == ("ict-credential-value", "ict-token-value")
