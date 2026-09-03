# Owner/reviewer work orders — constrained Pepperstone DEMO canary

**Date:** 2026-09-03  
**Status:** ACTIVE COORDINATION ORDER — **not** a submission authorization  
**Reviewed baseline before this document:** `main` = `e919fe8f30f5260752c17dac2a70b489bb17d72a`  
**Dev-2 branch reviewed:** `agent/contracts` = `d62722d661a182810a7c5e01129b3aaf4d1e981f`  
**Static Agent fork reviewed:** `DutchBugs/crumblr-static-agent-host` `main` = `f1e16b75e51bc24a1d9534635053d56150004534`

This document is an owner/reviewer coordination order. It is deliberately **not**
`feedback.1.29.md` and it is deliberately **not** `feedback.2.0.md`.

The product target is now explicit:

> Crumblr should be able to let one immutable external Static Trading Agent /
> strategy propose trades against the real Pepperstone **DEMO** account, while
> Crumblr remains the only authority for Risk, sizing, platform safety,
> execution, reconciliation and broker-side protection.

The route to that target is staged below. The first real-broker milestone is a
**one-shot, deliberately constrained DEMO canary**. Continuous autonomous DEMO
trading is a later promotion after the canary evidence has been reviewed.

---

## 0. Authority and invariants — unchanged

Owner Risk Policy v1 remains authoritative:

- max risk per trade: **2.0% of current equity**;
- max total open risk: **3.0%**;
- max daily loss: **4.0%**;
- max drawdown: **8.0%**;
- multiple positions are owner-permitted; no owner/business one-position rule;
- weekday overnight holding is permitted;
- Friday last entry is T-15 minutes from the canonical weekly close;
- Friday flat deadline is T-5 minutes;
- weekend holding is forbidden;
- HALT reset is human/operator-only; no automatic reset; no Supervisor reset.

Authority remains:

```text
Trading Agent proposes.
Core Risk constrains and sizes.
Platform Policy applies deterministic safety rules.
External Supervisor may APPROVE / VETO / UNKNOWN and is veto-only.
Execution service executes.
Reconciliation verifies broker truth.
```

External strategy semantics remain outside Crumblr Core. Pivot/FVG/MSS/ICT
meaning, strategy-specific state machines and strategy reason-code vocabularies
belong to the immutable StrategyArtifact / Trading Agent runtime.

### Still forbidden now

Until a later `review/feedback.2.0.md` explicitly says GO:

```text
real order_send                     = NO-GO
real automatic broker flatten       = NO-GO
ExecutionConfig.submission_enabled  = false
ExecutionConfig.feedback_2_0_approved = false
ExecutionConfig.flatten_submission_enabled = false
```

No developer is authorized by this work order to turn those values on, enable
MT5 AlgoTrading to make a test pass, or perform a real broker write.

---

## 1. Reviewer state assessment

### 1.1 Shared Core is materially stronger and the previous Dev-1 work order is complete

Dev 1 has now shipped the owner 2/3/4/8 policy, exact Core open-risk accounting,
multiple-position Risk semantics, the Friday/weekend session policy, restart
loss/drawdown recovery hardening and broker-side protective-stop verification.
The protective-stop check correctly escalates a missing/mismatched broker SL
for a Crumblr-attributed position.

This is enough to start the **execution activation engineering** phase. It is not
yet enough to submit a real order.

### 1.2 Hosted CI is not green yet, but the remaining current failure is understood

Current main CI run #106 collected 1,341 tests and finished with **1,339 passed,
2 failed**. PostgreSQL 17 client/server alignment, lint, format and mypy passed.
Both failures are the two `test_agent_decision_path.py` assertions that still
expect the pre-PL-006 HALT timing on `main`.

Dev 2 already fixed those exact assertions on `agent/contracts` commit
`d62722d`. Therefore the first coordination action is to bring Dev 2 current
with main, review/merge that branch and require a fresh fully-green hosted run.
Do not create a second Core fix for the same two tests.

