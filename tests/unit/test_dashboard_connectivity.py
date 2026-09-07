"""`dashboard.state._connectivity`/`_heartbeat_expired`: the liveness

corrective (2026-09-07 dashboard smoke test). The reader's own last-written
`status` field is frozen the instant the writer process stops running --
these tests prove the *independent* liveness check on top of it: a fresh
producer heartbeat is required before `CONNECTED`/`HEALTHY` can be shown at
all, regardless of what `status` says, and an expired or missing heartbeat
only ever makes the result *more* cautious, never less.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from crumblr.dashboard.state import _connectivity, _heartbeat_expired

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _health(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "status": "HEALTHY",
        "connected": True,
        "heartbeat_at_utc": NOW.isoformat(),
        "heartbeat_max_age_seconds": 30.0,
    }
    fields.update(overrides)
    return fields


class TestHeartbeatExpired:
    def test_a_fresh_heartbeat_is_not_expired(self) -> None:
        health = _health(heartbeat_at_utc=(NOW - timedelta(seconds=10)).isoformat())
        assert _heartbeat_expired(health, now=NOW) is False

    def test_a_heartbeat_older_than_its_own_max_age_is_expired(self) -> None:
        health = _health(heartbeat_at_utc=(NOW - timedelta(seconds=31)).isoformat())
        assert _heartbeat_expired(health, now=NOW) is True

    def test_a_heartbeat_exactly_at_the_boundary_is_not_yet_expired(self) -> None:
        health = _health(heartbeat_at_utc=(NOW - timedelta(seconds=30)).isoformat())
        assert _heartbeat_expired(health, now=NOW) is False

    def test_a_missing_heartbeat_field_fails_closed_to_expired(self) -> None:
        health = {"status": "HEALTHY", "connected": True}
        assert _heartbeat_expired(health, now=NOW) is True

    def test_a_missing_max_age_field_fails_closed_to_expired(self) -> None:
        health = {"status": "HEALTHY", "connected": True, "heartbeat_at_utc": NOW.isoformat()}
        assert _heartbeat_expired(health, now=NOW) is True

    def test_a_malformed_heartbeat_timestamp_fails_closed_to_expired(self) -> None:
        health = _health(heartbeat_at_utc="not-a-timestamp")
        assert _heartbeat_expired(health, now=NOW) is True

    def test_a_non_numeric_max_age_fails_closed_to_expired(self) -> None:
        health = _health(heartbeat_max_age_seconds="soon")
        assert _heartbeat_expired(health, now=NOW) is True

    def test_a_syntactically_valid_but_naive_timestamp_fails_closed_not_a_crash(self) -> None:
        """`now` is always timezone-aware; a naive `heartbeat_at_utc` (no

        offset) would raise `TypeError` on subtraction if not caught --
        owner-flagged hardening, 2026-09-08."""
        health = _health(heartbeat_at_utc="2026-09-07T12:00:00")
        assert _heartbeat_expired(health, now=NOW) is True


class TestConnectivity:
    def test_no_snapshot_at_all_is_unknown(self) -> None:
        assert _connectivity(None, now=NOW) == ("UNKNOWN", "UNKNOWN")

    def test_healthy_with_a_fresh_heartbeat_is_connected_and_healthy(self) -> None:
        health = _health(status="HEALTHY")
        assert _connectivity(health, now=NOW) == ("CONNECTED", "HEALTHY")

    def test_healthy_with_an_expired_heartbeat_is_never_connected_or_healthy(self) -> None:
        """The exact scenario the 2026-09-07 smoke test found: a reader

        process exits after its last write said HEALTHY, and nothing ever
        updates the file again."""
        health = _health(status="HEALTHY", heartbeat_at_utc=(NOW - timedelta(hours=1)).isoformat())

        connectivity, data_feed = _connectivity(health, now=NOW)

        assert connectivity != "CONNECTED"
        assert data_feed != "HEALTHY"
        assert (connectivity, data_feed) == ("DISCONNECTED", "STALE")

    def test_healthy_with_no_heartbeat_evidence_at_all_is_never_connected_or_healthy(self) -> None:
        health = {"status": "HEALTHY", "connected": True}

        assert _connectivity(health, now=NOW) == ("DISCONNECTED", "STALE")

    def test_stale_status_with_a_fresh_heartbeat_and_connected_is_connected_and_stale(self) -> None:
        health = _health(status="STALE", connected=True)
        assert _connectivity(health, now=NOW) == ("CONNECTED", "STALE")

    def test_stale_status_with_an_expired_heartbeat_stays_stale_not_downgraded_further(
        self,
    ) -> None:
        health = _health(
            status="STALE", connected=True, heartbeat_at_utc=(NOW - timedelta(hours=1)).isoformat()
        )
        assert _connectivity(health, now=NOW) == ("DISCONNECTED", "STALE")

    def test_disconnected_status_maps_correctly_regardless_of_heartbeat_freshness(self) -> None:
        health = _health(status="DISCONNECTED", connected=False)
        assert _connectivity(health, now=NOW) == ("DISCONNECTED", "DOWN")

    def test_unhealthy_status_with_an_expired_heartbeat_is_not_softened_to_stale(self) -> None:
        """An expired heartbeat must only ever make the result more

        cautious -- it must never *upgrade* an already-worse DOWN reading
        back down to the milder STALE."""
        health = _health(
            status="UNHEALTHY",
            connected=False,
            heartbeat_at_utc=(NOW - timedelta(hours=1)).isoformat(),
        )
        assert _connectivity(health, now=NOW) == ("DISCONNECTED", "DOWN")

    def test_an_unrecognized_status_string_is_unknown_regardless_of_heartbeat(self) -> None:
        health = _health(status="SOME_FUTURE_STATUS_NOBODY_HAS_MAPPED_YET")
        assert _connectivity(health, now=NOW) == ("UNKNOWN", "UNKNOWN")
