# Domain contract package — for reviewer/human approval (M0)

Requested by review 1.14 §13, repeated by review 1.15 §11 and review 1.16
§11: "Provide the actual current contract definitions or a generated
contract package," checked specifically for immutability, extra-field
rejection, Decimal/time semantics, ownership boundaries, execution
permissions, risk/supervisor separation, and which fields may or may not be
agent-controlled. "Do not mark this approved merely because tests exist."

This document describes what the code actually does, as of commit
`6bdb5b1` (2026-08-27) — it is a description, not a specification;
`build.md` remains the specification, and any gap between the two belongs
in `review/DEVIATIONS.md`, not here. Line numbers below refer to
`domain/models.py` at that commit and will drift; the class names will not.

**What changed since the previous package (commit `f67f341`, 2026-08-25).**
That package predated Phase 4's execution engineering and described
`ApprovedOrder`/`ExecutionResult` as "not yet constructed anywhere." That
was already imprecise about the pre-existing replay/paper simulator (§2),
and Phase 4 has since added a second, real (non-sending) producer of
`ApprovedOrder` against an actual MT5 terminal. §2 and §4 are the sections
that changed materially; §1, §3, §5 changed only in wording; §6/§7 are
updated to match.

**Post-approval update, not yet re-reviewed.** Review 1.24 §7 approved
this package at the reviewer/technical level against commit `6bdb5b1`.
Since then, `risk/submission_gate.py::evaluate_submission_gate()` (F-049)
became a real function instead of an always-refusing stub — §4 updated to
describe it accurately. This is routine implementation progress under
review 1.25 §9's changed cadence (no formal review requested for this
alone); it will be part of the eventual `feedback.2.0` readiness bundle.

---

## 1. Cross-cutting guarantees

Every contract in this package inherits from `Contract`
(`domain/models.py:46-72`), a Pydantic `BaseModel` with:

```python
model_config = ConfigDict(
    frozen=True,
    extra="forbid",
    str_strip_whitespace=True,
    validate_default=True,
)
```

- **Immutability.** `frozen=True` — every field is read-only after
  construction; there is no setter, no `object.__setattr__` escape hatch in
  application code, and Pydantic raises on an attempted mutation. A
  "changed" decision is a new object, never an edited one. This is why
  Phase 4's execution-time (FINAL) `RiskDecision` (§5) is never written
  into the sealed `DecisionCapsule` that already holds the intent-time one
  — it lives in a separate, append-only record instead (§4).
- **Extra-field rejection.** `extra="forbid"` — an unrecognised key raises
  at construction/deserialization rather than being silently dropped or
  absorbed as a default. A renamed or removed field is a loud failure, not
  a quiet one. The one designed exception: `_discard_computed_fields`
  (`domain/models.py:56-72`) strips `@computed_field` properties (see §3)
  from *incoming* data before validation, because those are round-tripped
  out on `model_dump()` but must be recomputed on load, not accepted as
  input — accepting them as input would let a tampered digest overwrite the
  one the loader recomputes.
- **Decimal semantics.** Every monetary or size-bearing field uses
  `ExactDecimal`/`Price`/`Volume`/`RiskFraction` (`domain/money.py`), each a
  `Decimal` with a `BeforeValidator` that raises on any `float` input —
  `Decimal(1.1)`'s binary rounding error can never enter a contract, even
  by accident. `Price`/`Volume` are further constrained `> 0`;
  `RiskFraction` to `(0, 1]`. The one genuine `float` field left anywhere in
  this package is `TradeIntent.confidence` — `domain/hashing.py::fingerprint()`
  deliberately raises (`TypeError: float cannot be fingerprinted
  deterministically; use Decimal`) rather than let a raw float enter a
  digest, so anything that fingerprints a `TradeIntent` (directly, or via
  its own `decision_hash`, §3) must go through `repr()` first, exactly as
  `decision_hash` already does.
- **Time semantics.** Every timestamp field uses `UtcDatetime`
  (`domain/timeutils.py`), which rejects naive datetimes outright and
  normalises any aware datetime to UTC. There is no field anywhere in this
  package that can hold local time, broker-chart time, or an ambiguous
  instant.