### 1.3 Real MT5 mutation is still structurally absent

`OrderCheckMt5Gateway` can perform a real `order_check`, but its `order_send`,
`cancel_pending_orders` and `close_all_positions` methods still unconditionally
raise `ExecutionDisabledError`. The `SubmissionGate` exists and is tested, but
there is no real `SubmissionOrchestrator` / sending adapter.

This is intentional and remains a hard boundary while this work is built.

### 1.4 The existing pre-submission reconciliation still assumes a flat book

`ExecutionOrchestrator._process()` currently reconciles every new execution
candidate against `ExpectedState.flat(...)` before FINAL Risk. That is correct
for the very first one-shot canary, but it is **not** correct for continuous
trading after Crumblr already owns an open position: the second order would be
blocked as an unexpected position even if the whole portfolio is within the
owner's 3% open-risk budget.

Therefore:

- first DEMO canary: explicit flat-account / one-shot operational restriction is
  acceptable;
- continuous DEMO mode: must derive the pre-submission expected book from
  durable execution + flatten history, not `ExpectedState.flat()`.

Do not describe the present real-execution layer as multi-position capable until
that second point is fixed and tested.

### 1.5 Definite real-send event semantics are not implemented yet

`expected_state.py` intentionally maps the reserved real-broker events
`SUBMITTED`, `BROKER_ACK`, `FILLED` and `CLOSED` to `UNDETERMINED`. Today the only
submission outcome that can create determined attributed tickets is
`AMBIGUOUS_OUTCOME_RESOLVED`.

A real sender must therefore define and test the durable meaning of a definite
broker success/rejection/partial fill and update expected-exposure derivation.
Do **not** fake every successful order as an "ambiguous outcome resolved" just
because that path already understands tickets.

### 1.6 First canary must be MARKET-only

The current ambiguous-outcome recovery tracks `magic` through open positions,
not pending MT5 orders. LIMIT/STOP orders can therefore become broker-side
pending orders that the current recovery layer cannot honestly attribute.

First DEMO canary is **MARKET only**. Pending-order support is a later slice.

### 1.7 Exact DEMO account identity needs a stronger activation pin

The shipped paper config verifies the Pepperstone demo server, currency,
leverage and `is_demo`, but `expected_login` remains `null`. A different demo
account on the same server with matching currency/leverage is therefore not an
adequate first-canary target identity.

Before real submission, add an owner-approved exact **account reference** pin
without storing MT5 credentials in git. Prefer the existing account-ref /
`AccountState.login_hash` style or an equivalent non-credential identifier.
Never put the raw password in config, logs or review documents. Do not require
this work order to expose the raw account number either.

The SubmissionGate / reconciliation path must fail closed when the observed
account reference is not the exact approved canary account.

### 1.8 Dev 2's correction slice is good, but not merged and not the end of the agent track

The reviewed branch correctly:

- uses Core `assess_open_risk()` rather than a second agent Risk definition;
- distinguishes exact open risk `0` from unknown `None`;
- rejects a proposal whose `strategy_artifact_hash` does not equal the assigned
  immutable artifact hash;
- keeps external reason codes opaque/structurally bounded;
- has the corrected PL-006 test expectations.

It is currently behind the latest Core item-9 commits and must sync before PR.

The external Supervisor is still **not wired** into the agent decision path.
`supervisor_review.py` contains safe review-evaluation logic, but no real
external Supervisor transport/service is in the execution chain yet.

AG-012 also remains open before real agent-driven submission: real-submission
candidates must converge on one serialized/shared final Risk-to-side-effect
authority, not independent per-process budgets.

### 1.9 The current Static Agent fork cannot yet make a genuine HEALTHY neutral-context decision

The fork's current `TraderContext 1.0` still requires a `features` block whose
producer is `CRUMBLR_FROZEN_STRATEGY_CORE`, and the HEALTHY path consumes a
precomputed strategy-specific observation/reason vocabulary. That is the old
interface corrected by feedback 1.28.

