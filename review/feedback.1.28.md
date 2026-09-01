# feedback.1.28.md — Strategy-neutral Core & external strategy ownership

**Project:** Crumblr — Autonomous EUR/USD Trading Platform  
**Review version:** 1.28  
**Date:** 2026-09-01  
**Reviewed baseline:** `main` at `f3080bc` including AG-015, current external-agent path, current Core strategy registry, and the current `DutchBugs/crumblr-static-agent-host` fork  
**Previous:** `feedback.1.27.md`

## Verdict

**GO — but with an architectural correction before the Static Agent bridge becomes the production external-agent shape.**

AG-015 is not merely a reason-code vocabulary mismatch. It exposed a deeper ownership mistake in the bridge design: **Crumblr must not have to implement or emulate an external Trading Agent's strategy in order to prepare that agent's input.**

This review is a valid early exception to the normal `feedback.2.0` cadence because `feedback.1.27.md` explicitly allowed escalation when the Static Agent integration exposed an authority-boundary / architectural question. This is such a question.

Phase 4 remains PASSED. Core submission-safety work continues. `order_send` remains **NO-GO** and `ExecutionConfig.feedback_2_0_approved` remains `false`.

---

# 1. Product architecture decision

The authoritative split is now:

```text
TRAINER / RESEARCH PLANE
creates and evaluates candidate strategy/model artifacts
        ↓ human/promotion gate
versioned approved StrategyArtifact
        ↓ assigned to
EXTERNAL TRADING AGENT
owns strategy logic, setup detection, strategy features, strategy reason codes,
entry/SL/TP proposal geometry and NO_TRADE reasoning
        ↓ proposes only
CRUMBLR PLATFORM
owns trusted market/broker context, identity, assignment, audit, Risk, Policy,
position sizing, credentials, execution, reconciliation, kill switch
        ↓
EXTERNAL SUPERVISOR
may APPROVE/VETO/UNKNOWN as an independent review layer,
but can never override Risk or mutate the proposal
```

The core invariant remains:

> **Agent proposes. Risk engine constrains. Supervisor vetoes. Execution service executes. Reconciliation verifies.**

But there is now an equally important product invariant:

> **Crumblr is strategy-neutral. Strategy semantics belong to the assigned strategy artifact and the Trading Agent that runs it.**

---

# 2. What AG-015 proved

The current Static Agent fork is tied to a frozen `ICT Silver Bullet / Pivot 2.2` strategy package. Its `reason_codes.json` is a closed, strategy-specific state vocabulary. Its Python `CrumblrStaticTrader` expects `TraderContext.features.observation` to already contain a fully-computed setup and validates the supplied reason codes against that strategy package.

Crumblr's own `ict_v1` is a **different** internal strategy with different setup semantics and different reason codes.

Therefore these approaches are explicitly rejected:

```text
REJECTED A:
Crumblr re-implements Pivot 2.2 only so the external Pivot 2.2 agent can run.

REJECTED B:
Crumblr maps its own ict_v1 states/reason codes into Pivot 2.2-looking states.

REJECTED C:
Crumblr invents a generic ICT vocabulary and pretends both strategies mean the same thing.
```

All three would make the platform strategy-specific and/or fabricate semantic evidence.

The correct conclusion is not "find a better mapping." The correct conclusion is:

> **the strategy computation is on the wrong side of the interface.**

---

# 3. Correction to feedback.1.27 §5.1

`feedback.1.27.md` said the outbound Static Agent payload should include a "trusted feature observation required by the assigned strategy." That wording is **superseded by this review**.

Crumblr must not compute a strategy-specific observation for an external agent.

The external context must instead contain **trusted, strategy-neutral source data and platform state** from which the assigned Trading Agent can make its own strategy decision.

For the current EUR/USD M5 scope, a first `AgentMarketContextV1` / equivalent may contain:

