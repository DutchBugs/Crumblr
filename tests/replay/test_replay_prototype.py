"""End-to-end replay of the production flow (build.md §13.3).

These are the tests that make the prototype worth having: they assert that the
control plane actually refuses things, not merely that the pipeline runs. Each
one arranges a condition the platform is required to survive and checks the
recorded reason code, because "it did not trade" and "it refused for the right
reason" are different claims.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from scripts.run_replay import build_instrument_spec

from crumblr.application.orchestration import ReplayOrchestrator, RunResult
from crumblr.config import PlatformConfig, load_config
from crumblr.domain.enums import Environment, ReasonCode, RiskVerdict, Side
from crumblr.domain.models import ApprovedOrder, InstrumentSpec
from crumblr.market_data.synthetic import (
    FaultInjection,
    GeneratedTick,
    SyntheticMarketConfig,
    generate_ticks,
)
from crumblr.mt5_gateway.simulated import SimulatedBroker

REPO_ROOT = Path(__file__).resolve().parents[2]
STARTING_BALANCE = Decimal("10000")


@pytest.fixture(scope="module")
def shipped_config() -> PlatformConfig:
    """The configuration as committed — currently running `ict_v1`."""
    return load_config(Environment.PAPER, config_dir=REPO_ROOT / "config")


@pytest.fixture(scope="module")
def config(shipped_config: PlatformConfig) -> PlatformConfig:
    """The same platform, driven by `baseline_v1`.

    These tests exercise the *pipeline* — that refusals are recorded, that a
    halt stops execution, that fills trace back to an approved volume. They
    need a strategy that trades often enough to produce those events, and
    `ict_v1` deliberately does not: it demands a full confluence and finds
    almost none on a random walk.

    Swapping the strategy here keeps the pipeline tests honest instead of
    loosening the entry model to make them pass. `ict_v1` has its own tests in
    `tests/unit/test_ict_strategy.py`, built on hand-constructed setups where
    the right answer is known, and `TestIctIntegration` below proves it runs
    through this same pipeline.
    """
    agent = shipped_config.trading_agent.model_copy(update={"strategy_id": "baseline_v1"})
    return shipped_config.model_copy(update={"trading_agent": agent})


def replay(
    config: PlatformConfig,
    *,
    bars: int = 400,
    seed: int = 20260817,
    faults: FaultInjection | None = None,
    balance: Decimal = STARTING_BALANCE,
    server: str | None = None,
) -> RunResult:
    spec = build_instrument_spec()
    broker = SimulatedBroker(
        spec,
        starting_balance=balance,
        server=server or config.account_guard.expected_server,
    )
    ticks = list(
        generate_ticks(
            SyntheticMarketConfig(seed=seed, bar_count=bars, faults=faults or FaultInjection()),
            spec,
        )
    )
    return ReplayOrchestrator(config, spec, broker, starting_equity=balance).run(ticks)


def _decision_hashes(run: RunResult) -> list[str]:
    return [c.trade_intent.decision_hash for c in run.capsules if c.trade_intent is not None]


def _build_order(
    *,
    spec: InstrumentSpec,
    side: Side,
    tick: GeneratedTick,
    stop_loss_price: Decimal,
    take_profit_price: Decimal,
    volume: Decimal = Decimal("0.05"),
) -> ApprovedOrder:
    from uuid import uuid4

    from crumblr.domain.enums import EntryType

    return ApprovedOrder(
        order_request_id=uuid4(),
        intent_id=uuid4(),
        intent_risk_decision_id=uuid4(),
        supervisor_decision_id=uuid4(),
        broker_symbol=spec.broker_symbol,
        side=side,
        entry_type=EntryType.MARKET,
        volume=volume,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        max_slippage_points=20,
        created_at_utc=tick.received_time_utc,
        expires_at_utc=tick.received_time_utc.replace(year=2030),
        environment=Environment.PAPER,
    )


class TestTheFlowRuns:
    def test_every_window_is_accounted_for(self, config: PlatformConfig) -> None:
        result = replay(config)
        tally = result.tally
        assert tally.windows == 400
        assert tally.features_unavailable + tally.no_trade + tally.intents == tally.windows

    def test_no_trade_is_the_common_outcome(self, config: PlatformConfig) -> None:
        """build.md §30.4 treats NO_TRADE as first class, not as a failure.

        Measured with the loss gates widened so the run does not halt. This is
        a claim about how selective the *strategy* is, and a halted run cannot
        answer it: the agent is not told about halts by design, so it keeps
        proposing while the risk engine refuses everything, and the intent
        count then measures the halt rather than the strategy.
        """
        risk = config.risk.model_copy(
            update={"max_daily_loss": Decimal("0.5"), "max_drawdown": Decimal("0.9")}
        )
        tally = replay(config.model_copy(update={"risk": risk})).tally
        assert not replay(config.model_copy(update={"risk": risk})).halted
        assert tally.no_trade > tally.intents

    def test_the_agent_keeps_proposing_after_a_halt(self, config: PlatformConfig) -> None:
        """Separation of powers: the agent is not told the system is halted.

        It proposes, the risk engine refuses. That keeps the record of what the
        strategy would have done, which is the counterfactual build.md §25.1
        wants, and it keeps the halt decision in one place instead of two.
        """
        risk = config.risk.model_copy(update={"max_daily_loss": Decimal("0.0005")})
        result = replay(config.model_copy(update={"risk": risk}), bars=900)
        assert result.halted
        assert result.tally.intents > 0
        assert result.tally.risk_reasons.get(ReasonCode.SYSTEM_HALTED.value, 0) > 0
        assert result.tally.orders_filled < result.tally.intents

    def test_a_capsule_is_sealed_for_every_evaluated_window(self, config: PlatformConfig) -> None:
        result = replay(config)
        evaluated = result.tally.windows - result.tally.features_unavailable
        assert len(result.capsules) == evaluated

    def test_orders_only_follow_a_pass_and_an_approval(self, config: PlatformConfig) -> None:
        tally = replay(config).tally
        assert tally.orders_filled <= tally.supervisor_approved <= tally.risk_passed


class TestDeterminism:
    """build.md §13.3: the same input must produce the same result."""

    def test_two_identical_runs_agree_exactly(self, config: PlatformConfig) -> None:
        first, second = replay(config), replay(config)
        assert first.final_equity == second.final_equity
        assert first.tally.orders_filled == second.tally.orders_filled
        assert [c.capsule_id for c in first.capsules] == [c.capsule_id for c in second.capsules]

    def test_decision_hashes_reproduce(self, config: PlatformConfig) -> None:
        first, second = replay(config), replay(config)
        assert _decision_hashes(first) == _decision_hashes(second)
        assert _decision_hashes(first), "the run produced no intents to compare"

    def test_provenance_fingerprints_reproduce(self, config: PlatformConfig) -> None:
        first, second = replay(config), replay(config)
        assert [c.provenance_fingerprint for c in first.capsules] == [
            c.provenance_fingerprint for c in second.capsules
        ]

    def test_a_different_seed_produces_a_different_run(self, config: PlatformConfig) -> None:
        assert replay(config).final_equity != replay(config, seed=99).final_equity


class TestGuardrailsFire:
    """Each fault must produce its own recorded reason code."""

    def test_a_spread_spike_blocks_the_trade(self, config: PlatformConfig) -> None:
        result = replay(config, bars=900, faults=FaultInjection(spread_spike_every=7))
        assert result.tally.risk_reasons.get(ReasonCode.SPREAD_TOO_WIDE.value, 0) > 0

    def test_a_stale_tick_blocks_the_trade(self, config: PlatformConfig) -> None:
        result = replay(config, bars=900, faults=FaultInjection(stale_tick_every=7))
        assert result.tally.risk_reasons.get(ReasonCode.STALE_MARKET_DATA.value, 0) > 0

    def test_suspect_data_quality_blocks_the_trade(self, config: PlatformConfig) -> None:
        """A strategy must not trade on SUSPECT data (build.md §12.3)."""
        result = replay(config, bars=900, faults=FaultInjection(suspect_quality_every=7))
        assert result.tally.risk_reasons.get(ReasonCode.STALE_MARKET_DATA.value, 0) > 0

    def test_a_faulty_window_never_reaches_execution(self, config: PlatformConfig) -> None:
        """Whatever else happens, no capsule pairs a refusal with a fill."""
        result = replay(
            config,
            bars=900,
            faults=FaultInjection(
                spread_spike_every=7, stale_tick_every=11, suspect_quality_every=13
            ),
        )
        for capsule in result.capsules:
            if capsule.risk_decision is None:
                continue
            if capsule.risk_decision.verdict is not RiskVerdict.PASS:
                assert capsule.execution_result is None, (
                    f"capsule {capsule.capsule_id} was refused "
                    f"({capsule.risk_decision.reason_codes}) yet carries a fill"
                )

    def test_the_wrong_server_halts_the_system(self, config: PlatformConfig) -> None:
        """build.md §8.2: an account mismatch is a halt, not a warning."""
        result = replay(config, bars=400, server="SomeOtherBroker-Live")
        assert result.halted
        assert ReasonCode.WRONG_ACCOUNT in result.halt_reasons
        assert result.tally.orders_filled == 0


class TestKillSwitch:
    """build.md §8.2. Tripping is automatic; resetting is not."""

    @staticmethod
    def _hair_trigger(config: PlatformConfig) -> PlatformConfig:
        """Same configuration with a daily-loss gate any losing trade will breach.

        Forcing the gate beats hunting for a seed that happens to lose money:
        the behaviour under test is the halt, not the strategy's luck.
        """
        risk = config.risk.model_copy(update={"max_daily_loss": Decimal("0.0005")})
        return config.model_copy(update={"risk": risk})

    def test_a_loss_gate_halts_the_system(self, config: PlatformConfig) -> None:
        result = replay(self._hair_trigger(config), bars=900)
        assert result.halted, "the daily-loss gate never tripped"
        assert ReasonCode.DAILY_LOSS_LIMIT in result.halt_reasons

    def test_nothing_is_submitted_after_the_halt(self, config: PlatformConfig) -> None:
        result = replay(self._hair_trigger(config), bars=900)
        halted_index = next(
            (
                index
                for index, capsule in enumerate(result.capsules)
                if capsule.risk_decision is not None
                and ReasonCode.SYSTEM_HALTED in capsule.risk_decision.reason_codes
            ),
            None,
        )
        assert halted_index is not None, "no capsule recorded the halt"
        assert all(
            capsule.execution_result is None for capsule in result.capsules[halted_index:]
        ), "orders were still submitted after the kill switch tripped"

    def test_the_halt_is_recorded_with_a_reason(self, config: PlatformConfig) -> None:
        result = replay(self._hair_trigger(config), bars=900)
        assert result.halt_reasons, "a halt must record why it happened"


class TestSeparationOfPowers:
    """The architectural claim from build.md §1, checked against real runs."""

    def test_no_intent_ever_carries_a_size(self, config: PlatformConfig) -> None:
        result = replay(config)
        for capsule in result.capsules:
            if capsule.trade_intent is not None:
                assert not hasattr(capsule.trade_intent, "volume")
                assert not hasattr(capsule.trade_intent, "lot_size")

    def test_every_fill_traces_back_to_a_risk_approved_volume(self, config: PlatformConfig) -> None:
        result = replay(config)
        fills = 0
        for capsule in result.capsules:
            if capsule.execution_result is None:
                continue
            fills += 1
            assert capsule.risk_decision is not None
            assert capsule.risk_decision.verdict is RiskVerdict.PASS
            assert capsule.supervisor_decision is not None
            assert capsule.execution_result.executed_volume == (
                capsule.risk_decision.approved_volume
            )
        assert fills > 0, "the run produced no fills to verify"

    def test_approved_risk_never_exceeds_the_configured_budget(
        self, config: PlatformConfig
    ) -> None:
        result = replay(config)
        for capsule in result.capsules:
            decision = capsule.risk_decision
            if decision is None or decision.verdict is not RiskVerdict.PASS:
                continue
            assert decision.risk_amount is not None
            assert decision.account_equity is not None
            budget = decision.account_equity * config.risk.max_risk_per_trade
            assert decision.risk_amount <= budget

    def test_stops_are_always_protective(self, config: PlatformConfig) -> None:
        result = replay(config)
        for capsule in result.capsules:
            intent = capsule.trade_intent
            if intent is None or intent.stop_loss_price is None:
                continue
            if intent.side is Side.BUY:
                assert intent.stop_loss_price < intent.reference_price
            else:
                assert intent.stop_loss_price > intent.reference_price


class TestMultiplePositionsPermitted:
    """Owner risk policy v1 (D1.3/D1.4): O-004 withdrawn, `OWNER_POLICY_V1.md`

    §2's own acceptance example proved end to end — the replacement for
    the deleted `test_one_exposure_policy.py::TestTheReplayNeverStacks`,
    which asserted the opposite of what the owner now requires.

    Not driven through the agent/strategy replay: `baseline_v1`'s own
    `already_positioned` guard (`trading_agent/baseline.py`) refuses to
    pyramid the *same* direction, so a second concurrent position can only
    occur via a direction reversal while the first is still open — rare
    enough on a synthetic random walk that forcing it would mean hunting
    seeds or loosening loss gates until one turns up. Doing that would be
    tuning against synthetic data in substance even without touching
    `baseline_v1` itself, which `CLAUDE.md` §4 rules out. Instead this
    drives `SimulatedBroker.order_send` directly — the same real
    broker/`PositionState` machinery `TestIdempotency` above exercises —
    so the proof is end to end through execution and real accounting
    without depending on the strategy ever choosing to stack.
    """

    def test_the_broker_may_hold_more_than_one_position(self) -> None:
        spec = build_instrument_spec()
        broker = SimulatedBroker(spec, starting_balance=STARTING_BALANCE)
        ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=5), spec))
        broker.advance(ticks[0])

        broker.order_send(
            _build_order(
                spec=spec,
                side=Side.BUY,
                tick=ticks[0],
                stop_loss_price=ticks[0].bid - Decimal("0.00300"),
                take_profit_price=ticks[0].ask + Decimal("0.00600"),
            )
        )
        broker.order_send(
            _build_order(
                spec=spec,
                side=Side.SELL,
                tick=ticks[0],
                stop_loss_price=ticks[0].ask + Decimal("0.00300"),
                take_profit_price=ticks[0].bid - Decimal("0.00600"),
            )
        )

        assert len(broker.positions()) == 2

    def test_the_resulting_book_is_valued_within_the_owner_budget(self) -> None:
        """The real book two open positions produce, valued by

        `risk/portfolio_risk.py::assess_open_risk` against the broker's
        own equity, must fit inside the owner's `max_open_risk` (`0.03`)
        — proof that real accounting, not a count, is what would gate a
        third.
        """
        from crumblr.risk.portfolio_risk import assess_open_risk

        spec = build_instrument_spec()
        broker = SimulatedBroker(spec, starting_balance=STARTING_BALANCE)
        ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=5), spec))
        broker.advance(ticks[0])

        broker.order_send(
            _build_order(
                spec=spec,
                side=Side.BUY,
                tick=ticks[0],
                stop_loss_price=ticks[0].bid - Decimal("0.00300"),
                take_profit_price=ticks[0].ask + Decimal("0.00600"),
                volume=Decimal("0.33"),
            )
        )
        broker.order_send(
            _build_order(
                spec=spec,
                side=Side.SELL,
                tick=ticks[0],
                stop_loss_price=ticks[0].ask + Decimal("0.00300"),
                take_profit_price=ticks[0].bid - Decimal("0.00600"),
                volume=Decimal("0.33"),
            )
        )

        assessment = assess_open_risk(
            broker.positions(), specs={spec.broker_symbol: spec}, equity=broker.equity
        )
        assert assessment.is_established
        assert assessment.fraction is not None
        assert Decimal("0") < assessment.fraction <= Decimal("0.03")


class TestIdempotency:
    """build.md §7 invariant 2 — a retry must not double the exposure."""

    def test_resubmitting_an_order_does_not_open_a_second_position(
        self, config: PlatformConfig
    ) -> None:
        from uuid import uuid4

        from crumblr.domain.enums import EntryType
        from crumblr.domain.models import ApprovedOrder

        spec = build_instrument_spec()
        broker = SimulatedBroker(spec, starting_balance=STARTING_BALANCE)
        ticks = list(generate_ticks(SyntheticMarketConfig(bar_count=5), spec))
        broker.advance(ticks[0])

        order = ApprovedOrder(
            order_request_id=uuid4(),
            intent_id=uuid4(),
            intent_risk_decision_id=uuid4(),
            supervisor_decision_id=uuid4(),
            broker_symbol=spec.broker_symbol,
            side=Side.BUY,
            entry_type=EntryType.MARKET,
            volume=Decimal("0.05"),
            stop_loss_price=ticks[0].bid - Decimal("0.00300"),
            take_profit_price=ticks[0].ask + Decimal("0.00600"),
            max_slippage_points=20,
            created_at_utc=ticks[0].received_time_utc,
            expires_at_utc=ticks[0].received_time_utc.replace(year=2030),
            environment=Environment.PAPER,
        )

        first = broker.order_send(order)
        second = broker.order_send(order)

        assert len(broker.positions()) == 1
        assert first.mt5_position_ticket == second.mt5_position_ticket
        assert first.execution_id == second.execution_id


class TestIctIntegration:
    """`ict_v1` must run through the same pipeline as any other strategy.

    These assertions are deliberately about plumbing, not performance. The ICT
    model is selective by design and the replay data is a random walk with no
    order flow, so how *often* it trades here carries no information — and any
    test that demanded a particular trade count would be pressure to loosen the
    entry model until the number came out right.
    """

    def test_the_shipped_configuration_runs_ict(self, shipped_config: PlatformConfig) -> None:
        assert shipped_config.trading_agent.strategy_id == "ict_v1"

    def test_ict_completes_a_replay(self, shipped_config: PlatformConfig) -> None:
        result = replay(shipped_config, bars=1200)
        assert result.tally.windows == 1200
        assert len(result.capsules) > 0

    def test_ict_records_why_it_declined(self, shipped_config: PlatformConfig) -> None:
        """Every refusal names the condition that failed."""
        result = replay(shipped_config, bars=1200)
        assert result.tally.no_trade_reasons
        assert any(
            reason.startswith(("outside_killzone", "no_liquidity_sweep", "no_fair_value_gap"))
            for reason in result.tally.no_trade_reasons
        )

    def test_ict_capsules_carry_its_own_feature_version(
        self, shipped_config: PlatformConfig
    ) -> None:
        result = replay(shipped_config, bars=1200)
        assert {c.feature_set_version for c in result.capsules} == {"ict-features-v1"}

    def test_ict_replay_is_deterministic(self, shipped_config: PlatformConfig) -> None:
        first = replay(shipped_config, bars=1200)
        second = replay(shipped_config, bars=1200)
        assert first.final_equity == second.final_equity
        assert [c.provenance_fingerprint for c in first.capsules] == [
            c.provenance_fingerprint for c in second.capsules
        ]

    def test_any_ict_intent_still_passes_through_risk_and_supervisor(
        self, shipped_config: PlatformConfig
    ) -> None:
        result = replay(shipped_config, bars=4000)
        for capsule in result.capsules:
            if capsule.execution_result is None:
                continue
            assert capsule.risk_decision is not None
            assert capsule.risk_decision.verdict is RiskVerdict.PASS
            assert capsule.supervisor_decision is not None
