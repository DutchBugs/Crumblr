# Crumblr Owner Risk & Trading Policy v1

**Status:** OWNER-APPROVED  
**Date:** 2026-09-02  
**Applies to:** current PAPER / SHADOW / DEMO-readiness product direction  
**Does not authorize:** real broker submission, `order_send`, or live-account trading

This document records the owner's current risk and operating policy. It replaces the earlier v1 assumptions that Crumblr must always be intraday-only and may hold only one EUR/USD exposure at a time. Those old assumptions may remain in historical evidence/tests where needed, but they are no longer the production product policy.

`feedback.2.0` remains the separate authorization gate for making real Pepperstone DEMO `order_send` reachable. `ExecutionConfig.feedback_2_0_approved` remains `false` until that review explicitly says otherwise.

## 1. Risk limits

Use the existing Crumblr percentage/fraction semantics and deterministic Risk Engine.

| Policy | Owner value |
|---|---:|
| Maximum risk per new trade | **2.0%** of current account/paper equity |
| Maximum total open risk | **3.0%** |
| Maximum daily loss | **4.0%** |
| Maximum drawdown | **8.0%** |

Interpretation:

- per-trade sizing remains deterministic and authoritative in Crumblr Risk;
- total open risk is a portfolio budget, not a position-count approximation;
- daily loss uses Crumblr's existing session-loss ledger semantics against session-start equity;
- drawdown uses Crumblr's existing high-water-mark semantics;
- a proposal that would take projected open risk above 3.0% is blocked even if its own requested risk is <= 2.0%;
- a Trading Agent may request less than 2.0%; 2.0% is a ceiling, not a target.

## 2. Multiple positions are allowed

There is **no owner-imposed fixed position-count limit** for the current product policy. Multiple positions are allowed provided all Risk rules remain satisfied, especially the 3.0% total-open-risk ceiling.

Therefore position count by itself must not be the production business-rule reason to refuse a trade.

The current hard-coded `risk.policies.MAX_EXPOSURES_PER_SYMBOL = 1` / old O-004 assumption must not remain the production external-agent rule. Likewise, if `RiskConfig.max_open_positions` remains as a technical circuit breaker, it must be clearly classified as an operational safety ceiling rather than silently representing this owner policy. Do not invent a new owner position-count limit.

Any technical anti-loop/rate ceiling may remain as defense-in-depth, but it is not a trading-strategy rule and must be separately named/audited.

## 3. Session / overnight / weekend policy

Weekday overnight positions are **allowed**. There is no normal daily last-entry cutoff and no normal daily flatten deadline.

Weekend holding is **not allowed**.

For the canonical Friday market close:

- from **15 minutes before market close**, no new positions may be opened;
- all remaining positions must be **confirmed flat no later than 5 minutes before market close**;
- if Crumblr cannot confirm the required flatten, fail closed / HALT and surface the incident; never assume the book is flat;
- derive the close from the canonical market/session calendar already owned by Core; do not hard-code a UTC hour that will drift with New York daylight-saving time.

Exact boundary semantics:

```text
now < close - 15m       new entries may be considered normally
now >= close - 15m      no new entries
now >= close - 5m       any remaining exposure requires flatten / escalation
weekend                  no carried exposure permitted
```

The previous generic daily `OVERNIGHT_EXPOSURE` rule should therefore be replaced/refactored for production behavior so a normal weekday rollover is not treated as an incident.

## 4. HALT reset authority

A hard HALT may be reset **only by an authorized human/operator through an explicit manual action**.

Not allowed:

- automatic reset;
- timer-based reset;
- Trading Agent reset;
- external Supervisor reset;
- recovery code silently returning the system to RUNNING.

An external Supervisor may later provide advice such as `CLEAR_TO_RESET`, but it has no reset authority. The actual reset remains a human/operator control and must be audited.

## 5. AlgoTrading / real broker authority

MT5 AlgoTrading is an operator-controlled terminal capability and may be switched on/off when deliberately required. It is never sufficient by itself to authorize submission.

Crumblr retains its own independent submission gates. For real broker submission all of them must pass, including the future explicit `feedback.2.0` authorization.

For PAPER_LITE, AlgoTrading and real `order_send` are unnecessary and should remain irrelevant to the fill path.

## 6. PAPER_LITE owner authorization

The owner authorizes a separate **PAPER_LITE** integration mode for engineering/product validation with:

```text
real read-only Pepperstone EUR/USD market feed
-> external/static Trading Agent
-> AgentGateway
-> platform-owned TradeIntent
-> real deterministic Crumblr Risk
-> strategy-neutral platform Policy
-> external Supervisor may be skipped in PAPER_LITE
-> simulated fill / simulated portfolio only
```

This authorization is deliberately narrow.

The external Supervisor may be skipped only when broker side effects are structurally impossible and the resulting position exists only in the paper/simulated broker state. Do not create a fake Supervisor `APPROVE`; audit the fact that external supervision was skipped because the run was PAPER_LITE.

PAPER_LITE must **not** bypass:

- Agent identity / assignment / context binding;
- deterministic Risk;
- the owner risk limits in this document;
- platform safety / reconciliation policy appropriate to the paper path;
- idempotency / audit requirements.

PAPER_LITE is not permission to call Pepperstone `order_send`, even on a DEMO account.

## 7. Configuration and future Settings UI direction

Risk-bearing settings should remain versioned and immutable for each decision. A decision/capsule must always identify the exact policy/config version that produced it.

The desired future operator experience is `Dashboard -> Settings`, but the dashboard should not directly mutate an in-memory `RiskConfig`. The safe target is a control-plane flow:

```text
Dashboard Settings
-> authenticated operator control API
-> validate complete policy
-> create new immutable policy/config version
-> record who/when/change reason
-> explicitly activate version
-> subsequent decisions use the new version
```

Historical decisions continue to point to their historical policy version.

This Settings work is useful but is not a reason to delay the current safety and external-agent convergence work.

## 8. Current non-negotiable gate

Until a later formal review says otherwise:

```text
order_send = NO-GO
ExecutionConfig.feedback_2_0_approved = false
```