```text
BINDING / PROVENANCE
- context_id
- DecisionContextBundle.content_hash
- assignment_id
- strategy_artifact_id/hash
- issued_at / expires_at

MARKET — PLATFORM-OWNED, STRATEGY-NEUTRAL
- canonical symbol
- timeframe
- current trusted bid/ask/spread snapshot
- bounded window of confirmed closed M5 bars required by the assignment/artifact manifest
- exact source bar identities/timestamps
- market-data quality / freshness

INSTRUMENT / BROKER FACTS — READ-ONLY
- broker symbol
- digits / point / tick size
- stops level
- volume min/max/step where needed as information
- instrument-spec identity/version

PLATFORM STATE — READ-ONLY
- session/safety state that is genuinely platform-defined
- bounded exposure/portfolio summary where appropriate
- reconciliation/safety health where appropriate
- context evidence identity (`agent_context_v1` / current feature_snapshot_id binding)

OPTIONAL NON-AUTHORITATIVE HINTS
- max requested-risk band from assignment
- minimum stop-distance hint
- other typed policy hints
```

These are observations/constraints. They are **not** setup detections.

Crumblr must not send fields such as:

```text
liquidity_sweep_detected
FVG_CONFIRMED
WAITING_FOR_MSS
PIVOT_2_2_CONFIRMED
OTE entry
strategy-specific regime
strategy-specific reason code
```

unless those values were produced by that strategy's own versioned runtime/artifact and are being returned as agent evidence — never fabricated by Core.

---

# 4. Strategy artifact / assignment becomes the extensibility point

The `TradingAssignment` already pins a `strategy_artifact_id` and `strategy_artifact_hash`. That is the correct direction.

Extend the strategy-artifact metadata only as needed so Crumblr can supply data **without understanding the strategy**. Examples of acceptable declarative metadata:

```text
required symbol(s)
required timeframe(s)
required closed-bar lookback
requires current quote: yes/no
requires news snapshot: yes/no
input contract version
output contract version
agent/runtime compatibility
strategy artifact hash
optional model artifact hash
```

This is capability/input metadata, not trading logic.

A Trainer may create new candidate strategy/model artifacts, but a Trading Agent may not silently self-promote or replace its assigned strategy at runtime. Human/promotion governance still chooses which immutable artifact/version is assigned.

That gives Crumblr diversity **without** giving an agent uncontrolled strategy mutation authority.

---

# 5. External agent reason codes are strategy-owned evidence

For the external-agent boundary, `TradeProposal.reason_codes` / `NoTradeDecision.reason_codes` must be treated as **opaque, bounded, auditable strategy-local strings**, not as a vocabulary Crumblr understands semantically.

Crumblr may enforce structural rules such as:

```text
- non-empty when required
- maximum count
- maximum string length
- safe character/encoding rules
- proposal binds to the assigned strategy artifact hash
- evidence references resolve where required
```

Crumblr must **not** maintain a global whitelist of strategy reason codes.

If a reason-code vocabulary is versioned, its vocabulary/hash belongs to the StrategyArtifact manifest and may be audited as provenance. It is not a Core trading rule.

This means a future second Trading Agent can use completely different reason codes without any Core code change.

---

# 6. Risk rules versus strategy rules

The owner is correct to separate these.

## Strategy rules — Agent / Trainer owned

Examples:

```text
ICT / Pivot / FVG / MSS / liquidity-sweep methodology
moving averages
mean reversion
breakout logic
ML model predictions
entry setup state machines
strategy-specific market regimes
strategy-specific confidence meaning
strategy reason-code vocabulary
strategy-specific NO_TRADE logic
```

None of those are required in Crumblr Core for an external strategy to work.

## Hard safety / broker rules — Crumblr Risk owned

Examples:

```text
correct account/server/environment
DEMO/live guard
account/terminal permissions
trusted/fresh market data
spread limits
symbol allowlist
instrument min stop distance / stops level
requested-risk ceiling
final deterministic position sizing
max open risk
max positions / one EURUSD exposure v1
max daily loss
max drawdown
last-entry cutoff
flatten deadline / no overnight v1
duplicate-intent protection
fresh execution-time revalidation
broker-side protection verification
```

These are strategy-independent and must remain deterministic and authoritative in Crumblr.

## Supervisor / Policy

The external Supervisor may receive the proposal, immutable strategy/model provenance, selected strategy evidence, platform safety summary, RiskDecision and relevant broker/policy facts as **read-only review context**.

It may only return:

```text
APPROVE
VETO
UNKNOWN
```

Timeout/error/invalid/missing remains `UNKNOWN` and therefore no approval.

The Supervisor does not replace Risk, does not size, does not modify the intent, and cannot waive a broker/safety rule.

---

# 7. Current deterministic pretrade policy is partly strategy-coupled

