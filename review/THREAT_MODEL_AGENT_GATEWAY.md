# Threat model — Agent Gateway and external-agent input

**Status:** Step A deliverable (ADR-005 §6 names this file; it did not yet
exist when ADR-005 was written — this closes that gap before Step B begins).
**Date:** 2026-08-27
**Scope:** the trust boundary between external agent processes and Crumblr,
from `AgentIdentity`/`TradingAssignment` issuance through a `TradeProposal`
or `SupervisorReview` reaching the (not yet built) Agent Gateway. Does not
re-analyze Phase 4's already-reviewed execution chain (`ExecutionOrchestrator`
onward) — that boundary is unchanged and out of scope here.
**Companion documents:** `review/adr/ADR-005-external-agent-trust-boundary.md`
(the binding decision), `review/EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md`
(owner-supplied source), `src/crumblr/agent_gateway/contracts.py` (the
contracts this model assumes).

---

## 1. Trust boundary

```text
                          TRUST BOUNDARY
                                |
  external Trading Agent       |     Crumblr (trusted)
  external Supervisor Agent    |
  (any process, any host,      |     Agent Gateway (Step B, not yet built)
   any implementation,         |       ↓
   assumed hostile-capable —   |     platform-owned TradeIntent
   see §2)                     |       ↓
                                |     intent-time Risk / deterministic
        AgentIdentity  ------->|     Policy Gate / DecisionCapsule /
        TradingAssignment <----|     ExecutionOrchestrator (Phase 4,
        DecisionContextBundle <|     unchanged, out of scope here)
        TradeProposal  ------->|
        NoTradeDecision ------>|
        ProposalWithdrawal --->|
        SupervisorReview ----->|
                                |
```

Everything left of the line is untrusted input. Everything right of it,
including the Gateway itself once built, is Crumblr's own trusted computing
base. This mirrors ADR-005 rule 2 ("Crumblr owns safety authority") exactly:
the boundary is not a courtesy filter, it is where trust actually changes.

---

## 2. Trust assumptions about an external agent

An external agent — Trading or Supervisor — is treated as:

- **Not necessarily malicious, but not necessarily correct.** It may be
  buggy, stale, misconfigured, prompt-injected (if LLM-backed), compromised,
  or simply crashed mid-exchange. The design must be safe under all of
  these, not only under "the agent behaves as intended."
- **Capable of replaying, delaying, duplicating, or never sending a
  message.** No assumption of at-most-once or exactly-once delivery from
  the agent's side of the wire.
- **Capable of supplying any well-formed-looking payload**, including one
  that is internally consistent but references stale, expired, or
  never-issued context.
- **Never in possession of anything Crumblr did not explicitly issue it.**
  No broker credentials, no database access, no ambient authority. This is
  the non-negotiable list in `CRUMBLR_DEV2_AGENT_INTEGRATION_INSTRUCTIONS_V2.md`
  §2, restated here as a threat-model invariant rather than a policy
  reminder: an agent that somehow obtained broker credentials or DB write
  access is a failure of *this* boundary, not a downstream problem.

Free text from an agent (reason codes' prose, `calibration_notes`, a
malformed field) is data, never a command or policy input — guide §10's red
flag "vrije agent-chat als integratieprotocol" (free agent chat as
integration protocol) is treated as a standing prohibition, not a design
choice to be revisited later.

---

## 3. Assets being protected

| Asset | Why it matters |
|---|---|
| Broker credentials / MT5 session | Direct financial control if leaked |
| Final lot size / risk sizing authority | An agent proposing a size is a request; anything that let it become the *approved* size bypasses Risk |
| `TradeIntent` construction authority | Only the Gateway may mint one from a validated proposal; an agent-supplied `TradeIntent` would be an unaudited forgery |
| Kill-switch / HALT state | Reset authority must stay internal (Dev-2 instructions §2) |
| Audit integrity (append-only proposal/decision history) | The record of "what was proposed and why" is itself evidence; it must not be forgeable or silently overwritable |
| Database write path | No agent writes rows directly; everything is mediated and validated first |
| `DecisionCapsule` sealing authority | Stays platform-only per ADR-005 §3 |

---

