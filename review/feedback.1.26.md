# feedback.1.26.md — Convergence, Observability & DEMO Readiness

**Project:** Crumblr — Autonomous EUR/USD Trading Platform  
**Review version:** 1.26  
**Date:** 2026-09-01  
**Reviewed baseline:** `main` at `5e11884`, `status.md`, `review/AGENT_STATUS.md`, `review/AGENT_FEEDBACK.md`, `review/INTEGRATION_NOTICES.md`, current GitHub Actions state, current dashboard/broker-state read paths  
**Previous:** `feedback.1.25.md`

## Verdict

**GO — START A NEW DEVELOPMENT PHASE: CONVERGENCE + OBSERVABILITY + DEMO READINESS**

Phase 4 remains **PASSED** and is not reopened.

This document is an owner-requested exception to review 1.25's normal cadence. The reason is not a new architecture cycle; it is that two parallel development tracks are now real, the Agent Gateway is on `main`, the execution path has advanced, and the owner needs one fresh coordination document that both developers can treat as the current plan.

`order_send` remains **NO-GO**.

This file does **not** satisfy `ExecutionConfig.feedback_2_0_approved`. That flag must remain false. The next submission-authorization review remains:

```text
feedback.2.0.md
```

and must explicitly say GO before any DEMO `order_send` becomes reachable.

---

# 1. New phase definition

Call the development phase from this point:

> **Phase 5 — Convergence, Observability & DEMO Readiness**

This is a development phase, not an autonomy promotion.

Its purpose is to stop building isolated foundations and make the already-built pieces converge into an operator-visible, evidence-producing system.

Three lanes run in parallel:

```text
Lane A — Dev 1 / Core Submission Safety
Lane B — Dev 2 / External Agent Integration
Lane C — Read-only Operational Observability
```

The lanes converge at `feedback.2.0`.

---

# 2. Current integrated state accepted

The repo now contains substantially more than the older 2026-08-28 snapshots implied.

Accepted current state:

```text
DEV 1
- SubmissionGate built
- SubmissionGate actually called by ExecutionOrchestrator
- F-062 self-referential config approval bug found and fixed
- order_send still structurally unreachable

DEV 2
- Agent contracts merged to main
- Agent Gateway ingestion/audit merged to main
- dedicated worktree + dedicated test DB adopted
- AG-007 rate-limit concurrency race fixed
- AG-008 fail-open interrupted-retry bug fixed
- AG-009 evidence enforcement gap fixed
- HTTP proposal/NO_TRADE transport merged to main
- proposal → TradeIntent still not implemented
- external Supervisor still not implemented

PROCESS
- Dev 1 and Dev 2 now use separate worktrees and separate test databases
- cross-track notices exist
- main is the integration point
```

The old shared-checkout/shared-test-DB problem is considered operationally resolved unless it recurs under the new isolation model.

---

# 3. One source-of-truth rule from now on

The current documentation is useful but too easy to read out of order.

From this review onward use this hierarchy:

```text
1. status.md TOP SECTION
   = integrated current truth for owner/reviewer

2. review/AGENT_STATUS.md
   = detailed Dev-2 work log

3. review/AGENT_FEEDBACK.md
   = Dev-2 AG-### register only

4. review/INTEGRATION_NOTICES.md
   = only cross-track contract/schema/process notices

5. review/FEEDBACK.md
   = project F-### register

6. feedback.X.Y.md
   = immutable formal reviewer decisions
```

## Mandatory `status.md` header

Dev 1 keeps the top of `status.md` compact and current. At minimum it must show:

```text
current main SHA
last hosted CI result
Dev 1: DONE / NEXT / BLOCKED
Dev 2: DONE / NEXT / BLOCKED
F-051 state
owner blockers
order_send = NO-GO / GO
next formal review target
```

Detailed historical entries may remain below, but an old historical paragraph must not override the top current-state section.

A meaningful slice merged to `main` should update the relevant track status in the same session. Do not let phrases such as "not merged", "no HTTP transport", or "not committed" remain in current-state prose after Git says otherwise.

No new per-track feedback-number series is introduced.

---

# 4. F-063 — hosted CI is currently broken by the uv invocation

**Severity:** HIGH BEFORE M0 / `feedback.2.0`  
**Owner:** Dev 1  
**Status:** OPEN

The newest hosted CI run on current `main` fails before tests on both Linux and Windows.

Current workflow combines:

```text
UV_FROZEN=1
uv sync --locked
```