A genuine HEALTHY Static Agent must instead receive neutral Crumblr facts
(current quote, confirmed closed M5 bars, instrument facts, provenance/platform
state) and compute Pivot-2.2 **inside the Agent process**.

Do not solve this by putting Pivot/FVG/MSS feature computation back into Core.

### 1.10 PAPER_LITE is merged, but now contains transitional glue that should disappear

PAPER_LITE was correctly built fail-closed while Shared Core/Agent seams were in
flight. Now that Dev 1 has shipped the shared Friday/weekend calendar and Dev 2
has the exact-open-risk decision path ready, the Lite track should remove its
temporary parallel semantics after the Dev-2 merge:

- no second Friday/session calendar;
- no PAPER_LITE hardcoded copy of owner risk semantics where shared config/Core
  can be used;
- no "second directional entry unavailable" guard once the shared agent path
  consumes exact Core open risk;
- use one generic external-Agent response contract, not a paper-only trading
  decision protocol.

---

## 2. Coordination order — execute in this sequence

## Phase 0 — converge `main` and get hosted CI green

### Dev 2

1. Merge/rebase the latest `main` (including item 9) into `agent/contracts`.
2. Preserve the already-reviewed D2.2/Core-risk, 0-vs-None and
   `STRATEGY_ARTIFACT_MISMATCH` changes.
3. Run full ruff/format/mypy/test gate on the dedicated Dev-2 database.
4. Push the branch and open a PR to `main`.
5. Do not add Supervisor or new Static Agent work to that PR; keep the
   convergence PR reviewable.

### Dev 1

- Do not duplicate Dev-2's two PL-006 test fixes on Core.
- Review the Dev-2 merge only for cross-track Core invariants / item-9 conflicts.
- Do not start real `order_send` wiring on stale main; branch the new execution
  work only after the Dev-2 convergence merge.

### Reviewer acceptance

Phase 0 is complete only when a **new main hosted CI run is fully green** on:

- Linux lint + format + strict mypy + full tests;
- Windows MT5-host test job;
- secret scan;
- database reachable assertion;
- backup/restore test actually ran and passed.

---

## Phase A — genuine external Static Agent product proof, still zero broker writes

This phase closes the strategy-neutrality/product gap before real execution is
made technically possible.

### Static Agent developer / fork owner

Create a new neutral-context contract version; do not silently change the
meaning of the existing TraderContext 1.0.

Required HEALTHY path:

```text
AgentMarketContextV1 / compatible neutral contract
  -> validate immutable assignment/artifact binding
  -> compute Pivot-2.2 state inside the Agent runtime
  -> NO_TRADE or directional proposal
  -> return Crumblr TradeProposal / NoTradeDecision semantics
```

Requirements:

- Pivot/FVG/MSS/sweep/setup computation lives in the Agent plane.
- Current quote and confirmed closed M5 bars are source data, not a precomputed
  setup from Core.
- Keep the frozen strategy artifact/source hash immutable. Runtime adapter
  version and strategy artifact version are separate concepts.
- If strategy logic changes, create a new immutable StrategyArtifact/version and
  require human promotion; do not silently mutate the assigned artifact.
- The Agent never receives MT5 credentials or Crumblr DB credentials.
- The Agent never names final lot size and never executes.
- Return `agent_id`, `assignment_id`, `context_hash` and exact
  `strategy_artifact_hash` binding so Gateway can verify them.
- HEALTHY no-signal must return honest NO_TRADE; do not manufacture a signal for
  an integration test.
- Add tests for both a genuine HEALTHY NO_TRADE and a genuine HEALTHY directional
  proposal produced from neutral bars.

For non-local deployment, F-064 applies: encrypted authenticated transport is
mandatory. Plain HTTP is acceptable only on an explicitly local/controlled
boundary. Never expose the bearer token in URL/query/log output.

### Dev 2

