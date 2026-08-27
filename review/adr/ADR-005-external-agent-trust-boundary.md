# ADR-005 — External-agent trust boundary

**Status:** ACCEPTED — Step A implemented, remainder is Step B onward
**Date:** 2026-08-27
**Drivers:** owner decision O-007 (`review/FEEDBACK.md`), review 1.25
(`review/feedback.1.25.md`), `review/EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md`
(owner-supplied, referred to below as "the guide")
**Supersedes:** nothing. Extends the Phase-4 execution architecture
(ADR-001, ADR-002, ADR-003, ADR-004) without modifying any of it.
**Implementation:** `src/crumblr/agent_gateway/contracts.py`,
`tests/unit/test_agent_gateway_contracts.py`

---

## 1. The decision being recorded

The owner has adopted, as product direction:

> De agents draaien buiten Crumblr. Crumblr is het betrouwbare instrument
> waarmee zij marktcontext lezen, voorstellen indienen, risico laten
> toetsen, uitvoering aanvragen en bewijs teruglezen.
>
> (External agents run outside Crumblr. Crumblr is the trusted instrument
> they use to read market context, submit proposals, have risk checked,
> request execution, and read back evidence.)

Review 1.25 registered this as O-007 and gave two MVP-scoping
clarifications; the guide gives the full architecture. This ADR is the
binding technical record of both, restated against this repository's
actual files rather than left as prose in two separate documents a future
reader would have to reconcile by hand.

**This ADR records Step A only** — architecture contracts, still without
execution (guide §8 Step A). It does not authorize, build, or wire an
Agent Gateway, an endpoint, or any mapping from external input into a
`TradeIntent`. It changes nothing about `order_check` authorization or
the `order_send` NO-GO.

---

## 2. Eight binding rules (guide §2, restated as ADR text)

These are not suggestions a future implementation may weigh against other
concerns — they are the acceptance criteria review 1.25/the guide expect
any external-agent-shaped change to meet, and this ADR's own reviewers
should hold future work to them:

1. **Agents are external principals.** An agent has its own identity,
   version, assignment and runtime. Stopping, restarting or replacing an
   agent must never stop or restart Crumblr.
2. **Crumblr owns safety authority.** Risk, policy gates, sizing,
   execution, broker credentials, idempotency, reconciliation, kill
   switches and audit stay inside Crumblr and are deterministic wherever
   they bound financial risk.
3. **No agent receives broker or database privileges.** Agents get only
   typed, authenticated interfaces. They never write directly to
   Crumblr's database and never call MT5 directly.
4. **Strategies come from assignments and versioned artifacts.** `ict_v1`
   is not the leading product architecture. ICT and `baseline_v1` remain
   as reference, benchmark and regression fixtures; new strategies are not
   added as imports into the Crumblr core.
5. **A directional proposal always carries an explicit SL and TP.** The
   agent never supplies lot size. Crumblr determines volume from current
   account, market, broker and risk-policy data.
6. **A hard rule is never reduced to one score.** Risk may produce
   diagnostics and sub-scores, but financially relevant acceptance stays
   `PASS`, `BLOCK` or `HALT` with machine-readable reasons. A high
   composite score can never compensate for a hard block.
7. **A Supervisor Agent is not a safety foundation.** Semantic review of
   the reasoning may be external and probabilistic. A deterministic
   Policy Gate inside Crumblr stays active alongside it and can never be
   overruled by the external supervisor.
8. **Promotion stays human.** Strategy, Backtest and Training Agents may
   produce proposals and evidence, but never autonomously promote a
   strategy, prompt, model, risk policy or market into execution.

---

## 3. The target chain, and what stays authoritative unchanged