Current `uv` refuses that combination:

```text
the argument `--locked` cannot be used with `UV_FROZEN`
```

This is now the concrete hosted-CI blocker; do not keep narrating the older numpy dependency failure as the current cause.

## Required fix

Prefer the simpler contract:

```text
remove UV_FROZEN=1 from the workflow
keep: uv sync --locked
```

Then push and require a real hosted run where:

```text
Linux lint/types/tests = green
Windows tests = green
PostgreSQL integration = actually ran
gitleaks = green
overall workflow = green
```

Close F-063 only with the hosted Actions run URL/id and result recorded in `status.md` / `review/FEEDBACK.md`.

---

# 5. AG-006 decision — unblock it without building a fake generic strategy engine

There has been drift in the description of AG-006.

The required invariant remains:

```text
TradeIntent.feature_snapshot_id stays REQUIRED.
```

Do not make it optional.

But AG-006 is **not** a reason to first invent one universal `compute_features()` that normalizes `baseline_v1`, `ict_v1`, and every future external strategy.

The existing feature persistence layer is already intentionally generic over `FeatureEvidence` and explicitly allows multiple concrete feature shapes. That is enough for the product direction.

## Required semantics

Before Crumblr issues an external `DecisionContextBundle`, Crumblr must have materialized durable, point-in-time evidence for the context the external Trader is allowed to see.

Use this model:

```text
platform context publication
    ↓
platform-owned FeatureEvidence / context-evidence snapshot persisted
    ↓
DecisionContextBundle carries trusted feature_snapshot_id
    ↓
external Trader receives the bundle
    ↓
TradeProposal binds to exact context hash
    ↓
Agent Gateway verifies issued context + evidence reference
    ↓
platform creates TradeIntent
    ↓
TradeIntent.feature_snapshot_id = bundle.feature_snapshot_id
```

For an internal strategy context, that ID may refer to the existing baseline/ICT feature evidence.

For a genuinely external-agent context that does not match either concrete strategy feature shape, add one deliberately named platform-owned evidence shape, e.g. `agent_context_v1`, rather than pretending a baseline or ICT feature calculation occurred.

Requirements:

```text
- snapshot created by Crumblr, never by the external agent
- snapshot exists durably before the bundle is issued
- snapshot is immutable/content-addressed or otherwise identity-stable
- bundle content_hash includes the feature snapshot reference
- Gateway refuses an unknown/missing snapshot
- no placeholder UUIDs
- no post-hoc fabricated snapshot after a proposal arrives
- external evidence_refs remain separate from this platform evidence identity
```

## Ownership

Dev 2 owns the `DecisionContextBundle` addition and proposal→TradeIntent mapping.

If one small shared `FeatureEvidence` contract/type addition is required, use the existing Dev1↔Dev2 handshake and add one integration notice. Do not wait for a broad cross-strategy refactor.

Once this is implemented and tested, close AG-006 and proceed immediately to the shared integration path.

---

# 6. Dev 1 work order — Core / Execution

Dev 1 remains the owner of financial safety and submission mechanics.

Priority order:

```text
1. Fix F-063 hosted CI and prove it green.
2. Support/restart F-051 part 2 evidence path with baseline_v1.
3. Implement SUBMISSION_STARTED at the exact durable pre-side-effect point.
4. Harden execution-event same-id/different-content conflict handling.
5. Build submission idempotence around future order_send.
6. Build ambiguous-outcome recovery — no blind resubmission after timeout/crash.
7. Build automatic flatten submission for the intraday deadline.
8. Derive post-fill expected state from durable platform execution history and reconcile it against broker truth.
9. Verify broker-side SL after a fill; absence/mismatch fails closed and escalates.
10. Assemble the final `feedback.2.0` readiness evidence.
```

Ordering rule:

> Do not add a real `order_send` call merely because items 1–5 exist. Submission stays unreachable until the whole readiness bundle is complete and `feedback.2.0` explicitly authorizes it.

Dev 1 should not wait for the external-agent track to finish before completing the Crumblr Execution Proof.

---

# 7. Dev 2 work order — Agent Integration

Priority order:

```text
1. Implement the AG-006 decision above.
2. Build TradeProposal → platform-owned TradeIntent mapping.
3. Add the shared no-MT5 integration path:
   DecisionContextBundle
   → proposal / NO_TRADE
   → Gateway
   → TradeIntent
   → intent-time Risk
   → deterministic Policy
   → capsule boundary.
4. Prove one genuine Crumblr context can reach the external Trader path in SHADOW with zero broker submission.
5. Implement the external Supervisor boundary:
   APPROVE / VETO / UNKNOWN,
   timeout/error/invalid = UNKNOWN,
   no mutation authority.
6. Integrate the colleague's real external Trader/Supervisor runtime through the typed boundary.
7. Replace placeholder code provenance before any agent-driven promotion.
```

The HTTP transport that now exists is useful, but deployment is not the next milestone by itself.

Do not add admin routes for identity/assignment/context issuance to the agent-facing app unless separately reviewed.

---

# 8. F-064 — HTTP Agent Gateway may not become an unprotected remote service

**Severity:** HIGH BEFORE REMOTE AGENT DEPLOYMENT  
**Owner:** Dev 2  
**Status:** OPEN / NOT A LOCAL-SHADOW BLOCKER

The HTTP boundary is now code on `main`, using the interim shared-secret identity mechanism.

That is acceptable for local/in-process or tightly controlled shadow integration.

It is **not** authorization to expose the Gateway publicly over an unprotected network.

Until a stronger transport/auth deployment boundary exists:

```text
- bind locally/private only
- do not expose directly to the public internet
- do not transmit credentials over plaintext transport
- no admin operations on the agent-facing surface
```

Before remote/non-local agent operation, require authenticated encrypted transport (TLS/mTLS or equivalent deployment boundary) and preserve the existing identity/assignment checks behind it.

This does not block fixture tests, local shadow tests, or an external process on the same controlled host/network.

---

# 9. Dashboard Observability v1.1 — authorized bounded side slice

The owner explicitly wants more visible operational truth now. That is reasonable at this maturity level, provided the dashboard remains read-only and never becomes an alternate control plane.

## Temporary ownership

Dev 2 may take one bounded Dashboard Observability slice after the AG-006 contract/mapping slice is either landed or waiting on a shared handshake.

This is a deliberate temporary exception to the normal Agent-only file ownership because it keeps Dev 1 focused on submission safety.

Dev 2 may edit:

```text
src/crumblr/dashboard/**
tests for the dashboard
```

It may consume existing read-only persistence APIs.

If a new Core persistence method is needed, request it from Dev 1 rather than modifying execution/risk semantics directly.

## Keep the read-only boundary

The dashboard must continue to have:

```text
no MetaTrader5 import
no broker credentials
no order_send
no cancel/flatten action
no HALT reset
no risk-config mutation
no Agent Gateway admin mutation
no direct operational control buttons
```

## Required visible data

Use persisted platform truth, not a second direct MT5 connection.

Add a clear operational panel for the latest durable broker snapshot. The existing `BrokerStateStore` already exposes a read path for latest account state plus positions and pending orders.

Show at least:

```text
ACCOUNT / DEMO
- masked account reference
- server
- environment clearly DEMO
- currency
- leverage
- balance
- equity
- floating profit/loss
- margin used
- free margin
- margin level
- account trade_allowed
- terminal AlgoTrading/trade_allowed
- broker snapshot observed-at time + age

LIVE MARKET DATA
- EUR/USD bid
- ask
- spread
- latest tick time + age
- latest closed M5 bar time
- data feed HEALTHY / STALE / DOWN
- reader connectivity
- reconnect count if available
- stored tick/bar counts and gap/anomaly state

EXPOSURE
- open positions count
- side / volume
- open price / current price
- SL / TP
- floating P/L
- pending order count

SAFETY / EXECUTION READINESS
- RUNNING / HALTED safety state
- latest reconciliation state if available
- current execution/submission readiness as informational state only
- `order_send: DISABLED / NO-GO` until the real gate changes

DECISION / AGENT ACTIVITY
- keep the existing clear distinction between replay/internal decisions and live feed
- later add latest external proposal/NO_TRADE and Supervisor verdict once those exist durably
```

## Freshness rules

Every live-looking number must display its observation timestamp or a clear age/freshness status.

If persisted broker state is old, render it as `STALE` rather than presenting old balance/equity/positions as current.

The dashboard may auto-refresh its **read-only page** every few seconds, but it must not create broker reads merely to refresh the screen.

## Acceptance

Tests must prove:

```text
- dashboard still has no mutating HTTP route
- dashboard package still has no MT5 import
- missing broker snapshot renders UNKNOWN/no-data, not zeros
- stale broker snapshot is visibly stale
- balance/equity Decimal values are not rounded into misleading binary floats
- account reference remains masked
- DEMO is unmistakable
- database outage differs from "no data yet"
```

