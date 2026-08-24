"""M1 first contact: read a live MT5 terminal and report what it says.

    uv run python scripts/mt5_probe.py
    uv run python scripts/mt5_probe.py --json probe.json

Windows x86-64 only — the `MetaTrader5` package ships no other wheels.

This script exists because every broker fact the platform depends on is
currently a *claim*: the symbol name, the digits, the tick value, the filling
modes, whether the account nets or hedges. They were written from
documentation, not from observation (`review/DEVIATIONS.md` D-035). The probe
reads them from the terminal and prints them so a human can compare them with
`config/paper.yaml` and with `build.md`, and record the differences as
deviations rather than quietly patching the code to match.

Three properties are deliberate:

- **It cannot trade.** It uses `ReadOnlyMt5Gateway`, whose execution methods
  raise (D-036). There is no order interface reachable from this file.
- **It reports raw values alongside interpreted ones.** MT5 hands back integer
  enums and bitmasks. The decode below is from the documentation and is exactly
  the thing first contact is supposed to verify, so both are printed and
  neither is presented as settled.
- **The account guard is reported, not enforced.** A mismatch is the most
  interesting possible result on the first run — the entity question APP-013 is
  open — so the guard's verdict is printed rather than raised as a crash.

The password is read from the environment and never printed, logged or
serialised. `Mt5Credentials.__repr__` redacts it as well.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from crumblr.config import AccountGuardConfig, load_config
from crumblr.domain.enums import Environment
from crumblr.mt5_gateway.client import Mt5Client, Mt5Credentials, Mt5UnavailableError
from crumblr.mt5_gateway.readonly import AccountGuardError, ReadOnlyMt5Gateway

REPO_ROOT = Path(__file__).resolve().parent.parent

ACCOUNT_TRADE_MODES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
"""`ACCOUNT_TRADE_MODE_*`. Paper mode requires DEMO."""

ACCOUNT_MARGIN_MODES = {0: "RETAIL_NETTING", 1: "EXCHANGE", 2: "RETAIL_HEDGING"}
"""`ACCOUNT_MARGIN_MODE_*` — owner question Q2, deliberately never guessed.

