# feedback.1.25.md — MVP Convergence & External-Agent Direction

**Project:** Crumblr — Autonomous EUR/USD Trading Platform  
**Review version:** 1.25  
**Date:** 2026-08-27  
**Reviewed:** `status(20260827-131638).md`, `EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md`  
**Previous:** `feedback.1.24.md`

## Verdict

**GO — ADOPT EXTERNAL-AGENT DIRECTION, FREEZE THE APPROVED CORE, CONVERGE ON MVP**

Phase 4 remains **PASSED**.

The external-agent architecture direction is accepted as owner direction, with
two MVP-scoping clarifications below.

This review intentionally opens **no new broad architecture cycle**.

Unless a material safety defect or a fundamental deviation is discovered, the
next planned formal gate review should be:

```text
feedback.2.0.md
```

not a sequence of documentation-only `feedback.1.26`, `.1.27`, etc.

`order_send` remains **NO-GO** until `feedback.2.0` gives an explicit GO.

---

# 1. Product direction accepted

The target architecture is:

```text
external agents
    ↓ typed/authenticated boundary
Crumblr
    ↓
deterministic Risk + Policy
    ↓
existing Phase-4 execution chain
    ↓
MT5 / broker
```

Crumblr is the trusted trading instrument/control plane.

Agents are external principals. They do not receive:

```text
MT5 access
broker credentials
database write access
final lot-size authority
risk-policy mutation
HALT-reset authority
self-promotion authority
```

The already-approved core remains authoritative:

```text
TradeIntent
→ intent-time Risk
→ deterministic policy
→ DecisionCapsule
→ ExecutionOrchestrator
→ fresh broker observation
→ reconciliation
→ FINAL Risk
→ ApprovedOrder
→ order_check
→ later gated order_send
```

Do **not** rewrite this chain to implement the external-agent model.

---

# 2. Two MVP clarifications

## A. External Supervisor

The architecture guide calls the external Supervisor optional.

Owner direction in this project is stronger:

> For the **agent-driven MVP**, an external Supervisor Agent is required.

That does **not** make it the safety foundation.

Correct authority:

```text
Risk BLOCK/HALT
    → cannot be overridden

Deterministic Policy Gate BLOCK/HALT
    → cannot be overridden

External Supervisor
    → APPROVE / VETO / UNKNOWN only
    → cannot mutate side/price/SL/TP/volume/risk
    → timeout/error/invalid response = UNKNOWN = no approval
```

The current `evaluator.pretrade` should be treated conceptually as the
platform-owned deterministic Policy Gate.

A large rename/refactor is not required before MVP.

## B. TP requirement

External directional `TradeProposal`s must contain both:

```text
SL
TP
```

But do **not** destabilize the approved internal Phase-4 contracts merely to
make every historic/replay `TradeIntent` require TP immediately.

For MVP:

```text
external TradeProposal missing SL/TP
→ reject at Agent Gateway
→ never create TradeIntent
```

The internal `TradeIntent` contract may be tightened later under a deliberate
contract migration if that still adds value.

---

# 3. Define two milestones instead of one giant MVP

## Milestone A — Crumblr Execution Proof

Goal:

> Prove that Crumblr itself can safely execute and reconcile one deliberately
> constrained Pepperstone DEMO order.

This may use the current in-process `baseline_v1`.

It is **not** evidence of the final external-agent product.

Required path:

```text
finish F-051 part 2
hosted CI green
owner risk policy fixed
submission-era safety complete
feedback.2.0 GO
one DEMO canary
broker ack/fill
SL verified
reconciliation
safe closure
complete audit
```

The real Phase-4 `order_check` evidence already exists and does not need to be
repeated merely to make it green. Its rejection with terminal AlgoTrading off
was an honest result.

## Milestone B — Agent-Driven MVP

Goal:

> One external Trading Agent, independently reviewed by one external
> Supervisor Agent, safely drives the already-proven Crumblr execution path on
> DEMO.

Minimum new agent scope:

```text
AgentIdentity
TradingAssignment
DecisionContextBundle
TradeProposal
SupervisorReview
Agent Gateway
append-only proposal/audit mapping
one external Trader
one external Supervisor
shadow/replay evidence
then agent-driven DEMO canary
```

Not required for this MVP:

```text
Training Agent
Strategy Agent
Backtest Agent service
full artifact-management product
news platform
multi-market support
multi-agent ensemble/arbitrator
automatic strategy optimisation
LLM self-promotion
```

Those are post-MVP capabilities.

---

# 4. Parallel development split

Once the current core developer starts the next execution phase, a second
developer may safely work in parallel.

## Core developer

Owns:

```text
F-051 completion support
SubmissionGate
durable execution activation
SUBMISSION_STARTED before broker side effect
order_send idempotence
ambiguous-outcome recovery
automatic flatten submission
post-fill reconciliation
broker-side SL verification
execution-event conflict hardening
```

## Agent-integration developer

Owns:

```text
external-agent ADR
Agent Gateway
agent identity
assignments
proposal ingress
proposal → internal TradeIntent mapping
external Supervisor boundary
shadow/challenger operation
agent provenance
```

