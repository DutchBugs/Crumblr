# Owner work orders — 2026-09-02

**Owner policy:** `review/OWNER_POLICY_V1.md`  
**Formal next routine gate:** `feedback.2.0.md`  
**Real broker submission:** still NO-GO

This document turns the owner's now-complete risk/session decisions into concrete work for the two existing development tracks. It is a coordination/work-order document, not a new formal reviewer gate and not `feedback.1.29`.

---

# DEV 1 — Core / Execution / Risk configuration

## Objective

Finish the Core safety path while replacing the two old product assumptions that are no longer owner policy:

```text
OLD: one EUR/USD exposure at a time
NEW: multiple positions allowed if total open risk stays <= 3%

OLD: no overnight positions / daily flatten
NEW: weekday overnight allowed; weekend holding forbidden
```

Do not weaken any broker/account/safety invariant while making those changes.

## Priority order

### D1.1 — Keep hosted CI as a hard gate

Before treating a Core safety slice as complete, hosted Linux + Windows + PostgreSQL restore proof + secret scan must be green. Do not substitute local green for hosted green.

### D1.2 — Implement Owner Risk Policy v1 as versioned configuration

Required values:

```text
max_risk_per_trade = 0.02
max_open_risk      = 0.03
max_daily_loss     = 0.04
max_drawdown       = 0.08
```

The values must remain part of the substantive config/policy hash so every RiskDecision/DecisionCapsule can identify the exact policy version used.

Do not treat 2% as a target. An Agent may request less; Risk sizes deterministically and may block.

### D1.3 — Remove the old one-exposure business rule

`risk.policies.MAX_EXPOSURES_PER_SYMBOL = 1` / O-004 is superseded by `OWNER_POLICY_V1.md`.

Required behavior:

- multiple EUR/USD positions are allowed;
- position count alone is not an owner-policy refusal reason;
- projected total open risk must remain <= 3%;
- if `RiskConfig.max_open_positions` remains, classify it as a separately named technical/circuit-breaker ceiling, not as the owner's trading rule;
- do not invent a replacement owner count limit.

Acceptance examples must include at least:

```text
open risk 1.0% + proposal 2.0% = 3.0% -> may pass this budget leg
open risk 1.1% + proposal 2.0% = 3.1% -> BLOCK OPEN_RISK_LIMIT
several small positions totaling < 3.0% -> not blocked merely by count
```

### D1.4 — Make open-risk accounting real, not count-based

The 3% budget must be based on deterministic portfolio risk, not `number_of_positions * max_risk_per_trade`.

Provide/reuse the smallest Core-owned function/seam that can determine current open risk from trusted position state, stop-loss geometry, volume, instrument specification and account/paper equity. If an open position has no trustworthy protective-stop/risk geometry, fail closed rather than counting it as zero risk.

Coordinate the resulting read-only value/seam with Dev 2 through the normal shared-contract handshake.

### D1.5 — Replace daily intraday-only policy with weekend-flat policy

Weekday rollover is allowed and must no longer create an `OVERNIGHT_EXPOSURE` incident simply because a position crossed a normal daily 17:00 New York rollover.

Required Friday behavior, derived from the canonical market/session calendar:

```text
before market-close - 15m     normal entry eligibility
at/after market-close - 15m   no new entries
at/after market-close - 5m    remaining positions require flatten/escalation
weekend                        no carried position permitted
```

Use the existing market/session calendar and New York DST handling. Do not create a fixed UTC Friday timestamp.

The automatic-flatten machinery already built remains useful; change the trigger from universal daily flatten to policy-driven weekend flatten. If flat state cannot be confirmed by the required deadline, HALT / surface the incident rather than assuming success.

Preserve historical/replay evidence where practical; do not rewrite old evidence to pretend the earlier O-003/O-004 decisions never existed.

### D1.6 — HALT reset stays human-only

Preserve/enforce:

```text
human/operator manual reset = allowed and audited
automatic reset             = forbidden
Trading Agent reset         = forbidden
external Supervisor reset   = forbidden
```

A future Supervisor recommendation may be displayed, but it cannot invoke the reset authority.

### D1.7 — Finish the existing execution-safety punch list

Continue the already-started Core path. In particular, broker-side SL/protection verification remains required before the real submission gate can be considered ready.

Do not bypass or fork the existing durable submission/recovery/reconciliation architecture to support PAPER_LITE.

### D1.8 — Prepare, but do not rush, the future Settings activation seam

The desired future UX is Dashboard -> Settings, but do not make the read-only dashboard directly mutate Risk state.

Preferred future seam:

```text
operator control API
-> validate full policy
-> create immutable policy version
-> audit who/when/why
-> explicitly activate
```

This may be staged after the current safety work; it must not delay the Core critical path.

## Dev 1 must NOT do

- do not implement external-agent strategy semantics;
- do not build the PAPER_LITE orchestrator;
- do not add a second execution stack;
- do not make real `order_send` reachable;
- do not set `feedback_2_0_approved=true`.

## Dev 1 done condition for this work order

Report back when:

1. Owner Policy v1 is represented correctly in versioned config/Risk semantics;
2. multiple positions are supported by actual total-open-risk accounting;
3. weekday overnight is allowed and Friday 15m/5m weekend-flat behavior is tested;
4. HALT reset remains human-only;
5. the current Core execution-safety slice, including broker-side protection verification as applicable, is complete;
6. hosted CI is fully green;
7. `order_send` is still NO-GO.