```text
ContextBundle (Crumblr-issued)
        ↓
external Trading Agent
        ↓
TradeProposal / NoTradeDecision
        ↓
Agent Gateway (Step B)
  auth · assignment · schema · expiry · idempotency · evidence
        ↓
platform-owned TradeIntent
        ↓
intent-time Risk Engine
        ↓
deterministic Policy Gate
        ↓
optional external Supervisor review
        ↓
platform seal of DecisionCapsule
        ↓
existing ExecutionOrchestrator
        ↓
fresh broker observation · reconciliation · FINAL Risk
        ↓
order_check · later gated order_send · post-fill reconciliation
```

**Unchanged, and protected from a rebuild** (guide §6, review 1.25's own
instruction not to reopen Phase 4):

- `TradeIntent` — remains the sole platform-owned trusted internal
  contract. Nothing external is ever treated as a `TradeIntent` directly;
  the Agent Gateway (Step B) is what will eventually construct one from a
  validated `TradeProposal`, and that mapping does not exist yet.
- `DecisionCapsule` — remains platform-sealed, immutable, unchanged.
- `ExecutionOrchestrator`, `OrderCheckMt5Gateway`, the append-only
  `execution_requests`/`execution_events` audit trail — remain the sole
  downstream execution authority, exactly as review 1.24 formally passed
  them.
- `evaluator.pretrade` — reframed *conceptually* as the deterministic
  Policy Gate the guide's rule 7 describes. No rename, no refactor. It
  already cannot be overruled by anything (the external Supervisor does
  not exist yet to test that against, but the existing veto-only,
  cannot-mutate-the-intent structure is exactly what rule 7 requires).
- An external Supervisor's verdict is a separate authority from
  `evaluator.pretrade`'s own `SupervisorDecision` — hence
  `ExternalSupervisorVerdict` (§5 below) being a distinct enum from the
  internal `SupervisorVerdict`, not a shared value space.

---

## 4. Current-vs-target gaps

The guide's §7 lists ten gaps between the current in-process strategy
model and this target architecture. Restated here only where this ADR
adds something the guide's own listing didn't already say precisely:

- **D-048** (`review/DEVIATIONS.md`) already tracks gap 9 —
  `CODE_COMMIT = "uncommitted-prototype"` — as a pre-Milestone-B migration
  item. Not duplicated here.
- Gap 10 (directional `TradeIntent` requires SL but leaves TP optional)
  is resolved **at the boundary, not internally**: `TradeProposal`
  requires both SL and TP at construction (§5 below); a proposal missing
  either never reaches the Agent Gateway, let alone becomes a
  `TradeIntent`. The internal `TradeIntent` contract is deliberately not
  tightened — guide §2.B and review 1.25 §2.B are both explicit that this
  would destabilize the approved internal contracts for no correctness
  gain, since every path that can construct a real `TradeIntent` today
  already supplies both.
- Gaps 1-3 (`trading_agent/registry.py`'s in-process strategy selection,
  `LiveDecisionOrchestrator` running that strategy itself, one global
  `trading_agent.strategy_id` in `PlatformConfig`) are Step B/C work —
  building the Agent Gateway and wiring an external Trader against it.
  Untouched by this ADR.
- Gaps 5-8 (no working agent/research service, no post-trade evaluator/
  Training loop, no point-in-time news contract, hidden EUR/USD/M5
  defaults) are Step B-D work, out of scope for Step A.

---

## 5. Contracts shipped in this pass

Five contracts named in guide §5, plus three added by the owner's
mandatory review tweaks (all recorded here so the reasoning survives
alongside the code, not only in a code comment):

