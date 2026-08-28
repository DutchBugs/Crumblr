# Agent Integration track — status

**Workstream:** Dev 2 — External Agent Integration
**Owning instructions:** `review/CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md`
**Formal direction:** `feedback.1.25.md` (project-wide `review/FEEDBACK.md`)
**This document is Dev-2-owned.** Canonical `status.md` and
`review/FEEDBACK.md` remain Dev-1-owned; only a short consolidated summary
gets handed to Dev 1 at a meaningful merged milestone (instructions §15).

---

## 1. Where this track actually stands

| Step | Scope | State |
|---|---|---|
| A — design/contracts | ADR-005, threat model, eight contracts, structural tests | **Complete, committed** (`agent/contracts`, commit `cc16e4f`, 2026-08-27). |
| B — Agent Gateway in shadow | auth, assignment enforcement, idempotent proposal/NO_TRADE persistence, fail-closed error handling | **Ingestion + audit layer complete and tested, 2026-08-28. `TradeProposal → TradeIntent` mapping deliberately NOT built this pass — see AG-006 below.** Not yet committed (see §4). |
| C — Supervisor boundary | external Supervisor wired in, fail-closed on timeout/error | **Not started** (AG-003). |
| D — research/training plane | artifact registry, Backtest Requests, Training | **Deliberately not started** — out of scope before MVP per instructions §10. |
| E — first agent-driven canary | full Step B/C bundle + everything Milestone A (Crumblr Execution Proof) already requires | **Not started**, blocked on B/C. |

**Nothing in `src/crumblr/agent_gateway/` or `src/crumblr/persistence/agent_gateway.py`
is imported by anything outside itself and its own tests.** Verified by
grep 2026-08-28. No transport (HTTP/gRPC/queue) exists yet for an external
process to reach the Gateway at all — everything proven so far is proven
by calling `AgentGateway` directly from tests. No agent path can reach
execution, MT5, or the database outside this package's own six tables.

### What Step B actually proves (ADR-005 §8 "first proof target")

> One external Trader consumes one genuine Crumblr decision context and
> returns explicit NO_TRADE or a valid BUY/SELL proposal with SL+TP, and
> Crumblr durably records identity, assignment, context and outcome in
> SHADOW with zero broker execution.

Built and tested against this exact bar — `AgentGateway`
(`agent_gateway/gateway.py`) plus six new PostgreSQL tables
(`agent_identities`, `agent_credentials`, `agent_trading_assignments`,
`agent_decision_context_bundles`, `agent_decision_outcomes`,
`agent_decision_events`; migration `d4b6e2f81a37`, off Dev-1's confirmed
head `c9e1d5a3f286`):