---

# DEV 2 — External Agent Integration

## Objective

Continue the strategy-neutral external-agent path and adapt it to Owner Risk Policy v1 without taking ownership of Core Risk or PAPER_LITE execution.

## Priority order

### D2.1 — Consume Owner Policy v1; do not duplicate it

The external-agent path must use the same authoritative Core Risk semantics:

```text
2% max risk per trade
3% max total open risk
4% daily loss
8% drawdown
multiple positions allowed within the 3% total budget
weekday overnight allowed
Friday entries stop T-15m
Friday flat required T-5m
```

No agent-local copy of these limits and no agent-specific risk implementation.

### D2.2 — Remove the current count-based open-risk approximation

The current agent decision path must not model open risk as:

```text
max_risk_per_trade * len(open_positions)
```

That becomes incorrect as soon as the owner allows multiple differently-sized positions.

Consume the Core-owned exact open-risk seam/value from D1.4 (or another reviewed equivalent). If exact open risk cannot be established from trusted state, fail closed.

### D2.3 — Keep `AgentMarketContextV1` strategy-neutral

Continue the F-066 direction:

- Core provides trusted neutral market/broker/platform facts;
- the assigned Agent/StrategyArtifact owns setup computation and strategy reason codes;
- no Pivot/FVG/MSS/ICT semantics are manufactured in Core;
- reason codes remain opaque bounded strategy-local evidence.

Use `AgentMarketContextV1` as the Crumblr-owned source contract for new adapters rather than letting every adapter invent its own Core-facing semantics.

### D2.4 — Complete the genuine HEALTHY Static Agent path

Coordinate with the Static Agent/strategy-runtime developer so the Agent side turns neutral market data into its own Pivot-2.2 observation honestly.

Then prove:

```text
HEALTHY Crumblr market context
-> Static Agent runtime
-> honest NO_TRADE or directional agent output
-> translate directional output to Crumblr TradeProposal (never accept its legacy TradeIntent as authoritative)
-> AgentGateway
-> platform-owned TradeIntent
-> Core Risk
-> strategy-neutral platform Policy
-> capsule
-> zero real broker submissions
```

### D2.5 — External Supervisor remains part of the full production path

Continue the external Supervisor work for the normal external-agent path:

```text
APPROVE / VETO / UNKNOWN
missing / timeout / malformed / stale / wrong binding = UNKNOWN
Supervisor never sizes, mutates, waives Risk, resets HALT or executes
```

For PAPER_LITE only, the owner permits the external Supervisor to be skipped because fills are simulated. Do not encode that as a generic `skip_supervisor=true` escape hatch.

If Dev 3 needs a marker/contract, prefer an explicit paper-only outcome/evidence such as:

```text
SUPERVISOR_SKIPPED_PAPER_MODE
```

It must never masquerade as a real Supervisor `APPROVE`.

### D2.6 — AG-012 remains a cross-track requirement before real agent-driven submission

Continue design/coordination with Dev 1 for one serialized/shared Risk authority around the relevant risk-session critical section. Do not treat fresh `recover_session()` on the agent side as sufficient for real submission.

PAPER_LITE may be built in parallel because it cannot create broker side effects, but AG-012 remains required before agent-driven real submission can be authorized at `feedback.2.0`.

### D2.7 — Support Dev 3 only through narrow stable seams

Dev 3 owns the PAPER_LITE orchestration. Dev 2 should only provide/approve minimal existing interfaces needed for:

- strategy-neutral context creation;
- Static Agent HTTP call/translation;
- AgentGateway submission;
- agent decision path invocation;
- explicit paper-only external-supervisor-skipped evidence if needed.

Do not absorb the Lite runner into the Agent Gateway package.

## Dev 2 must NOT do

- do not build a second Risk engine;
- do not compute Core-owned broker/risk state in the external Agent;
- do not give the Agent MT5/DB credentials;
- do not reintroduce strategy semantics into Crumblr Core;
- do not build the PAPER_LITE simulated broker itself unless a tiny shared adapter is explicitly requested;
- do not make real `order_send` reachable.

## Dev 2 done condition for this work order

Report back when:

1. the external-agent path uses exact Core-owned total-open-risk state, not position-count approximation;
2. `AgentMarketContextV1` is the basis for the genuine adapter path;
3. a HEALTHY strategy-owned Static Agent decision reaches Gateway -> TradeIntent -> Risk -> Policy -> capsule honestly;
4. Supervisor evaluation/wiring is ready for the full path or any remaining external service dependency is explicitly named;
5. AG-012 has a reviewed cross-track implementation plan / implementation as required for `feedback.2.0`;
6. Dev 3 can consume stable seams without changing AgentGateway ownership;
7. zero real broker submissions remain proven.

---

# Shared guardrails

Both tracks must preserve:

> Agent proposes. Risk engine constrains. Supervisor vetoes. Execution service executes. Reconciliation verifies.

And the newer extensibility invariant:

> Crumblr is strategy-neutral. Strategy semantics belong to the assigned StrategyArtifact/Trading Agent.

Until a future formal review explicitly changes this:

```text
order_send = NO-GO
ExecutionConfig.feedback_2_0_approved = false
```