The current internal `evaluator/pretrade.py` policy contains assumptions that are reasonable for the old internal-strategy path but must **not** become universal requirements for the external-agent path:

```text
allowed_strategy_ids containing baseline_v1-style IDs
veto_on_unknown_regime
FeatureEvidence.regime as a mandatory approval concept
min/max confidence without a declared confidence semantic
```

AG-013 (`agent_context_v1.regime = UNKNOWN` therefore always vetoed) is a symptom of the same coupling AG-015 exposed.

Required direction:

1. Keep the current deterministic policy intact where needed for the legacy/internal replay path so existing evidence/tests are not casually broken.
2. Build/derive an **external-agent platform policy** whose hard checks are strategy-neutral:
   - reconciliation / incident / safety health,
   - authenticated active assignment,
   - artifact provenance/assignment match,
   - platform proposal/rate limits,
   - other platform invariants.
3. Do not require a Crumblr-computed `Regime` for external-agent approval.
4. Do not globally interpret an agent's `confidence` unless the assigned artifact explicitly declares a calibrated semantic the policy understands. A deterministic rule agent saying `confidence=1` may mean "rule satisfied," not "100% win probability."

This does **not** weaken safety. Strategy-independent safety remains in Risk/Policy; strategy semantics stop pretending to be universal platform safety.

---

# 8. Existing `baseline_v1` / `ict_v1` inside Crumblr

The current Core repository really does contain strategy implementations and a registry that hard-wires `baseline_v1` and `ict_v1`.

Do **not** delete them abruptly. They currently support replay, historical tests, F-051 evidence and comparison/twin behavior.

From this review onward classify them as:

> **legacy/internal reference strategies — not the production extensibility model.**

Rules:

```text
- New external-agent code may not import baseline.py / ict.py to manufacture an agent's strategy state.
- External-agent context publication may not depend on registry.resolve().
- A new external strategy must not require a Core strategy implementation.
- Existing internal strategies may remain for replay/reference while migration is staged.
- Later, move them to an examples/reference-strategy package or separate strategy repo if that can be done without destabilizing Core evidence.
```

The production direction is TradingAssignment + external StrategyArtifact + external Trading Agent.

---

# 9. Static Agent fork — new long-term bridge

The fork's existing unhealthy-market short circuit is still a good first transport proof:

```text
market_data_health != HEALTHY
→ real safe NO_TRADE
→ transport/auth/schema/idempotency proven
```

Dev 2 may continue that smoke test now.

But it is **not** the final integration contract.

For a genuine HEALTHY directional/NO_TRADE strategy decision, the Static Agent side must own the missing Pivot-2-2 computation. The fork already owns the frozen Pivot-2-2 strategy package/provenance; therefore its outer runtime (or a strategy-runtime component beside it in the same agent plane) must turn Crumbler's neutral market context into its own strategy-specific observation/reason codes before producing `TradeProposal` / `NoTradeDecision`.

Target:

```text
CRUMBLR
trusted neutral market/broker context
        ↓
STATIC AGENT / STRATEGY RUNTIME
Pivot-2-2 feature/setup/state computation
strategy-local reason codes
NO_TRADE or TradeProposal
        ↓
CRUMBLR AGENT GATEWAY
structural validation + assignment/context binding
        ↓
platform-owned TradeIntent
        ↓
Risk → external-agent Policy → external Supervisor
        ↓
STOP in SHADOW
```

Do not move the Pivot-2-2 implementation into Crumblr to make the bridge easier.

---

# 10. New project-wide finding F-066

**F-066 — External-agent production path must be strategy-neutral**  
**Severity:** HIGH BEFORE DIRECTIONAL EXTERNAL-AGENT SHADOW PROMOTION / `feedback.2.0`  
**Owners:** Dev 2 for Crumblr boundary; external Agent Developer for the Static Agent runtime; Dev 1 only for shared Core seams  
**Status:** OPEN

Closes when all are proven:

```text
1. external context contains neutral trusted data, not Core-computed strategy setup state;
2. external agent owns its strategy-specific computation and vocabulary;
3. Gateway accepts strategy-local reason codes structurally without global semantic mapping;
4. strategy artifact/assignment provenance remains immutable and enforced;
5. external-agent Risk/Policy path has no mandatory baseline_v1/ict_v1/Pivot-2-2 semantic dependency;
6. external-agent Policy no longer requires a Core-generated regime;
7. one HEALTHY Static Agent context reaches an honest strategy-owned NO_TRADE or TradeProposal;
8. a second toy/test agent with a deliberately different reason-code vocabulary can use the same Core path without Core code changes;
9. zero broker submission remains structurally proven during this shadow evidence.
```

