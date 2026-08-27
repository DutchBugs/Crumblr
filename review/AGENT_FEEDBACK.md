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

## Register

| ID | Severity | Summary | Status | Closes when |
|---|---|---|---|---|
| AG-001 | HIGH | No `service_identity` authentication mechanism exists yet — `AgentIdentity` is a validated data shape, not an enforced credential | OPEN | Gateway auth implemented + tested (Step B) |
| AG-002 | HIGH | No idempotent-claim persistence for `proposal_fingerprint` exists yet | OPEN | Gateway proposal store implemented + tested (Step B) |
| AG-003 | HIGH | No Supervisor response-handling code exists — `UNKNOWN`-on-timeout/error is documented intent in the contract, not yet enforced behavior | OPEN | Step C Supervisor boundary implemented + tested |
| AG-004 | HIGH | No assignment-scope server-side enforcement exists — nothing yet looks up a `TradingAssignment` and checks a proposal against it | OPEN | Gateway authorization implemented + tested (Step B) |
| AG-005 | MEDIUM (no exposure yet; escalates to HIGH if an ingestion path is proposed) | No evidence/news ingestion path exists — the SSRF mitigation in the threat model (§4.7) is a constraint on a not-yet-built system | OPEN — deferred to Step D by design | Step D, when an ingestion path is actually designed |

**None of these are exploitable today.** `src/crumblr/agent_gateway/` is
not imported by anything outside itself and its own tests — verified by
grep this session. There is no live Gateway to attack. This register is
the acceptance bar for Step B/C, not an incident list.

---

## Closed

*(none yet)*

---

## Notes

- Full threat rationale for each item above lives in
  `review/THREAT_MODEL_AGENT_GATEWAY.md` §7, which this table summarizes;
  that document is the source of truth for *why*, this table is for
  *status*.
- A finding here moves to Closed only with evidence (a commit, a file, a
  test), same discipline as the project-wide register in
  `review/FEEDBACK.md`.
