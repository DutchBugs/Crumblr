"""The M1 first-contact probe, against a fake terminal.

These tests prove the probe reads and decodes what it claims to read. They
prove nothing about a real terminal — that is the probe's whole purpose, and it
is the one thing no test on this machine can do.

The fake here differs from the one in `test_mt5_readonly_gateway.py` in one
deliberate way: `filling_mode` and the symbol's `trade_mode` are **integers**,
because that is what MetaTrader 5 actually returns. The gateway currently
stringifies them (D-037), and the probe decodes them, so that the first
connection settles which reading is right before anything depends on it.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest
from scripts.mt5_probe import (
    MissingCredentialsError,
    probe,
    raw_account_facts,
    raw_symbol_facts,
    read_credentials,
    sanitize_report,
)

from crumblr.config import AccountGuardConfig
from crumblr.mt5_gateway.client import Mt5Client, Mt5Credentials
from crumblr.mt5_gateway.enums import decode_filling_modes

GUARD = AccountGuardConfig.model_validate(
    {
        "expected_server": "PepperstoneUK-Demo",
        "expected_login": None,
        "require_demo_account": True,
        "expected_currency": "EUR",
        "expected_leverage": 30,
    }
)


def account_info(**overrides: Any) -> SimpleNamespace:
    fields: dict[str, Any] = {
        "login": 5_000_123,
        "server": "PepperstoneUK-Demo",
        "name": "Demo Account",
        "company": "Pepperstone Limited",
        "currency": "EUR",
        "trade_mode": 0,  # ACCOUNT_TRADE_MODE_DEMO
        "margin_mode": 2,  # ACCOUNT_MARGIN_MODE_RETAIL_HEDGING
        "trade_allowed": True,
        "trade_expert": True,
        "limit_orders": 200,
        "margin_so_call": 100.0,
        "margin_so_so": 50.0,
        "balance": 10_000.0,
        "equity": 10_000.0,
        "margin": 0.0,
        "margin_free": 10_000.0,
        "margin_level": 0.0,
        "leverage": 30,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def symbol_info(**overrides: Any) -> SimpleNamespace:
    """A symbol as MT5 reports one: integer enums, a bitmask, and floats."""
    fields: dict[str, Any] = {
        "name": "EURUSD.a",
        "description": "Euro vs US Dollar",
        "path": "Forex\\Majors\\EURUSD.a",
        "currency_base": "EUR",
        "currency_profit": "USD",
        "trade_contract_size": 100_000.0,
        "digits": 5,
        "point": 1e-05,
        "trade_tick_size": 1e-05,
        "trade_tick_value": 1.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
        "trade_stops_level": 10,
        "trade_freeze_level": 0,
        "filling_mode": 3,  # FOK | IOC
        "trade_mode": 4,  # SYMBOL_TRADE_MODE_FULL
        "spread": 12,
        "spread_float": True,
        "swap_long": -7.2,
        "swap_short": 1.4,
        "swap_rollover3days": 3,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class FakeMt5:
    """A terminal that answers the way MT5 does — ints, floats and bitmasks."""

    def __init__(
        self, *, symbols: tuple[str, ...] = ("EURUSD.a", "EURUSD.a.cfd", "GBPUSD.a")
    ) -> None:
        self._symbols = symbols

    def initialize(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def login(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def shutdown(self) -> None:
        return None

    def last_error(self) -> tuple[int, str]:
        return (1, "Success")

    def version(self) -> tuple[Any, ...]:
        return (500, 4620, "20 Aug 2026")

    def terminal_info(self) -> SimpleNamespace:
        return SimpleNamespace(connected=True, trade_allowed=True, ping_last=32)

    def account_info(self) -> SimpleNamespace:
        return account_info()

    def symbols_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return tuple(SimpleNamespace(name=name) for name in self._symbols)

    def symbol_select(self, _symbol: str, _enable: bool) -> bool:
        return True

    def symbol_info(self, _symbol: str) -> SimpleNamespace:
        return symbol_info()

    def symbol_info_tick(self, _symbol: str) -> SimpleNamespace:
        return SimpleNamespace(bid=1.08500, ask=1.08512, time=1_767_000_000)

    def copy_rates_from_pos(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()

    def copy_ticks_from(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()

    def positions_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()

    def orders_get(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
        return ()


def connected_client(fake: FakeMt5) -> Mt5Client:
    client = Mt5Client(fake)
    client.connect(Mt5Credentials(login=5_000_123, password="x", server="PepperstoneUK-Demo"))
    return client


class TestCredentials:
    def test_missing_variables_are_named(self) -> None:
        with pytest.raises(MissingCredentialsError, match="CRUMBLR_MT5_PASSWORD"):
            read_credentials({"CRUMBLR_MT5_LOGIN": "1", "CRUMBLR_MT5_SERVER": "S"})

    def test_an_empty_value_counts_as_missing(self) -> None:
        """An exported-but-blank variable is the common way this goes wrong."""
        with pytest.raises(MissingCredentialsError, match="CRUMBLR_MT5_SERVER"):
            read_credentials(
                {
                    "CRUMBLR_MT5_LOGIN": "1",
                    "CRUMBLR_MT5_PASSWORD": "x",
                    "CRUMBLR_MT5_SERVER": "",
                }
            )

    def test_credentials_are_assembled_and_the_password_stays_out_of_repr(self) -> None:
        credentials = read_credentials(
            {
                "CRUMBLR_MT5_LOGIN": "5000123",
                "CRUMBLR_MT5_PASSWORD": "hunter2",
                "CRUMBLR_MT5_SERVER": "PepperstoneUK-Demo",
            }
        )
        assert credentials.login == 5_000_123
        assert "hunter2" not in repr(credentials)

    def test_the_process_environment_is_the_default_source(self) -> None:
        os.environ.pop("CRUMBLR_MT5_LOGIN", None)
        with pytest.raises(MissingCredentialsError):
            read_credentials()


class TestFillingModeDecode:
    """The mask is the thing most likely to be misread on first contact."""

    @pytest.mark.parametrize(
        ("mask", "expected"),
        [
            (1, ("FOK",)),
            (2, ("IOC",)),
            (3, ("FOK", "IOC")),
            (6, ("IOC", "BOC")),
            (0, ()),
        ],
    )
    def test_bits_decode_to_names(self, mask: int, expected: tuple[str, ...]) -> None:
        assert decode_filling_modes(mask) == expected


class TestRawReads:
    def test_margin_mode_is_reported_by_name(self) -> None:
        """Q2 — hedging or netting — must be read, never inferred."""
        facts = raw_account_facts(connected_client(FakeMt5()))
        assert facts["margin_mode"] == "RETAIL_HEDGING (2)"

    def test_an_unrecognised_enum_says_so_rather_than_guessing(self) -> None:
        class Odd(FakeMt5):
            def account_info(self) -> SimpleNamespace:
                return account_info(margin_mode=99)

        facts = raw_account_facts(connected_client(Odd()))
        assert facts["margin_mode"] == "UNKNOWN (99)"

    def test_the_password_is_never_in_the_report(self) -> None:
        facts = raw_account_facts(connected_client(FakeMt5()))
        assert "password" not in {key.lower() for key in facts}

    def test_symbol_facts_carry_both_the_raw_mask_and_the_decode(self) -> None:
        facts = raw_symbol_facts(connected_client(FakeMt5()), "EURUSD.a")
        assert facts["filling_mask"] == 3
        assert facts["filling_modes"] == ("FOK", "IOC")
        assert facts["symbol_trade_mode"] == "FULL (4)"

    def test_floats_are_rendered_exactly_rather_than_rounded(self) -> None:
        """`repr` keeps 1e-05 as 1e-05; formatting it would lose the point size."""
        facts = raw_symbol_facts(connected_client(FakeMt5()), "EURUSD.a")
        assert facts["point"] == "1e-05"
        assert facts["swap_long"] == "-7.2"


class TestProbe:
    def test_it_resolves_the_suffixed_symbol_and_lists_the_alternatives(self) -> None:
        report = probe(connected_client(FakeMt5()), GUARD, "EUR/USD")
        assert report["resolved_symbol"] == "EURUSD.a"
        assert report["symbol_candidates"] == ["EURUSD.a", "EURUSD.a.cfd"]

    def test_a_guard_mismatch_is_a_result_not_a_crash(self) -> None:
        """The first run is likeliest to mismatch, and the facts still matter."""
        wrong = GUARD.model_copy(update={"expected_server": "PepperstoneEU-Demo"})
        report = probe(connected_client(FakeMt5()), wrong, "EUR/USD")
        assert report["account_guard"]["passed"] is False
        assert "PepperstoneEU-Demo" in report["account_guard"]["mismatches"]
        assert report["instrument"]["digits"] == 5

    def test_a_matching_account_passes_the_guard(self) -> None:
        report = probe(connected_client(FakeMt5()), GUARD, "EUR/USD")
        assert report["account_guard"] == {"passed": True, "mismatches": None}

    def test_the_probe_touches_no_mutating_terminal_call(self) -> None:
        """A regression guard: the probe must stay a read.

        The terminal here fails the test if the probe reaches for anything that
        changes broker state, rather than trusting that it does not.
        """

        class TripwireMt5(FakeMt5):
            def order_send(self, *_args: Any, **_kwargs: Any) -> None:
                raise AssertionError("the probe attempted to send an order")

            def order_check(self, *_args: Any, **_kwargs: Any) -> None:
                raise AssertionError("the probe attempted to check an order")

        report = probe(connected_client(TripwireMt5()), GUARD, "EUR/USD")
        assert report["open_positions"] == 0


class TestSanitizeReport:
    """F-031 (review 1.8): the account number must not reach git or chat."""

    def test_the_account_number_is_redacted(self) -> None:
        report = probe(connected_client(FakeMt5()), GUARD, "EUR/USD")
        sanitized = sanitize_report(report)
        assert sanitized["account"]["login"] == "<redacted>"

    def test_the_original_report_is_not_mutated(self) -> None:
        report = probe(connected_client(FakeMt5()), GUARD, "EUR/USD")
        sanitize_report(report)
        assert report["account"]["login"] == 5_000_123

    def test_technical_broker_facts_survive_unchanged(self) -> None:
        """Server, currency, leverage and the decoded modes are the whole point."""
        report = probe(connected_client(FakeMt5()), GUARD, "EUR/USD")
        sanitized = sanitize_report(report)
        assert sanitized["account"]["server"] == "PepperstoneUK-Demo"
        assert sanitized["account"]["currency"] == "EUR"
        assert sanitized["account"]["leverage"] == 30
        assert sanitized["account"]["margin_mode"] == "RETAIL_HEDGING (2)"
        assert sanitized["instrument"] == report["instrument"]
        assert sanitized["resolved_symbol"] == report["resolved_symbol"]