## 4. STRIDE per boundary-crossing message

### 4.1 `AgentIdentity` (issuance/authentication)

- **Spoofing** — an agent claiming another agent's `agent_id`. Mitigated by
  `service_identity` being the actual authenticatable credential (guide's
  own framing: "a display name is not an identity" — `contracts.py` docstring),
  not a self-asserted UUID. Step B must authenticate `service_identity`
  cryptographically (mTLS/SPIFFE-shaped, per the field's own naming) before
  trusting any `agent_id` in a later message. **Not yet built — tracked as
  AG-001.**
- **Tampering** — a suspended/retired identity still being accepted.
  Mitigated structurally: `AgentStatus` is a closed enum and every Gateway
  authorization check must consult current status, not a cached one, before
  admitting a proposal (ADR-005 §7 test matrix, "Identity" row).
- **Repudiation** — an agent denying it sent a proposal. Mitigated by the
  append-only audit requirement (§6 below) plus authenticated
  `service_identity` binding every stored record to a verifiable sender.
- **Elevation of privilege** — an identity registered as `TRADER` acting as
  `SUPERVISOR` (or vice versa). `AgentRole` is closed and every
  authorization check must be role-scoped, not merely identity-scoped.

### 4.2 `TradingAssignment` (authorization scope)

- **Tampering** — an agent supplying a proposal against a wider scope
  (different symbol, larger risk band, more frequent than the assignment
  allows) than it was actually granted. Mitigated by construction:
  `TradingAssignment` is issued and stored by Crumblr, never accepted as
  agent-supplied input; the Gateway (Step B) must always look up the
  assignment by `assignment_id` server-side and reject any proposal whose
  claimed scope isn't a subset, never trust a proposal's self-description
  of its own assignment.
- **Denial of service** — an agent flooding proposals past
  `max_proposals_per_hour`. The field exists for exactly this; Step B must
  enforce it as a hard admission-time check, not an advisory metric.
- **Information disclosure** — an assignment leaking another agent's
  strategy artifact hash or risk band through error messages. Step B error
  responses must not echo back another principal's assignment detail on a
  scope-mismatch rejection.

### 4.3 `DecisionContextBundle` (what an agent is allowed to see)

- **Tampering** — an agent-supplied `context_hash` on a later `TradeProposal`
  that doesn't match anything Crumblr actually issued, or matches a bundle
  that has expired. `content_hash` (`computed_field`, complete-content
  fingerprint excluding only `context_id`) makes this checkable: Step B
  must reject any `TradeProposal.context_hash` that does not equal a
  bundle `content_hash` Crumblr itself issued and that has not expired
  (`expires_at_utc`). A proposal citing a hash Crumblr never produced is a
  forged-context attempt and must fail closed, not be silently accepted as
  "close enough."
- **Information disclosure** — the bundle itself is the deliberate,
  minimal disclosure surface: market snapshot, instrument spec, portfolio
  *summary* (a hash, not raw account detail), session/data-quality state,
  optional hints, optional news reference. Nothing broker-credential-shaped
  or DB-row-shaped is representable in this contract's fields — verified by
  reading `contracts.py` directly, not merely by intent.
- **Replay** — reusing an old, expired bundle's hash against a fresh
  proposal to smuggle stale context past a freshness check. Mitigated by
  the expiry check above plus `NO_TRADE`/proposal expiry (§4.4).

### 4.4 `TradeProposal` / `NoTradeDecision` (the agent's actual output)

- **Tampering** — a proposal missing SL/TP, or one where SL/TP sit on the
  wrong side of entry (an accelerator disguised as a brake). Mitigated at
  construction: `_check_stop_and_target_direction` and the required-field
  types make an invalid proposal impossible to construct, not merely
  possible-but-rejected-later. This is the strongest guarantee this model
  can make, because it is enforced by the type system before any Gateway
  code runs at all.
- **Repudiation / replay** — a duplicated or replayed proposal message
  causing two submissions. `proposal_fingerprint` (complete-content,
  excludes `proposal_id`) is the mechanism: Step B's idempotency rule
  (ADR-005 §7) — same `proposal_id` + same fingerprint = safe retry; same
  `proposal_id` + different fingerprint = fail-closed conflict — must be
  enforced with the same claim discipline `persistence/execution.py`
  already proves for internal execution requests. **Not yet built —
  tracked as AG-002.**
- **Denial of service / resource exhaustion** — expired proposals retried
  indefinitely, or a burst of proposals against one assignment. Covered by
  `expires_at_utc` rejection and `max_proposals_per_hour` (§4.2).
- **Spoofed absence** — treating "no proposal arrived" as `NO_TRADE`. This
  is the exact failure ADR-005/owner tweak 1 forbids: a crashed or silent
  agent must never read as an intentional no-trade decision. `NoTradeDecision`
  is structurally unrelated to `TradeProposal` (proven by
  `TestNoTradeDecisionIsIndependentOfProposal`) specifically so the Gateway
  cannot collapse "nothing received" and "explicitly declined" into one
  code path.

### 4.5 `ProposalWithdrawal`

- **Tampering** — a withdrawal accepted after `SUBMISSION_STARTED`, which
  would let an agent claim authority it never had (Dev-2 instructions §2:
  "submit/cancel/flatten broker orders directly" is forbidden; a
  late-honoured withdrawal is a laundering of exactly that). Step B must
  check the event timeline authoritatively (via
  `ExecutionEventType.SUBMISSION_STARTED`, the same marker Phase 4 already
  emits) before setting `honoured=True`, never trust the agent's own claim
  about timing.
- **Repudiation** — a withdrawal silently dropped rather than recorded.
  `ProposalWithdrawal.honoured` covers both outcomes explicitly; the
  contract has no code path that represents "attempt not recorded at all."

### 4.6 `SupervisorReview`

- **Elevation of privilege** — a `SupervisorReview` that mutates side,
  entry, SL, TP, volume, or risk. Structurally impossible today: the
  contract carries no fields that could feed those values back into a
  `TradeIntent`; it is a verdict-plus-references record only. Step B/C must
  preserve this by construction (no code path may read a field from
  `SupervisorReview` into an intent-mutating position) rather than relying
  on a runtime check to catch a violation after the fact.
- **Spoofing an approval** — a timeout, transport error, or malformed
  Supervisor response being read as `APPROVE`. `ExternalSupervisorVerdict.UNKNOWN`
  exists for exactly this and the Gateway's response-handling code (Step C)
  must default to constructing `UNKNOWN`, never omit constructing a review
  at all on failure — a missing review must not be treated the same as a
  `VETO`-shaped "nothing to block on" either; it must positively read as
  unreviewed. **Not yet built — tracked as AG-003.**
- **Tampering** — a review claiming to be about a `TradeIntent` it never
  actually reviewed. Mitigated by `trade_intent_id` +
  `trade_intent_decision_hash` binding (owner tweak 2): Step B must verify
  the hash matches the real intent before accepting the review as
  applicable, not merely store the claimed pair.

### 4.7 Evidence and news references

- **Server-side request forgery / arbitrary fetch** — the single highest-risk
  item in this model. `evidence_refs` and `news_snapshot_id` are typed as
  `UUID` references, not URLs or fetch instructions, specifically so an
  agent can never cause Crumblr (or anything Crumblr calls) to dereference
  attacker-controlled network locations. This is owner tweak 6 and guide
  red flag "nieuws zonder publicatietijd, revisiestatus en
  snapshotreferentie gebruiken" (news without publication time, revision
  status and snapshot reference). **Binding rule for Step B/D:** whatever
  ingests news/evidence into the content-addressed store the reference
  points at must do so through a separate, Crumblr-controlled ingestion
  path with its own provenance — an agent proposing a UUID must never be
  able to cause that UUID to be freshly created from agent-supplied
  content at read time. If evidence ingestion is ever exposed to an agent,
  it is a distinct write path with its own authorization, not implied by
  `evidence_refs` existing on `TradeProposal`.
