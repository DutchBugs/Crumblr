"""Supervisor pre-trade policy — layer 1 (build.md §10.2).

Deterministic hard rules only. No statistics, no model, no language model. The
supervisor sees the same intent the risk engine saw and answers one question:
is this decision inside the envelope the strategy was approved to operate in?

It cannot alter the intent. Its output is APPROVE, VETO or HALT, and the
absence of any field through which it could change side, price or size is the
point rather than an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_URL, UUID, uuid5

from crumblr.domain.enums import (
    IncidentStatus,
    ReasonCode,
    ReconciliationStatus,
    Regime,
    SupervisorVerdict,
)
from crumblr.domain.models import SupervisorDecision, TradeIntent
from crumblr.domain.timeutils import UtcDatetime
from crumblr.trading_agent.base import FeatureEvidence

POLICY_VERSION = "supervisor-policy-v2"
"""Bumped for F-024: the frequency check now reports itself as uncalibrated
rather than silently passing, which changes what an approval from this policy
means. A stored decision names the policy that made it, so the name has to
move when the meaning does."""

SIGNAL_FREQUENCY_CHECK = "signal_frequency"
CONFIDENCE_BAND_CHECK = "confidence_band"

KNOWN_REGIMES: frozenset[Regime] = frozenset(
    {Regime.TREND, Regime.RANGE, Regime.HIGH_VOLATILITY, Regime.LOW_VOLATILITY}
)


@dataclass(frozen=True)
class SupervisorPolicy:
    """Envelope the strategy is permitted to operate in."""

    enabled: bool = True
    veto_on_unknown_regime: bool = True
    allowed_strategy_ids: frozenset[str] = frozenset({"baseline_v1"})
    allowed_model_versions: frozenset[str] | None = None
    """None means "no model expected"; an intent naming one is then out of envelope."""

    min_confidence: float = 0.0
    max_confidence: float = 1.0

    max_intents_per_hour: int | None = None
    """Signal-frequency anomaly threshold, or None for "not calibrated yet".

    Review 1.6 F-024 closed the excuse this check had been living on. The old
    threshold of 20/hour was justified by the decision cadence not being
    settled; owner decision O-002 settled it at M5, which permits at most 12
    windows an hour. A threshold of 20 against a structural maximum of 12
    cannot fire — it is not a lenient control, it is an absent one wearing a
    number.

    The reviewer offered two honest options: a deterministic structural rate
    limit with a documented rationale, or an explicit "uncalibrated" state
    until real EUR/USD observations exist. This is the second, and it is the
    right one, because the only defensible calibration would come from
    observing a real feed and no real feed has been observed. Calibrating
    against synthetic trade frequency would be fitting a control to a random
    walk.

    `None` does not mean the check passes. It means the check did not run, and
    `SupervisorDecision.uncalibrated_checks` says so on every decision.
    """


@dataclass(frozen=True)
class SupervisorContext:
    """Observed system state the policy is evaluated against.

    The two safety fields default to `UNKNOWN`, not to their safe values. A
    caller that has not wired up reconciliation gets a refusal, not an
    approval — which is the whole point of review finding F-002. Defaulting
    them to MATCHED and CLEAR made an approval read as though both had been
    verified when neither had been asked.
    """

    intents_in_last_hour: int = 0
    incident_status: IncidentStatus = IncidentStatus.UNKNOWN
    reconciliation_status: ReconciliationStatus = ReconciliationStatus.UNKNOWN


def uncalibrated_checks(policy: SupervisorPolicy) -> tuple[str, ...]:
    """Which of the policy's checks cannot currently fail.

    Computed from the policy rather than from a run, because it is a statement
    about the configuration and stays true for a run that made no decisions at
    all. Used both on every decision and by the run report, so the two cannot
    disagree about what was in force.
    """
    absent: list[str] = []
    # A band spanning the range `TradeIntent` already constrains confidence to
    # is not a check; it is the contract restated.
    if policy.min_confidence <= 0.0 and policy.max_confidence >= 1.0:
        absent.append(CONFIDENCE_BAND_CHECK)
    if policy.max_intents_per_hour is None:
        absent.append(SIGNAL_FREQUENCY_CHECK)
    return tuple(absent)


def evaluate(
    intent: TradeIntent,
    features: FeatureEvidence,
    policy: SupervisorPolicy,
    context: SupervisorContext,
    *,
    now: UtcDatetime,
) -> SupervisorDecision:
    """Judge an intent against the deterministic policy.

    Collects every violated rule rather than stopping at the first, so a veto
    explains the full picture to whoever reads the incident later.
    """
    reasons: list[ReasonCode] = []

    # --- Safety state, checked before anything else ------------------------
    # Whether the system knows its own position state is not a supervisor
    # opinion, so it is not subject to the supervisor being switched off.
    # Disabling policy judgement must not become a way to launder unknown
    # safety state into an approval (review finding F-002).
    if context.reconciliation_status is ReconciliationStatus.MISMATCHED:
        return _decide(
            intent,
            SupervisorVerdict.HALT,
            (ReasonCode.RECONCILIATION_MISMATCH,),
            now,
            features.regime,
        )
    if context.reconciliation_status is ReconciliationStatus.UNKNOWN:
        return _decide(
            intent,
            SupervisorVerdict.HALT,
            (ReasonCode.RECONCILIATION_UNKNOWN,),
            now,
            features.regime,
        )

    if not policy.enabled:
        # A disabled supervisor still records a decision — silence would leave
        # a hole in the audit trail where an approval should be.
        return _decide(intent, SupervisorVerdict.APPROVE, (), now, features.regime)

    if intent.strategy_id not in policy.allowed_strategy_ids:
        reasons.append(ReasonCode.STRATEGY_NOT_ENABLED)

    if policy.allowed_model_versions is None:
        if intent.model_version is not None:
            reasons.append(ReasonCode.MODEL_VERSION_NOT_ALLOWED)
    elif intent.model_version not in policy.allowed_model_versions:
        reasons.append(ReasonCode.MODEL_VERSION_NOT_ALLOWED)

    if features.regime not in KNOWN_REGIMES and policy.veto_on_unknown_regime:
        reasons.append(ReasonCode.UNKNOWN_REGIME)

    # Checks that cannot fail are reported as absent rather than as passed —
    # the misreading D-028 was raised about, and what F-024 asks for.
    absent = uncalibrated_checks(policy)

    if CONFIDENCE_BAND_CHECK not in absent and not (
        policy.min_confidence <= intent.confidence <= policy.max_confidence
    ):
        reasons.append(ReasonCode.CONFIDENCE_OUT_OF_RANGE)

    if (
        policy.max_intents_per_hour is not None
        and context.intents_in_last_hour > policy.max_intents_per_hour
    ):
        reasons.append(ReasonCode.SIGNAL_FREQUENCY_ANOMALY)

    # An incident register that cannot be read is not a clear one.
    if context.incident_status is IncidentStatus.ACTIVE:
        reasons.append(ReasonCode.ACTIVE_INCIDENT)
    elif context.incident_status is IncidentStatus.UNKNOWN:
        reasons.append(ReasonCode.INCIDENT_STATE_UNKNOWN)

    if reasons:
        return _decide(
            intent,
            SupervisorVerdict.VETO,
            tuple(dict.fromkeys(reasons)),
            now,
            features.regime,
            uncalibrated=absent,
        )
    return _decide(
        intent,
        SupervisorVerdict.APPROVE,
        (),
        now,
        features.regime,
        uncalibrated=absent,
    )


def _decide(
    intent: TradeIntent,
    verdict: SupervisorVerdict,
    reasons: tuple[ReasonCode, ...],
    now: UtcDatetime,
    regime: Regime,
    *,
    uncalibrated: tuple[str, ...] = (),
) -> SupervisorDecision:
    return SupervisorDecision(
        decision_id=_decision_id(intent, verdict.value),
        intent_id=intent.intent_id,
        verdict=verdict,
        reason_codes=reasons,
        decided_at_utc=now,
        policy_version=POLICY_VERSION,
        statistical_monitor_version=None,
        observed_regime=regime,
        uncalibrated_checks=uncalibrated,
    )


def _decision_id(intent: TradeIntent, discriminator: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"crumblr:supervisor:{intent.decision_hash}:{discriminator}")
