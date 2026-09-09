"""Real round-trip proof against the actual Windows Credential Manager.

Marked `windows_only` (and skipped by platform, matching the convention
`pyproject.toml` already declares for MT5-dependent tests) since there is
no fake here — this is deliberately the one place that exercises the real
`Advapi32.dll` calls, on the one host that can. Everything else in
`crumblr.local_admin` is tested against this module's public functions
(`write`/`read`/`status`/`delete`), never against the raw ctypes layer
directly, so a bug here is the one place it would actually be caught.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest

from crumblr.local_admin import credential_store

pytestmark = [
    pytest.mark.windows_only,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager only"),
]

TEST_NAME = "TEST_CREDENTIAL_STORE_ROUNDTRIP_DELETE_ME"


@pytest.fixture(autouse=True)
def _clean_up_test_credential() -> Iterator[None]:
    credential_store.delete(TEST_NAME)
    yield
    credential_store.delete(TEST_NAME)


class TestCredentialStoreRoundTrip:
    def test_missing_credential_reads_as_none_and_not_configured(self) -> None:
        assert credential_store.read(TEST_NAME) is None
        status = credential_store.status(TEST_NAME)
        assert status.configured is False
        assert status.last_written_utc is None

    def test_write_then_read_returns_the_exact_value(self) -> None:
        credential_store.write(TEST_NAME, "a-plausible-secret-value-!@#$ünïcode")
        assert credential_store.read(TEST_NAME) == "a-plausible-secret-value-!@#$ünïcode"

    def test_status_reports_configured_with_a_recent_timestamp_never_the_value(self) -> None:
        from datetime import UTC, datetime

        before = datetime.now(UTC)
        credential_store.write(TEST_NAME, "whatever")
        status = credential_store.status(TEST_NAME)
        after = datetime.now(UTC)

        assert status.configured is True
        assert status.last_written_utc is not None
        # Clock skew tolerance for FILETIME's own resolution/rounding.
        low = before.timestamp() - 2
        high = after.timestamp() + 2
        assert low <= status.last_written_utc.timestamp() <= high

    def test_replace_overwrites_rather_than_duplicating(self) -> None:
        credential_store.write(TEST_NAME, "first")
        credential_store.write(TEST_NAME, "second")
        assert credential_store.read(TEST_NAME) == "second"

    def test_delete_then_read_returns_none_again(self) -> None:
        credential_store.write(TEST_NAME, "to-be-deleted")
        assert credential_store.delete(TEST_NAME) is True
        assert credential_store.read(TEST_NAME) is None

    def test_deleting_an_already_absent_credential_is_not_an_error(self) -> None:
        assert credential_store.read(TEST_NAME) is None
        assert credential_store.delete(TEST_NAME) is False