Item 8 is important: it is the regression test that proves we actually built a platform rather than another single-strategy integration.

---

# 11. Dev 2 revised work order

The previously built shared no-MT5 `TradeIntent → Risk → Policy → capsule` path remains useful. Do not throw it away.

Proceed:

```text
A. Finish the unhealthy-market Static Agent NO_TRADE smoke proof.
   This is honest transport/auth/idempotency evidence and does not depend on AG-015.

B. Stop work on ict_v1 → Pivot-2-2 semantic mapping. It is rejected.

C. Replace StaticAgentContextPayload's strategy-specific observation requirement
   with AgentMarketContextV1 / equivalent strategy-neutral market-data payload.

D. Keep DecisionContextBundle/content_hash as the trusted platform binding.
   `agent_context_v1` remains an audit/context-evidence anchor; do not pretend it
   contains strategy analysis.

E. Make the external Gateway reason-code handling structural/opaque.

F. Split the external-agent Policy path away from internal strategy assumptions
   (`Regime`, hard-coded strategy IDs, globally interpreted confidence).

G. Coordinate the fork-side strategy-runtime work with the external Agent Developer.

H. Add strategy-neutrality tests with two incompatible reason vocabularies.

I. Then run a HEALTHY genuine Static Agent shadow decision end-to-end.

J. Continue to the external Supervisor boundary.
```

No MT5 submission is added.

---

# 12. Dev 1 work order impact

Dev 1 should **not** reimplement Pivot 2.2 and should not interrupt the Core submission-safety critical path to rewrite all legacy strategy code.

Continue current Core work.

Only support this architecture when Dev 2 needs a small shared seam for:

```text
neutral market/bar snapshot publication
instrument spec / broker-state read-only data
Risk/Policy platform inputs
strategy-neutral artifact/assignment provenance
```

Do not add external strategy semantics to Core.

A later cleanup can isolate/move `trading_agent/baseline.py`, `ict.py`, and related detectors after the external path has proven it no longer depends on them.

---

# 13. External Agent Developer work order

The Static Agent repo is now the owner of Pivot-2-2 strategy semantics.

Required direction:

```text
- preserve the frozen strategy artifact/source hash;
- add/identify a strategy-runtime adapter that evaluates neutral Crumblr market data;
- produce the package's own setup states/reason codes honestly;
- emit external TradeProposal / NoTradeDecision semantics;
- no MT5 credentials;
- no direct Crumblr DB access;
- no final lot sizing;
- no Risk/Policy bypass;
- no order_send;
- no strategy self-promotion.
```

If the existing Python host cannot compute the strategy today, that is an Agent-side implementation gap — **not a reason to push the strategy into Crumblr.**

---

# 14. Why this gives Crumblr more freedom without reducing safety

Yes: this architecture is materially more extensible.

With the corrected boundary, Crumblr can eventually host agents using:

```text
ICT variants
trend following
mean reversion
breakout systems
statistical models
ML models
hybrid deterministic/AI strategies
future trainer-produced strategy artifacts
```

without adding each methodology to Core.

What remains fixed in Core is exactly what should be fixed:

```text
who is allowed to propose
what data/provenance the proposal binds to
how much may be risked
whether broker/account/market state is safe
whether the proposal is still valid
whether safety/reconciliation is healthy
whether execution may occur
whether the broker result matches expectation
```

That is the right separation between **freedom of strategy** and **rigidity of safety**.

---

# 15. Review cadence

Do not create `feedback.1.29` for normal implementation progress.

The next normal formal gate remains `feedback.2.0.md`.

Return early only if this strategy-neutral boundary itself requires a Phase-4 safety invariant change, external agents need new authority, or a material safety defect appears.

Short owner/reviewer checkpoints are sufficient for:

```text
- unhealthy-market Static Agent smoke complete;
- strategy-neutral context contract landed;
- first HEALTHY strategy-owned Static Agent decision complete.
```

Until `feedback.2.0` explicitly says otherwise:

```text
order_send = NO-GO
ExecutionConfig.feedback_2_0_approved = false
```