| Contract | Purpose | Owner tweak(s) reflected |
|---|---|---|
| `AgentIdentity` | An external agent's authenticatable identity — role, runtime version, `service_identity` (a display name is not an identity), status, capability claims | — |
| `TradingAssignment` | The concrete, versioned scope one agent may propose within: market, timeframe, strategy artifact, validity window, rate limit, requested-risk band, required Supervisor policy | Supervisor policy version is **required**, not optional — an external Supervisor is required for the agent-driven MVP (O-007) |
| `PolicyHints` | A typed, closed replacement for what the guide describes as an open payload | **Tweak 4** — no `dict[str, Any]` crosses the agent boundary |
| `DecisionContextBundle` | Immutable references to what an agent was allowed to see: market snapshot, instrument spec, portfolio summary, session/data-quality state, optional policy hints, optional news reference. Carries a `content_hash` (`computed_field`, same pattern as `TradeIntent.decision_hash`) so a freshness claim cannot be forged | `news_snapshot_id` is a reference, never a fetch instruction — **tweak 6**, see the threat model |
| `TradeProposal` | A directional proposal: side, entry, SL, TP (both required), confidence, requested risk fraction, evidence references, expiry. Carries a `proposal_fingerprint` (`computed_field`, excludes `proposal_id`) | **Tweak 3** — canonical fingerprint for Step B idempotency, modeled directly on `ExecutionRequestStore.claim()`. **Tweak 5** — `requested_risk_fraction` documented explicitly as a request only. `evidence_refs` are references, never fetch instructions — **tweak 6** |
| `NoTradeDecision` | A durable, explicit record that an agent evaluated a window and chose not to propose | **Tweak 1** — never inferred from a `TradeProposal`'s absence, and structurally distinct from "no response arrived" (a future Gateway observation, not an agent decision) |
| `ProposalWithdrawal` | A durable, auditable record of a withdrawal attempt, `honoured` distinguishing success from too-late refusal | **Tweak 6** — valid only strictly before `ExecutionEventType.SUBMISSION_STARTED` (`domain/enums.py`, the exact marker Phase-4 already reserves for M5); every attempt is audited, never silently dropped |
| `SupervisorReview` | An external Supervisor's typed verdict on one proposal, veto-only, cannot mutate side/price/SL/TP/risk | **Tweak 2** — binds the exact platform-owned `TradeIntent` (`trade_intent_id`/`trade_intent_decision_hash`), plus optional references to the relevant `RiskDecision` and the Policy Gate's own decision, not only the external proposal's identity |

`ExternalSupervisorVerdict` (`APPROVE`/`VETO`/`UNKNOWN`) is a new,
deliberately separate enum from the internal `SupervisorVerdict`
(`APPROVE`/`VETO`/`HALT`) — timeout, error or an invalid response reads
as `UNKNOWN`, never as approval (guide §2.7, rule 7 above).

Every contract subclasses `crumblr.domain.models.Contract` directly,
reusing its `frozen=True`/`extra="forbid"`/Decimal/UTC guarantees rather
than inventing a second set — the same properties guide §6 specifically
praises about the existing internal contracts.

**Nothing outside `src/crumblr/agent_gateway/` imports this package.**
There is no Agent Gateway service, no auth, no mapping from a
`TradeProposal` to a `TradeIntent`. It is inert by the simple fact that
nothing calls it yet, not by a feature flag or a config toggle — the same
"structural, not conventional" discipline the rest of this codebase
already holds itself to.

---

## 6. Migration plan (guide §8, restated against this repository)

- **Step A — architecture contracts, still without execution (this ADR
  and this pass).** ADR + threat model + the eight contracts above +
  their structural tests. Nothing about `order_check` authorization or
  the `order_send` NO-GO changes. **Done.**
- **Step B — external Trading Agent in shadow.** Build an Agent Gateway
  (a new module under `src/crumblr/agent_gateway/`, alongside
  `contracts.py`) that validates a `TradeProposal` against its
  `TradingAssignment` and durably registers it — auth, schema, expiry,
  idempotency (the `proposal_fingerprint` binding from §5), evidence
  reference validation. Run one external Trading Agent against historical
  replay and live shadow data; keep the current in-process strategy
  running only as a comparison/twin. Prove idempotency, timeouts, agent
  failure, conflicting retries, invalid assignments, and restart
  recovery — the test matrix in §7 below is what this step is built and
  tested against.
- **Step C — Supervisor boundary.** Add the external Supervisor Agent as
  a separate authority alongside the (conceptually named, not renamed)
  Policy Gate. Prove supervisor failure is fail-closed and that it can
  neither overrule a Risk block nor mutate an intent.