- **Stale/misleading evidence presented as current** — an evidence ref
  pointing at outdated news being read as timely. Deferred to Step D
  (research/training plane) per ADR-005 §6; not a Step A/B concern since no
  evidence ingestion path exists yet.

---

## 5. Cross-cutting requirements (apply to every message type above)

- **Fail closed on ambiguity.** Any unparseable, partially-invalid, or
  ambiguous input is refused outright — never defaulted permissively,
  never "best-effort" repaired. This is the same posture Phase 4's own
  fail-closed recovery (F-054) already establishes; the Gateway inherits
  it rather than reinventing a softer version at a new boundary.
- **No half-committed state on agent crash.** A crash mid-exchange must
  leave either nothing recorded or a complete, valid record — never a
  partial row a later read could misinterpret as something it isn't. Same
  append-only claim discipline `persistence/execution.py` proves.
- **Authenticated identity on every write**, not only the first message of
  a session. A long-lived agent process must be re-verified (status,
  assignment validity) on each admission, not once at connection time.
- **No unstructured payload anywhere.** `PolicyHints` closes the one place
  the guide originally left open (`dict[str, Any]`); this model treats any
  future field addition that reopens an `Any`-typed or free-text-as-command
  surface as a structural regression, not a convenience.

---

## 6. What Step A already mitigates (by construction, not by policy)

