# CRUMBLR_DEV1_CORE_EXECUTION_INSTRUCTIONS.md

**Workstream:** Dev 1 — Core / Execution  
**Project:** Crumblr  
**Status:** operating instruction after `feedback.1.25.md`  
**Next planned formal gate:** `feedback.2.0.md`

---

# 1. Your role

You own the **Core / Execution track**.

Your job is to take the already-approved Crumblr safety/execution core from
non-sending Phase 4 to one deliberately constrained, fully auditable Pepperstone
DEMO execution proof.

You do **not** own the external-agent runtime or Agent Gateway internals.

Your core chain remains:

```text
platform-owned TradeIntent
→ intent-time Risk
→ deterministic Policy Gate
→ required authorities
→ DecisionCapsule
→ ExecutionOrchestrator
→ fresh broker observation
→ reconciliation
→ FINAL Risk
→ ApprovedOrder
→ order_check
→ later gated order_send
→ broker result
→ post-fill reconciliation
```

This chain is protected. Do not redesign it merely because the external-agent
track is being added in parallel.

---

# 2. Your primary scope

You own:

```text
SubmissionGate / F-049
durable execution activation authority
SUBMISSION_STARTED before broker side effect
order_send idempotence
ambiguous-outcome recovery
automatic flatten submission
post-fill reconciliation
broker-side SL verification
execution-event content-conflict hardening
F-051 part 2 support
CI/M0 closure support
feedback.2.0 readiness bundle
```

You also remain coordinator for:

```text
canonical status.md
review/FEEDBACK.md
Alembic migration ordering/head
shared core contract changes
```

---

# 3. Protected Dev-2 area

Do not modify these without explicit coordination:

```text
src/crumblr/agent_gateway/**
tests/**/agent_gateway/**
review/AGENT_STATUS.md
review/AGENT_FEEDBACK.md
review/adr/ADR-005-external-agent-trust-boundary.md
review/THREAT_MODEL_AGENT_GATEWAY.md
```

If a core change requires a change there, raise a cross-track notice first.

---

# 4. Shared-contract area

Treat these as **shared**, not Core-owned:

```text
src/crumblr/domain/models.py
src/crumblr/domain/enums.py
src/crumblr/domain/events.py
shared provenance contracts
DecisionCapsule public shape
TradeIntent public shape
SupervisorDecision / RiskDecision public shape
ApprovedOrder public shape
Alembic revision graph
pyproject.toml / dependency lock when cross-track
```

Any semantic change to a shared contract uses a handshake:

```text
propose change
→ state exact reason and compatibility impact
→ Dev 2 acknowledges impact
→ both affected test suites updated
→ merge
```

Do not merge a shared-contract semantic change first and inform Dev 2 later.

---

# 5. Cross-track integration boundary

Dev 2 must integrate **before** the existing core chain.

Expected handoff:

```text
external agent
→ Agent Gateway
→ validated platform-owned TradeIntent
→ Core path
```

Do not expose:

```text
MT5
broker credentials
execution state mutation
direct DB write privileges
final lot sizing
Risk policy mutation
HALT reset
```

to Dev 2's external agents.

Dev 1 should not depend on Agent Gateway internals.

---

# 6. Git / branch convention

Use:

```text
core/<short-topic>
```

Examples:

```text
core/submission-gate
core/ambiguous-recovery
core/post-fill-reconciliation
```

Commit prefix:

```text
[core] ...
```

Every meaningful commit/merge message or PR description includes one impact
label:

```text
IMPACT: NONE
IMPACT: DEV1
IMPACT: DEV2
IMPACT: SHARED-CONTRACT
IMPACT: MIGRATION
```

If the impact is `DEV2`, `SHARED-CONTRACT`, or `MIGRATION`, include one short
sentence explaining what Dev 2 must know or do.

---

# 7. Integration notices

Dev 1 owns the canonical shared notice log:

```text
review/INTEGRATION_NOTICES.md
```

Keep it very small and append-only.

Format:

```text
YYYY-MM-DD — DEV1|DEV2
Changed:
Impact:
Action required:
Relevant commit:
```

Example:

