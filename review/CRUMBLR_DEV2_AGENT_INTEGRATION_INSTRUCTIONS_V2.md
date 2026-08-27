# CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md

**Workstream:** Dev 2 — External Agent Integration  
**Project:** Crumblr  
**Status:** supersedes the previous Dev-2 handoff  
**Formal direction:** `feedback.1.25.md`  
**Next planned formal gate:** project-wide `feedback.2.0.md`

---

# 1. Your role

You own the **Agent Integration track**.

Your job is to make Crumblr safely usable by external trading agents while
leaving the already-approved Risk/Execution/Reconciliation core intact.

Target boundary:

```text
external Trader
→ Agent Gateway
→ platform-owned TradeIntent
→ intent-time Risk
→ deterministic Policy Gate
→ external Supervisor
→ DecisionCapsule
→ EXISTING APPROVED EXECUTION CHAIN
```

Agents are external principals. Crumblr remains the safety authority.

---

# 2. Non-negotiable authority rules

An external agent must never:

```text
call MT5
receive broker credentials
write directly to Crumblr's database
choose final lot size
alter Risk policy
override Risk BLOCK/HALT
override deterministic Policy BLOCK/HALT
reset HALT
submit/cancel/flatten broker orders directly
seal a DecisionCapsule
promote itself or another artifact
treat free text as executable policy/command
```

External Supervisor for the agent-driven MVP:

```text
APPROVE
VETO
UNKNOWN
```

Timeout/error/malformed/missing review:

```text
UNKNOWN
→ no approval
```

---

# 3. Your owned area

You own:

```text
src/crumblr/agent_gateway/**
tests/**/agent_gateway/**
review/AGENT_STATUS.md
review/AGENT_FEEDBACK.md
review/adr/ADR-005-external-agent-trust-boundary.md
review/THREAT_MODEL_AGENT_GATEWAY.md
```

Use local workstream finding IDs:

```text
AG-001
AG-002
AG-003
...
```

Do not create global reviewer `F-###` findings yourself.

---

# 4. Protected Dev-1 area

Do not modify these without explicit coordination:

```text
src/crumblr/application/execution.py
src/crumblr/risk/**
src/crumblr/mt5_gateway/**
src/crumblr/application/reconciliation.py
src/crumblr/persistence/execution.py
SubmissionGate semantics
FINAL Risk semantics
execution idempotency/recovery semantics
post-fill reconciliation semantics
```

If Agent Gateway integration appears to require a change there, stop and raise
the exact blocker.

Do not "clean up" the execution chain while integrating agents.

---

# 5. Shared-contract area

Treat these as shared:

```text
src/crumblr/domain/models.py
src/crumblr/domain/enums.py
src/crumblr/domain/events.py
TradeIntent public shape
DecisionCapsule public shape
RiskDecision / SupervisorDecision public shape
shared provenance
ApprovedOrder public shape
Alembic revision graph
pyproject.toml / dependency lock when cross-track
```

Any semantic change uses a handshake:

```text
propose exact change
→ state reason + compatibility impact
→ Dev 1 acknowledges
→ affected tests on both tracks updated
→ merge
```

Do not modify a shared contract and let Dev 1 discover it from failing CI.

---

# 6. Step A — design/contracts

Implement:

```text
ADR-005
Agent Gateway threat model
AgentIdentity
TradingAssignment
DecisionContextBundle
PolicyHints
AgentDecision / NoTradeDecision
TradeProposal
SupervisorReview
contract tests
```

Required details:

### Explicit NO_TRADE

Never infer:

```text
no proposal = NO_TRADE
```

because no response may mean crash/timeout/transport loss.

Persist an explicit `NO_TRADE` decision.

### TradeProposal fingerprint

`TradeProposal` must expose a deterministic canonical content fingerprint.

Future Gateway invariant:

```text
same proposal_id + same fingerprint
→ idempotent retry

same proposal_id + different fingerprint
→ fail-closed conflict
```

### SL + TP

Directional external proposals require both before a platform `TradeIntent` may
be created.

### Requested risk

`requested_risk_fraction` is only an external request/cap.

It is not approved risk or lot size.

### PolicyHints

No open `dict[str, Any]` policy semantics at the external boundary.

Use a typed, frozen contract.

### Supervisor binding

Bind the review to:

```text
proposal_id
proposal_fingerprint
intent_id
intent_fingerprint / decision_hash
```

and, where practical, references to the relevant intent-time Risk / deterministic
Policy decisions.

### Withdrawal

Document and later enforce:

```text
before SUBMISSION_STARTED
→ withdrawal may be accepted and audited

at/after SUBMISSION_STARTED
→ agent no longer has withdrawal authority
```

### Evidence/news

Evidence refs must not silently become arbitrary URL-fetch capability.

Prefer references to approved immutable records.

---

# 7. Step B — Agent Gateway in shadow

After Step A tests are green, continue directly unless a material blocker exists.

Gateway responsibilities:

```text
service identity/authentication
assignment authorization
schema validation
context-hash binding
expiry
proposal-rate limits
idempotency
conflicting retry detection
explicit NO_TRADE handling
withdrawal before submission
append-only proposal/audit persistence
TradeProposal → platform TradeIntent mapping
fail-closed error handling
```

External agents never supply a trusted internal `TradeIntent` directly.

The Gateway creates the platform-owned `TradeIntent`.

---

# 8. First proof target