- **String bounds.** Free-text fields (`notes`, `summary`, `root_cause`,
  `retcode_comment`, `error_detail`, …) carry explicit `max_length`
  constraints and `str_strip_whitespace=True` — no unbounded text can enter
  a persisted contract.

These properties are structural, not convention: a contract that violated
one would fail to construct. `tests/unit/test_control_plane_contracts.py`
and `tests/unit/test_numeric_boundaries.py` assert them directly, but the
guarantee itself is the Pydantic `model_config` and the type aliases, which
is what makes it hold even for code paths no test happens to exercise.

---

## 2. Ownership boundaries — who constructs what

build.md §6's pipeline (`Trading Agent → Risk Engine → Supervisor →
Execution Service → Reconciliation`) is mirrored in which package
constructs which contract. No contract in this list is constructed by more
than one *kind* of stage in normal operation; where a later stage reads an
earlier stage's contract, it reads it as data, never mutates it (mutation
is structurally impossible — see §1). Two contracts below now have two
independent producers each, described in full rather than collapsed into
one row, because the two paths reach genuinely different broker adapters
(§4).

| Contract | Constructed by | Read by |
|---|---|---|
| `MarketTick` / `MarketBar` / `MarketSnapshot` / `Bar` | `market_data/pipeline.py`, `mt5_gateway/readonly.py` | Trading Agent, persistence |
| `InstrumentSpec` | `mt5_gateway/readonly.py` (from a live terminal), `market_data/synthetic.py` (replay) | Risk Engine (sizing), Trading Agent, reconciliation, `ExecutionOrchestrator` |
| `TradeIntent` | Trading Agent only | Risk Engine |
| `RiskDecision` | Risk Engine's `evaluate()` (intent-time, once per intent) **and**, independently, `evaluate()`'s Phase-4 sibling `revalidate_fixed_volume_at_execution_time()` (execution-time / "FINAL", `risk/policies.py`, ADR-001) — the same function, reused verbatim for its whole checklist, called a second time against freshly observed inputs immediately before a real `order_check` | Supervisor, `DecisionCapsule` (intent-time only — see below), `ExecutionOrchestrator`, `ApprovedOrder.intent_risk_decision_id`/`.final_risk_decision_id` |
| `SupervisorDecision` | Supervisor (`evaluator/pretrade.py`) only | `ApprovedOrder.supervisor_decision_id`, `DecisionCapsule` |
| `ApprovedOrder` | **Two independent producers, never the same code path.** (1) `application/execution.py::ExecutionOrchestrator._process()` — the real, non-sending Phase 4 preflight; only after a fresh execution-time `RiskDecision` (above) has itself returned `PASS`. (2) `application/orchestration.py::ReplayOrchestrator._execute()` — the pre-existing replay/paper simulator; runs immediately after the Supervisor's `APPROVE`, with no execution-time revalidation step (`final_risk_decision_id` stays `None` on this path, by design — see §3) | (1) `OrderCheckMt5Gateway.order_check` (`mt5_gateway/execution.py`) — a real, non-mutating MT5 dry run; nothing past that. (2) `SimulatedBroker.order_check`/`.order_send` (`mt5_gateway/simulated.py`) — an in-memory fill simulator, never a real MT5 terminal |
| `OrderCheckCompleted` | `OrderCheckMt5Gateway.order_check` (real MT5 dry run) **or** `SimulatedBroker.order_check` (in-memory) — never both for the same `ApprovedOrder`, since the two producers above never share a request | (1) `ExecutionOrchestrator`, as an `ExecutionEventType.ORDER_CHECKED`/`ORDER_CHECK_REJECTED` execution-event payload. (2) `ReplayOrchestrator`, as an `OrderCheckCompleted` journal event |
| `ExecutionResult` | `SimulatedBroker.order_send` **only** (`mt5_gateway/simulated.py`) — the replay/paper simulator. No code path that can reach a real MT5 terminal ever constructs one: `OrderCheckMt5Gateway.order_send` (the only execution-capable *real* adapter) always raises `ExecutionDisabledError` before returning anything, and `ReadOnlyMt5Gateway.order_send` always raises `ReadOnlyViolationError` — see §4 | Reconciliation (replay/paper only — see `ReplayOrchestrator._reconciliation_status()`), `DecisionCapsule.execution_result` (replay/paper only) |
| `AccountState` / `PositionState` / `PendingOrderState` | `mt5_gateway/readonly.py::ReadOnlyMt5Gateway` (live reads, one request at a time) | Account guard, Risk Engine, `ExecutionOrchestrator`'s FINAL Risk call |
| `BrokerAccountSnapshot` / `BrokerPositionSnapshot` / `BrokerPendingOrderSnapshot` | `application/broker_state.py::capture_broker_state` (F-047) | `persistence/broker_state.py::BrokerStateStore`, `application/reconciliation.py`, `ExecutionOrchestrator` (via the same `capture_broker_state()` call — review 1.22/1.23 F-058, below) |
| `Incident` | Not yet constructed anywhere — the register has a contract but no producer (`status.md` §3 Data checklist: "the contract exists; no register") | Supervisor's `IncidentStatus` input (currently hard-coded `CLEAR` in replay — see `orchestration.py::_incident_status`) |
| `DecisionCapsule` | `application/recording.py` (assembled from the intent-time contracts at the end of one decision window) | Journal, audit, replay verification, `ExecutionOrchestrator` (reads sealed capsules to find eligible work; never mutates them — see below) |

**`ApprovedOrder`/`ExecutionResult` are no longer "unbuilt".** The previous
package (`f67f341`) described them that way; that was already imprecise —
the replay/paper simulator (`SimulatedBroker`, wired only by
`scripts/run_replay.py::ReplayOrchestrator`, never by anything live) has
constructed both since before that snapshot, producing real simulated
fills against synthetic/replayed price data. What Phase 4 (this commit)
adds is the *second* producer of `ApprovedOrder`: `ExecutionOrchestrator`,
which reaches a real MT5 terminal for exactly one live capability
(`order_check`, a non-mutating broker-side dry run) and never for
`order_send`. `ExecutionResult` still has exactly one producer anywhere in
the codebase — the in-memory simulator — and that remains true after Phase
4, because nothing on the real-MT5 path ever gets far enough to construct
one (§4).

**The execution-time (FINAL) `RiskDecision` is never written into the
`DecisionCapsule`.** `DecisionCapsule` is sealed once, at the end of the
*intent-time* decision window (`application/recording.py`), and §1's
`frozen=True` makes it structurally impossible to add anything to it
afterward. FINAL Risk's own `RiskDecision` — and, on `PASS`, the
`ApprovedOrder` it authorized — instead live in a separate, append-only
execution audit trail: `ExecutionEventType.FINAL_RISK_PASSED`/
`FINAL_RISK_BLOCKED` events in `persistence/execution.py::ExecutionEventStore`,
carrying the complete serialized `RiskDecision` in their payload (review
1.22 F-057; ADR-001 constraint 4 was corrected to describe this design
rather than "the capsule records both"). This is not a `domain/models.py`
contract — it is a SQLAlchemy-backed, append-only table
(`execution_events`, alongside the immutable `execution_requests`) plus two
small dataclasses (`ClaimResult`, `ExecutionEventRecord`) — described fully
in §4, because it is the mechanism the execution-permission claim actually
rests on.

**`Incident` is still specified but unbuilt** — it exists in the type
system ahead of the register that will populate it; build.md §9 defines
its shape, and having the type checked and reviewed now means the register
is built against an already-approved shape rather than inventing one under
schedule pressure.

---

## 3. Fields that are, and are not, agent-controlled

The Trading Agent constructs exactly one contract: `TradeIntent`. Its field
list is the complete answer to "what can the agent decide":

**Agent-controlled** (`TradeIntent`, `domain/models.py:316-424`): `symbol`,
`side`, `entry_type`, `reference_price`, `stop_loss_price`,
`take_profit_price`, `confidence`, `reason_codes`,
`requested_risk_fraction`, `expires_at_utc`, `strategy_id`/
`strategy_version`/`model_version`, `feature_snapshot_id`.

**Deliberately absent from `TradeIntent`** — and therefore structurally
uncontrollable by any Trading Agent, including a future AI/LLM-assisted
one (review 1.15 §8's explicit boundary):

- **Lot size / volume.** The agent requests a *risk fraction*
  (`requested_risk_fraction`, bounded to `(0, 1]`); only `RiskDecision`
  (Risk Engine output) carries `approved_volume`, computed from account
  state and the broker's own `InstrumentSpec`. An agent cannot ask for "0.5
  lots" — it can only ask for "up to this fraction of equity," and even
  that request can be refused or reduced downstream. On the real Phase-4
  path specifically, a second, independent `RiskDecision` (FINAL Risk) must
  also approve — and `revalidate_fixed_volume_at_execution_time()` is
  contractually forbidden from *resizing*: its only two outcomes are PASS
  with the original `approved_volume` unchanged, or BLOCK/HALT. There is no
  path, agent-controlled or otherwise, by which a volume different from
  what the first `RiskDecision` approved reaches a real `ApprovedOrder`.
- **Final approval.** Only `RiskDecision.verdict` and
  `SupervisorDecision.verdict` exist to approve or refuse; `TradeIntent` has
  no verdict field of its own to set.
- **Order submission.** `ApprovedOrder` is never constructed from a
  `TradeIntent` directly — both `intent_risk_decision_id` and
  `supervisor_decision_id` are required, non-defaulted fields, so
  Pydantic itself refuses an `ApprovedOrder` built without both a
  `RiskDecision` and a `SupervisorDecision` already on record. On the real
  Phase-4 path, `final_risk_decision_id` is populated too — it stays
  optional on the contract only because the pre-existing replay/paper
  simulator path (§2) has no execution-time revalidation step and must
  keep constructing valid orders without one; `ExecutionOrchestrator`
  always sets it on the path that can reach a real terminal, and
  `OrderCheckMt5Gateway.order_check()` itself now refuses (`Missing
  FinalRiskDecisionError`, review 1.23 F-061) any order presented without
  it — fail-closed at the one place a real broker call actually happens,
  not merely trusted to every future caller supplying it correctly.
- **Execution/broker/credential access.** No contract the agent constructs
  carries a broker connection, a credential, or a method that reaches one —
  `TradeIntent` is a plain data record with no behaviour beyond
  `is_expired()` and its own validators.
- **HALT / risk-policy state.** Nothing in `TradeIntent` can set
  `KillSwitchState`, a risk-config value, or any field on `RiskDecision`/
  `SupervisorDecision` beyond what those types' own producers (Risk Engine,
  Supervisor) construct independently.

This matches review 1.15 §8's explicit list of what an agent — of any
implementation — must never receive: MT5 credentials, `order_send` access,
HALT-reset authority, risk-policy mutation, final unrestricted lot-size
authority, promotion authority. None of those is reachable through any
field on any contract the agent is permitted to construct.

---

## 4. Execution permissions

**No contract in this package can cause a real broker order to be sent
today.** This section changed the most since the previous package, because
Phase 4 made `order_check` a *real*, live capability for the first time —
the claim below is now load-bearing on that fact rather than on "nothing
executes anything yet," and is correspondingly more specific.

**There are exactly two adapters anywhere in this codebase that can hold a
real MT5 connection**, both under `src/crumblr/mt5_gateway/`:

- `ReadOnlyMt5Gateway` (`readonly.py`) — the M1 adapter, unchanged by
  Phase 4. `order_check`, `order_send`, `cancel_pending_orders` and
  `close_all_positions` all raise `ReadOnlyViolationError`
  unconditionally (`readonly.py:668-679`).
  `tests/unit/test_mt5_readonly_gateway.py::TestExecutionIsStructurallyImpossible`
  asserts this for all four.
- `OrderCheckMt5Gateway` (`execution.py`, Phase 4) — a deliberately
  separate class, not a modification of the read-only one (D-036), built
  specifically so the one real capability it adds cannot leak into the
  adapter every other component still holds. It implements exactly one
  live, broker-touching method: **`order_check`** — MT5's own server-side
  dry run; it validates a request and reports margin/rejection
  information, creates no ticket, and opens no market exposure.
  `order_send`, `cancel_pending_orders` and `close_all_positions` are
  hard-coded to unconditionally raise `ExecutionDisabledError` — there is
  no config flag read inside any of the three, nothing that could switch
  them on by mistake, because the code simply does not implement the
  action (`tests/unit/test_mt5_execution_gateway.py::TestExecutionStaysDisabled`
  asserts all three). `order_check()` itself fails closed at the very
  start, before building the MT5 request or touching the terminal, if
  `order.final_risk_decision_id is None` (`MissingFinalRiskDecisionError`,
  review 1.23 F-061) — not a reachable failure today
  (`ExecutionOrchestrator` always supplies it), defense-in-depth at the
  one place a real broker call actually happens rather than trust placed
  in every future caller.

  `order_check`'s own real-terminal evidence is still open: Phase 4 is
  tested only against a scripted fake terminal (`FakeMt5`,
  `tests/unit/test_mt5_execution_gateway.py`,
  `tests/integration/test_execution_orchestrator.py`) — review 1.22 §10
  named real-terminal `order_check` evidence as waiting specifically on
  the F-057/F-058 fixes, which are now closed (review 1.23), but the real
  run itself has not happened yet.

**The one place `order_send` does not raise anywhere in this codebase**
is `SimulatedBroker.order_send` (`mt5_gateway/simulated.py`) — an
in-memory fill simulator that opens no MT5 connection and cannot reach
one. It is wired only by `scripts/run_replay.py::ReplayOrchestrator`, the
replay/backtest CLI, against synthetic or previously-replayed price data;
nothing in the live pipeline (`LiveReader`, `LiveDecisionOrchestrator`,
`ExecutionOrchestrator`) ever holds a reference to it. Grepped directly
against `src/`: the only `.order_send(` *call* anywhere in the codebase is
`application/orchestration.py:491`, `self._broker.order_send(order)`,
where `self._broker` is a `BrokerPort`-typed dependency that production
wiring only ever supplies as `SimulatedBroker`.

**The append-only execution audit trail (Phase 4) is what makes "claimed
once, acted on once" durable**, not merely in-process discipline:

- `execution_requests` — one immutable row per `order_request_id`, ever.
  `ExecutionRequestStore.claim()`'s `INSERT ... ON CONFLICT (order_request_id)
  DO NOTHING RETURNING order_request_id` gives exactly one concurrent
  caller the claim; everyone else gets `ClaimResult(claimed=False)` if
  their fingerprint matches what is already stored, or
  `ExecutionRequestConflictError` if it does not — a genuine content
  conflict fails closed rather than reading as a harmless retry. Since
  review 1.23 F-059, that fingerprint
  (`application/execution.py::_approval_chain_fingerprint`) binds the
  *complete* serialized `TradeIntent`/intent-time `RiskDecision`/
  `SupervisorDecision` content plus `DecisionCapsule.provenance_fingerprint`
  — not a hand-picked field subset that could silently stop covering a new
  field (`SupervisorDecision.uncalibrated_checks` was the concrete gap the
  reviewer named).
- `execution_events` — an append-only log, one row per lifecycle step
  (`ExecutionEventType`: `REQUEST_CLAIMED`, `INELIGIBLE`, `GATE_CLOSED`,
  `RECONCILIATION_BLOCKED`, `FINAL_RISK_PASSED`, `FINAL_RISK_BLOCKED`,
  `ORDER_CHECKED`, `ORDER_CHECK_REJECTED`, plus `SUBMISSION_STARTED` and
  five further members reserved for M5 and never emitted by anything
  Phase 4 builds). `event_id` is derived from `(order_request_id,
  event_type)`, not random, so a retry after a crash converges on the same
  row instead of duplicating history.
- Both tables are in `persistence/schema.py::APPEND_ONLY_TABLES` — the
  application database role is never granted `UPDATE` on either, the same
  mechanism ADR-003 already uses for the main journal.
- `ExecutionEventStore.count_events_since(SUBMISSION_STARTED, ...)`
  (review 1.23 F-060, reopened once for using the wrong authority) is the
  real, durable order-frequency count FINAL Risk's `PortfolioState.
  orders_in_last_hour` reads — a real query against a real event type
  Phase 4 structurally never emits, so the honest value today is `0`, not
  a placeholder.

**Everything else that already gated a live order stays as it was:**

- The dashboard (the only web-facing surface) registers no route other
  than `GET` (`tests/integration/test_dashboard.py::TestReadOnlyBoundary`)
  and never imports `MetaTrader5` or `crumblr.mt5_gateway`.
- `config/live.yaml` does not exist in the repository (confirmed at this
  commit), and the config loader requires both an in-file acknowledgement
  and `CRUMBLR_ALLOW_LIVE=1` before a live environment can even be
  selected.
- `Environment.LIVE` reaching the execution preflight gate is itself a
  named refusal (`ReasonCode.LIVE_EXECUTION_NOT_PERMITTED`,
  `risk/execution_preflight_gate.py`) — the same rule enforced one layer
  further in, structurally, rather than trusted to never be reached.
- `risk/submission_gate.py::evaluate_submission_gate()` — the real F-049
  multi-gate `order_send` would eventually need — is now a real, tested,
  pure function (no longer the always-refusing stub this document
  described as of `6bdb5b1`; see the note below). It checks all nine of
  review 1.15 §14's required conditions simultaneously, three of them
  (`RiskConfig.approved_config_version`, `ExecutionConfig
  .submission_enabled`, `ExecutionConfig.feedback_2_0_approved`) against
  new, durable config fields that default to the closed/unapproved state
  and that no shipped `config/*.yaml` file sets — proven closed against
  the actual shipped configuration by
  `tests/unit/test_execution_gates.py::TestSubmissionGate
  ::test_the_gate_is_closed_against_the_actual_shipped_config`. **Nothing
  calls this function anywhere in `src/`** — there is no
  `SubmissionOrchestrator` — so it remains exactly as unreachable as the
  stub was; only the function itself stopped being a placeholder.

---

## 5. Risk / Supervisor separation

`RiskDecision` and `SupervisorDecision` are independently-constructed
contracts with no shared mutable state and no ability to alter each
other's verdict:

- **`RiskDecision`** (`domain/models.py:432-464`) is deterministic — no
  field on it is derived from a policy judgement, only from arithmetic
  against account state, the intent, and configured limits. A `PASS` must
  carry `approved_volume` and `stop_distance_points`; anything else must
  carry `reason_codes` and must not carry a volume — enforced by
  `_check_verdict_consistency`, so "approved with no volume" or "blocked
  with a volume" cannot be constructed at all. This holds identically for
  both callers of the check logic: `evaluate()` (intent-time) and
  `revalidate_fixed_volume_at_execution_time()` (FINAL, Phase 4) both
  return a `RiskDecision` and both go through the same consistency check,
  since the latter is not a separate implementation but a second call into
  the former's checklist.
- **FINAL Risk never resizes.** `revalidate_fixed_volume_at_execution_time()`
  (`risk/policies.py`, ADR-001) reuses `evaluate()` verbatim for every
  check it performs against freshly observed inputs — system/account
  state, market data quality and spread, session window, intent expiry,
  exposure, order frequency, and loss gates. Its *sizing* result is used
  only as a safety ceiling, never adopted: the outcome is always exactly
  PASS with `prior_decision.approved_volume` unchanged, or BLOCK/HALT. A
  fresh evaluation that would size a *smaller* volume than the one already
  approved is exactly the situation this function refuses into, not
  silently shrinks into (`review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md`
  point 1). Its own clock (`final_now`) is taken immediately before it
  runs — after every other read in `ExecutionOrchestrator._process()`,
  including the fresh tick and the risk-session recovery step that shares
  the same boundary (review 1.23 F-058) — so a slow broker call earlier in
  the same attempt cannot leave it judging a stale timestamp.
- **`SupervisorDecision`** (`domain/models.py:467-498`) is veto-only by
  construction: "there is no field through which the supervisor could alter
  side, price or size. It judges the intent it was given" (the class's own
  docstring). It carries `observed_regime`, `uncalibrated_checks` (F-024 —
  which of its own checks did *not* run, named rather than silently passed)
  and `reason_codes`, but nothing that could rewrite what the Risk Engine
  already decided. There is no execution-time Supervisor step in Phase 4 —
  the Supervisor's `APPROVE` from intent-time is the only Supervisor
  verdict `ApprovedOrder.supervisor_decision_id` can ever reference.
- **Ordering is structural, not conventional.** `SupervisorDecision`
  doesn't reference `RiskDecision` by field, but the pipeline that
  constructs them (`evaluator/pretrade.py`) only runs the Supervisor after
  a `RiskDecision`; `ApprovedOrder.intent_risk_decision_id` and
  `.supervisor_decision_id` are both required, non-defaulted fields, so an
  order cannot be built while skipping either stage's record — a contract
  bypassing one of the two would fail Pydantic validation, not merely fail
  a code review.
- **Reconciliation status is not a Supervisor opinion.** The Supervisor's
  `SupervisorContext.reconciliation_status: ReconciliationStatus`
  (`evaluator/pretrade.py`) is read, not set, by the Supervisor — review
  F-002's original finding was that this must never default to "matched";
  `ReconciliationStatus.MISMATCHED`/`UNKNOWN` both force `HALT`
  unconditionally (`evaluator/pretrade.py:132-145`), before any policy
  switch is consulted. `ExecutionOrchestrator` reads the same
  `reconcile()` result before FINAL Risk ever runs, and refuses
  (`ExecutionEventType.RECONCILIATION_BLOCKED`) on anything but `MATCHED`
  — real reconciliation against a real broker-state observation captured
  once, immediately beforehand (review 1.22/1.23 F-058), not the replay
  environment's honest-but-trivial `MATCHED` (there is only one book —
  `orchestration.py:528-537` says so in its own docstring).

---

## 6. Per-contract summary

Everything below inherits §1's guarantees (frozen, extra-forbidden, exact
Decimal, UTC-only) without exception; this table names only what is
specific to each contract.

| Contract | Specific invariant | Notes |
|---|---|---|
| `MarketSnapshot` | Rejects a crossed quote (`ask < bid`) at construction | Derived data; the raw tick that produced it is persisted separately and never overwritten even when flagged `SUSPECT` |
| `Bar` | Rejects OHLC values that contradict each other (`high < low`, `high` below the open/close range, etc.) | Shape only; `MarketBar` is the stored record with provenance |
| `InstrumentSpec` | `spec_version` is a content hash over semantic fields only — `tick_value` and `captured_at_utc` excluded (F-039) | Broker symbol never hard-coded; discovered per account |
| `TradeIntent` | `stop_loss_price` mandatory for BUY/SELL; stop must sit on the correct side of `reference_price`; FLAT must carry neither stop nor risk fraction | The only contract the Trading Agent constructs — see §3. `decision_hash` is its own complete-content fingerprint (excludes only `intent_id`), safe against its one `float` field via `repr()` |
| `RiskDecision` | PASS ⇔ has volume + stop distance; anything else ⇔ has reason codes + no volume | Deterministic; two independent callers (intent-time, FINAL) share one check function — see §5 |
| `SupervisorDecision` | Non-APPROVE must carry reason codes; `uncalibrated_checks` names controls not currently in force | Veto-only, intent-time only; see §5 |
| `ApprovedOrder` | FLAT is refused (a close instruction, not an order); non-MARKET requires an explicit price | **Built, two independent producers** — see §2/§4. `final_risk_decision_id` set only by the real (non-sending) Phase-4 path |
| `ExecutionResult` | No cross-field invariant enforced beyond type/bounds — this is intentionally a wide "everything the broker told us" record | **Built only by the replay/paper simulator** (`SimulatedBroker.order_send`) — never by anything that reaches a real MT5 terminal; see §4 |
| `OrderCheckCompleted` | No cross-field invariant beyond type/bounds | **Built, two independent producers** (real MT5 dry run via `OrderCheckMt5Gateway`; in-memory via `SimulatedBroker`) — the one live broker capability Phase 4 exercises against a real terminal |
| `AccountState` | `login_hash` is a truncated fingerprint of `login`+`server` — the raw login is never persisted through this field | Live read only, one request at a time; never durably stored as-is (see `BrokerAccountSnapshot`) |
| `PositionState` | Rejects `side=FLAT` | Live read; `BrokerPositionSnapshot` is the durable record |
| `BrokerAccountSnapshot` | Never carries the raw login (`account_ref`, same fingerprint as `login_hash`); `position_set_state`/`pending_order_set_state` distinguish a confirmed-empty set from a failed query | F-047, 2026-08-25. Now also the single observation `ExecutionOrchestrator` reconciles and hands FINAL Risk (F-058) — real-terminal validation still pending (F-051) |
| `BrokerPositionSnapshot` / `BrokerPendingOrderSnapshot` | Tied to their parent by `snapshot_id`; a row exists only when the matching set state is `COMPLETE` | F-047; see D-045 for the one known v0 gap (no instrument-spec comparison) |
| `Incident` | A closed incident must record a root cause; `blocks_promotion` is true for any open SEV-0/SEV-1 | Contract exists; no producer yet (§2) |
| `DecisionCapsule` | `provenance_fingerprint` binds market snapshot, feature version, model, risk config and code commit into one digest | Assembled once per decision window by `application/recording.py`; immutable once written. Never gains a FINAL `RiskDecision` field — that lives in the separate execution audit trail (§2/§4) |

---

## 7. What this package does not claim

- It does not claim CI has run against a hosted runner and returned green
  on the latest push (tracked separately — review 1.21 §8/1.22 §10/1.23
  §10; the last known local reproduction of both CI jobs was clean).
- It does not claim `OrderCheckMt5Gateway.order_check` — the one live,
  broker-touching capability Phase 4 built — has ever met a real MT5
  terminal. Every assertion about it in this package is against a scripted
  fake terminal (`FakeMt5`). Real-terminal evidence is a named open item.
- It does not claim `order_send`, or anything past `order_check`, has ever
  been exercised against a real broker, on any environment value — no code
  path in this repository reaches that regardless of `Environment`; the
  only non-raising `order_send` implementation is the in-memory replay
  simulator (§4).
- It does not claim `BrokerAccountSnapshot`/`BrokerPositionSnapshot`/
  `BrokerPendingOrderSnapshot` have been validated against a real MT5
  terminal (F-051, open).
- It does not claim `Incident` is wired to a real register.
- It does not claim the execution audit trail (`execution_requests`/
  `execution_events`) has been exercised under real concurrent load —
  `ExecutionRequestStore.claim()`'s conflict/idempotence behaviour is
  tested against a real PostgreSQL (`tests/integration/
  test_execution_persistence.py`), not against genuine concurrent workers.
- It does not claim this document's own review is complete — that is what
  it exists to enable: the M0 local-policy checklist item "domain
  contracts reviewed by a human" is still open pending this submission.
- Tests exist for every invariant named above
  (`tests/unit/test_control_plane_contracts.py`,
  `tests/unit/test_numeric_boundaries.py`,
  `tests/unit/test_trade_intent.py`,
  `tests/integration/test_execution_orchestrator.py`,
  `tests/integration/test_execution_persistence.py`,
  `tests/unit/test_mt5_execution_gateway.py`, and the other model-specific
  suites), but per review 1.14 §13's own instruction, their existence is
  not offered here as a substitute for review — this document exists so a
  human reviewer can check the actual invariants against build.md's
  requirements without re-deriving them from the source.
