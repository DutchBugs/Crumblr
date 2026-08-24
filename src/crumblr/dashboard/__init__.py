"""Dashboard v0 — read-only, outside the broker execution boundary.

Review 1.9 F-035 and review 1.12 §8 set the hard boundary this package must
never cross:

    reads PostgreSQL / read-only application health
    displays state

    no MetaTrader5 import
    no broker credentials
    no BUY/SELL buttons
    no order_send
    no HALT reset
    no risk-policy mutation

Nothing in this package imports `MetaTrader5` or `crumblr.mt5_gateway`, reads
`.env`, or exposes a route other than `GET`. `LiveReader` health reaches this
process only through the JSON snapshot `scripts/mt5_live_reader.py` writes to
disk (`reader_health.py`) — never through a live MT5 connection of its own.

This is v0 against build.md §22's full observability-dashboard spec and
Milestone 8's full operator-dashboard spec (manual HALT, audit search, order/
position detail) — see `review/DEVIATIONS.md` D-043 for what is deliberately
not here yet and why.
"""

from __future__ import annotations
