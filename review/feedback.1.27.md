# feedback.1.27.md — Two-track checkpoint & Static Agent integration kickoff

**Project:** Crumblr — Autonomous EUR/USD Trading Platform  
**Review version:** 1.27  
**Date:** 2026-09-01  
**Reviewed baseline:** `main` at `ebf87e3`, current `status.md`, current Dev-2 Agent status, hosted Actions run 60, and the current fork `DutchBugs/crumblr-static-agent-host`  
**Previous:** `feedback.1.26.md`

## Verdict

**GO — continue Phase 5 and start the Static Trading Agent integration now, in SHADOW only.**

This is another explicit owner-requested coordination checkpoint, not a reopening of Phase 4 and not an execution authorization.

`order_send` remains **NO-GO**. `ExecutionConfig.feedback_2_0_approved` remains `false`.

The next normal formal submission review remains `feedback.2.0.md`.

---

# 1. Both developers must respond once, in their existing owned status files

Do not create more status families.

After reading this review, each track writes one short response block in its existing owned document during the same development session:

```text
Dev 1 → status.md / review/FEEDBACK.md as appropriate
Dev 2 → review/AGENT_STATUS.md / review/AGENT_FEEDBACK.md as appropriate
```

The response should contain only:

```text
ACK feedback.1.27
current branch/worktree
current main SHA fetched
what is DONE since 1.26
what is NEXT
what is BLOCKED
what needs the other track
```

No essay, no new feedback-number series, no duplicate project status file.

The top compact block of `status.md` remains the integrated owner/reviewer truth.

---

# 2. Current state accepted

The project has moved materially since review 1.26.

Accepted current state:

```text
CORE / DEV 1
- SubmissionGate exists and is called for real
- F-051 part 2 is CLOSED
- real Pepperstone DEMO EUR/USD data produced two baseline_v1 decisions
- those decisions reached intent-time Risk PASS and Supervisor APPROVE
- decision pipeline was then stopped and safety returned to HALTED
- real MT5 order_send remains structurally unreachable

AGENT / DEV 2
- agent_context_v1 evidence exists
- AG-006 is closed
- TradeProposal → platform-owned TradeIntent is implemented and merged
- the shared no-MT5 Risk → Policy → capsule path is next
- external Supervisor is still later in the sequence

STATIC AGENT FORK
- separate HTTP trader service exists
- it has no MT5, broker, database or execution authority
- it already has a frozen deterministic StaticTrader internally
- its current public contract is older/different from Crumblr's new Agent Gateway contract
```

This is enough to begin integration work without waiting for `feedback.2.0`.

---

# 3. F-065 — hosted CI is now failing only on reviewer Markdown formatting

**Severity:** MEDIUM now / HIGH before `feedback.2.0`  
**Owner:** Dev 1  
**Status:** OPEN

The previous `uv` install problem is fixed. Hosted run 60 proves:

```text
Dependency install: PASS
Ruff lint: PASS
Windows tests: PASS
No-secrets scan: PASS
Linux job: FAIL at ruff format --check
```

The formatter is trying to rewrite Python-looking code fences inside immutable historical reviewer artifacts:

```text
review/feedback.1.22.md
review/feedback.1.23.md
review/feedback.1.24.md
```

Do **not** modify those immutable reviewer files just to satisfy Ruff.

Required fix: scope the formatter to actual Python/source/test paths, or explicitly exclude reviewer Markdown from format checking while preserving source formatting enforcement.

Then require a fresh hosted run where Linux + Windows + PostgreSQL test coverage + no-secrets all complete green.

Dev 1 should record the hosted run URL/id and close F-065 only after the green run exists.

---

# 4. Static Agent integration finding — the current contracts do not yet meet in the middle

This review compared both repositories directly.

The new Crumblr `DecisionContextBundle` is intentionally an immutable **reference/binding object**. It carries IDs/hashes such as:

```text
market_snapshot_id
instrument_spec_version
portfolio_summary_hash
feature_snapshot_id
session/data-quality state
content_hash
```

It does **not** contain the actual market/instrument/feature values an external strategy needs to make a decision.

The current Static Agent, correctly, has no direct Crumblr database access. Its current `TraderContext 1.0` therefore expects actual typed values supplied by Crumblr: strategy identity, market metadata, instrument spec and feature observation.

Therefore:

> **Do not try to send the bare `DecisionContextBundle` to the Static Agent and do not give the Static Agent database access so it can resolve the references itself.**

Crumblr must materialize an outbound, read-only decision payload from its trusted snapshots and bind that payload to the exact issued `DecisionContextBundle`.