Netting and hedging differ in what a second order does to an existing
position, which is why the one-exposure rule is written to hold under both.
"""

SYMBOL_TRADE_MODES = {
    0: "DISABLED",
    1: "LONGONLY",
    2: "SHORTONLY",
    3: "CLOSEONLY",
    4: "FULL",
}
"""`SYMBOL_TRADE_MODE_*`."""

SYMBOL_FILLING_FLAGS = ((1, "FOK"), (2, "IOC"), (4, "BOC"))
"""`SYMBOL_FILLING_*` — a bitmask, not a value. A symbol may allow several."""


class MissingCredentialsError(RuntimeError):
    """The environment does not carry a full set of MT5 credentials."""


def read_credentials(environ: dict[str, str] | None = None) -> Mt5Credentials:
    """Assemble credentials from the environment, or say exactly what is absent.

    build.md §21 keeps these out of the repository. On a workstation they come
    from `.env`; in production from the Windows Credential Manager or a secret
    manager. Either way this process is the only one that sees them.
    """
    env = os.environ if environ is None else environ
    missing = [
        name
        for name in ("CRUMBLR_MT5_LOGIN", "CRUMBLR_MT5_PASSWORD", "CRUMBLR_MT5_SERVER")
        if not env.get(name)
    ]
    if missing:
        raise MissingCredentialsError(
            "missing " + ", ".join(missing) + ". Copy .env.example to .env and fill it in, "
            "or export them from the secret store. They never belong in config/."
        )
    return Mt5Credentials(
        login=int(env["CRUMBLR_MT5_LOGIN"]),
        password=env["CRUMBLR_MT5_PASSWORD"],
        server=env["CRUMBLR_MT5_SERVER"],
    )


def decode_filling_modes(mask: int) -> tuple[str, ...]:
    """Decode `symbol_info.filling_mode`, which is a bitmask of allowed modes.

    The gateway currently stores the mask verbatim (see D-037). Decoding here
    first means the mapping is confirmed against a terminal before anything
    starts depending on it.
    """
    return tuple(name for bit, name in SYMBOL_FILLING_FLAGS if mask & bit)


def _named(value: object, table: dict[int, str]) -> str:
    """Render an MT5 integer enum as `NAME (n)`, or flag it as unrecognised."""
    if not isinstance(value, int):
        return f"UNREADABLE ({value!r})"
    return f"{table.get(value, 'UNKNOWN')} ({value})"


def raw_account_facts(client: Mt5Client) -> dict[str, Any]:
    """Read the fields `AccountState` does not carry.

    `AccountState` models what the risk engine needs. Margin mode, the trade
    mode as an enum and the account's own limits are facts about the venue that
    a human has to decide about, so they are read here rather than folded into
    a contract that would imply the platform already handles them.
    """
    info = client.checked("account_info", client.module.account_info())
    return {
        "login": int(info.login),
        "server": str(info.server),
        "name_present": bool(getattr(info, "name", "")),
        "company": str(getattr(info, "company", "")),
        "currency": str(info.currency),
        "leverage": int(info.leverage),
        "trade_mode": _named(int(info.trade_mode), ACCOUNT_TRADE_MODES),
        "margin_mode": _named(int(getattr(info, "margin_mode", -1)), ACCOUNT_MARGIN_MODES),
        "trade_allowed": bool(info.trade_allowed),
        "trade_expert": bool(getattr(info, "trade_expert", False)),
        "limit_orders": int(getattr(info, "limit_orders", 0)),
        "margin_so_call": float(getattr(info, "margin_so_call", 0.0)),
        "margin_so_so": float(getattr(info, "margin_so_so", 0.0)),
    }


def raw_symbol_facts(client: Mt5Client, broker_symbol: str) -> dict[str, Any]:
    """Read the symbol specification, including the parts the gateway flattens."""
    info = client.checked("symbol_info", client.module.symbol_info(broker_symbol))
    filling_mask = int(info.filling_mode)
    tick = client.module.symbol_info_tick(broker_symbol)
    return {
        "name": str(info.name),
        "description": str(getattr(info, "description", "")),
        "path": str(getattr(info, "path", "")),
        "digits": int(info.digits),
        "point": repr(info.point),
        "tick_size": repr(info.trade_tick_size),
        "tick_value": repr(info.trade_tick_value),
        "contract_size": repr(info.trade_contract_size),
        "volume_min": repr(info.volume_min),
        "volume_max": repr(info.volume_max),
        "volume_step": repr(info.volume_step),
        "stops_level": int(info.trade_stops_level),
        "freeze_level": int(info.trade_freeze_level),
        "symbol_trade_mode": _named(int(info.trade_mode), SYMBOL_TRADE_MODES),
        "filling_mask": filling_mask,
        "filling_modes": decode_filling_modes(filling_mask),
        "spread_points": int(getattr(info, "spread", -1)),
        "spread_float": bool(getattr(info, "spread_float", False)),
        "swap_long": repr(getattr(info, "swap_long", None)),
        "swap_short": repr(getattr(info, "swap_short", None)),
        "swap_rollover_3days": int(getattr(info, "swap_rollover3days", -1)),
        "current_bid": repr(getattr(tick, "bid", None)) if tick else None,
        "current_ask": repr(getattr(tick, "ask", None)) if tick else None,
    }


def probe(client: Mt5Client, guard: AccountGuardConfig, canonical_symbol: str) -> dict[str, Any]:
    """Collect everything worth recording from one connected terminal.

    The guard is evaluated last and its failure is captured as a result, not
    raised: on the first contact a mismatch is a finding to record, and losing
    the symbol and instrument facts to a traceback would waste the connection.
    """
    gateway = ReadOnlyMt5Gateway(client, guard, canonical_symbol=canonical_symbol)

    report: dict[str, Any] = {
        "terminal": gateway.terminal_health(),
        "account": raw_account_facts(client),
    }

    symbols = client.checked("symbols_get", client.module.symbols_get())
    wanted = canonical_symbol.replace("/", "").upper()
    report["symbol_candidates"] = sorted(
        str(symbol.name)
        for symbol in symbols
        if str(symbol.name).upper().replace(".", "").startswith(wanted)
    )
    report["symbols_total"] = len(symbols)

    broker_symbol = gateway.resolve_symbol()
    report["resolved_symbol"] = broker_symbol
    report["instrument"] = raw_symbol_facts(client, broker_symbol)

    positions = gateway.positions()
    report["open_positions"] = len(positions)
    report["position_symbols"] = sorted({position.broker_symbol for position in positions})

    try:
        gateway.account()
    except AccountGuardError as error:
        report["account_guard"] = {"passed": False, "mismatches": str(error)}
    else:
        report["account_guard"] = {"passed": True, "mismatches": None}

    return report


def render(report: dict[str, Any], guard: AccountGuardConfig) -> None:
    """Print the report, then say plainly what a human still has to decide."""
    print("\n" + "=" * 78)
    print("  MT5 FIRST CONTACT — read-only probe")
    print("=" * 78)

    print("\n  Terminal")
    for key, value in report["terminal"].items():
        print(f"    {key:<24} {value}")

    print("\n  Account")
    for key, value in report["account"].items():
        print(f"    {key:<24} {value}")

    guard_result = report["account_guard"]
    if guard_result["passed"]:
        print(f"\n    account guard             PASSED against {guard.expected_server!r}")
    else:
        print("\n    account guard             FAILED — this is a finding, not a crash:")
        print(f"      {guard_result['mismatches']}")

    print(f"\n  Symbols ({report['symbols_total']} total, matching candidates listed)")
    for candidate in report["symbol_candidates"]:
        marker = "  <- resolved" if candidate == report["resolved_symbol"] else ""
        print(f"    {candidate}{marker}")

    print("\n  Instrument")
    for key, value in report["instrument"].items():
        print(f"    {key:<24} {value}")

    print(f"\n  Open positions            {report['open_positions']}")
    if report["position_symbols"]:
        print(f"    on                      {', '.join(report['position_symbols'])}")

    print("\n" + "-" * 78)
    print("  What to do with this")
    print("-" * 78)
    print("""
    1. Compare `resolved_symbol` with anything that assumed "EURUSD". If the
       broker uses a suffix, the assumption was a bug that only a real account
       could reveal.
    2. Record `margin_mode` as the answer to owner question Q2 — hedging or
       netting. The platform supports exactly one and must not be asked to
       guess.
    3. Compare `server`, `currency` and `leverage` with config/paper.yaml. The
       entity question (APP-013 / D-034) is settled by `company` and `server`,
       not by intent.
    4. Copy digits, point, tick size, tick value, contract size and the volume
       min/max/step into the record. Sizing rounds down against these; a wrong
       tick value misprices every position the risk engine builds.
    5. Note `filling_modes` and `stops_level`. The stops level is the floor a
       proposed stop has to clear, and the platform's own floor must not sit
       below it.
    6. `swap_long` / `swap_short` are the numbers the fill model does not have
       (D-010). Recording them is the first step to a cost model that means
       something.

    Anything here that disagrees with the code is a deviation to write down
    (review/DEVIATIONS.md), not a value to paste over. First contact is
    discovery — APP-014.
""")
    print("=" * 78 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--canonical-symbol",
        default="EUR/USD",
        help="the canonical symbol to resolve against the broker's names",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="also write the report as JSON, for attaching to a status entry",
    )
    parser.add_argument(
        "--environment",
        default=Environment.PAPER.value,
        help="which configuration supplies the account guard (default: paper)",
    )
    args = parser.parse_args()

    config = load_config(Environment(args.environment), config_dir=REPO_ROOT / "config")

    try:
        credentials = read_credentials()
    except MissingCredentialsError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        client = Mt5Client()
        with client:
            client.connect(
                credentials,
                terminal_path=os.environ.get("CRUMBLR_MT5_TERMINAL_PATH") or None,
            )
            report = probe(client, config.account_guard, args.canonical_symbol)
    except Mt5UnavailableError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    render(report, config.account_guard)

    if args.json is not None:
        args.json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"  Report written to {args.json}\n")

    return 0 if report["account_guard"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
