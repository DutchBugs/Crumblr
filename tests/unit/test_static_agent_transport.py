"""agent_gateway/static_agent_transport.py -- the TraderContext 1.0 wire
payload for the "market data unhealthy" NO_TRADE smoke case.

Structural/API-contract tests only -- no dependency on the actual
`DutchBugs/crumblr-static-agent-host` fork being cloned anywhere. This
module's construction was separately, manually verified end-to-end
against a real (git-clean, `core.autocrlf=false`) checkout of the fork's
own `crumblr_strategy_agent.cli evaluate` command: the generated payload
validated, `input_identity` matched the fork's independent recomputation,
and it produced the expected `NO_TRADE` / `MARKET_DATA_STALE` /
`CRUMBLR_INTEGRATION`-sourced decision -- recorded in
`review/AGENT_STATUS.md`, not repeated here as an automated dependency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from crumblr.agent_gateway.static_agent_transport import (
    STATIC_AGENT_CANONICAL_SYMBOL,
    STATIC_AGENT_STRATEGY_IDENTITY,
    STATIC_AGENT_TIMEFRAME,
    InstrumentSpecFacts,
    build_unhealthy_market_context,
    canonical_decimal,
    canonical_json,
    compute_input_identity,
)

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def spec(**overrides: object) -> InstrumentSpecFacts:
    fields: dict[str, object] = {
        "broker_symbol": "EURUSD",
        "digits": 5,
        "point": Decimal("0.00001"),
        "tick_size": Decimal("0.00001"),
        "observed_at_utc": NOW - timedelta(minutes=1),
    }
    fields.update(overrides)
    return InstrumentSpecFacts(**fields)  # type: ignore[arg-type]


def context(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "decision_window_id": "window-1",
        "decision_time_utc": NOW,
        "mode": "LIVE_SHADOW",
        "market_data_health": "STALE",
        "last_completed_bar_close_time_utc": NOW - timedelta(minutes=5),
        "broker_symbol": "EURUSD",
        "instrument_spec": spec(),
        "available_at_utc": NOW - timedelta(minutes=5),
        "source_bar_ids": ("EURUSD:M5:2026-09-01T11:55:00+00:00",),
    }
    fields.update(overrides)
    return build_unhealthy_market_context(**fields)  # type: ignore[arg-type]


class TestSchemaShape:
    def test_root_fields_match_the_schema_exactly(self) -> None:
        payload = context()
        assert set(payload) == {
            "schema_version",
            "decision_window_id",
            "decision_time_utc",
            "mode",
            "data_origin",
            "strategy",
            "market",
            "instrument_spec",
            "features",
            "input_identity",
        }
        assert payload["schema_version"] == "1.0"
        assert payload["data_origin"] == "LIVE_FORWARD"

    def test_strategy_block_matches_the_fork_consts_exactly(self) -> None:
        payload = context()
        assert payload["strategy"] == STATIC_AGENT_STRATEGY_IDENTITY
        # Cross-checked independently against both
        # contracts/crumblr-trader-context-1.0.schema.json's `const` fields
        # and strategy_assets/ict_sb_eurusd_pivot2/5.0/manifest.json.
        assert STATIC_AGENT_STRATEGY_IDENTITY == {
            "strategy_id": "ICT_SB_EURUSD_PIVOT2",
            "version": "5.0",
            "source_hash": "eb6e762a95d35ada8f25734440c9ee3008dcbbfe5ced8e3a3d3cda3e6293cda7",
            "profile": "EURUSD_PIVOT2_CORE_V5",
            "config_id": "EURUSD_V5_DEFAULTS",
        }

    def test_market_block_uses_the_forks_symbol_and_timeframe_consts(self) -> None:
        payload = context()
        market = payload["market"]
        assert isinstance(market, dict)
        assert market["canonical_symbol"] == STATIC_AGENT_CANONICAL_SYMBOL == "EURUSD"
        assert market["timeframe"] == STATIC_AGENT_TIMEFRAME == "M5"
        assert market["market_data_health"] == "STALE"

    def test_features_observation_is_schema_valid_and_never_claims_a_real_signal(self) -> None:
        payload = context()
        features = payload["features"]
        assert isinstance(features, dict)
        observation = features["observation"]
        assert isinstance(observation, dict)
        assert observation["event_type"] == "NO_TRADE"
        assert observation["uses_only_confirmed_data"] is True
        assert observation["reason_codes"] == ["NOT_EVALUATED_MARKET_DATA_UNHEALTHY"]
        # Not one of the fork's closed Pivot-2-2 vocabulary tokens (AG-015)
        # -- deliberately, since this is never read on this code path.
        assert "PIVOT_2_2_CONFIRMED" not in observation["reason_codes"]

    def test_timestamps_are_truncated_to_whole_seconds(self) -> None:
        payload = context(decision_time_utc=NOW.replace(microsecond=123456))
        assert payload["decision_time_utc"] == NOW.isoformat()

    def test_decimals_are_rendered_without_trailing_zeros_or_exponents(self) -> None:
        payload = context(
            instrument_spec=spec(point=Decimal("0.00001"), tick_size=Decimal("1.50000"))
        )
        instrument_spec_block = payload["instrument_spec"]
        assert isinstance(instrument_spec_block, dict)
        assert instrument_spec_block["point"] == "0.00001"
        assert instrument_spec_block["tick_size"] == "1.5"


class TestRefusals:
    def test_refuses_to_claim_market_data_is_healthy(self) -> None:
        with pytest.raises(ValueError, match="HEALTHY"):
            context(market_data_health="HEALTHY")

    def test_refuses_empty_source_bar_ids(self) -> None:
        with pytest.raises(ValueError, match="source_bar_ids"):
            context(source_bar_ids=())

    def test_refuses_a_naive_datetime(self) -> None:
        """Self-review finding: `UtcDatetime`'s coercion only runs inside
        Pydantic model construction -- this module's plain function
        parameters get none of that for free, so it must check UTC-ness
        itself rather than silently emit a malformed timestamp."""
        with pytest.raises(ValueError, match="UTC"):
            context(decision_time_utc=datetime(2026, 9, 1, 12, 0, 0))  # noqa: DTZ001

    def test_refuses_a_non_utc_timezone(self) -> None:
        offset_tz = timezone(timedelta(hours=2))
        with pytest.raises(ValueError, match="UTC"):
            context(decision_time_utc=datetime(2026, 9, 1, 14, 0, 0, tzinfo=offset_tz))


class TestNoSharedMutableState:
    def test_mutating_one_payloads_reason_codes_does_not_leak_into_another(self) -> None:
        """Self-review finding: the placeholder observation was built via
        `dict(_PLACEHOLDER_OBSERVATION)`, a shallow copy that shared the
        same `reason_codes` list object across every call."""
        first = context()
        observation = first["features"]["observation"]  # type: ignore[index]
        observation["reason_codes"].append("INJECTED_BY_CALLER")

        second = context()
        assert second["features"]["observation"]["reason_codes"] == [  # type: ignore[index]
            "NOT_EVALUATED_MARKET_DATA_UNHEALTHY"
        ]


class TestInputIdentity:
    def test_format_is_input_prefixed_sha256_hex(self) -> None:
        payload = context()
        identity = payload["input_identity"]
        assert isinstance(identity, str)
        assert identity.startswith("input_")
        assert len(identity) == len("input_") + 64
        int(identity.removeprefix("input_"), 16)  # raises ValueError if not hex

    def test_identical_inputs_produce_the_identical_identity(self) -> None:
        first = context()
        second = context()
        assert first["input_identity"] == second["input_identity"]

    def test_a_changed_field_changes_the_identity(self) -> None:
        first = context()
        second = context(market_data_health="UNKNOWN")
        assert first["input_identity"] != second["input_identity"]

    def test_matches_recomputing_directly_from_the_same_sub_objects(self) -> None:
        payload = context()
        recomputed = compute_input_identity(
            strategy=payload["strategy"],  # type: ignore[arg-type]
            market=payload["market"],  # type: ignore[arg-type]
            instrument_spec=payload["instrument_spec"],  # type: ignore[arg-type]
            features=payload["features"],  # type: ignore[arg-type]
        )
        assert payload["input_identity"] == recomputed


class TestCanonicalHelpers:
    def test_canonical_json_sorts_keys_and_uses_compact_separators(self) -> None:
        assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_canonical_decimal_strips_trailing_zeros(self) -> None:
        assert canonical_decimal(Decimal("1.50000")) == "1.5"

    def test_canonical_decimal_strips_a_bare_integer_trailing_point(self) -> None:
        assert canonical_decimal(Decimal("2.00000")) == "2"

    def test_canonical_decimal_never_uses_exponent_notation(self) -> None:
        assert canonical_decimal(Decimal("0.00001")) == "0.00001"