```text
2026-08-28 — DEV1
Changed: SUBMISSION_STARTED event is now emitted immediately before broker side effect.
Impact: DEV2 proposal withdrawal becomes invalid from this event onward.
Action required: Agent Gateway withdrawal tests must use this event as authority.
Relevant commit: abc1234
```

Dev 2 may provide its notice in its merge/PR; Dev 1 ensures the canonical log is
updated when the change lands.

Do not use `status.md` as the place to discover cross-track API changes.

---

# 8. Alembic rule

Dev 1 is the **migration traffic controller**.

Before Dev 2 creates a migration, Dev 1 provides/confirms the current head.

Before Dev 1 creates a migration while Dev 2 has one in flight, coordinate first.

Required flow:

```text
sync main
→ confirm current Alembic head
→ create revision
→ run migration-equivalence tests
→ merge
```

Avoid parallel uncoordinated heads.

If two heads appear anyway, resolve deliberately; do not hide the divergence.

---

# 9. Test ownership

Your local primary suite covers:

```text
Risk
execution
SubmissionGate
reconciliation
MT5 adapters
execution persistence
safety state
Phase-4 invariants
```

Both tracks must additionally run a small shared contract/integration suite
whenever a shared boundary changes.

Target shared path:

```text
external proposal fixture
→ Gateway mapping
→ platform TradeIntent
→ Risk
→ deterministic Policy
→ capsule construction/seal boundary
```

No real MT5 and no `order_send` in this cross-track suite.

This suite is the alarm for:

> both branches are green independently, but the combined product is broken.

---

# 10. Feedback ownership

## Dev 1

Continue to own:

```text
review/FEEDBACK.md
status.md
```

Use existing/core finding IDs there.

Do not copy Dev-2 implementation detail into these documents continuously.

Only add a short consolidated agent-track milestone to `status.md` after it has
actually landed on main.

## Dev 2

Dev 2 owns:

```text
review/AGENT_FEEDBACK.md
review/AGENT_STATUS.md
```

with local workstream finding IDs:

```text
AG-001
AG-002
AG-003
...
```

Those are not global reviewer `F-###` numbers.

---

# 11. Formal review numbering

There is still **one project-wide reviewer line**.

Do not create:

```text
feedback-dev1.*
feedback-dev2.*
```

Current direction:

```text
feedback.1.25.md
→ implementation + evidence
→ feedback.2.0.md
```

Do not request new formal review artifacts for normal implementation progress.

Raise the reviewer only if:

```text
a material financial-safety defect is found
a protected Phase-4 invariant must change
the Dev-1/Dev-2 authority boundary is disputed
an external path can unexpectedly reach execution
the complete feedback.2.0 readiness bundle is ready
```

---

# 12. Merge/sync cadence

Do not force both developers to rebase after every commit.

Normal cadence:

```text
work independently
→ sync main at least daily or at meaningful milestone
→ run own suite
→ run shared integration suite if boundary changed
→ add/consume integration notice
→ merge
```

Shared-contract or migration changes require earlier coordination.

---

# 13. Do not wait unnecessarily for Dev 2

The first **Crumblr Execution Proof** may use the existing in-process
`baseline_v1`.

Dev 2 does not block:

```text
F-051 part 2
SubmissionGate
execution recovery work
automatic flatten
post-fill reconciliation
feedback.2.0 execution readiness
```

unless the first canary is explicitly intended to be labelled **agent-driven**.

If the first canary uses the in-process baseline, label it:

```text
CRUMBLR EXECUTION PROOF
```

not:

```text
AGENT-DRIVEN MVP
```

---

# 14. Definition of done for Dev 1 before feedback.2.0

The Core track is ready to request `feedback.2.0.md` when the coherent evidence
bundle contains at minimum:

```text
hosted CI green
owner-approved risk policy
F-051 part 2 evidence
real SubmissionGate
durable execution activation
SUBMISSION_STARTED pre-side-effect
submission idempotence
ambiguous outcome recovery
automatic flatten submission
post-fill reconciliation from durable platform history
broker-side SL verification
HALT-reset authority
terminal/account execution permission checks
DEMO/account/server guard
market-data health
broker-state freshness/completeness
reconciliation MATCHED
safety RUNNING
full relevant test evidence
```

Until `feedback.2.0` GO:

```text
order_send = NO-GO
```
