"""MT5 integer enum decoding, shared by the gateway and the first-contact probe.

D-037: `symbol_info.trade_mode` and `.filling_mode` are integers, the latter a
bitmask, and the gateway used to store `str(value)` — the digit, not the
name. `scripts/mt5_probe.py` carried its own decode tables written from
documentation; keeping two copies is exactly how the two are able to "agree
and both be wrong together" (`review/DEVIATIONS.md` D-037). One table now,
imported by both.

Confirmed against a real Pepperstone terminal 2026-08-24 (first contact):
`filling_mode=2` decoded to `IOC`, `trade_mode=4` decoded to `FULL` — both
matching the documented mapping below. See `status.md` §13.
"""

from __future__ import annotations

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


def decode_filling_modes(mask: int) -> tuple[str, ...]:
    """Decode `symbol_info.filling_mode`, a bitmask of allowed fill types."""
    return tuple(name for bit, name in SYMBOL_FILLING_FLAGS if mask & bit)


def decode_enum(value: int, table: dict[int, str]) -> str:
    """Render an MT5 integer enum as its name, or flag it as unrecognised.

    Never guesses: an unrecognised value becomes `UNKNOWN(n)` rather than a
    plausible-looking name, so a broker constant this table does not yet know
    about is visible instead of silently misread.
    """
    return table.get(value, f"UNKNOWN({value})")
