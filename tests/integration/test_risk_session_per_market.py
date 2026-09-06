"""Two markets' risk ledgers never cross-contaminate (Market Universe, ADR-022).

Before `canonical_symbol` existed on `risk_session_states`, `load_latest()`
read "the single latest row in the table," full stop — a second market
would have silently shared EUR/USD's equity/drawdown/loss ledger. This is
the one genuine safety gap the Market Universe closes: real PostgreSQL,
not a fake, for the guarantee that matters most (`RiskLedgerLock` already
locks per `canonical_symbol`; this proves the store underneath it actually
honours that same key).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Engine

from crumblr.domain.timeutils import utc_now
from crumblr.persistence.risk_session import PostgresRiskSessionStore
from crumblr.risk.session import RiskSessionState

pytestmark = pytest.mark.integration

NOW = utc_now()


def _state(canonical_symbol: str, *, current_equity: Decimal) -> RiskSessionState:
    return RiskSessionState(
        canonical_symbol=canonical_symbol,
        trading_day=NOW.date(),
        session_start_equity=Decimal("10000"),
        current_equity=current_equity,
        peak_equity=current_equity,
        realized_pnl=current_equity - Decimal("10000"),
        max_drawdown_fraction=Decimal("0"),
        max_session_loss_fraction=Decimal("0"),
        open_risk_fraction=Decimal("0"),
        open_position_count=0,
        recorded_at_utc=NOW,
    )


class TestTwoMarketsNeverCrossContaminate:
    def test_load_latest_for_one_symbol_never_sees_another_symbols_row(
        self, engine: Engine
    ) -> None:
        store = PostgresRiskSessionStore(engine)
        store.save(_state("EUR/USD", current_equity=Decimal("9850")))
        store.save(_state("BTC/USD", current_equity=Decimal("20000")))

        eur = store.load_latest(canonical_symbol="EUR/USD")
        btc = store.load_latest(canonical_symbol="BTC/USD")

        assert eur.state is not None
        assert eur.state.canonical_symbol == "EUR/USD"
        assert eur.state.current_equity == Decimal("9850")

        assert btc.state is not None
        assert btc.state.canonical_symbol == "BTC/USD"
        assert btc.state.current_equity == Decimal("20000")

    def test_a_write_to_one_symbol_does_not_move_the_others_latest(self, engine: Engine) -> None:
        store = PostgresRiskSessionStore(engine)
        store.save(_state("EUR/USD", current_equity=Decimal("9850")))
        store.save(_state("BTC/USD", current_equity=Decimal("20000")))

        # Three more EUR/USD writes -- BTC/USD's own latest must not move,
        # the way it would if `load_latest` ordered by `sequence` alone
        # across the whole table rather than filtering by symbol first.
        for equity in (Decimal("9800"), Decimal("9700"), Decimal("9600")):
            store.save(_state("EUR/USD", current_equity=equity))

        btc = store.load_latest(canonical_symbol="BTC/USD")
        assert btc.state is not None
        assert btc.state.current_equity == Decimal("20000")

        eur = store.load_latest(canonical_symbol="EUR/USD")
        assert eur.state is not None
        assert eur.state.current_equity == Decimal("9600")

    def test_a_symbol_with_no_rows_yet_reports_absent_not_another_symbols_state(
        self, engine: Engine
    ) -> None:
        store = PostgresRiskSessionStore(engine)
        store.save(_state("EUR/USD", current_equity=Decimal("9850")))

        record = store.load_latest(canonical_symbol="BTC/USD")

        assert record.is_known
        assert record.state is None