After Phase-0 merge, start a separate Agent slice.

1. Move the reusable neutral external-Agent HTTP response envelope/adapter into
   the Agent Gateway layer. PAPER_LITE must not be the owner of the production
   wire contract.
2. Keep `TradeProposal` / `NoTradeDecision` as the authoritative decision
   contracts and Gateway as the only producer of platform `TradeIntent`.
3. Replace the unhealthy-market-only legacy transport as the final proof path
   with the new neutral-context Static Agent contract. Legacy smoke support may
   remain for compatibility, but must not be mistaken for HEALTHY acceptance.
4. Prove F-066 with two agents/reason vocabularies through the same unchanged
   Core path.

### Dev 3

After Dev 2's neutral Agent seam is available:

1. Remove the temporary PAPER_LITE session/calendar duplicate and use Shared
   Core D1.5.
2. Remove the temporary exact-open-risk/single-position guard and let the shared
   Agent decision path use Core `assess_open_risk()`.
3. Replace paper-only wire-envelope ownership with Dev 2's generic Agent adapter.
4. Run the real product proof:

```text
real Pepperstone read-only EUR/USD market feed
 -> neutral Agent context
 -> genuine HEALTHY Static Agent
 -> NO_TRADE or TradeProposal
 -> Gateway
 -> Core Risk
 -> platform Policy
 -> explicit PAPER_LITE Supervisor skip audit fact
 -> SimulatedBroker
 -> durable paper position/P&L/SL/TP/audit
```

For a final **directional** paper proof, wait for a strategy-valid signal. Do not
force the Pivot strategy or alter the reason vocabulary simply to produce a
fill. If the observation window only yields honest NO_TRADE, record that as a
valid HEALTHY-agent proof and continue observing later for the directional proof.

Zero real MT5 mutation calls are allowed in Phase A.

---

## Phase B — build real DEMO execution capability, but keep it unactivatable

### Dev 1 — new execution slice

Build this against the post-Phase-0 main. The entire slice must remain safe to
merge while all activation flags are false.

### B1. Keep the current non-sending adapter permanently obvious

Prefer leaving `OrderCheckMt5Gateway` as an order-check-only adapter. Add a
separate, explicitly named DEMO mutation capability/adapter rather than turning
an old non-sending type into a sending type invisibly.

The real mutation adapter must refuse any non-DEMO account/environment even if a
caller is wrong.

### B2. Implement exactly one real submission side effect

Required order:

```text
existing approved capsule
 -> fresh coherent broker state
 -> FINAL fixed-volume Core Risk
 -> order_check
 -> submission-time freshness/safety check
 -> SubmissionGate
 -> acquire one shared execution/Risk authority (Phase C / AG-012)
 -> atomically claim one-shot canary permit when canary mode is used
 -> durable SUBMISSION_STARTED
 -> exactly one broker order_send call
 -> durable normalized outcome
 -> immediate broker-state capture
 -> expected-state derivation
 -> reconciliation
 -> protective-stop verification
```

No automatic retry after a timeout/crash/lost response. Once
`SUBMISSION_STARTED` exists, uncertainty goes to broker-state recovery.

The final side-effect clock boundary must be fresh. Do not use a stale `now` from
before a slow `order_check` to authorize a later broker write. If the decision,
quote, session window or safety state is no longer fresh after `order_check`,
refuse the send rather than "catching up" by blind retry.

### B3. Normalize definite broker outcomes honestly

Implement and test durable semantics for real:

- rejection;
- accepted/submitted;
- broker acknowledgement;
- full fill;
- partial fill;
- transport exception/timeout/ambiguous response.

Update `derive_expected_exposure()` so any newly-emitted real event has an honest
exposure meaning. Reserved `SUBMITTED/BROKER_ACK/FILLED/CLOSED` may no longer
remain permanently `UNDETERMINED` once production code starts emitting them.

Never fabricate `AMBIGUOUS_OUTCOME_RESOLVED` for an ordinary definite success.