- **Identity/authentication** — interim shared-secret credential
  (`agent_gateway/auth.py`, salted SHA-256, constant-time compare), fails
  closed on unknown agent, wrong credential, or suspended/retired status.
  Not the final mTLS/SPIFFE mechanism `service_identity` is named for
  (AG-001, closed to a first-draft-acceptable degree, not to the threat
  model's ultimate target).
- **Assignment authorization** — every proposal/NO_TRADE is checked
  server-side against a durably-registered `TradingAssignment`: ownership,
  validity window, requested-risk band, hourly rate limit. Never trusts a
  proposal's own description of its scope.
- **Context-hash binding + expiry** — a proposal's `context_hash` must
  match a `DecisionContextBundle` Crumblr actually issued, for the correct
  assignment, not yet expired.
- **Idempotency / conflicting-retry detection** — `TradeProposal.proposal_fingerprint`
  and a new `NoTradeDecision.decision_fingerprint` (added this pass,
  mirroring the proposal one) are claimed via the same
  `INSERT ... ON CONFLICT DO NOTHING RETURNING` primitive
  `persistence/execution.py` already proves for internal execution
  requests. An identical retry replays the original result with no new
  side effect; a conflicting retry raises `DecisionConflictError`.
- **Fail-closed audit discipline** — every submission is durably claimed
  (`RECEIVED`) *before* any authorization check runs, so a legitimate
  refusal is a normal, auditable, machine-readable outcome
  (`AgentRejectionReason`), never a silently-dropped attempt.
- **Explicit `NO_TRADE`** — structurally independent of `TradeProposal`
  (proven at Step A); this pass adds the same idempotent-claim and
  audit-trail treatment NO_TRADE needs to actually flow through the
  Gateway, not only exist as a contract.
- **Restart safety** — proven against real PostgreSQL: a second,
  independently-constructed `AgentGateway` (simulating a crashed-and-
  restarted process) replays an identical retry safely and still fails
  closed on a conflicting one, because no Gateway method caches identity,
  assignment, or claim state in memory between calls.

### What Step B deliberately does not do yet

- **No `TradeProposal → TradeIntent` mapping.** `TradeIntent` requires a
  non-optional `feature_snapshot_id: UUID`; an externally-originated
  proposal has no computed feature snapshot. This is a shared-contract
  semantic question (`CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md`
  §4/§5: stop and raise rather than force it alone) — tracked as **AG-006**
  in `review/AGENT_FEEDBACK.md`, not silently worked around. ADR-005's own
  Step B description doesn't actually require this mapping either — Risk/
  Policy Gate/`DecisionCapsule` sealing is Step C territory (§9).
- **No `ProposalWithdrawal` enforcement.** The contract exists (Step A);
  wiring its `SUBMISSION_STARTED`-cutoff rule needs the intent-mapping
  above to exist first, since there is no execution timeline to check
  against yet.
- **No wire transport.** Everything is proven by calling `AgentGateway`
  directly. "Malformed input rejected" (ADR-005 §8) is proven at the
  contract level (Step A's 31 tests — pydantic validation makes an invalid
  `TradeProposal` unconstructible) but not yet at a wire-deserialization
  boundary, because no such boundary exists yet.

---

## 2. Evidence, this session (2026-08-28)

- `uv run pytest tests/unit/test_agent_gateway_contracts.py -q` — **29
  passed** (was 27 at Step A; +2 for the new `decision_fingerprint`
  computed field's own tests, added this pass for AG-002).
- `uv run pytest tests/unit/test_agent_gateway.py -q` — **24 passed**
  (new this session — the full ADR-005 §7 test matrix against in-memory
  stores).
- `uv run pytest tests/integration/test_agent_gateway_store.py -q -m
  integration` — **6 passed** (new this session — real PostgreSQL,
  restart-safety, concurrent-claim atomicity).
- `uv run pytest tests/integration/test_migrations.py -q -m integration`
  — **8 passed** (proves the new migration and `persistence/schema.py`
  agree exactly, run in isolation — see the note on shared-database
  contention below).
- `uv run ruff check` / `ruff format --check` on every new/changed file —
  clean.
- `uv run mypy` (project-wide, via the configured invocation) — **no
  issues in 147 source files**.
- `uv run pytest -m "not integration" -q` (full non-integration suite) —
  **834 passed, 1 skipped** (unrelated pre-existing Windows/MT5-importability
  skip), proving Step B did not disturb Phase 4 or Step A.

**Observed, not a defect in this work:** a full-suite run including
integration tests intermittently failed with `relation "..." already
exists` / `relation "..." does not exist` errors unrelated to any specific
table. Root-caused to test/database contention, not a code defect —
`tests/integration/conftest.py::engine` drops and recreates the entire
schema per test against a single fixed database URL
(`persistence.engine.DEFAULT_TEST_URL`), and this session's own evidence
(`review/FEEDBACK.md`/`status.md` changing on disk mid-session, see the
git-log entries around 2026-08-27/28) points to a concurrent Dev-1 session
running its own integration suite against the same shared local Postgres
instance at the same time. Every test in this track's own scope passes
cleanly and repeatably when run without that collision (`tests/integration/test_agent_gateway_store.py`
run alone: 6/6, twice). Not raised as a formal finding — it is a shared
test-infrastructure property, not specific to this track, and instructions
§18 doesn't name this as an escalation trigger — but worth Dev 1 knowing
about if it recurs during any coordinated CI push.

---

## 3. Local finding register (AG-###)

See `review/AGENT_FEEDBACK.md` for the full register with evidence. Summary:

| ID | Summary | Status |
|---|---|---|
| AG-001 | `service_identity` authentication mechanism | **CLOSED** (interim shared-secret, not final mTLS/SPIFFE) |
| AG-002 | Idempotent-claim persistence for `proposal_fingerprint`/`decision_fingerprint` | **CLOSED** |
| AG-003 | Supervisor response-handling / fail-closed-on-timeout | OPEN — Step C |
| AG-004 | Assignment-scope server-side enforcement | **CLOSED** |
| AG-005 | Evidence/news ingestion path (SSRF mitigation unproven, no exposure yet) | OPEN — deferred to Step D by design |
| AG-006 | `TradeIntent.feature_snapshot_id` semantics for agent-originated intents — blocks `TradeProposal → TradeIntent` mapping | OPEN — needs a Dev-1 handshake (shared contract) |

---

## 4. Outstanding process item — not yet committed

Step B's entire diff (contracts addition, gateway/stores/auth/errors/events
modules, schema additions, migration, Postgres store implementations, unit
+ integration tests, this document and `AGENT_FEEDBACK.md`) is currently
uncommitted on branch `agent/contracts`, on top of the already-committed
Step A (`cc16e4f`). Per `CLAUDE.md` §4, commits happen only when the user
asks — Step A was committed this way earlier in the session; Step B is
held pending the same confirmation.

---

## 5. Next actions

1. Ask the user/owner whether to commit Step B.
2. Raise AG-006 (the `feature_snapshot_id` question) with Dev 1 — this is
   the one place Step B's own scope genuinely touches shared-contract
   territory and instructions say to stop and raise rather than resolve it
   alone.
3. Once AG-006 is resolved: implement `TradeProposal → TradeIntent`
   mapping, then Step C (external Supervisor boundary, AG-003).
4. Do not request formal reviewer input for routine continuation — only
   if AG-006's resolution turns out to require a Phase-4-invariant change
   (instructions §18).

---

## 6. Summary for Dev 1 (canonical `status.md`, when next handed over)

> Agent Integration track, Step B (Agent Gateway ingestion + audit layer,
> ADR-005 §8's "first proof target"): identity/credential authentication,
> assignment authorization, context-hash binding + expiry, idempotent
> proposal/NO_TRADE claiming with conflict detection, fail-closed audit
> trail. Six new PostgreSQL tables via migration `d4b6e2f81a37` (off your
> confirmed head `c9e1d5a3f286`) — `agent_identities`, `agent_credentials`,
> `agent_trading_assignments`, `agent_decision_context_bundles`,
> `agent_decision_outcomes`, `agent_decision_events`, all in
> `APPEND_ONLY_TABLES`. 30 new/changed tests (24 unit + 6 integration),
> plus 29 Step-A contract tests (2 new, for a `NoTradeDecision.decision_fingerprint`
> field this pass needed). Full non-integration suite (834 tests) and the
> migration-equivalence suite both green; nothing outside this track's own
> files imports any of it. **One open item needs your input: AG-006** —
> `TradeIntent.feature_snapshot_id` is non-optional and I don't think it's
> my call alone what it should mean for an agent-originated decision;
> see `review/AGENT_FEEDBACK.md` for the exact question. Not committed yet,
> pending the same go-ahead Step A got.
