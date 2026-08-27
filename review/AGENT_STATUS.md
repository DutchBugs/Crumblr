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
| A — design/contracts | ADR-005, threat model, eight contracts, structural tests | **Complete this session.** All artifacts existed except the threat model, which was missing despite ADR-005 §6 naming it — written this session (`review/THREAT_MODEL_AGENT_GATEWAY.md`). Everything in this row is currently **uncommitted** (see §4). |
| B — Agent Gateway in shadow | auth, assignment enforcement, idempotent proposal store, `TradeProposal → TradeIntent` mapping, fail-closed error handling | **Not started.** Gap list is `review/THREAT_MODEL_AGENT_GATEWAY.md` §7 (AG-001..AG-004). |
| C — Supervisor boundary | external Supervisor wired in, fail-closed on timeout/error | **Not started** (AG-003 in the threat model is the specific gap). |
| D — research/training plane | artifact registry, Backtest Requests, Training | **Deliberately not started** — out of scope before MVP per instructions §10. |
| E — first agent-driven canary | full Step B/C bundle + everything Milestone A (Crumblr Execution Proof) already requires | **Not started**, blocked on B/C. |

**Nothing in `src/crumblr/agent_gateway/` is imported by anything outside
itself and its own tests.** Verified by grep this session — the package is
structurally inert, not merely intended to be. No agent path can reach
execution, MT5, or the database.

---

## 2. Evidence for Step A, this session (2026-08-27)

- `uv run pytest tests/unit/test_agent_gateway_contracts.py -q` — **27
  passed**.
- `uv run ruff check` / `ruff format --check` on
  `src/crumblr/agent_gateway/` and the test file — clean.
- `uv run mypy src/crumblr/agent_gateway/` — no issues.
- `uv run pytest -m "not integration" -q` (full non-integration suite,
  proving Step A did not disturb Phase 4) — **792 passed, 1 skipped**
  (the skip is the pre-existing Windows-`MetaTrader5`-importability case,
  unrelated to this track).

No integration-marked tests exist for this track yet (there is no Gateway
to integrate against).

---

## 3. Local finding register (AG-###)

See `review/AGENT_FEEDBACK.md` for the full register. Summary:

| ID | Summary | Status |
|---|---|---|
| AG-001 | No `service_identity` authentication mechanism | OPEN — Step B |
| AG-002 | No idempotent-claim persistence for `proposal_fingerprint` | OPEN — Step B |
| AG-003 | No Supervisor response-handling / fail-closed-on-timeout code | OPEN — Step C |
| AG-004 | No assignment-scope server-side enforcement | OPEN — Step B |
| AG-005 | No evidence/news ingestion path (SSRF mitigation unproven, no exposure yet) | OPEN — deferred to Step D by design |

---

## 4. Outstanding process item — not yet committed

`git status` at session start showed the entire Step A deliverable
(`src/crumblr/agent_gateway/`, `tests/unit/test_agent_gateway_contracts.py`,
`review/adr/ADR-005-external-agent-trust-boundary.md`, both dev instruction
files) as **untracked**. Per `CLAUDE.md` §4, commits happen only when the
user asks. Flagging here rather than committing unilaterally: a full,
tested, quality-gate-clean Step A currently exists only in the working
tree, not in git history.

---

## 5. Next actions

1. Ask the user/owner whether to commit Step A (contracts + ADR-005 +
   threat model) before starting Step B, given `CLAUDE.md`'s commit
   discipline.
2. Begin Step B: Agent Gateway service — auth, assignment enforcement,
   idempotent proposal persistence, fail-closed error handling,
   `TradeProposal → TradeIntent` mapping. This requires an Alembic
   migration (proposal/audit tables), which per instructions §13 requires
   confirming the current Dev-1 Alembic head before creating a revision.
3. Do not request formal reviewer input for this — normal implementation
   progress stays inside the workstream per instructions §18.

---

## 6. Summary for Dev 1 (canonical `status.md`, when next handed over)

> Agent Integration track, Step A (ADR-005 architecture contracts:
> `AgentIdentity`, `TradingAssignment`, `PolicyHints`,
> `DecisionContextBundle`, `TradeProposal`, `NoTradeDecision`,
> `ProposalWithdrawal`, `SupervisorReview`) is implementation-complete and
> test-green (27 unit tests), plus a companion threat model
> (`review/THREAT_MODEL_AGENT_GATEWAY.md`). Nothing outside
> `src/crumblr/agent_gateway/` imports it; Phase 4 is untouched and its
> full suite (792 tests) still passes. Not yet committed — pending owner
> confirmation. Step B (the actual Gateway service) has not started.