### B4. Harden ambiguous recovery before it can be real

Current magic lookup treats `len(matches) > 0` as "submitted". Before real
submission, explicitly handle:

```text
0 matching positions -> no observed position; record the safe determination
1 matching position  -> attributed candidate; continue reconciliation
>1 matching positions -> integrity ambiguity; fail closed / HALT, never silently accept
```

The first canary is MARKET-only, so no pending-order ambiguity is permitted.

### B5. Implement real per-ticket close / flatten before enabling entries

A system that can open but cannot reliably close is not canary-ready.

Use the existing `FlattenPlan` / `FlattenInstruction` ticket, side and exact
broker-reported volume. A close must target the existing hedging-account ticket
and must not accidentally open an opposite hedge/new position.

Implement:

- policy-driven Friday flatten;
- operator-requested flatten;
- retry/recovery semantics for an ambiguous close;
- confirmation from fresh broker state that the ticket is gone;
- durable outcome/audit.

`flatten_submission_enabled` remains independently false until feedback2.0.

Protective-stop failure must still allow the safe remediation path to close the
position; it must never auto-reset the HALT.

### B6. Remove the flat-book assumption before continuous DEMO promotion

For the **first canary only**, starting from a confirmed flat account is an
explicit operational restriction.

Before any later continuous DEMO mode, replace `_process()`'s unconditional
`ExpectedState.flat()` pre-submission expectation with the Core-owned durable
expected exposure derived from execution + flatten history. Then prove:

```text
existing authorized position risk 1.0%
+ new requested risk 2.0%
= 3.0% -> may pass budget leg

existing authorized position risk 1.1%
+ new requested risk 2.0%
= 3.1% -> BLOCK OPEN_RISK_LIMIT
```

Multiple small positions below 3% must not be refused merely because a position
already exists.

### B7. Exact account binding

Add an owner-approved exact account-reference pin for real DEMO submission.
Do not store MT5 password or other credential material in git.

Submission must require simultaneously:

- exact approved account reference;
- exact expected Pepperstone DEMO server;
- demo account flag;
- expected currency/leverage;
- current reconciliation MATCHED.

A different demo account is a refusal even if all other fields look plausible.

### B8. One-shot DEMO canary permit

Build a durable, operator-issued one-shot permit. This is an **operational test
restriction**, not a new owner trading rule.

At minimum bind the permit to:

- exact account reference + server;
- `EUR/USD`;
- MARKET entry only;
- exact external `agent_id` / `assignment_id` / StrategyArtifact hash for an
  agent-driven canary;
- a maximum requested-risk fraction chosen by the owner;
- one submission attempt;
- a short validity window;
- operator identity, reason and creation timestamp;
- the `order_request_id` that consumed it.

Permit claim must be atomic and durable. A consumed/expired permit can never be
reused or auto-reset; another attempt needs a new explicit operator permit.

**Reviewer recommendation for the first canary cap: 0.25% of equity or lower.**
This number is a recommendation, **not owner-approved policy yet**. Do not
hardcode or activate it until the owner confirms the canary envelope before
feedback2.0.

### Dev-1 Phase-B definition of done

- all real mutation code exists behind structurally closed gates;
- no shipped config enables it;
- tests use fake/mock MT5 only;
- no real broker write performed;
- order-check-only adapter remains non-sending;
- MARKET send, definite/ambiguous result handling and ticket-close paths have
  unit/integration coverage;
- restart after `SUBMISSION_STARTED` never causes a blind resubmit;
- restart after flatten commitment never causes a duplicate/reverse close;
- one-shot permit is proven atomic/idempotent.

---

## Phase C — external Supervisor + one serialized final Risk authority

### Dev 2 — External Supervisor

The existing platform Policy and the external Supervisor are two different veto
layers. **Do not overwrite or relabel the platform Policy decision as the
external Supervisor approval.**

Required agent-driven route:

```text
TradeProposal
 -> Gateway identity/assignment/artifact/context checks
 -> TradeIntent
 -> Core Risk
 -> deterministic platform Policy
 -> external Supervisor request
 -> APPROVE / VETO / UNKNOWN
 -> durable review binding
 -> execution candidate
```

External Supervisor must receive only read-only proposal/provenance/Risk/platform
facts and may not size, mutate, execute, waive Risk, reset HALT or promote a
strategy.

Timeout, malformed response, unavailable service, expired review or any binding
mismatch = `UNKNOWN` and no execution authorization.

Bind and persist at least proposal ID, TradeIntent ID + decision hash, immutable
StrategyArtifact provenance, review expiry and verdict/reasons. Execution must
be able to prove that an **external-agent** candidate has a genuinely bound,
unexpired external `APPROVE`; a platform Policy `APPROVE` alone is not enough.

If no Supervisor service exists yet, a deterministic reference Supervisor in a
separate process is acceptable for the first DEMO canary, provided it has zero
MT5/DB credentials and the exact same APPROVE/VETO/UNKNOWN authority limits.

### Dev 1 + Dev 2 — close AG-012 at the execution convergence point

Do not build an Agent-only risk lock. Every capsule source ultimately converges
on the same execution-time FINAL Risk authority.

Before any real side effect, enforce **at most one final-Risk-to-broker-side-effect
critical section per account**. A database-backed lease/advisory lock or another
durable single-authority mechanism is acceptable; the required property matters
more than the primitive chosen.

The critical section must prevent two workers/processes from both seeing the
same pre-trade portfolio budget and both submitting before either one's effect is
visible.

For the first one-shot canary, the one-shot permit is an additional safety
layer, not a substitute for closing AG-012.

---

## Phase D — pre-feedback2.0 dry drill

Still **zero real broker writes**.

Run the exact intended agent-driven DEMO path up to the side-effect boundary:

```text
real Pepperstone read-only data
 -> genuine HEALTHY Static Agent
 -> Gateway
 -> Core Risk
 -> platform Policy
 -> real external Supervisor
 -> execution preflight
 -> FINAL Risk
 -> real order_check
 -> SubmissionGate
 -> STOP before order_send
```

Evidence bundle must show:

1. exact approved DEMO account reference/server;
2. owner 2/3/4/8 policy and exact config hash;
3. Friday/weekend policy active;
4. Safety RUNNING, reconciliation MATCHED;
5. real GOOD/fresh quote and current instrument spec pin;
6. immutable Agent identity/assignment/StrategyArtifact binding;
7. genuine external Supervisor binding;
8. AG-012/single execution authority active;
9. one-shot canary permit logic tested but not consumed by a real send;
10. order_check accepted for the exact would-send request;
11. hosted main CI fully green;
12. real sender and real ticket-close code covered by fake-MT5 tests;
13. `order_send` remains unreachable because feedback2.0 / activation flags are still closed.

Do not enable AlgoTrading merely to create green evidence. If the terminal toggle
is off, the dry drill should report that gate leg honestly.

---

## Phase E — formal `feedback.2.0` and owner activation

Only after Phases 0-D are complete should the reviewer write
`review/feedback.2.0.md`.

That review answers the binary question:

> Is Crumblr ready to make `order_send` reachable for one deliberately
> constrained Pepperstone DEMO canary under the owner-approved risk policy?

If and only if feedback2.0 is GO, the owner then confirms:

- exact approved risk/config hash;
- exact approved DEMO account reference;
- exact immutable Static Agent assignment/artifact;
- first-canary maximum requested-risk fraction;
- one-shot permit expiry/window;
- whether/when to enable MT5 AlgoTrading.

Only after those explicit acts may the relevant execution/flatten activation
flags be set true for the canary. Merging code must never enable them by itself.

---

## Phase F — first agent-driven real DEMO canary

The first canary is deliberately narrower than normal owner trading policy:

