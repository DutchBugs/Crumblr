"""`Mt5Client` connection handling, against a fake terminal.

Review 1.11 F-031 reopened a finding review 1.8 had already accepted once:
the account login is not a password, but it must never sit whole in a routine
log line, because the project's own rule for first-contact evidence is that
the full login must not enter shared logs, review artifacts or Git. These
tests are the proof that `mt5.connected` — the one log line every successful
connect produces — cannot regress back into leaking it.
"""

from __future__ import annotations

import io
from typing import Any, cast

from crumblr.mt5_gateway.client import Mt5Client, Mt5Credentials, Mt5Module, mask_login
from crumblr.observability.logging import configure_logging


class FakeMt5:
    """The minimum surface `Mt5Client.connect` needs.

    Does not implement the rest of `Mt5Module` — these tests never call
    anything beyond `connect`, so the cast below is honest about what is
    actually exercised.
    """

    def initialize(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def login(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def last_error(self) -> tuple[int, str]:
        return (1, "Success")


def connected_client(fake: FakeMt5, credentials: Mt5Credentials) -> Mt5Client:
    client = Mt5Client(cast("Mt5Module", fake))
    client.connect(credentials)
    return client


CREDENTIALS = Mt5Credentials(login=5_000_123, password="hunter2", server="PepperstoneUK-Demo")


class TestMaskLogin:
    def test_only_the_last_three_digits_survive(self) -> None:
        assert mask_login(5_000_123) == "***123"

    def test_a_short_login_is_fully_masked(self) -> None:
        assert mask_login(12) == "***"

    def test_two_different_accounts_do_not_collide_on_sight(self) -> None:
        """Not a security property — just useful enough to be worth keeping."""
        assert mask_login(5_000_123) != mask_login(5_000_456)


class TestCredentialsRepr:
    def test_repr_does_not_contain_the_full_login(self) -> None:
        rendered = repr(CREDENTIALS)
        assert "5000123" not in rendered
        assert "5_000_123" not in rendered
        assert "***123" in rendered

    def test_repr_never_contains_the_password(self) -> None:
        assert "hunter2" not in repr(CREDENTIALS)


class TestConnectLogging:
    def test_mt5_connected_does_not_contain_the_full_login(self) -> None:
        stream = io.StringIO()
        configure_logging(stream=stream, level="DEBUG")

        connected_client(FakeMt5(), CREDENTIALS)

        rendered = stream.getvalue()
        assert "mt5.connected" in rendered
        assert "5000123" not in rendered

    def test_mt5_connected_still_carries_a_masked_reference(self) -> None:
        import json

        stream = io.StringIO()
        configure_logging(stream=stream, level="DEBUG")

        connected_client(FakeMt5(), CREDENTIALS)

        record = next(
            json.loads(line) for line in stream.getvalue().splitlines() if "mt5.connected" in line
        )
        assert record["account_ref"] == "***123"
        assert record["server"] == "PepperstoneUK-Demo"
