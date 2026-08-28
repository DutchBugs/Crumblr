# Agent Integration track — local finding register

**This document is Dev-2-owned**, per
`review/CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md` §15. It uses
local workstream IDs (`AG-###`), never the project-wide reviewer `F-###`
series that lives in `review/FEEDBACK.md`. Do not create `feedback-dev2.*`
files — the one project-wide reviewer line stays `feedback.1.25.md` →
`feedback.2.0.md`.

These are self-identified gaps (found while building/reviewing this
track's own work against `review/THREAT_MODEL_AGENT_GATEWAY.md`), not
findings from an external reviewing agent. Escalate to the project-wide
reviewer only per instructions §18 (material safety ambiguity, a Phase-4
invariant needing to change, an authority-boundary dispute, an agent path
unexpectedly reaching execution, or a complete MVP readiness bundle).

---

## Open

| ID | Severity | Summary | Status | Closes when |
|---|---|---|---|---|
| AG-003 | HIGH | No Supervisor response-handling code exists — `UNKNOWN`-on-timeout/error is documented intent in the contract, not yet enforced behavior | OPEN | Step C Supervisor boundary implemented + tested |
| AG-005 | MEDIUM (no exposure yet; escalates to HIGH if an ingestion path is proposed) | No evidence/news ingestion path exists — the SSRF mitigation in the threat model (§4.7) is a constraint on a not-yet-built system | OPEN — deferred to Step D by design | Step D, when an ingestion path is actually designed |
| AG-006 | MEDIUM — blocks `TradeProposal → TradeIntent` mapping, not the shadow-ingestion milestone | The internal `TradeIntent` contract requires a non-optional `feature_snapshot_id: UUID`. An externally-originated proposal has no computed feature snapshot — only a `DecisionContextBundle` and `evidence_refs`. Constructing a real `TradeIntent` from an accepted `TradeProposal` therefore requires deciding what `feature_snapshot_id` means for an agent-originated decision, which is a semantic question about a **shared contract** (`CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md` §5 lists `TradeIntent` public shape as shared; §4 says stop and raise rather than force a change to protected/shared territory alone) | A Dev-1 handshake settles the semantics (e.g. a dedicated feature-snapshot record for agent-issued context, or a documented, agreed reinterpretation), then the mapping is implemented and tested |

## Closed

| ID | Severity | Summary | Status | Evidence |
|---|---|---|---|---|
| AG-001 | HIGH | No `service_identity` authentication mechanism existed | **CLOSED — interim mechanism shipped 2026-08-28** | `agent_gateway/auth.py::hash_credential`/`verify_credential` (salted SHA-256, constant-time compare) + `AgentCredentialStore`. Not the final mTLS/SPIFFE-shaped mechanism `service_identity`'s own naming implies — a real, fail-closed, testable boundary in the meantime. `tests/unit/test_agent_gateway.py::TestIdentity` (6 tests) |
| AG-002 | HIGH | No idempotent-claim persistence for `proposal_fingerprint` existed | **CLOSED — shipped 2026-08-28** | `agent_decision_outcomes` table (`persistence/schema.py`), `PostgresAgentDecisionOutcomeStore`/`InMemoryAgentDecisionOutcomeStore` (`agent_gateway/stores.py`, `persistence/agent_gateway.py`) — same `INSERT ... ON CONFLICT DO NOTHING RETURNING` claim discipline `persistence/execution.py` proves for internal execution requests. Also extended to `NoTradeDecision` via a new `decision_fingerprint` computed field (`agent_gateway/contracts.py`), which did not exist at Step A. `tests/unit/test_agent_gateway.py::TestIdempotency` (4 tests), `tests/integration/test_agent_gateway_store.py::TestRestartSafety`/`TestConcurrentClaimIsAtomic` (4 tests) |
| AG-004 | HIGH | No assignment-scope server-side enforcement existed | **CLOSED — shipped 2026-08-28** | `AgentGateway._evaluate_proposal`/`_evaluate_no_trade` (`agent_gateway/gateway.py`) always look up `TradingAssignment` by `assignment_id` server-side and check ownership, validity window, risk-fraction band and rate limit — never trusts a proposal's own description of its scope (threat model §4.2). `tests/unit/test_agent_gateway.py::TestAssignmentScope` (6 tests) |

---

## Notes

- Full threat rationale for each item above lives in
  `review/THREAT_MODEL_AGENT_GATEWAY.md` §7, which this table summarizes;
  that document is the source of truth for *why*, this table is for
  *status*.
- A finding here moves to Closed only with evidence (a commit, a file, a
  test), same discipline as the project-wide register in
  `review/FEEDBACK.md`.
- **None of AG-003/AG-005/AG-006 are exploitable today.** No transport
  (HTTP/gRPC/queue) exists yet for an external process to actually reach
  `AgentGateway`, and nothing outside `src/crumblr/agent_gateway/` and
  `src/crumblr/persistence/agent_gateway.py` imports this track's code —
  verified by grep 2026-08-28.