This slice is capped: do not turn it into a dashboard redesign cycle. Ship the operational data visibility, then return Dev 2 to the Agent/Supervisor path.

---

# 10. F-051 part 2 remains immediate evidence work

Do not lose the real-data checkpoint behind new coding.

The known state was already sufficient for `baseline_v1` warm-up but the reader/live-decision processes stopped.

Run:

```text
mt5_live_reader.py
+
live_decision.py
```

against the intended soak environment.

Valid evidence is either:

```text
NO_TRADE
or
BUY/SELL proposal flowing through the existing non-sending live-decision path
```

Do not force a trade just to close the checkpoint.

This evidence remains separate from profitability and separate from the new external-agent product proof.

---

# 11. Owner decisions still required before `feedback.2.0`

Engineering must not silently promote placeholder values.

Owner approval is still required for:

```text
risk per trade
maximum daily loss
maximum drawdown
last-entry cutoff
mandatory flatten deadline
HALT-reset authority
```

`config/paper.yaml` values remain placeholders until explicitly approved.

AlgoTrading remains off until the pre-submission bundle is complete. Do not enable it merely to make an earlier `order_check` appear green.

---

# 12. Integration rhythm for both developers

At session start:

```text
git fetch origin
confirm own dedicated worktree
confirm branch prefix
confirm own test DB
read the top current-state section of status.md
read new INTEGRATION_NOTICES since last sync
```

For shared-contract/migration changes:

```text
proposal
→ explicit handshake
→ integration notice
→ both affected suites
→ shared integration suite
→ merge to main
```

Do not coordinate through stale narrative alone.

`main` is the integration truth. A local branch statement that conflicts with `origin/main` is historical, not current.

---

# 13. Phase-5 exit criteria

Phase 5 is complete when the following are true:

```text
FOUNDATION
[ ] hosted CI green
[ ] F-051 part 2 complete
[ ] owner risk policy approved

CORE
[ ] SUBMISSION_STARTED durable pre-side-effect
[ ] execution-event content conflicts fail closed
[ ] submission idempotence complete
[ ] ambiguous outcome recovery complete
[ ] automatic flatten submission complete
[ ] post-fill reconciliation based on durable platform history
[ ] broker-side SL verification complete

AGENT
[ ] AG-006 closed
[ ] proposal → TradeIntent mapping complete
[ ] shared integration path green
[ ] external Trader shadow proof complete
[ ] external Supervisor fail-closed boundary complete

OBSERVABILITY
[ ] dashboard shows fresh/stale live-market state correctly
[ ] dashboard shows latest durable DEMO balance/equity/margin
[ ] dashboard shows positions/pending exposure
[ ] dashboard remains structurally read-only
```

Then assemble the single `feedback.2.0` bundle.

---

# 14. What `feedback.2.0` must decide

The next formal submission review should not be another progress memo.

It must answer one binary operational question:

> Is Crumblr ready to make `order_send` reachable for exactly one deliberately constrained Pepperstone DEMO canary under owner-approved risk policy?

Required evidence remains:

```text
hosted CI green
owner-approved risk policy
F-051 part 2
automatic flatten submission
real SubmissionGate
durable human execution activation
SUBMISSION_STARTED pre-side-effect
submission idempotence
ambiguous outcome recovery
post-fill reconciliation
broker-side SL verification
HALT-reset authority
terminal/account permissions
DEMO/account/server guards
market/broker/reconciliation/safety health
relevant tests
```

If that first canary is called agent-driven, additionally require:

```text
Agent Gateway
TradingAssignment
external Trader
external Supervisor
agent/proposal provenance
shadow failure evidence
```

Until then:

```text
order_send = NO-GO
ExecutionConfig.feedback_2_0_approved = false
```

---

# Final decision

**PROCEED.**

The project has earned a new development phase because the safety core, real broker read path, persistent evidence layer, Agent Gateway, and parallel-development process now exist as working components.

The next step is not a broader architecture rewrite.

It is convergence:

```text
make CI honestly green
make real data flow again
finish submission safety
connect the external proposal path to the trusted TradeIntent boundary
add the required external Supervisor
make the operational truth visible on a read-only dashboard
then request feedback.2.0
```

Preserve the core invariant throughout:

> **Agent proposes. Risk engine constrains. Supervisor vetoes. Execution service executes. Reconciliation verifies.**