This is the missing seam for the first genuine external Trader hookup.

---

# 5. Required bridge design — keep the frozen strategy frozen

Do not rewrite Jari's frozen Static Trader and do not import it into Crumblr.

Build a narrow integration bridge around it.

Target shape:

```text
Crumblr trusted snapshots
        ↓
DecisionContextBundle issued
        ↓
StaticAgentContextPayload v1
(actual read-only values + exact bundle binding)
        ↓ HTTP
Static Agent Host
        ↓
legacy Static Trader decision
        ↓
Crumblr bridge validates + translates
        ↓
NoTradeDecision OR TradeProposal
        ↓
AgentGateway
        ↓
platform-owned TradeIntent
        ↓
Risk → Policy → capsule
        ↓
STOP (shadow)
```

## 5.1 Outbound context payload

Dev 2 owns the Crumblr-side contract/adapter.

The payload sent to the external Static Agent must contain enough typed data to make the frozen decision while preserving provenance. At minimum bind:

```text
context_id
DecisionContextBundle.content_hash
assignment_id
feature_snapshot_id
market_snapshot_id
issued_at / expires_at
canonical symbol + timeframe from TradingAssignment
market-data health + last closed M5 time
trusted broker symbol / instrument-spec fields required by the Static Agent
trusted feature observation required by the assigned strategy
source-bar/evidence identities
```

The payload may carry actual read-only values, but every value must be derived from a durable Crumblr snapshot/reference already bound into the platform context.

The external agent must not receive broker credentials, raw account login, mutation handles, database credentials, Risk configuration writers or execution capability.

## 5.2 Response translation

The current Static Agent outer response still calls a directional result `TRADE_INTENT` and contains its own nested `trade_intent` object.

That name is legacy at the boundary.

Crumblr must **never** accept that object as the platform `TradeIntent`.

For the first bridge:

```text
agent decision_type=NO_TRADE
    → construct Crumblr NoTradeDecision

agent decision_type=TRADE_INTENT
    → treat nested geometry/reason fields as UNTRUSTED PROPOSAL MATERIAL
    → construct Crumblr TradeProposal
    → submit through AgentGateway
    → let AgentGateway construct the only authoritative platform TradeIntent
```

Ignore/recompute any agent-provided `intent_id`, executable flag, execution authority, next-component instruction or routing authority.

The bridge may preserve deterministic strategy proposal identity as provenance, but Crumblr remains authoritative for the Gateway proposal ID/fingerprint rules and all internal IDs after ingress.

## 5.3 Long-term cleanup

Once the smoke/shadow bridge works, prefer updating the fork's **outer API contract** to emit the Crumblr external `TradeProposal` / `NoTradeDecision` shape directly.

That later cleanup must leave the frozen strategy package/source hash untouched.

Do not block the first smoke test on that cleanup.

---

# 6. Dev 2 work order — start the Static Agent hookup

Do these in order.

```text
A. Complete shared no-MT5 integration path
   TradeIntent → fresh intent-time Risk → deterministic Policy → capsule boundary.

B. Implement StaticAgentContextPayload / context-materialization adapter.

C. Implement a Static Agent HTTP client with strict timeout, response-size limit,
   schema validation, no redirects to arbitrary hosts, and fail-closed handling.

D. Implement legacy response → Crumblr NoTradeDecision / TradeProposal translation.

E. Submit translated outcomes through the existing AgentGateway.

F. Prove idempotent replay: same external response cannot create a second distinct
   platform proposal/intent.

G. Prove failures: timeout, 401, 422, malformed JSON, wrong context binding,
   stale/expired context and strategy identity mismatch all produce no executable path.

H. Run first synthetic transport smoke test against the forked Static Agent service.

I. Run first genuine Crumblr live-shadow context through the Static Agent when the
   required assigned-strategy evidence is available. NO_TRADE is valid proof.

J. Only after that, continue to the external Supervisor boundary.
```

All A–I are zero-order work. No MT5 submission is added.

## Required integration evidence

The first acceptable real shadow chain is:

```text
real persisted Crumblr M5/broker context
→ issued platform context + materialized outbound payload
→ separate Static Agent process
→ explicit NO_TRADE or directional proposal
→ AgentGateway auth/assignment/context/idempotency
→ platform-owned TradeIntent if directional
→ Risk
→ Policy
→ capsule/audit
→ zero order_send calls
```

---

# 7. AG-012 — shadow workaround accepted, execution architecture still unresolved

