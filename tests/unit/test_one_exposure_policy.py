"""One EUR/USD exposure at a time (owner decision O-004; review 1.6 F-025).

The owner approved this as a v1 business rule, and review 1.6 §4 lists the
cases it has to cover. They are all here, with one addition the review implies
but does not spell out: a short followed by a BUY. If the rule were about
*direction* rather than about exposure, that case would slip through.

The rule sits above the account model on purpose. Q2 — whether the Pepperstone
demo turns out to be hedging or netting — is still unanswered, and the answer
must not change the outcome: a netting account would net the second order into
the first position and a hedging account would open a parallel one, and v1 is
permitted to do neither.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from crumblr.config import RiskConfig
from crumblr.domain.enums import ReasonCode, RiskVerdict, Side
from crumblr.domain.models import PositionState, RiskDecision
from crumblr.risk import policies
from tests.conftest import FIXED_NOW, make_instrument_spec
from tests.unit.test_risk_engine import EQUITY, evaluate, healthy_intent, portfolio, risk_config

SPEC = make_instrument_spec()


def open_position(side: Side, *, broker_symbol: str = SPEC.broker_symbol) -> PositionState:
    return PositionState(
        ticket=1,
        broker_symbol=broker_symbol,
        side=side,
        volume=Decimal("0.10"),
        open_price=Decimal("1.08500"),
        opened_at_utc=FIXED_NOW - timedelta(minutes=30),
        profit=Decimal("0"),
        swap=Decimal("0"),
        observed_at_utc=FIXED_NOW,
    )


def decide(*, positions: tuple[PositionState, ...], side: Side) -> RiskDecision:
    """Evaluate a healthy intent against a book that already holds `positions`.

    `max_open_positions` is raised out of the way so that a refusal here can
    only be the O-004 rule. The two limits are different rules and a test that
    cannot tell them apart proves neither.
    """
    long_side = side is Side.BUY
    return evaluate(
        intent=healthy_intent(
            side=side,
            # A SELL's stop sits above the reference and its target below it;
            # the contract enforces that, so the two sides cannot share values.
            stop_loss_price=Decimal("1.08000") if long_side else Decimal("1.09000"),
            take_profit_price=Decimal("1.08900") if long_side else Decimal("1.08100"),
        ),
        portfolio_state=portfolio(open_positions=positions, open_risk_fraction=Decimal("0.005")),
        risk_context=None if not positions else _context_without_position_cap(),
    )


def _context_without_position_cap() -> policies.RiskContext:
    from tests.unit.test_risk_engine import context

    return context(risk=risk_config(max_open_positions=99, max_open_risk=Decimal("0.10")))


class TestTheFourCasesTheReviewNames:
    """feedback.1.6 §4, case for case."""

    def test_no_exposure_and_a_buy_is_not_refused_for_this_reason(self) -> None:
        decision = decide(positions=(), side=Side.BUY)

        assert ReasonCode.SYMBOL_EXPOSURE_EXISTS not in decision.reason_codes
        assert decision.verdict is RiskVerdict.PASS

    def test_a_long_position_blocks_another_buy(self) -> None:
        decision = decide(positions=(open_position(Side.BUY),), side=Side.BUY)

        assert decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.SYMBOL_EXPOSURE_EXISTS in decision.reason_codes

    def test_a_long_position_blocks_a_sell(self) -> None:
        """The case a direction-based rule would let through.

        On a netting account this would reduce or reverse the position; on a
        hedging account it would open an opposing one. Neither is a thing v1
        may do without an explicit close workflow.
        """
        decision = decide(positions=(open_position(Side.BUY),), side=Side.SELL)

        assert decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.SYMBOL_EXPOSURE_EXISTS in decision.reason_codes

    def test_a_short_position_blocks_another_sell(self) -> None:
        decision = decide(positions=(open_position(Side.SELL),), side=Side.SELL)

        assert decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.SYMBOL_EXPOSURE_EXISTS in decision.reason_codes

    def test_a_short_position_blocks_a_buy(self) -> None:
        """Implied rather than listed, and the symmetric half of the case above."""
        decision = decide(positions=(open_position(Side.SELL),), side=Side.BUY)

        assert decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.SYMBOL_EXPOSURE_EXISTS in decision.reason_codes


class TestTheRuleIsAboutOneInstrument:
    def test_a_position_in_another_instrument_does_not_block_this_one(self) -> None:
        """O-004 is per symbol. The portfolio cap is the rule for the rest."""
        decision = decide(
            positions=(open_position(Side.BUY, broker_symbol="GBPUSD"),), side=Side.BUY
        )

        assert ReasonCode.SYMBOL_EXPOSURE_EXISTS not in decision.reason_codes

    def test_the_refusal_is_distinguishable_from_the_portfolio_cap(self) -> None:
        """Two rules, two codes.

        An incident report saying MAX_OPEN_POSITIONS sends an operator to the
        configuration; one saying SYMBOL_EXPOSURE_EXISTS sends them to a
        decision the owner made. Sharing a code would send them to the wrong
        place half the time.
        """
        decision = evaluate(
            portfolio_state=portfolio(
                open_positions=(open_position(Side.BUY),), open_risk_fraction=Decimal("0.005")
            ),
        )

        assert ReasonCode.SYMBOL_EXPOSURE_EXISTS in decision.reason_codes
        assert ReasonCode.MAX_OPEN_POSITIONS in decision.reason_codes


class TestTheRuleIsNotConfigurable:
    def test_the_limit_is_a_constant_not_a_config_field(self) -> None:
        """It is an owner decision, not a budget.

        A YAML key would let someone raise it without the decision that should
        accompany doing so. Raising it means a code change, a review, and a
        row in status.md §10.
        """
        assert policies.MAX_EXPOSURES_PER_SYMBOL == 1
        assert "max_exposures_per_symbol" not in RiskConfig.model_fields

    def test_raising_the_portfolio_cap_does_not_lift_the_exposure_rule(self) -> None:
        """The failure this guards against: a config change quietly enabling stacking."""
        from tests.unit.test_risk_engine import context

        decision = evaluate(
            portfolio_state=portfolio(
                open_positions=(open_position(Side.BUY),), open_risk_fraction=Decimal("0.005")
            ),
            risk_context=context(
                risk=risk_config(max_open_positions=10, max_open_risk=Decimal("0.10"))
            ),
        )

        assert decision.verdict is RiskVerdict.BLOCK
        assert ReasonCode.SYMBOL_EXPOSURE_EXISTS in decision.reason_codes
        assert ReasonCode.MAX_OPEN_POSITIONS not in decision.reason_codes


class TestTheReplayNeverStacks:
    """The property end to end, rather than one check in isolation."""

    @pytest.mark.parametrize("bars", [400])
    def test_no_replay_window_ever_holds_two_positions(self, bars: int) -> None:
        from pathlib import Path

        from scripts.run_replay import build_instrument_spec

        from crumblr.application.orchestration import ReplayOrchestrator
        from crumblr.config import load_config
        from crumblr.domain.enums import Environment
        from crumblr.market_data.synthetic import SyntheticMarketConfig, generate_ticks
        from crumblr.mt5_gateway.simulated import SimulatedBroker

        repo_root = Path(__file__).resolve().parents[2]
        shipped = load_config(Environment.PAPER, config_dir=repo_root / "config")
        agent = shipped.trading_agent.model_copy(update={"strategy_id": "baseline_v1"})
        config = shipped.model_copy(update={"trading_agent": agent})

        spec = build_instrument_spec()
        broker = SimulatedBroker(
            spec, starting_balance=EQUITY, server=config.account_guard.expected_server
        )
        result = ReplayOrchestrator(config, spec, broker, starting_equity=EQUITY).run(
            list(generate_ticks(SyntheticMarketConfig(bar_count=bars), spec))
        )

        worst = max(len(capsule.position_state_after) for capsule in result.capsules)
        assert worst <= 1, f"the replay held {worst} simultaneous positions"