- **Step D — research and training plane.** Artifact registry and
  reproducible Backtest Requests first; the evaluator that also labels
  rejections and `NO_TRADE`; Training producing only change proposals,
  never a direct change.
- **Step E — first agent-driven DEMO canary.** The already-authorized
  real-terminal `order_check` evidence (review 1.24 §8) stands on its own
  — it proves the broker boundary, not the agent architecture, and does
  not need repeating for this. A canary built on the current in-process
  strategy may only ever be described as a Crumblr execution proof. The
  first canary described as **agent-driven** must go through the Agent
  Gateway and still clear every existing `feedback.2.0` condition:
  `SubmissionGate`, ambiguous-outcome recovery, automatic flatten,
  post-fill reconciliation, and everything else already listed in
  `status.md`'s "What's needed next" table and review 1.24 §12.

---

## 7. Test matrix for Step B (planning-level — no Gateway exists yet to run these against)

| Area | Required scenario |
|---|---|
| Identity | A valid `AgentIdentity` is accepted; a suspended/retired identity is refused; an unknown `agent_id` is refused |
| Authorization / assignment scope | A proposal outside its assignment's symbol, timeframe or validity window is refused; a proposal exceeding `max_proposals_per_hour` is refused; a proposal outside `allowed_risk_fraction_min`/`_max` is refused before it ever reaches Risk |
| Idempotency | Same `proposal_id` + matching `proposal_fingerprint` = safe retry, no duplicate effect (mirrors `ExecutionRequestStore.claim()`); same `proposal_id` + a different fingerprint = fail-closed conflict, never a silent overwrite |
| Expiry | An expired `DecisionContextBundle` is refused; an expired `TradeProposal` is refused; an expired `SupervisorReview` reads as `UNKNOWN`, not as its stored verdict |
| Agent failure / timeout | A Supervisor timeout or error yields `UNKNOWN`, never approval; a crashed agent mid-proposal leaves no half-committed state (mirrors the append-only claim discipline `persistence/execution.py` already proves) |
| Replay | A replayed or duplicated wire message never causes a second submission |
| Withdrawal | A withdrawal before `SUBMISSION_STARTED` is honoured and audited; a withdrawal at or after `SUBMISSION_STARTED` is refused and still audited (`ProposalWithdrawal.honoured=False`) |
| Fail-closed | Any ambiguous, unparseable, or partially-invalid input is refused, never defaulted permissively |

---

## 8. Before/after-canary scope split (guide §3, review 1.25 §3)

**Required before the first agent-driven DEMO canary:** Agent Gateway,
`AgentIdentity`/`TradingAssignment`/`DecisionContextBundle`/`TradeProposal`/
`SupervisorReview` wired for real, one external Trading Agent, the required
external Supervisor Agent, shadow/replay evidence, agent/proposal
provenance — on top of everything Milestone A (Crumblr Execution Proof)
already requires.

**Explicitly deferred, not required for this MVP:** a Training Agent, a
Strategy Agent, a Backtest Agent service, a full artifact-management
product, a news platform, multi-market support, a multi-agent ensemble or
arbitrator, automatic strategy optimisation, LLM self-promotion of any
kind.

---

## 9. Consequences

- A future reviewer checking this ADR against the code can verify Step A
  is genuinely inert by grepping for imports of `crumblr.agent_gateway`
  outside `tests/` and the package itself — none should exist until Step
  B begins.
- The Phase-4 formal-PASS status (review 1.24) is unaffected — nothing
  this ADR authorizes touches `application/execution.py`,
  `mt5_gateway/execution.py`, or the append-only execution audit tables.
- Step B's Agent Gateway, when built, must reuse `TradeProposal
  .proposal_fingerprint` for its idempotent claim logic and
  `ExecutionEventType.SUBMISSION_STARTED` for its withdrawal-cutoff check
  — both already exist for exactly this reason and should not be
  reinvented.