First meaningful milestone:

> One external Trader consumes one genuine Crumblr decision context and returns
> explicit NO_TRADE or a valid BUY/SELL proposal with SL+TP, and Crumblr durably
> records identity, assignment, context and outcome in SHADOW with zero broker
> execution.

Prove:

```text
agent process can disappear without making Crumblr unsafe
unauthorized assignment rejected
expired context rejected
identical retry idempotent
conflicting retry fail closed
NO_TRADE distinct from no response
timeout is not approval
malformed input rejected
restart does not duplicate logical proposal
```

---

# 9. External Supervisor milestone

After Trader/Gateway shadow path works:

```text
TradeProposal
→ Gateway
→ platform TradeIntent
→ Risk
→ deterministic Policy Gate
→ external Supervisor
→ platform seal
```

Supervisor may not mutate:

```text
side
entry
SL
TP
volume
risk
execution state
```

Risk/Policy failure cannot be repaired by Supervisor approval.

---

# 10. Out of scope before agent MVP

Do not build yet:

```text
Training Agent
Strategy Agent
Backtest Agent service
multi-agent ensemble
automatic strategy optimisation
self-promotion
multi-market platform
full news platform
artifact-management UI
live-money trading
```

Preserve provenance/evidence needed later, but do not let these delay MVP.

---

# 11. Git / branch convention

Use:

```text
agent/<short-topic>
```

Examples:

```text
agent/contracts
agent/gateway-shadow
agent/supervisor-boundary
```

Commit prefix:

```text
[agent] ...
```

Every meaningful commit/merge/PR includes:

```text
IMPACT: NONE
IMPACT: DEV1
IMPACT: DEV2
IMPACT: SHARED-CONTRACT
IMPACT: MIGRATION
```

If not `NONE`, add one sentence explaining the action required by the other
track.

---

# 12. Integration notices

Dev 1 owns the canonical:

```text
review/INTEGRATION_NOTICES.md
```

For every Dev-2 change with Core impact, include this block in your PR/merge
description and `AGENT_STATUS.md`:

```text
Integration Notice
Changed:
Impact on Core:
Action required:
Relevant commit:
```

Dev 1 copies/records it in the canonical integration-notice log when merged.

Do not rely on Dev 1 reading your entire `AGENT_STATUS.md`.

---

# 13. Alembic rule

Dev 1 coordinates migration ordering.

Before creating any Dev-2 migration:

```text
sync main
→ ask/confirm current Dev-1 Alembic head
→ create revision from that head
→ run migration tests
```

Do not independently create a revision from a stale head.

If Dev 1 has a migration in flight, coordinate before creating yours.

---

# 14. Tests

Your primary suite covers:

```text
agent contracts
Gateway auth
assignment scope
idempotency
expiry
NO_TRADE
timeout/failure behavior
Supervisor boundary
shadow behavior
restart recovery
```

When shared boundaries change, also run the shared integration-contract suite:

```text
external proposal fixture
→ Gateway
→ TradeIntent
→ Risk
→ deterministic Policy
→ capsule boundary
```

No real MT5 / `order_send` in this suite.

The existing Phase-4 tests must continue to pass unchanged.

---

# 15. Feedback separation

You own:

```text
review/AGENT_FEEDBACK.md
review/AGENT_STATUS.md
```

Dev 1 owns:

```text
review/FEEDBACK.md
status.md
```

Do not continuously edit canonical `status.md`.

At a meaningful merged milestone, provide Dev 1 a short 5–10 line summary for
canonical status.

Formal reviewer artifacts remain project-wide:

```text
feedback.1.25.md
→ build/evidence
→ feedback.2.0.md
```

Do not create:

```text
feedback-dev2.*
```

---

# 16. Merge/sync cadence

Normal cadence:

```text
work independently
→ sync main at least daily or at meaningful milestone
→ run Agent suite
→ run shared integration suite if boundary changed
→ issue integration notice if needed
→ merge
```

Do not block on every Core commit.

Shared-contract and migration changes require earlier coordination.

---

# 17. Do not block Dev 1 unnecessarily

Dev 1 may complete the first:

```text
CRUMBLR EXECUTION PROOF
```

with `baseline_v1`.

Your Agent Gateway does not need to be finished for that narrower execution proof.

Your work becomes mandatory before a canary may be described as:

```text
AGENT-DRIVEN MVP
```

---

# 18. When to request reviewer input

Do not request formal review for every contract/file.

Raise reviewer only if:

```text
material security/safety ambiguity
Phase-4 invariant appears to require change
authority-boundary disagreement with Dev 1
agent path can unexpectedly reach execution
complete agent-driven canary readiness bundle is ready
```

Normal implementation/test fixes stay inside the workstream.

---

# 19. Definition of done — Dev-2 shadow milestone

Ready when:

```text
AgentIdentity enforced
TradingAssignment scope enforced
DecisionContextBundle immutable/fingerprinted/expiring
NO_TRADE explicit
TradeProposal immutable/fingerprinted
SL + TP mandatory
requested risk non-authoritative
Gateway creates platform TradeIntent
proposal persistence append-only/idempotent
conflicting retry fail closed
timeout/no-response distinct from NO_TRADE
external Trader proven on replay + genuine shadow context
external Supervisor boundary proven fail-closed
no agent can reach MT5/credentials/direct DB writes
existing Phase-4 tests still pass
shared integration-contract suite green
```
