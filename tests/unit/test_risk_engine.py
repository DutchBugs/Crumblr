"""The deterministic risk gateway (build.md §8).

Each test arranges exactly one violation and asserts the reason code, so a
regression names the check that broke. The sizing tests matter most: they are
the arithmetic standing between a risk budget and an account.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from crumblr.config import ExecutionConfig, RiskConfig
from crumblr.domain.enums import DataQuality, ReasonCode, RiskVerdict, Side
from crumblr.domain.models import (
    AccountState,
    InstrumentSpec,
    MarketSnapshot,
    PositionState,
    RiskDecision,
    TradeIntent,
)
from crumblr.risk import policies
from crumblr.risk.kill_switch import EquityLedger, KillSwitch
from crumblr.risk.sizing import loss_per_lot, normalise_volume, realised_risk, size_position
from tests.conftest import (
    FIXED_NOW,
    make_account_state,
    make_instrument_spec,
    make_intent,
    make_snapshot,
)

SPEC = make_instrument_spec()
EQUITY = Decimal("10000")


def risk_config(**overrides: object) -> RiskConfig:
    fields: dict[str, object] = {
        "max_risk_per_trade": Decimal("0.005"),
        "max_open_risk": Decimal("0.02"),
        "max_daily_loss": Decimal("0.02"),
        "max_drawdown": Decimal("0.10"),
        "max_orders_per_hour": 6,
        "max_open_positions": 1,
        "min_stop_distance_points": 50,
    }
    fields.update(overrides)
    return RiskConfig.model_validate(fields)


def execution_config(**overrides: object) -> ExecutionConfig:
    fields: dict[str, object] = {
        "max_spread_points": 25,
        "max_market_data_age_ms": 2000,
        "order_timeout_ms": 5000,
        "max_slippage_points": 20,
    }
    fields.update(overrides)
    return ExecutionConfig.model_validate(fields)


def context(
    *,
    risk: RiskConfig | None = None,
    execution: ExecutionConfig | None = None,
    allowed_symbols: frozenset[str] = frozenset({"EUR/USD"}),
    require_demo_account: bool = True,
    expected_server: str = "DemoBroker-Demo",
    expected_login: int | None = None,
    expected_currency: str | None = None,
    expected_leverage: int | None = None,
) -> policies.RiskContext:
    return policies.RiskContext(
        risk=risk or risk_config(),
        execution=execution or execution_config(),
        allowed_symbols=allowed_symbols,
        require_demo_account=require_demo_account,
        expected_server=expected_server,
        expected_login=expected_login,
        expected_currency=expected_currency,
        expected_leverage=expected_leverage,
        risk_config_version="cfg-v1",
    )


def portfolio(
    *,
    account: AccountState | None = None,
    open_positions: tuple[PositionState, ...] = (),
    ledger: EquityLedger | None = None,
    orders_in_last_hour: int = 0,
    seen_decision_hashes: frozenset[str] = frozenset(),
    open_risk_fraction: Decimal = Decimal("0"),
) -> policies.PortfolioState:
    return policies.PortfolioState(
        account=account or make_account_state(equity=EQUITY, balance=EQUITY),
        open_positions=open_positions,
        ledger=ledger or EquityLedger(starting_equity=EQUITY),
        orders_in_last_hour=orders_in_last_hour,
        seen_decision_hashes=seen_decision_hashes,
        open_risk_fraction=open_risk_fraction,
    )


def healthy_intent(**overrides: object) -> TradeIntent:
    fields: dict[str, object] = {
        "created_at_utc": FIXED_NOW,
        "expires_at_utc": FIXED_NOW + timedelta(seconds=30),
        "stop_loss_price": Decimal("1.08000"),
        "requested_risk_fraction": Decimal("0.005"),
    }
    fields.update(overrides)
    return make_intent(**fields)


def evaluate(
    *,
    intent: TradeIntent | None = None,
    snapshot: MarketSnapshot | None = None,
    spec: InstrumentSpec = SPEC,
    portfolio_state: policies.PortfolioState | None = None,
    risk_context: policies.RiskContext | None = None,
    kill_switch: KillSwitch | None = None,
    now: datetime = FIXED_NOW + timedelta(milliseconds=100),
) -> RiskDecision:
    """Evaluate a healthy BUY intent unless the test overrides something."""
    return policies.evaluate(
        intent or healthy_intent(),
        snapshot or make_snapshot(event_time_utc=FIXED_NOW),
        spec,
        portfolio_state or portfolio(),
        risk_context or context(),
        kill_switch or KillSwitch(),
        now=now,
    )


class TestHealthyIntentPasses:
    def test_a_clean_intent_is_approved_with_a_volume(self) -> None:
        decision = evaluate()
        assert decision.verdict is RiskVerdict.PASS
        assert decision.approved_volume is not None
        assert decision.reason_codes == ()

    def test_the_approved_volume_respects_the_budget(self) -> None:
        decision = evaluate()
        assert decision.risk_amount is not None
        assert decision.risk_amount <= EQUITY * Decimal("0.005")


class TestMarketDataChecks:
    def test_stale_data_is_blocked(self) -> None:
        decision = evaluate(now=FIXED_NOW + timedelta(seconds=30))
        assert ReasonCode.STALE_MARKET_DATA in decision.reason_codes

    def test_suspect_quality_is_blocked(self) -> None:
        decision = evaluate(
            snapshot=make_snapshot(event_time_utc=FIXED_NOW, data_quality=DataQuality.SUSPECT)
        )
        assert ReasonCode.STALE_MARKET_DATA in decision.reason_codes

    def test_a_wide_spread_is_blocked(self) -> None:
        decision = evaluate(
            snapshot=make_snapshot(
                event_time_utc=FIXED_NOW,
                bid=Decimal("1.08500"),
                ask=Decimal("1.09000"),
                spread_points=500,
            )
        )
        assert ReasonCode.SPREAD_TOO_WIDE in decision.reason_codes

    def test_a_future_timestamp_is_treated_as_stale(self) -> None:
        """Clock skew must not read as fresh data."""
        decision = evaluate(now=FIXED_NOW - timedelta(seconds=5))
        assert ReasonCode.STALE_MARKET_DATA in decision.reason_codes


class TestAccountGuard:
    def test_a_live_account_in_paper_mode_halts(self) -> None:
        decision = evaluate(
            portfolio_state=portfolio(account=make_account_state(is_demo=False, equity=EQUITY))
        )
        assert decision.verdict is RiskVerdict.HALT
        assert ReasonCode.LIVE_ACCOUNT_IN_PAPER_MODE in decision.reason_codes

    def test_the_wrong_server_halts(self) -> None:
        decision = evaluate(
            portfolio_state=portfolio(
                account=make_account_state(server="Other-Live", equity=EQUITY)
            )
        )
        assert decision.verdict is RiskVerdict.HALT
        assert ReasonCode.WRONG_ACCOUNT in decision.reason_codes

    def test_the_wrong_login_halts(self) -> None:
        decision = evaluate(risk_context=context(expected_login=999))
        assert ReasonCode.WRONG_ACCOUNT in decision.reason_codes

    def test_a_disconnected_account_is_blocked(self) -> None:
        decision = evaluate(
            portfolio_state=portfolio(account=make_account_state(connected=False, equity=EQUITY))
        )
        assert ReasonCode.ACCOUNT_NOT_CONNECTED in decision.reason_codes

    def test_disabled_expert_trading_is_blocked(self) -> None:
        decision = evaluate(
            portfolio_state=portfolio(
                account=make_account_state(expert_allowed=False, equity=EQUITY)
            )
        )
        assert ReasonCode.EXPERT_TRADING_DISABLED in decision.reason_codes


class TestExposureLimits:
    def test_the_open_position_limit_is_enforced(self) -> None:
        position = PositionState(
            ticket=1,
            broker_symbol="EURUSD",
            side=Side.BUY,
            volume=Decimal("0.05"),
            open_price=Decimal("1.08500"),
            opened_at_utc=FIXED_NOW,
            profit=Decimal("0"),
            swap=Decimal("0"),
            observed_at_utc=FIXED_NOW,
        )
        decision = evaluate(portfolio_state=portfolio(open_positions=(position,)))
        assert ReasonCode.MAX_OPEN_POSITIONS in decision.reason_codes

    def test_the_order_frequency_limit_is_enforced(self) -> None:
        decision = evaluate(portfolio_state=portfolio(orders_in_last_hour=6))
        assert ReasonCode.ORDER_FREQUENCY_LIMIT in decision.reason_codes

    def test_an_oversized_risk_request_is_blocked(self) -> None:
        decision = evaluate(
            intent=healthy_intent(
                stop_loss_price=Decimal("1.08000"),
                requested_risk_fraction=Decimal("0.05"),
            )
        )
        assert ReasonCode.RISK_PER_TRADE_LIMIT in decision.reason_codes

    def test_the_portfolio_risk_budget_is_enforced(self) -> None:
        decision = evaluate(portfolio_state=portfolio(open_risk_fraction=Decimal("0.02")))
        assert ReasonCode.OPEN_RISK_LIMIT in decision.reason_codes


class TestLossGates:
    def test_the_daily_loss_gate_halts(self) -> None:
        ledger = EquityLedger(starting_equity=EQUITY)
        ledger.update(EQUITY * Decimal("0.97"))
        decision = evaluate(portfolio_state=portfolio(ledger=ledger))
        assert decision.verdict is RiskVerdict.HALT
        assert ReasonCode.DAILY_LOSS_LIMIT in decision.reason_codes

    def test_the_drawdown_gate_halts(self) -> None:
        ledger = EquityLedger(starting_equity=EQUITY)
        ledger.update(EQUITY * Decimal("1.20"))
        ledger.start_new_session()
        ledger.update(EQUITY)
        decision = evaluate(portfolio_state=portfolio(ledger=ledger))
        assert ReasonCode.MAX_DRAWDOWN in decision.reason_codes


class TestIntentValidity:
    def test_an_expired_intent_is_blocked(self) -> None:
        decision = evaluate(now=FIXED_NOW + timedelta(seconds=31))
        assert ReasonCode.INTENT_EXPIRED in decision.reason_codes

    def test_a_duplicate_intent_is_blocked(self) -> None:
        intent = healthy_intent()
        decision = evaluate(
            intent=intent,
            portfolio_state=portfolio(seen_decision_hashes=frozenset({intent.decision_hash})),
        )
        assert ReasonCode.DUPLICATE_INTENT in decision.reason_codes

    def test_an_unlisted_symbol_is_blocked(self) -> None:
        decision = evaluate(risk_context=context(allowed_symbols=frozenset({"GBP/USD"})))
        assert ReasonCode.SYMBOL_NOT_ALLOWED in decision.reason_codes

    def test_a_stop_tighter_than_policy_is_blocked(self) -> None:
        decision = evaluate(
            intent=healthy_intent(
                stop_loss_price=Decimal("1.08490"),
            )
        )
        assert ReasonCode.INVALID_STOP in decision.reason_codes


class TestKillSwitchInteraction:
    def test_a_halted_system_blocks_everything(self) -> None:
        switch = KillSwitch()
        switch.trip(
            reason_codes=(ReasonCode.RECONCILIATION_MISMATCH,),
            tripped_by="evaluator",
            occurred_at_utc=FIXED_NOW,
        )
        decision = evaluate(kill_switch=switch)
        assert ReasonCode.SYSTEM_HALTED in decision.reason_codes

    def test_an_agent_cannot_reset_a_halt(self) -> None:
        """build.md §8.2: reset needs an operator and an incident note."""
        switch = KillSwitch()
        switch.trip(
            reason_codes=(ReasonCode.MANUAL_HALT,),
            tripped_by="operator",
            occurred_at_utc=FIXED_NOW,
        )
        with pytest.raises(ValueError, match="identified operator"):
            switch.reset(operator="  ", incident_note="looks fine now")
        with pytest.raises(ValueError, match="incident note"):
            switch.reset(operator="levi", incident_note="")
        assert switch.is_halted

    def test_an_operator_can_reset_with_a_note(self) -> None:
        switch = KillSwitch()
        switch.trip(
            reason_codes=(ReasonCode.MANUAL_HALT,),
            tripped_by="operator",
            occurred_at_utc=FIXED_NOW,
        )
        switch.reset(operator="levi", incident_note="INC-1 closed: stale feed replaced")
        assert not switch.is_halted
        assert len(switch.history) == 1, "the halt must stay in the log after a reset"


class TestSizing:
    """The arithmetic between a risk budget and an account."""

    def test_size_scales_with_the_risk_budget(self) -> None:
        small = size_position(
            equity=EQUITY,
            risk_fraction=Decimal("0.005"),
            stop_distance_price=Decimal("0.00500"),
            spec=SPEC,
        )
        large = size_position(
            equity=EQUITY,
            risk_fraction=Decimal("0.010"),
            stop_distance_price=Decimal("0.00500"),
            spec=SPEC,
        )
        assert small.volume is not None and large.volume is not None
        assert large.volume > small.volume

    def test_a_wider_stop_produces_a_smaller_size(self) -> None:
        tight = size_position(
            equity=EQUITY,
            risk_fraction=Decimal("0.005"),
            stop_distance_price=Decimal("0.00200"),
            spec=SPEC,
        )
        wide = size_position(
            equity=EQUITY,
            risk_fraction=Decimal("0.005"),
            stop_distance_price=Decimal("0.02000"),
            spec=SPEC,
        )
        assert tight.volume is not None and wide.volume is not None
        assert wide.volume < tight.volume

    def test_realised_risk_never_exceeds_the_budget(self) -> None:
        """Sizing rounds down, so the carried risk is at most what was asked for."""
        for stop in ["0.00100", "0.00237", "0.00500", "0.01234", "0.05000"]:
            distance = Decimal(stop)
            result = size_position(
                equity=EQUITY,
                risk_fraction=Decimal("0.005"),
                stop_distance_price=distance,
                spec=SPEC,
            )
            if result.volume is None:
                continue
            assert realised_risk(result.volume, distance, SPEC) <= result.risk_amount

    def test_a_budget_below_the_broker_minimum_is_refused(self) -> None:
        result = size_position(
            equity=Decimal("50"),
            risk_fraction=Decimal("0.005"),
            stop_distance_price=Decimal("0.05000"),
            spec=SPEC,
        )
        assert result.volume is None
        assert result.rejection == "risk_budget_below_broker_minimum"

    def test_volume_is_snapped_down_to_the_broker_step(self) -> None:
        assert normalise_volume(Decimal("0.1749"), SPEC) == Decimal("0.17")

    def test_volume_is_capped_at_the_broker_maximum(self) -> None:
        assert normalise_volume(Decimal("500"), SPEC) == SPEC.volume_max

    def test_a_zero_stop_distance_is_refused(self) -> None:
        with pytest.raises(ValueError, match="stop distance must be positive"):
            loss_per_lot(Decimal("0"), SPEC)

    def test_non_positive_equity_is_refused(self) -> None:
        result = size_position(
            equity=Decimal("0"),
            risk_fraction=Decimal("0.005"),
            stop_distance_price=Decimal("0.00500"),
            spec=SPEC,
        )
        assert result.volume is None
        assert result.rejection == "non_positive_equity"


class TestTheAccountGuardChecksWhatItWasTold:
    """Pepperstone facts (O-001) are verified against the terminal, not assumed.

    Review 1.5 requires broker metadata to be discovered from the real account.
    Configuring a value is therefore a claim the guard checks, and a mismatch
    is a wrong account whatever the server name happens to say.
    """

    def test_a_matching_account_passes(self) -> None:
        decision = evaluate(
            risk_context=context(expected_currency="EUR", expected_leverage=30),
            portfolio_state=portfolio(
                account=make_account_state(
                    equity=EQUITY, balance=EQUITY, currency="EUR", leverage=30
                )
            ),
        )

        assert decision.verdict is RiskVerdict.PASS

    def test_the_wrong_account_currency_is_refused(self) -> None:
        """0.5% of an account is a different amount of money in another currency."""
        decision = evaluate(
            risk_context=context(expected_currency="EUR"),
            portfolio_state=portfolio(
                account=make_account_state(equity=EQUITY, balance=EQUITY, currency="USD")
            ),
        )

        assert decision.verdict is RiskVerdict.HALT
        assert ReasonCode.WRONG_ACCOUNT in decision.reason_codes

    def test_unexpected_leverage_is_refused(self) -> None:
        """Leverage sets margin per lot, and nothing else would notice a change."""
        decision = evaluate(
            risk_context=context(expected_leverage=30),
            portfolio_state=portfolio(
                account=make_account_state(equity=EQUITY, balance=EQUITY, leverage=500)
            ),
        )

        assert decision.verdict is RiskVerdict.HALT
        assert ReasonCode.WRONG_ACCOUNT in decision.reason_codes

    def test_an_unconfigured_expectation_checks_nothing(self) -> None:
        """Absent means "not yet discovered", which cannot be a refusal.

        The values are unknown until the demo account exists. Refusing on that
        basis would make the platform unstartable rather than safer, and the
        demo-account guard already covers the case that actually matters.
        """
        decision = evaluate(
            risk_context=context(),
            portfolio_state=portfolio(
                account=make_account_state(
                    equity=EQUITY, balance=EQUITY, currency="USD", leverage=500
                )
            ),
        )

        assert ReasonCode.WRONG_ACCOUNT not in decision.reason_codes

    def test_the_shipped_paper_configuration_carries_the_pepperstone_facts(self) -> None:
        """What the owner supplied on 2026-08-18, asserted rather than assumed."""
        from pathlib import Path

        from crumblr.config import load_config
        from crumblr.domain.enums import Environment

        repo_root = Path(__file__).resolve().parents[2]
        guard = load_config(Environment.PAPER, config_dir=repo_root / "config").account_guard

        assert guard.expected_server == "PepperstoneUK-Demo"
        assert guard.expected_currency == "EUR"
        assert guard.expected_leverage == 30
        assert guard.require_demo_account is True
        assert guard.expected_login is None, (
            "an account identity belongs in the secret store, not in config"
        )