The integration developer must not modify the Phase-4 execution semantics
without a concrete blocker.

---

# 5. MVP-friendly migration rules

Preserve existing work rather than deleting it.

```text
baseline_v1 / ict_v1
→ keep as benchmark, regression fixture and shadow twin

LiveDecisionOrchestrator
→ keep for existing/internal evidence
→ external Agent Gateway becomes the new product ingress

TradeIntent
→ remains platform-owned trusted internal contract

DecisionCapsule
→ remains platform-sealed

ExecutionOrchestrator
→ remains downstream execution authority
```

Prefer a durable boundary mapping such as:

```text
external proposal
→ append-only proposal record/event
→ platform TradeIntent
→ existing correlation/provenance chain
```

rather than making externally supplied objects trusted domain records.

Agent/assignment/artifact provenance must be reconstructible, but this does not
require a large rewrite of `DecisionCapsule` if an immutable linked audit record
can provide the same proof.

---

# 6. Small existing items that should change before agent MVP

These are migrations, not reasons to reopen Phase 4.

### Real code provenance

Replace:

```text
CODE_COMMIT = "uncommitted-prototype"
```

with real deployment/code provenance before an agent-driven promotion.

### Hidden EUR/USD/M5 assumptions

Do not generalize the entire platform now.

For MVP, EUR/USD + M5 may remain the only supported capability, but the new
Agent Gateway must obtain these from the `TradingAssignment`, not silently from
hard-coded defaults.

### Proposal audit

Persist all outcomes:

```text
NO_TRADE
proposal accepted
proposal rejected
expired
duplicate
conflicting retry
Risk block/halt
Policy veto/halt
Supervisor veto/unknown
execution result
```

This is also the future Training Agent's dataset.

---

# 7. Trainer decision

Do **not** build the Trainer now.

Build the evidence it will eventually consume.

The future Trainer:

```text
reads immutable history
evaluates all decision windows
separates strategy/data/policy/execution causes
produces TrainingFinding / StrategyChangeProposal
```

It never:

```text
changes the running Trader
changes Risk
changes policy
promotes a model
deploys code
```

Human promotion remains mandatory.

This keeps Trainer integration possible without letting it delay the MVP.

---

# 8. Immediate critical path

The current status contains one very concrete stalled item:

```text
82 real M5 bars exist
baseline_v1 needs 65
reader stopped
no real Trading-Agent DecisionCapsule exists
```

Therefore do not wait for 120 bars merely to close F-051 plumbing.

Restart:

```text
mt5_live_reader.py
+
live_decision.py
```

against the intended soak environment and use the already-qualified
`baseline_v1` evidence path to close F-051 part 2 naturally.

`ict_v1` can continue accumulating toward 120 separately.

Also complete in parallel:

```text
hosted CI confirmation
owner risk-policy decisions
optional owner domain-contract countersign
```

AlgoTrading should remain off until the actual SubmissionGate /
`feedback.2.0` readiness conditions are met. Do not enable it just to turn the
historic `order_check` result green.

---

# 9. Review cadence from here

The review process now changes deliberately.

Do **not** request a formal reviewer artifact for:

```text
documentation wording
one extra unit test
normal F-051 accumulation
routine refactors
minor dashboard work
individual Agent Gateway files
```

Bring the reviewer back when one of these happens:

```text
1. a material safety defect is discovered;
2. the implementation proposes changing a Phase-4 invariant;
3. the complete pre-submission/MVP readiness bundle is ready.
```

For case 3, the target artifact is directly:

```text
feedback.2.0.md
```

The goal is now working evidence, not accumulating review documents.

---

# 10. Required `feedback.2.0` readiness bundle

Before requesting `feedback.2.0`, provide one coherent bundle covering:

```text
hosted CI green
owner-approved risk policy
F-051 part 2 evidence
automatic flatten submission
real SubmissionGate
durable human execution activation
SUBMISSION_STARTED pre-side-effect persistence
submission idempotence
ambiguous outcome recovery
post-fill reconciliation from durable platform history
broker-side SL verification
HALT-reset authority
terminal/account permissions
DEMO/account/server guards
market/broker/reconciliation/safety health
all relevant tests
```

If the first canary is intended to be called **agent-driven**, add:

```text
external Agent Gateway
TradingAssignment
external Trader evidence
required external Supervisor evidence
agent/proposal provenance
shadow/replay failure tests
```

Otherwise the first canary may remain a narrower **Crumblr execution proof** and
must be labelled as such.

---

# Final decision

**Direction: GO.**

The external architecture review improves the product direction but does not
invalidate the work already completed.

The shortest safe path is:

```text
protect Phase 4
→ finish evidence + owner decisions
→ build the remaining submission safety once
→ feedback.2.0
→ DEMO execution proof

in parallel:
minimal external Agent Gateway in shadow
→ external Trader
→ external Supervisor
→ agent-driven MVP

later:
Trainer / research automation / multi-market
```

Do not turn the new vision into a platform rewrite before the first MVP.