```text
DEMO only
EUR/USD only
MARKET only
one exact Static Agent assignment/artifact
real external Supervisor required
one submission attempt only
owner-confirmed canary risk cap <= owner 2% hard limit
account confirmed flat before start
no pending orders
```

Execution evidence required after the attempt:

- `SUBMISSION_STARTED` exists before the broker side effect;
- exactly one MT5 `order_send` invocation for the consumed request;
- normalized broker response persisted;
- broker position/deal attribution agrees with the durable request identity;
- executed volume does not exceed FINAL Risk authorization;
- broker-side SL exists and exactly matches the intended stop;
- post-fill reconciliation is MATCHED;
- no duplicate position after restart/repoll;
- dashboard/audit can explain agent -> assignment/artifact -> proposal -> Risk ->
  platform Policy -> external Supervisor -> ApprovedOrder -> broker outcome.

After the evidence is captured, perform one **operator-approved controlled
flatten** using the real close path and verify the account returns to confirmed
flat. This deliberately proves the exit/remediation path in the same canary.

Any timeout, unknown broker outcome, account mismatch, missing/mismatched SL,
Supervisor UNKNOWN/VETO, Risk refusal, stale quote, reconciliation uncertainty
or canary-permit conflict fails closed. No automatic HALT reset and no automatic
second canary attempt.

After the first canary the system returns to a disarmed state. Continuous DEMO
autonomy is a later promotion/review, not implied by one successful canary.

---

## 3. Track ownership summary

### Dev 1 — Core / Execution

**NEXT:** real DEMO mutation adapter + real per-ticket close + honest real event
semantics + exact account pin + one-shot canary permit + shared serialized final
Risk/side-effect seam with Dev 2. Build only; keep disabled.

**DO NOT:** change external strategy semantics, build External Supervisor policy,
or activate broker writes.

### Dev 2 — External Agent / Supervisor

**FIRST:** converge/PR the already-finished `agent/contracts` correction slice so
main CI can go green.

**THEN:** generic neutral Agent wire contract + genuine Static Agent HEALTHY path
coordination + real External Supervisor wiring + AG-012 closure with Dev 1.

**DO NOT:** duplicate Core Risk, put Pivot semantics in Core, or create a second
execution stack.

### Dev 3 — PAPER_LITE / integration proof

**NEXT AFTER DEV-2 MERGE:** remove transitional duplicate calendar/risk guards,
consume shared seams, and produce the genuine HEALTHY Static Agent + real
read-only Pepperstone + simulated-fill proof.

**DO NOT:** add real MT5 mutation capability to PAPER_LITE or transfer its
Supervisor-skip privilege to DEMO execution.

### Static Agent developer

**NEXT:** StrategyArtifact-owned neutral-context runtime. The Agent computes
Pivot-2.2 itself and returns a Crumblr proposal/NO_TRADE with immutable artifact
binding. No MT5, no DB, no sizing, no execution.

---

## 4. Reviewer merge/activation checkpoints

A change is not ready merely because its local tests pass.

### Merge checkpoint

Before every major merge:

- branch synced to latest main;
- full local quality gate green;
- no unexpected changes outside track ownership;
- hosted CI green after merge;
- all new durable contracts/events are restart/idempotency tested;
- no credentials or account secrets added.

### `feedback.2.0` checkpoint

Do not request feedback2.0 until all of the following are true:

- genuine HEALTHY Static Agent path exists;
- external Supervisor is real and fail-closed;
- real sender exists but is disabled;
- real ticket-close/flatten exists but is disabled;
- exact account pin exists;
- first canary is MARKET-only and one-shot;
- AG-012/single final Risk authority is closed;
- definite and ambiguous execution outcomes are durably understood;
- post-fill protective SL verification can drive remediation;
- current main hosted CI is fully green;
- dry drill reaches the closed side-effect boundary against the real DEMO
  terminal without a broker write.

Until then:

```text
order_send = NO-GO
feedback_2_0_approved = false
```