The newly identified risk-ledger statefulness issue is real.

Two separate processes must not eventually maintain independent in-memory copies of one shared daily-loss/drawdown budget and both believe they are authoritative.

For **shadow only**, the documented interim mitigation is accepted:

```text
recover_session() freshly immediately before every Gateway-driven Risk evaluation
never rely on a long-lived cached agent-side EquityLedger
```

This narrows the problem enough for shadow evidence because broker submission remains unreachable.

Before an agent-driven `feedback.2.0` GO, this must become one shared serialized Risk authority / transactional budget update path, or another design with equivalent single-authority semantics.

Do not solve this by letting the external agent own or write risk state.

---

# 8. Dev 1 work order — keep Core on the submission-safety critical path

Dev 1 should not absorb the Static Agent implementation.

Priority:

```text
1. Fix F-065 hosted CI formatting scope and get hosted CI genuinely green.
2. Continue SUBMISSION_STARTED exact pre-side-effect semantics.
3. Harden execution-event same-id/different-content conflicts.
4. Finish future submission idempotence.
5. Finish ambiguous broker-outcome recovery / no blind resubmit.
6. Finish automatic flatten submission path.
7. Finish post-fill expected-state derivation + reconciliation.
8. Finish broker-side SL verification.
9. Support Dev 2 only where a read-only Core persistence/query seam is actually missing.
```

Do not implement a real MT5 `order_send` yet.

If Dev 2 needs data for the Static Agent bridge, expose the smallest read-only durable-snapshot seam necessary; do not give the agent process direct DB or broker access.

---

# 9. Dashboard Observability v1.1 remains authorized

The dashboard request from review 1.26 remains active.

Dev 2 may take the bounded dashboard slice when the Static Agent bridge is waiting on a Core handshake or after the first synthetic Static Agent smoke test. It must not delay Core submission-safety work.

Still required:

```text
latest durable DEMO balance/equity/P&L/margin/free-margin
masked account/server/environment
bid/ask/spread + tick freshness
latest closed M5 + freshness
positions/pending exposure
reader health
safety state
reconciliation state
order_send explicitly NO-GO
```

Every live-looking value shows timestamp/age and STALE/UNKNOWN distinctly.

Dashboard remains read-only and must never open a second MT5 connection.

---

# 10. Static Agent fork rules

Repository currently reviewed:

```text
DutchBugs/crumblr-static-agent-host
```

Treat it as the integration fork of Jari's upstream.

For the first integration slice:

```text
DO NOT change frozen strategy logic/source hash
DO NOT add MT5
DO NOT add broker credentials
DO NOT add Crumblr DB credentials
DO NOT add final lot sizing
DO NOT add execution authority
```

Allowed later fork-side changes are limited to outer contracts/adapters, service health/version metadata, auth/transport hardening and tests required to meet the Crumblr boundary.

There is existing version drift in the fork's documentation/contracts (`0.4.0` / `0.5.0` / static trader `0.2.0`). Normalize exposed service/version metadata before calling the integration stable, but do not block the first local synthetic smoke test on documentation cleanup.

---

# 11. Owner decisions still required before 2.0

No change:

```text
risk per trade
maximum daily loss
maximum drawdown
last-entry cutoff
mandatory flatten deadline
HALT-reset authority
terminal AlgoTrading enablement timing
```

Current config values remain placeholders until explicitly approved.

---

# 12. When to return to the reviewer

Do not request `feedback.1.28` for normal implementation progress.

Return early only if:

```text
- a material safety defect is found,
- a Phase-4 invariant must change,
- the Static Agent integration requires widening agent authority,
- or the team cannot resolve the context-materialization / AG-012 seam without an architectural decision.
```

Otherwise both tracks continue until the complete `feedback.2.0` readiness bundle exists.

A short owner check-in is enough when the first Static Agent synthetic smoke test and the first genuine live-shadow external decision are each complete.

---

# Final direction

**START THE STATIC AGENT INTEGRATION NOW, BUT KEEP IT BORING AND NON-EXECUTING.**

The immediate product proof is not whether the Static Agent is profitable and not whether it can place an order.

The proof is:

```text
Crumblr can publish one trustworthy decision context,
a genuinely separate Static Agent can make one deterministic decision,
Crumblr can translate and authenticate that decision as a proposal,
its own Risk/Policy authority can evaluate it,
and the entire chain is durable, replay-safe and incapable of broker submission.
```

Preserve the invariant:

> **Agent proposes. Risk engine constrains. Supervisor vetoes. Execution service executes. Reconciliation verifies.**
