"""TradeIntent -> intent-time Risk -> deterministic Policy -> DecisionCapsule.

Review 1.26 section 7 item 3 / feedback.1.27 section 6.A -- the "shared
no-MT5 integration path". A platform-owned `TradeIntent` the Agent Gateway
already constructed (`agent_gateway/gateway.py::_build_trade_intent`) is
evaluated through exactly the same Core Risk Engine
(`risk.policies.evaluate`) and deterministic Policy Gate
(`evaluator.pretrade.evaluate`) an internal strategy's intent goes through,
and sealed into a `DecisionCapsule` the same way. Nothing past that
boundary is reachable from here: no `ApprovedOrder`, no `order_check`, no
`order_send`. A NO_TRADE outcome (`intent=None`) still seals a capsule,
the same "every evaluated window is evidence" rule
`application/orchestration.py` and `application/live_decision.py` already
follow.

**No MT5 anywhere in this module or this package.** Account/position
state is never read directly here -- it is supplied by an injected
`PortfolioStateProvider`. The owner-requested reviewer decision
(`review/INTEGRATION_NOTICES.md`, 2026-09-01, "Record reviewer decision
for agent PortfolioState source") is explicit that broker-state sourcing
belongs behind the Agent Gateway boundary, in Core application
integration, never inside the Agent Gateway package and never inside the
external agent. A fake provider is correct for tests and for
synthetic/replay proof (`SimulatedBroker` included); a genuine
LIVE_SHADOW caller must back this with Core's own durable
`application.broker_state.capture_broker_state()` observation, driven
from a Core-adjacent process that holds the live MT5 connection --
structurally a sibling of `application/live_decision.py`, not code that
lives here (confirmed with Dev 1, `review/INTEGRATION_NOTICES.md`).

**AG-012 (`review/AGENT_FEEDBACK.md`).** `risk.policies.evaluate()`'s
`PortfolioState.ledger` is stateful per-process by design --
`application/live_decision.py` holds and mutates one across cycles. This
module deliberately does *not* do that: every call re-derives the ledger
fresh via `risk.session.recover_session()` rather than caching one across
calls -- the accepted shadow-only interim mitigation for two independent
processes each capable of evaluating against the same shared risk budget.
Not race-free without a single shared risk authority (still required
before `feedback.2.0` could treat agent-driven submission as real) --
this narrows the staleness window, it does not close it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.application.recording import RunRecorder
from crumblr.config import PlatformConfig
from crumblr.domain.enums import (
    Environment,
    IncidentStatus,
    ReasonCode,
    ReconciliationStatus,
    RiskVerdict,
    SupervisorVerdict,
)
from crumblr.domain.events import SystemHalted
from crumblr.domain.models import (
    AccountState,
    DecisionCapsule,
    InstrumentSpec,
    MarketSnapshot,
    PositionState,
    RiskDecision,
    SupervisorDecision,
    TradeIntent,
    VersionTag,
)
from crumblr.domain.timeutils import UtcDatetime
from crumblr.evaluator import pretrade
from crumblr.risk import policies, trading_window
from crumblr.risk.kill_switch import KillSwitch
from crumblr.risk.session import RiskSessionStore, recover_session
from crumblr.trading_agent.base import FeatureEvidence
from crumblr.trading_agent.sessions import trading_day

_EXTERNAL_AGENT_STRATEGY_ID = "external_agent"
"""Mirrors `agent_gateway.gateway._EXTERNAL_AGENT_STRATEGY_ID` -- every
`TradeIntent` the Gateway constructs carries this as `strategy_id`, so the
Policy Gate's `allowed_strategy_ids` must name the same constant rather
than `config.trading_agent.strategy_id` (the *internal* strategy)."""


class PortfolioStateProvider(Protocol):
    """Fresh account/position state for one evaluation.

    Deliberately narrow and injected, not read here -- see the module
    docstring. `SimulatedBroker`-backed fakes are correct for
    synthetic/replay proof only; a claim of genuine LIVE_SHADOW evidence
    must be backed by Core's own broker-state capture.
    """

    def current(self) -> PortfolioSnapshot: ...


@dataclass(frozen=True)
class PortfolioSnapshot:
    """One coherent account/position observation, pre-resolved by the
    caller -- this module never fetches or derives either field itself."""

    account: AccountState
    open_positions: tuple[PositionState, ...]
    reconciliation_status: ReconciliationStatus


@dataclass(frozen=True)
class AgentDecisionPathResult:
    """What one Risk -> Policy -> capsule evaluation produced.

    `capsule` is always populated -- NO_TRADE, a Risk BLOCK/HALT and a
    Supervisor VETO/HALT are each still sealed as evidence, never dropped.
    """

    capsule: DecisionCapsule
    risk_decision: RiskDecision | None
    supervisor_decision: SupervisorDecision | None


def evaluate_agent_trade_intent(
    intent: TradeIntent | None,
    *,
    outcome_id: UUID,
    strategy_version: VersionTag,
    snapshot: MarketSnapshot,
    spec: InstrumentSpec,
    features: FeatureEvidence,
    config: PlatformConfig,
    portfolio_state: PortfolioStateProvider,
    session_store: RiskSessionStore,
    kill_switch: KillSwitch,
    recorder: RunRecorder,
    environment: Environment,
    code_commit: str,
    now: UtcDatetime,
    seen_decision_hashes: frozenset[str] = frozenset(),
    orders_in_last_hour: int = 0,
    intents_in_last_hour: int = 0,
    incident_status: IncidentStatus = IncidentStatus.UNKNOWN,
) -> AgentDecisionPathResult:
    """Evaluate one Gateway-constructed `TradeIntent` (or NO_TRADE,
    `intent=None`) through intent-time Risk and the deterministic Policy
    Gate, and seal the result into a `DecisionCapsule`.

    `outcome_id` is the Gateway's own idempotency key for this outcome
    (`TradeProposal.proposal_id` / `NoTradeDecision.decision_id`) -- it is
    the only thing `capsule_id` is derived from, so a replayed identical
    outcome always seals the identical capsule (`CapsuleStore.seal()` is
    `ON CONFLICT DO NOTHING`-safe on that id).

    `orders_in_last_hour` defaults to `0`: the same documented v0 gap
    `application/live_decision.py` carries -- no order path is reachable
    from this function (or exists anywhere in the shadow-only agent path
    yet), so there is nothing to count. `incident_status` defaults to
    `UNKNOWN`, not `CLEAR` -- the fail-closed default `SupervisorContext`
    itself uses, so a caller that has not wired up a real incident read
    gets a refusal rather than an unearned approval (review finding
    F-002's rule, restated).
    """
    portfolio = portfolio_state.current()

    if intent is None:
        capsule = _seal(
            outcome_id=outcome_id,
            snapshot=snapshot,
            spec=spec,
            features=features,
            intent=None,
            risk_decision=None,
            supervisor_decision=None,
            positions=portfolio.open_positions,
            strategy_version=strategy_version,
            risk_config_version=config.config_version,
            environment=environment,
            code_commit=code_commit,
        )
        recorder.seal(capsule)
        return AgentDecisionPathResult(
            capsule=capsule, risk_decision=None, supervisor_decision=None
        )

    recorder.record(
        intent,
        correlation_id=snapshot.snapshot_id,
        occurred_at_utc=snapshot.event_time_utc,
        source="agent_gateway",
    )

    # AG-012: recovered fresh on every call, deliberately never cached
    # across calls -- see the module docstring.
    recovery = recover_session(
        session_store.load_latest(),
        live_equity=portfolio.account.equity,
        live_open_positions=len(portfolio.open_positions),
        market_day=trading_day(now),
    )
    if recovery.must_halt:
        _trip(
            kill_switch,
            recorder,
            reason_codes=recovery.reason_codes,
            tripped_by="agent_decision_path_risk_session_recovery",
            occurred_at_utc=now,
            correlation_id=snapshot.snapshot_id,
            detail=recovery.detail,
        )

    risk_portfolio = policies.PortfolioState(
        account=portfolio.account,
        open_positions=portfolio.open_positions,
        ledger=recovery.ledger,
        orders_in_last_hour=orders_in_last_hour,
        seen_decision_hashes=seen_decision_hashes,
        open_risk_fraction=config.risk.max_risk_per_trade * Decimal(len(portfolio.open_positions)),
    )
    risk_decision = policies.evaluate(
        intent,
        snapshot,
        spec,
        risk_portfolio,
        _risk_context(config),
        kill_switch,
        now=now,
    )
    recorder.record(
        risk_decision,
        correlation_id=snapshot.snapshot_id,
        occurred_at_utc=snapshot.event_time_utc,
        source="risk_engine",
    )
    if risk_decision.verdict is RiskVerdict.HALT:
        # policies.evaluate() only names a HALT in its verdict -- it never
        # trips the switch itself (every caller's own responsibility, the
        # same contract application/live_decision.py and
        # application/orchestration.py already honour).
        _trip(
            kill_switch,
            recorder,
            reason_codes=risk_decision.reason_codes,
            tripped_by="risk_engine",
            occurred_at_utc=now,
            correlation_id=snapshot.snapshot_id,
        )

    if risk_decision.verdict is not RiskVerdict.PASS:
        capsule = _seal(
            outcome_id=outcome_id,
            snapshot=snapshot,
            spec=spec,
            features=features,
            intent=intent,
            risk_decision=risk_decision,
            supervisor_decision=None,
            positions=portfolio.open_positions,
            strategy_version=strategy_version,
            risk_config_version=config.config_version,
            environment=environment,
            code_commit=code_commit,
        )
        recorder.seal(capsule)
        return AgentDecisionPathResult(
            capsule=capsule, risk_decision=risk_decision, supervisor_decision=None
        )

    supervisor_decision = pretrade.evaluate(
        intent,
        features,
        _supervisor_policy(config),
        pretrade.SupervisorContext(
            intents_in_last_hour=intents_in_last_hour,
            incident_status=incident_status,
            reconciliation_status=portfolio.reconciliation_status,
        ),
        now=now,
    )
    recorder.record(
        supervisor_decision,
        correlation_id=snapshot.snapshot_id,
        occurred_at_utc=snapshot.event_time_utc,
        source="supervisor",
    )
    if supervisor_decision.verdict is SupervisorVerdict.HALT:
        _trip(
            kill_switch,
            recorder,
            reason_codes=supervisor_decision.reason_codes,
            tripped_by="supervisor",
            occurred_at_utc=now,
            correlation_id=snapshot.snapshot_id,
        )

    capsule = _seal(
        outcome_id=outcome_id,
        snapshot=snapshot,
        spec=spec,
        features=features,
        intent=intent,
        risk_decision=risk_decision,
        supervisor_decision=supervisor_decision,
        positions=portfolio.open_positions,
        strategy_version=strategy_version,
        risk_config_version=config.config_version,
        environment=environment,
        code_commit=code_commit,
    )
    recorder.seal(capsule)
    return AgentDecisionPathResult(
        capsule=capsule, risk_decision=risk_decision, supervisor_decision=supervisor_decision
    )


def _trip(
    kill_switch: KillSwitch,
    recorder: RunRecorder,
    *,
    reason_codes: tuple[ReasonCode, ...],
    tripped_by: str,
    occurred_at_utc: UtcDatetime,
    correlation_id: UUID,
    detail: str | None = None,
) -> None:
    """Halt, and record it -- mirrors `application/live_decision.py`'s own
    `_trip` helper. Idempotent: a caller reusing the same `kill_switch`
    instance across evaluations (the AG-012-documented usage this module
    assumes) must not re-trip or double-record an already-halted switch.
    """
    if kill_switch.is_halted:
        return
    state_before = kill_switch.state
    kill_switch.trip(
        reason_codes=reason_codes,
        tripped_by=tripped_by,
        occurred_at_utc=occurred_at_utc,
        detail=detail,
    )
    recorder.record(
        SystemHalted(
            state_before=state_before,
            state_after=kill_switch.state,
            reason_codes=reason_codes,
            tripped_by=tripped_by,
            detail=detail,
        ),
        correlation_id=correlation_id,
        occurred_at_utc=occurred_at_utc,
        # Hardcoded, not `source=tripped_by` -- matches
        # `application/live_decision.py`/`application/orchestration.py`'s
        # own `_trip` helper exactly, which always records `SystemHalted`
        # as sourced from "risk_engine" regardless of `tripped_by`.
        source="risk_engine",
    )
    recorder.flush()


def _risk_context(config: PlatformConfig) -> policies.RiskContext:
    return policies.RiskContext(
        risk=config.risk,
        execution=config.execution,
        allowed_symbols=frozenset(config.enabled_symbols()),
        require_demo_account=config.account_guard.require_demo_account,
        expected_server=config.account_guard.expected_server,
        # Mirrors `live_decision.py`'s own reasoning: a `PortfolioStateProvider`
        # is not guaranteed to carry a trustworthy raw MT5 login (a
        # snapshot-derived implementation may not have one at all -- see
        # `BrokerAccountSnapshot`, which never carries it, build.md section 21).
        # Account identity is verified by reconciliation instead, not by this
        # field, so it is always `None` here rather than risking a false
        # `WRONG_ACCOUNT` BLOCK against a placeholder value.
        expected_login=None,
        expected_currency=config.account_guard.expected_currency,
        expected_leverage=config.account_guard.expected_leverage,
        risk_config_version=config.config_version,
        intraday=trading_window.policy_from_config(config.intraday),
    )


def _supervisor_policy(config: PlatformConfig) -> pretrade.SupervisorPolicy:
    return pretrade.SupervisorPolicy(
        enabled=config.supervisor.enabled,
        veto_on_unknown_regime=config.supervisor.veto_on_unknown_regime,
        allowed_strategy_ids=frozenset({_EXTERNAL_AGENT_STRATEGY_ID}),
        # `_build_trade_intent` (agent_gateway/gateway.py) always sets
        # `model_version=None` -- an external agent's own runtime/model
        # version is not a Crumblr-approved model artifact, deliberately
        # never forwarded onto the platform-owned TradeIntent (AG-010).
        allowed_model_versions=None,
        max_intents_per_hour=config.supervisor.max_intents_per_hour,
    )


def _seal(
    *,
    outcome_id: UUID,
    snapshot: MarketSnapshot,
    spec: InstrumentSpec,
    features: FeatureEvidence,
    intent: TradeIntent | None,
    risk_decision: RiskDecision | None,
    supervisor_decision: SupervisorDecision | None,
    positions: tuple[PositionState, ...],
    strategy_version: VersionTag,
    risk_config_version: VersionTag,
    environment: Environment,
    code_commit: str,
) -> DecisionCapsule:
    """Persist the immutable record of this evaluation (build.md section 11).

    `execution_result` is always `None` and `position_state_before`/`_after`
    are always identical -- no execution is reachable from this module, so
    there is nothing that could have changed the position book between
    observing it and sealing the capsule.
    """
    return DecisionCapsule(
        capsule_id=uuid5(NAMESPACE_URL, f"crumblr:agent-capsule:{outcome_id}"),
        occurred_at_utc=snapshot.event_time_utc,
        correlation_id=snapshot.snapshot_id,
        canonical_symbol=snapshot.symbol,
        broker_symbol=spec.broker_symbol,
        market_snapshot_id=snapshot.snapshot_id,
        feature_set_version=features.feature_set_version,
        feature_values_hash=features.feature_values_hash,
        strategy_version=strategy_version,
        model_version=None,
        model_output=None,
        trade_intent=intent,
        risk_config_version=risk_config_version,
        risk_decision=risk_decision,
        supervisor_decision=supervisor_decision,
        execution_result=None,
        position_state_before=positions,
        position_state_after=positions,
        code_commit=code_commit,
        environment=environment,
    )
