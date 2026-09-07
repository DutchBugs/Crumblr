"""`ExecutionOrchestrator` must not silently execute a second market

against EUR/USD's platform-default risk/execution thresholds or trading
calendar (Market Universe, ADR-022, owner correction 2026-09-07).

Real PostgreSQL for the stores (construction only — this proves wiring,
not a full `run_once()` cycle), a fake MT5 terminal for the adapter
(`_execution_fixtures.FakeMt5`, the same one `test_execution_orchestrator
.py` uses).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from sqlalchemy import Engine

from crumblr.application.execution import ExecutionOrchestrator, _approval_chain_fingerprint
from crumblr.config import (
    ExecutionConfig,
    ExecutionOverrides,
    IntradayConfig,
    MarketConfig,
    PlatformConfig,
    RiskConfig,
    RiskOverrides,
    SupervisorConfig,
    TradingAgentConfig,
)
from crumblr.domain.enums import AssetClass, Environment, ExecutionEventType
from crumblr.domain.models import DecisionCapsule, InstrumentSpec
from crumblr.mt5_gateway.client import Mt5Client, Mt5Credentials
from crumblr.mt5_gateway.execution import OrderCheckMt5Gateway
from crumblr.persistence.broker_state import BrokerStateStore
from crumblr.persistence.execution import ExecutionEventStore, ExecutionRequestStore
from crumblr.persistence.flatten import FlattenEventStore, FlattenRequestStore
from crumblr.persistence.instrument_specs import InstrumentSpecStore
from crumblr.persistence.journal import CapsuleStore
from crumblr.persistence.risk_session import PostgresRiskLedgerLock, PostgresRiskSessionStore
from crumblr.risk.calendars import AlwaysOpenCalendar, FxWeekdayCalendar
from crumblr.risk.kill_switch import KillSwitch
from tests.conftest import FIXED_NOW, make_intent, make_risk_decision, make_supervisor_decision
from tests.integration._execution_fixtures import (
    APPROVED_CANARY_ACCOUNT_REF,
    LOGIN,
    SERVER,
    STRATEGY_VERSION,
    FakeMt5,
    guard,
)

pytestmark = pytest.mark.integration

BTC_OVERRIDE_DAILY_LOSS = Decimal("0.01")
BTC_OVERRIDE_SLIPPAGE = 5


def two_market_config() -> PlatformConfig:
    """EUR/USD (platform default, unchanged) plus BTC/USD, `enabled: true`

    **for this test config only** — never touches `config/paper.yaml`,
    which keeps BTC/USD `enabled: false` per the owner's explicit
    instruction.
    """
    return PlatformConfig(
        environment=Environment.PAPER,
        markets=(
            MarketConfig(
                canonical_symbol="EUR/USD",
                enabled=True,
                asset_class=AssetClass.FX,
                broker_symbol="EURUSD",
            ),
            MarketConfig(
                canonical_symbol="BTC/USD",
                enabled=True,
                asset_class=AssetClass.CRYPTO,
                broker_symbol="BTCUSD",
                risk_overrides=RiskOverrides(max_daily_loss=BTC_OVERRIDE_DAILY_LOSS),
                execution_overrides=ExecutionOverrides(max_slippage_points=BTC_OVERRIDE_SLIPPAGE),
            ),
        ),
        risk=RiskConfig.model_validate(
            {
                "max_risk_per_trade": "0.02",
                "max_open_risk": "0.03",
                "max_daily_loss": "0.04",
                "max_drawdown": "0.08",
                "max_orders_per_hour": 6,
                "max_open_positions": 10,
                "min_stop_distance_points": 50,
            }
        ),
        execution=ExecutionConfig.model_validate(
            {
                "max_spread_points": 25,
                "max_market_data_age_ms": 60_000,
                "order_timeout_ms": 5000,
                "max_slippage_points": 20,
            }
        ),
        trading_agent=TradingAgentConfig.model_validate(
            {"strategy_id": "baseline_v1", "strategy_version": "0.1.0", "model_version": None}
        ),
        supervisor=SupervisorConfig.model_validate(
            {
                "enabled": True,
                "veto_on_unknown_regime": False,
                "halt_on_reconciliation_mismatch": True,
                "policy_version": "policy-v1",
                "max_intents_per_hour": None,
            }
        ),
        account_guard=guard(),
        intraday=IntradayConfig.model_validate(
            {
                "enabled": False,
                "last_entry_minutes_before_close": 0,
                "flatten_minutes_before_close": 0,
            }
        ),
    )


def _orchestrator(
    engine: Engine,
    config: PlatformConfig,
    *,
    canonical_symbol: str,
    terminal: FakeMt5 | None = None,
    activation_watermark: Any = None,
    clock: Any = None,
) -> Any:
    resolved_clock = clock or (lambda: FIXED_NOW + timedelta(seconds=1))
    client = Mt5Client(terminal or FakeMt5())
    client.connect(Mt5Credentials(login=LOGIN, password="x", server=SERVER))
    # The adapter's own clock (broker-clock-offset detection against the
    # fake terminal's fixed tick timestamp) must match the orchestrator's,
    # or ReadOnlyMt5Gateway._clock_offset() sees a huge, implausible gap
    # between "now" and the fake tick's fixed FIXED_NOW timestamp and
    # raises — mirrors _execution_fixtures.py::orchestrator()'s own
    # identical two-clock wiring.
    adapter = OrderCheckMt5Gateway(client, guard(), clock=resolved_clock)
    return ExecutionOrchestrator(
        config,
        capsules=CapsuleStore(engine),
        requests=ExecutionRequestStore(engine),
        events=ExecutionEventStore(engine),
        flatten_requests=FlattenRequestStore(engine),
        flatten_events=FlattenEventStore(engine),
        broker_state=BrokerStateStore(engine),
        instrument_specs=InstrumentSpecStore(engine),
        session_store=PostgresRiskSessionStore(engine),
        risk_ledger_lock=PostgresRiskLedgerLock(engine),
        kill_switch=KillSwitch(),
        adapter=adapter,
        canonical_symbol=canonical_symbol,
        activation_watermark=activation_watermark,
        clock=resolved_clock,
        worker_id=f"test-worker-{canonical_symbol}",
    )


class _TwoSymbolFakeMt5(FakeMt5):
    """`FakeMt5` resolves only `EURUSD` — a BTC/USD-bound worker's

    `resolve_symbol()` (fail-closed, exact-match, ADR-022 slice 2) would
    otherwise raise before ever reaching the reconciliation logic this
    file's cross-market tests exist to prove. Same fake account/order
    behaviour otherwise; only the resolvable symbol table is wider.
    """

    def symbols_get(self, *_a: Any, **_k: Any) -> tuple[Any, ...]:
        from types import SimpleNamespace

        return (SimpleNamespace(name="EURUSD"), SimpleNamespace(name="BTCUSD"))


def _instrument_spec(*, canonical_symbol: str, broker_symbol: str) -> InstrumentSpec:
    return InstrumentSpec(
        canonical_symbol=canonical_symbol,
        broker_symbol=broker_symbol,
        currency_base="EUR",
        currency_profit="USD",
        contract_size=Decimal("100000"),
        digits=5,
        point=Decimal("0.00001"),
        tick_size=Decimal("0.00001"),
        tick_value=Decimal("1"),
        volume_min=Decimal("0.01"),
        volume_max=Decimal("100"),
        volume_step=Decimal("0.01"),
        stops_level=10,
        freeze_level=0,
        filling_modes=("IOC", "FOK"),
        trade_mode="FULL",
        captured_at_utc=FIXED_NOW,
    )


def _fully_approved_two_market_config(
    *, eur_spec_version: str, btc_spec_version: str
) -> PlatformConfig:
    """Both markets `enabled: true`, both spec-pinned, and the platform-

    wide submission gates open — **for this test config only**, never
    `config/paper.yaml`. The four governance fields
    (`submission_enabled`/`feedback_2_0_approved`/
    `approved_canary_account_ref`/`RiskConfig.approved_config_version`)
    are deliberately excluded from `RiskOverrides`/`ExecutionOverrides`
    (`config.py`), so setting them once here opens the gate for both
    markets identically — matching `_execution_fixtures.py`'s own
    documented reasoning for why that exclusion exists.
    """
    base = PlatformConfig(
        environment=Environment.PAPER,
        markets=(
            MarketConfig(
                canonical_symbol="EUR/USD",
                enabled=True,
                asset_class=AssetClass.FX,
                broker_symbol="EURUSD",
                expected_spec_version=eur_spec_version,
            ),
            MarketConfig(
                canonical_symbol="BTC/USD",
                enabled=True,
                asset_class=AssetClass.CRYPTO,
                broker_symbol="BTCUSD",
                expected_spec_version=btc_spec_version,
            ),
        ),
        risk=RiskConfig.model_validate(
            {
                "max_risk_per_trade": "0.02",
                "max_open_risk": "0.03",
                "max_daily_loss": "0.04",
                "max_drawdown": "0.08",
                "max_orders_per_hour": 6,
                "max_open_positions": 10,
                "min_stop_distance_points": 50,
            }
        ),
        execution=ExecutionConfig.model_validate(
            {
                "max_spread_points": 25,
                "max_market_data_age_ms": 60_000,
                "order_timeout_ms": 5000,
                "max_slippage_points": 20,
            }
        ),
        trading_agent=TradingAgentConfig.model_validate(
            {
                "strategy_id": "baseline_v1",
                "strategy_version": STRATEGY_VERSION,
                "model_version": None,
            }
        ),
        supervisor=SupervisorConfig.model_validate(
            {
                "enabled": True,
                "veto_on_unknown_regime": False,
                "halt_on_reconciliation_mismatch": True,
                "policy_version": "policy-v1",
                "max_intents_per_hour": None,
            }
        ),
        account_guard=guard(),
        intraday=IntradayConfig.model_validate(
            {
                "enabled": False,
                "last_entry_minutes_before_close": 0,
                "flatten_minutes_before_close": 0,
            }
        ),
    )
    version = base.config_version
    return base.model_copy(
        update={
            "risk": base.risk.model_copy(update={"approved_config_version": version}),
            "execution": base.execution.model_copy(
                update={
                    "submission_enabled": True,
                    "feedback_2_0_approved": True,
                    "approved_canary_account_ref": APPROVED_CANARY_ACCOUNT_REF,
                }
            ),
        }
    )


def _sealed_capsule(
    engine: Engine, config: PlatformConfig, *, canonical_symbol: str, broker_symbol: str
) -> DecisionCapsule:
    """A minimal PASS/APPROVE-shaped capsule for `canonical_symbol`, sealed

    into real PostgreSQL — enough for `run_once()`'s `_is_intent_time_approved`
    check and the cross-market read/claim proofs below, not a claim that
    it would reach `ORDER_CHECKED` (no `InstrumentSpec` is recorded for
    BTC/USD in these tests).
    """
    intent = make_intent(
        symbol=canonical_symbol,
        created_at_utc=FIXED_NOW,
        expires_at_utc=FIXED_NOW + timedelta(minutes=10),
        reference_price="1.08500",
        stop_loss_price="1.08000",
        take_profit_price="1.09000",
        requested_risk_fraction="0.005",
    )
    capsule = DecisionCapsule(
        capsule_id=uuid4(),
        occurred_at_utc=FIXED_NOW,
        correlation_id=uuid4(),
        canonical_symbol=canonical_symbol,
        broker_symbol=broker_symbol,
        market_snapshot_id=uuid4(),
        feature_set_version="features-v1",
        feature_values_hash="abc123",
        strategy_version=STRATEGY_VERSION,
        model_version=None,
        trade_intent=intent,
        risk_config_version=config.config_version,
        risk_decision=make_risk_decision(
            intent.intent_id,
            risk_config_version=config.config_version,
            approved_volume="0.05",
            account_equity="10000",
            stop_distance_points=500,
            risk_amount="50",
        ),
        supervisor_decision=make_supervisor_decision(intent.intent_id),
        code_commit="deadbeef",
        environment=Environment.PAPER,
    )
    CapsuleStore(engine).seal(capsule)
    return capsule


def _order_request_id(capsule: DecisionCapsule) -> Any:
    assert capsule.trade_intent is not None
    return uuid5(NAMESPACE_URL, f"crumblr:order:{capsule.trade_intent.decision_hash}")


class TestCapsuleStoreReadAllIsBoundedBySymbol:
    def test_a_symbol_filter_excludes_every_other_markets_capsule(self, engine: Engine) -> None:
        config = two_market_config()
        eur_capsule = _sealed_capsule(
            engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD"
        )
        btc_capsule = _sealed_capsule(
            engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD"
        )

        eur_only = CapsuleStore(engine).read_all(canonical_symbol="EUR/USD")
        btc_only = CapsuleStore(engine).read_all(canonical_symbol="BTC/USD")

        assert [c.capsule_id for c in eur_only] == [eur_capsule.capsule_id]
        assert [c.capsule_id for c in btc_only] == [btc_capsule.capsule_id]

    def test_no_filter_still_returns_every_market(self, engine: Engine) -> None:
        """Regression guard: the new parameter must stay optional and

        default-preserving — every existing caller that never passes it
        keeps today's exact behaviour."""
        config = two_market_config()
        _sealed_capsule(engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD")
        _sealed_capsule(engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD")

        assert len(CapsuleStore(engine).read_all()) == 2


class TestExecutionOrchestratorNeverClaimsAnotherMarketsCapsule:
    """The owner's core finding, 2026-09-07/08: `run_once()` read every

    capsule for the environment and relied on nothing to stop a worker
    from claiming/processing a capsule that belonged to a different
    market. Proven both directions — a EUR/USD worker must never touch a
    BTC/USD capsule, and vice versa — and proven at the persistence
    layer, not just by inspecting `run_once()`'s return value: the wrong
    capsule must leave no `execution_requests` claim and no
    `execution_events` row at all.
    """

    def test_a_eur_usd_worker_never_claims_or_processes_a_btc_usd_capsule(
        self, engine: Engine
    ) -> None:
        config = two_market_config()
        eur_capsule = _sealed_capsule(
            engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD"
        )
        btc_capsule = _sealed_capsule(
            engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD"
        )

        orchestrator = _orchestrator(engine, config, canonical_symbol="EUR/USD")
        outcomes = orchestrator.run_once()

        # The BTC/USD capsule was never even seen by this worker's pass.
        assert all(outcome.capsule_id != btc_capsule.capsule_id for outcome in outcomes)

        requests = ExecutionRequestStore(engine)
        events = ExecutionEventStore(engine)

        eur_order_request_id = _order_request_id(eur_capsule)
        btc_order_request_id = _order_request_id(btc_capsule)

        # The EUR/USD capsule *was* claimed by the real run above — a
        # fresh claim attempt with the same id and the same real
        # approval-chain fingerprint now loses the race (a mismatched
        # fingerprint would raise `ExecutionRequestConflictError` instead
        # of reporting `claimed=False` — not what this probe is testing).
        eur_reclaim = requests.claim(
            order_request_id=eur_order_request_id,
            capsule_id=eur_capsule.capsule_id,
            intent_id=eur_capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint=_approval_chain_fingerprint(eur_capsule),
            claimed_by="test-probe",
            now=FIXED_NOW,
        )
        assert eur_reclaim.claimed is False, "the real run must have claimed the EUR/USD capsule"

        # The BTC/USD capsule was never claimed at all — this probe claim
        # is the first and only one, so it wins.
        btc_probe_claim = requests.claim(
            order_request_id=btc_order_request_id,
            capsule_id=btc_capsule.capsule_id,
            intent_id=btc_capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="probe",
            claimed_by="test-probe",
            now=FIXED_NOW,
        )
        assert btc_probe_claim.claimed is True, (
            "the BTC/USD capsule must never have been claimed by the EUR/USD worker"
        )

        # And no durable execution event exists for it either, from
        # before this probe claim.
        assert events.events_for(btc_order_request_id) == ()

    def test_a_btc_usd_worker_never_claims_or_processes_a_eur_usd_capsule(
        self, engine: Engine
    ) -> None:
        config = two_market_config()
        eur_capsule = _sealed_capsule(
            engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD"
        )
        _sealed_capsule(engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD")

        orchestrator = _orchestrator(engine, config, canonical_symbol="BTC/USD")
        orchestrator.run_once()

        requests = ExecutionRequestStore(engine)
        events = ExecutionEventStore(engine)
        eur_order_request_id = _order_request_id(eur_capsule)

        eur_probe_claim = requests.claim(
            order_request_id=eur_order_request_id,
            capsule_id=eur_capsule.capsule_id,
            intent_id=eur_capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint="probe",
            claimed_by="test-probe",
            now=FIXED_NOW,
        )
        assert eur_probe_claim.claimed is True, (
            "the EUR/USD capsule must never have been claimed by the BTC/USD worker"
        )
        assert events.events_for(eur_order_request_id) == ()


class TestReconciliationHistoryIsBoundedBySymbol:
    """The owner's second finding, 2026-09-08:

    `ExecutionOrchestrator.reconcile_once()` read every `SUBMISSION_STARTED`
    request across the whole environment via `request_ids_with_event()`
    (unscoped), while the broker observation and `ExpectedState` it then
    builds are bound to `self._canonical_symbol` — a EUR/USD worker could
    derive expected exposure from a BTC/USD request, or record a
    `RECONCILED` event against one. Proven at the persistence layer (the
    raw join/filter) and at the orchestrator layer (a real two-market
    `run_once()` sequence, real PostgreSQL, real fake-MT5 fills).
    """

    def test_request_ids_with_event_is_bounded_by_symbol(self, engine: Engine) -> None:
        config = _fully_approved_two_market_config(eur_spec_version="v1", btc_spec_version="v1")
        eur_capsule = _sealed_capsule(
            engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD"
        )
        btc_capsule = _sealed_capsule(
            engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD"
        )
        events = ExecutionEventStore(engine)
        requests = ExecutionRequestStore(engine)
        eur_order_request_id = _order_request_id(eur_capsule)
        btc_order_request_id = _order_request_id(btc_capsule)
        for capsule, order_request_id in (
            (eur_capsule, eur_order_request_id),
            (btc_capsule, btc_order_request_id),
        ):
            requests.claim(
                order_request_id=order_request_id,
                capsule_id=capsule.capsule_id,
                intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
                fingerprint=_approval_chain_fingerprint(capsule),
                claimed_by="test-seed",
                now=FIXED_NOW,
            )
            events.append(
                order_request_id=order_request_id,
                event_type=ExecutionEventType.SUBMISSION_STARTED,
                occurred_at_utc=FIXED_NOW,
            )

        eur_candidates = events.request_ids_with_event(
            ExecutionEventType.SUBMISSION_STARTED,
            environment=Environment.PAPER,
            canonical_symbol="EUR/USD",
        )
        btc_candidates = events.request_ids_with_event(
            ExecutionEventType.SUBMISSION_STARTED,
            environment=Environment.PAPER,
            canonical_symbol="BTC/USD",
        )
        unscoped = events.request_ids_with_event(ExecutionEventType.SUBMISSION_STARTED)

        assert eur_candidates == (eur_order_request_id,)
        assert btc_candidates == (btc_order_request_id,)
        assert set(unscoped) == {eur_order_request_id, btc_order_request_id}

    def test_count_events_since_is_bounded_by_symbol(self, engine: Engine) -> None:
        config = _fully_approved_two_market_config(eur_spec_version="v1", btc_spec_version="v1")
        eur_capsule = _sealed_capsule(
            engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD"
        )
        btc_capsule = _sealed_capsule(
            engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD"
        )
        events = ExecutionEventStore(engine)
        requests = ExecutionRequestStore(engine)
        for capsule in (eur_capsule, btc_capsule):
            order_request_id = _order_request_id(capsule)
            requests.claim(
                order_request_id=order_request_id,
                capsule_id=capsule.capsule_id,
                intent_id=capsule.trade_intent.intent_id,  # type: ignore[union-attr]
                fingerprint=_approval_chain_fingerprint(capsule),
                claimed_by="test-seed",
                now=FIXED_NOW,
            )
            events.append(
                order_request_id=order_request_id,
                event_type=ExecutionEventType.SUBMISSION_STARTED,
                occurred_at_utc=FIXED_NOW,
            )

        since = FIXED_NOW - timedelta(hours=1)
        eur_count = events.count_events_since(
            ExecutionEventType.SUBMISSION_STARTED,
            since,
            environment=Environment.PAPER,
            canonical_symbol="EUR/USD",
        )
        btc_count = events.count_events_since(
            ExecutionEventType.SUBMISSION_STARTED,
            since,
            environment=Environment.PAPER,
            canonical_symbol="BTC/USD",
        )
        unscoped_count = events.count_events_since(ExecutionEventType.SUBMISSION_STARTED, since)

        assert eur_count == 1
        assert btc_count == 1
        assert unscoped_count == 2, "a per-market count of 1 each must not silently become 2"

    def test_a_eur_usd_worker_never_reconciles_or_derives_exposure_from_a_btc_usd_request(
        self, engine: Engine
    ) -> None:
        eur_instrument_spec = _instrument_spec(canonical_symbol="EUR/USD", broker_symbol="EURUSD")
        btc_instrument_spec = _instrument_spec(canonical_symbol="BTC/USD", broker_symbol="BTCUSD")
        InstrumentSpecStore(engine).record(eur_instrument_spec)
        InstrumentSpecStore(engine).record(btc_instrument_spec)
        config = _fully_approved_two_market_config(
            eur_spec_version=eur_instrument_spec.spec_version,
            btc_spec_version=btc_instrument_spec.spec_version,
        )
        eur_capsule = _sealed_capsule(
            engine, config, canonical_symbol="EUR/USD", broker_symbol="EURUSD"
        )
        btc_capsule = _sealed_capsule(
            engine, config, canonical_symbol="BTC/USD", broker_symbol="BTCUSD"
        )
        terminal = _TwoSymbolFakeMt5()
        watermark = FIXED_NOW - timedelta(seconds=1)

        eur_orch = _orchestrator(
            engine,
            config,
            canonical_symbol="EUR/USD",
            terminal=terminal,
            activation_watermark=watermark,
        )
        btc_orch = _orchestrator(
            engine,
            config,
            canonical_symbol="BTC/USD",
            terminal=terminal,
            activation_watermark=watermark,
        )

        # EUR/USD: the real, unmodified pipeline. Pass 1 claims and
        # submits (the capsule-routing fix from the prior corrective
        # pass); pass 2 resolves the ambiguity and reconciles.
        eur_first = eur_orch.run_once()
        assert eur_first[0].event_type == ExecutionEventType.SUBMISSION_STARTED
        eur_order_request_id = eur_first[0].order_request_id
        assert eur_order_request_id == _order_request_id(eur_capsule)

        # BTC/USD: seeded directly at the SUBMISSION_STARTED/
        # AMBIGUOUS_OUTCOME_RESOLVED{submitted:false} state
        # `_recover_ambiguous_submission` would itself produce on a real
        # pass — BTC/USD cannot reach entry eligibility today by the
        # *correct*, separately-fixed session-policy design (fail-closed,
        # `AlwaysOpenCalendar`, prior corrective pass), which is a
        # different concern from the one under test here: whether
        # *reconciliation itself*, given a request that has somehow
        # reached SUBMISSION_STARTED, stays bounded to its own market.
        requests = ExecutionRequestStore(engine)
        events = ExecutionEventStore(engine)
        btc_order_request_id = _order_request_id(btc_capsule)
        requests.claim(
            order_request_id=btc_order_request_id,
            capsule_id=btc_capsule.capsule_id,
            intent_id=btc_capsule.trade_intent.intent_id,  # type: ignore[union-attr]
            fingerprint=_approval_chain_fingerprint(btc_capsule),
            claimed_by="test-seed",
            now=FIXED_NOW,
        )
        events.append(
            order_request_id=btc_order_request_id,
            event_type=ExecutionEventType.SUBMISSION_STARTED,
            occurred_at_utc=FIXED_NOW,
        )
        events.append(
            order_request_id=btc_order_request_id,
            event_type=ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
            occurred_at_utc=FIXED_NOW,
            payload={
                "magic_number": 0,
                "submitted": False,
                "matching_position_count": 0,
                "matching_tickets": [],
            },
        )

        # The call under test, EUR/USD side: its internal
        # request_ids_with_event() must never see the BTC/USD request.
        eur_second = eur_orch.run_once()
        assert eur_second[0].event_type == ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED
        eur_events_after = events.events_for(eur_order_request_id)
        assert eur_events_after[-1].event_type == ExecutionEventType.RECONCILED

        # The BTC/USD request's history is untouched by the EUR/USD
        # worker's reconciliation pass above — still exactly the two
        # events seeded before it, no RECONCILED leaked across markets.
        btc_events_after_eur_pass = events.events_for(btc_order_request_id)
        assert [e.event_type for e in btc_events_after_eur_pass] == [
            ExecutionEventType.SUBMISSION_STARTED,
            ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED,
        ]

        # The call under test, BTC/USD side: now reconciles its own
        # request, and only its own.
        btc_outcomes = btc_orch.reconcile_once()
        assert len(btc_outcomes) == 1
        assert btc_outcomes[0].order_request_id == btc_order_request_id
        btc_events_final = events.events_for(btc_order_request_id)
        assert btc_events_final[-1].event_type == ExecutionEventType.RECONCILED

        # And the EUR/USD request's history is unchanged by the BTC/USD
        # worker's own reconciliation pass — nothing appended a second
        # time.
        eur_events_final = events.events_for(eur_order_request_id)
        assert eur_events_final == eur_events_after


class TestExecutionOrchestratorUsesTheMarketsOwnConfig:
    def test_a_second_markets_risk_and_execution_overrides_reach_the_orchestrator(
        self, engine: Engine
    ) -> None:
        config = two_market_config()
        orchestrator = _orchestrator(engine, config, canonical_symbol="BTC/USD")

        assert orchestrator._risk_config.max_daily_loss == BTC_OVERRIDE_DAILY_LOSS
        assert orchestrator._risk_config.max_daily_loss != config.risk.max_daily_loss
        assert orchestrator._execution_config.max_slippage_points == BTC_OVERRIDE_SLIPPAGE
        assert isinstance(orchestrator._calendar, AlwaysOpenCalendar)

    def test_eur_usd_is_unaffected(self, engine: Engine) -> None:
        config = two_market_config()
        orchestrator = _orchestrator(engine, config, canonical_symbol="EUR/USD")

        assert orchestrator._risk_config.max_daily_loss == config.risk.max_daily_loss
        assert (
            orchestrator._execution_config.max_slippage_points
            == config.execution.max_slippage_points
        )
        assert isinstance(orchestrator._calendar, FxWeekdayCalendar)
