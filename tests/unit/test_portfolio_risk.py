"""Owner risk policy v1 (D1.4): `risk/portfolio_risk.py`.

Pure math, no I/O, hand-constructed `PositionState`/`InstrumentSpec` —
mirrors `test_risk_engine.py`'s own fixture style.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from crumblr.domain.enums import Side
from crumblr.domain.models import PositionState
from crumblr.domain.money import ZERO
from crumblr.risk import policies
from crumblr.risk.portfolio_risk import (
    NO_INSTRUMENT_SPEC,
    NO_PROTECTIVE_STOP,
    assess_open_risk,
)
from crumblr.risk.sizing import realised_risk
from tests.conftest import FIXED_NOW, make_instrument_spec

SPEC = make_instrument_spec()
EQUITY = Decimal("10000")


def position(**overrides: Any) -> PositionState:
    fields: dict[str, Any] = {
        "ticket": 900001,
        "broker_symbol": SPEC.broker_symbol,
        "side": Side.BUY,
        "volume": Decimal("0.10"),
        "open_price": Decimal("1.08500"),
        "stop_loss_price": Decimal("1.08000"),
        "opened_at_utc": FIXED_NOW,
        "profit": Decimal("0"),
        "swap": Decimal("0"),
        "observed_at_utc": FIXED_NOW,
    }
    fields.update(overrides)
    return PositionState(**fields)


class TestAssessOpenRisk:
    def test_a_flat_book_is_established_zero(self) -> None:
        assessment = assess_open_risk((), specs={}, equity=EQUITY)
        assert assessment.is_established
        assert assessment.fraction == ZERO
        assert assessment.risk_amount == ZERO

    def test_a_stopped_long_reports_its_entry_geometry(self) -> None:
        assessment = assess_open_risk(
            (
                position(
                    side=Side.BUY, open_price=Decimal("1.08500"), stop_loss_price=Decimal("1.08000")
                ),
            ),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        expected_amount = realised_risk(Decimal("0.10"), Decimal("0.00500"), SPEC)
        assert assessment.is_established
        assert assessment.risk_amount == expected_amount
        assert assessment.fraction == expected_amount / EQUITY

    def test_a_stopped_short_uses_the_stop_above_the_entry(self) -> None:
        long_assessment = assess_open_risk(
            (
                position(
                    side=Side.BUY, open_price=Decimal("1.08500"), stop_loss_price=Decimal("1.08000")
                ),
            ),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        short_assessment = assess_open_risk(
            (
                position(
                    side=Side.SELL,
                    open_price=Decimal("1.08500"),
                    stop_loss_price=Decimal("1.09000"),
                ),
            ),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        assert short_assessment.is_established
        assert short_assessment.risk_amount == long_assessment.risk_amount

    def test_two_differently_sized_positions_sum(self) -> None:
        assessment = assess_open_risk(
            (
                position(ticket=1, volume=Decimal("0.10")),
                position(ticket=2, volume=Decimal("0.20")),
            ),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        expected = realised_risk(Decimal("0.10"), Decimal("0.00500"), SPEC) + realised_risk(
            Decimal("0.20"), Decimal("0.00500"), SPEC
        )
        assert assessment.risk_amount == expected
        # Explicitly not the old count-based approximation.
        old_approximation = Decimal("0.005") * Decimal(2)
        assert assessment.fraction != old_approximation

    def test_a_position_with_no_stop_is_not_counted_as_zero(self) -> None:
        assessment = assess_open_risk(
            (position(stop_loss_price=None, ticket=42),),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        assert not assessment.is_established
        assert assessment.fraction is None
        assert assessment.risk_amount is None
        assert len(assessment.untrusted) == 1
        assert assessment.untrusted[0].ticket == 42
        assert assessment.untrusted[0].reason == NO_PROTECTIVE_STOP

    def test_one_untrusted_position_makes_the_whole_book_unestablished(self) -> None:
        valid = position(ticket=1)
        stopless = position(ticket=2, stop_loss_price=None)
        assessment = assess_open_risk(
            (valid, stopless), specs={SPEC.broker_symbol: SPEC}, equity=EQUITY
        )
        assert assessment.fraction is None

    def test_a_position_in_an_unspecified_symbol_is_untrusted(self) -> None:
        assessment = assess_open_risk(
            (position(broker_symbol="GBPUSD", ticket=7),),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        assert assessment.fraction is None
        assert assessment.untrusted[0].reason == NO_INSTRUMENT_SPEC

    def test_non_positive_equity_is_unestablished(self) -> None:
        assessment = assess_open_risk((position(),), specs={SPEC.broker_symbol: SPEC}, equity=ZERO)
        assert assessment.fraction is None
        assert assessment.detail == "non_positive_equity"

    def test_a_breakeven_stop_contributes_zero_and_does_not_raise(self) -> None:
        assessment = assess_open_risk(
            (position(open_price=Decimal("1.08500"), stop_loss_price=Decimal("1.08500")),),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        assert assessment.is_established
        assert assessment.fraction == ZERO

    def test_a_stop_beyond_breakeven_contributes_zero_not_negative(self) -> None:
        assessment = assess_open_risk(
            (
                position(
                    side=Side.BUY, open_price=Decimal("1.08500"), stop_loss_price=Decimal("1.09000")
                ),
            ),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        assert assessment.is_established
        assert assessment.fraction == ZERO

    def test_current_price_is_never_read(self) -> None:
        low = assess_open_risk(
            (position(current_price=Decimal("0.00001")),),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        high = assess_open_risk(
            (position(current_price=Decimal("99.00000")),),
            specs={SPEC.broker_symbol: SPEC},
            equity=EQUITY,
        )
        assert low.fraction == high.fraction

    def test_the_denominator_is_the_equity_supplied(self) -> None:
        small_equity = assess_open_risk(
            (position(),), specs={SPEC.broker_symbol: SPEC}, equity=Decimal("1000")
        )
        large_equity = assess_open_risk(
            (position(),), specs={SPEC.broker_symbol: SPEC}, equity=Decimal("100000")
        )
        assert small_equity.fraction is not None
        assert large_equity.fraction is not None
        assert small_equity.fraction > large_equity.fraction

    def test_the_assessment_is_a_pure_function_of_recorded_facts(self) -> None:
        first = assess_open_risk((position(),), specs={SPEC.broker_symbol: SPEC}, equity=EQUITY)
        second = assess_open_risk((position(),), specs={SPEC.broker_symbol: SPEC}, equity=EQUITY)
        assert first == second

    def test_the_one_exposure_constant_is_gone(self) -> None:
        assert not hasattr(policies, "MAX_EXPOSURES_PER_SYMBOL")

    def test_no_core_module_approximates_open_risk_by_position_count(self) -> None:
        """Structural guard, mirroring ADR-010's own

        `test_no_broker_fact_event_is_ever_emitted`: no module under
        `application/` or `risk/` may reintroduce the count-based
        approximation this module replaces. `agent_gateway/` is
        deliberately excluded — its own count-based approximation is
        Dev 2's separate D2.2 task, not this repo's to enforce here; the
        scan should widen once that lands.
        """
        pattern = re.compile(r"max_risk_per_trade\s*\*\s*Decimal\(\s*len\(")
        repo_root = Path(__file__).resolve().parents[2]
        offenders = []
        for base in ("src/crumblr/application", "src/crumblr/risk"):
            for path in (repo_root / base).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if pattern.search(text):
                    offenders.append(str(path))
        assert offenders == []