Structural guarantees already proven by `tests/unit/test_agent_gateway_contracts.py`
(27 tests) as of this pass:

- Every contract is immutable (`frozen=True` via `Contract`) — no in-place
  mutation after construction, anywhere in this package.
- Every contract rejects unknown fields (`extra="forbid"`) — an agent
  cannot smuggle an undeclared field through validation.
- `TradeProposal` cannot be constructed without both SL and TP, or with a
  stop on the wrong side of entry.
- `NoTradeDecision` and `TradeProposal` are independently constructible and
  structurally unrelated — absence can never be misread as either.
- `PolicyHints` rejects an unrecognized key — proven closed, not `Any` in
  disguise.
- `proposal_fingerprint` and `content_hash` are stable for identical
  content and change under any field mutation, including a nested
  `PolicyHints` change — the exact property idempotency/forgery detection
  depends on.
- `ExternalSupervisorVerdict` has no `HALT` value and is a distinct type
  from the internal `SupervisorVerdict` — an external verdict cannot be
  mistaken for, or silently merged with, an internal one.

## 7. What is NOT yet mitigated — open before Step B is safe to run

These are real gaps, not merely unbuilt features, and are the actual
work of Step B. Recorded here so review of Step B's implementation can be
checked directly against this list.

| ID | Gap | Severity | Closes when |
|---|---|---|---|
| AG-001 | No `service_identity` authentication mechanism exists — `AgentIdentity` is a data shape, not yet an enforced credential | HIGH | Gateway auth implemented + tested |
| AG-002 | No idempotent-claim persistence for `proposal_fingerprint` exists yet — the fingerprint is computable but nothing stores/checks it | HIGH | Gateway proposal store implemented + tested |
| AG-003 | No Supervisor response-handling code exists — `UNKNOWN`-on-failure is a documented intent, not yet an enforced behavior | HIGH | Step C Supervisor boundary implemented + tested |
| AG-004 | No assignment-scope enforcement exists — a `TradingAssignment` is not yet looked up/verified server-side against any proposal | HIGH | Gateway authorization implemented + tested |
| AG-005 | No evidence/news ingestion path exists — §4.7's SSRF mitigation is a constraint on a not-yet-built system, not a proven control | MEDIUM (no ingestion path = no exposure yet; escalates to HIGH the moment one is proposed) | Deferred to Step D by design |

None of these are exploitable today: `src/crumblr/agent_gateway/` is not
imported by anything outside itself and its tests (verified by grep, per
ADR-005 §9), so there is no live Gateway to attack yet. This table is the
acceptance bar for Step B, not an incident list.

---

## 8. Explicit non-goals of this document

- Does not re-threat-model Phase 4's execution chain — already reviewed
  and formally passed (review 1.24).
- Does not threat-model the Training/Strategy/Backtest agent plane (Step
  D) — out of scope before the agent-driven MVP per Dev-2 instructions §10.
- Does not assume a specific transport (HTTP/gRPC/message queue) for Step
  B — authentication and idempotency requirements above apply regardless
  of transport choice, and the transport decision belongs in Step B's own
  design note when it is written.
