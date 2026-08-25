# Domain contract package — for reviewer/human approval (M0)

Requested by review 1.14 §13, repeated by review 1.15 §11 and review 1.16
§11: "Provide the actual current contract definitions or a generated
contract package," checked specifically for immutability, extra-field
rejection, Decimal/time semantics, ownership boundaries, execution
permissions, risk/supervisor separation, and which fields may or may not be
agent-controlled. "Do not mark this approved merely because tests exist."

This document describes what the code in `src/crumblr/domain/models.py`
actually does, as of commit `f67f341` (2026-08-25) — it is a description,
not a specification; `build.md` remains the specification, and any gap
between the two belongs in `review/DEVIATIONS.md`, not here. Line numbers
below refer to `domain/models.py` at that commit and will drift; the class
names will not.

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
  "changed" decision is a new object, never an edited one.
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
  `RiskFraction` to `(0, 1]`.
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
than one stage in normal operation; where a later stage reads an earlier
stage's contract, it reads it as data, never mutates it (mutation is
structurally impossible — see §1).

| Contract | Constructed by | Read by |
|---|---|---|
| `MarketTick` / `MarketBar` / `MarketSnapshot` / `Bar` | `market_data/pipeline.py`, `mt5_gateway/readonly.py` | Trading Agent, persistence |
| `InstrumentSpec` | `mt5_gateway/readonly.py` (from a live terminal), `market_data/synthetic.py` (replay) | Risk Engine (sizing), Trading Agent, reconciliation |
| `TradeIntent` | Trading Agent only | Risk Engine |
| `RiskDecision` | Risk Engine only | Supervisor, `DecisionCapsule`, execution (future) |
| `SupervisorDecision` | Supervisor (`evaluator/pretrade.py`) only | Execution (future), `DecisionCapsule` |
| `ApprovedOrder` | Not yet constructed anywhere — no code path exists that builds one (M5 is NO-GO); the type exists ahead of the engine that will produce it | Execution Service (future) |
| `ExecutionResult` | Not yet constructed anywhere — same reason | Reconciliation (future), `DecisionCapsule` |
| `AccountState` / `PositionState` / `PendingOrderState` | `mt5_gateway/readonly.py::ReadOnlyMt5Gateway` (live reads, one request at a time) | Account guard, risk engine (future) |
| `BrokerAccountSnapshot` / `BrokerPositionSnapshot` / `BrokerPendingOrderSnapshot` | `application/broker_state.py::capture_broker_state` (F-047) | `persistence/broker_state.py::BrokerStateStore`, `application/reconciliation.py` |
| `Incident` | Not yet constructed anywhere — the register has a contract but no producer (`status.md` §3 Data checklist: "the contract exists; no register") | Supervisor's `IncidentStatus` input (currently hard-coded `CLEAR` in replay — see `orchestration.py::_incident_status`) |
| `DecisionCapsule` | `application/recording.py` (assembled from the other contracts at the end of one decision window) | Journal, audit, replay verification |

**Two contracts are specified but unbuilt** (`ApprovedOrder`,
`ExecutionResult`): they exist in the type system ahead of the engine that
will populate them, which is deliberate — build.md §6.4/§7 define their
shape as part of the M5 contract, and having the type checked and reviewed
now means M5's execution engine is built against an already-approved shape
rather than inventing one under schedule pressure. No code path constructs
either today; `ReadOnlyMt5Gateway`'s execution methods (`order_check`,
`order_send`) raise `ReadOnlyViolationError` unconditionally
(`mt5_gateway/readonly.py:591-611`), so there is no way to reach the point
where an `ApprovedOrder` would be built even accidentally.

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
  that request can be refused or reduced downstream.
- **Final approval.** Only `RiskDecision.verdict` and
  `SupervisorDecision.verdict` exist to approve or refuse; `TradeIntent` has
  no verdict field of its own to set.
- **Order submission.** `ApprovedOrder` and the execution path are not
  reachable from `TradeIntent` directly — `RiskDecision` and
  `SupervisorDecision` both sit between them, and `ApprovedOrder.
  risk_decision_id`/`supervisor_decision_id` are required fields (no
  default), so an `ApprovedOrder` cannot even be constructed without
  referencing both.
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

**No contract in this package can cause an order to be sent today.** This
is a structural claim, not a policy one:

- `ApprovedOrder` is never constructed by any code path (see §2).
- `ReadOnlyMt5Gateway` — the only MT5 adapter that exists — raises
  `ReadOnlyViolationError` unconditionally from `order_check`, `order_send`,
  `cancel_pending_orders`, and `close_all_positions`
  (`mt5_gateway/readonly.py:668-679`). `tests/unit/test_mt5_readonly_gateway.py
  ::TestExecutionIsStructurallyImpossible` asserts this for all four.
- The dashboard (the only web-facing surface) registers no route other than
  `GET` (`tests/integration/test_dashboard.py::TestReadOnlyBoundary`) and
  never imports `MetaTrader5` or `crumblr.mt5_gateway`.
- `config/live.yaml` does not exist in the repository, and the config
  loader requires both an in-file acknowledgement and
  `CRUMBLR_ALLOW_LIVE=1` before a live environment can even be selected.

The only contract with an `environment: Environment` field that a live
value could theoretically flow through is `ApprovedOrder` — which, again,
nothing constructs. There is no field on any *other* contract in this
package (`TradeIntent`, `RiskDecision`, `SupervisorDecision`,
`BrokerAccountSnapshot`, …) whose value can reach an MT5 write call.

---

## 5. Risk / Supervisor separation

`RiskDecision` and `SupervisorDecision` are two independently-constructed
contracts with no shared mutable state and no ability to alter each other's
verdict:

- **`RiskDecision`** (`domain/models.py:432-464`) is deterministic — no
  field on it is derived from a policy judgement, only from arithmetic
  against account state, the intent, and configured limits. A `PASS` must
  carry `approved_volume` and `stop_distance_points`; anything else must
  carry `reason_codes` and must not carry a volume — enforced by
  `_check_verdict_consistency`, so "approved with no volume" or "blocked
  with a volume" cannot be constructed at all.
- **`SupervisorDecision`** (`domain/models.py:467-498`) is veto-only by
  construction: "there is no field through which the supervisor could alter
  side, price or size. It judges the intent it was given" (the class's own
  docstring). It carries `observed_regime`, `uncalibrated_checks` (F-024 —
  which of its own checks did *not* run, named rather than silently passed)
  and `reason_codes`, but nothing that could rewrite what the Risk Engine
  already decided.
- **Ordering is structural, not conventional.** `SupervisorDecision`
  doesn't reference `RiskDecision` by field, but the pipeline that
  constructs them (`evaluator/pretrade.py`) only runs the Supervisor after
  a `RiskDecision`; `ApprovedOrder.risk_decision_id` and
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
  switch is consulted. `application/reconciliation.py` (F-047/reconciliation
  v0, 2026-08-25) is the intended eventual producer of this value outside
  replay; today only `ReplayOrchestrator._reconciliation_status()` supplies
  it, and it says so honestly in its own docstring
  (`orchestration.py:528-537`) — `MATCHED` in replay is real (there is only
  one book), not a placeholder standing in for a broker check.

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
| `TradeIntent` | `stop_loss_price` mandatory for BUY/SELL; stop must sit on the correct side of `reference_price`; FLAT must carry neither stop nor risk fraction | The only contract the Trading Agent constructs — see §3 |
| `RiskDecision` | PASS ⇔ has volume + stop distance; anything else ⇔ has reason codes + no volume | Deterministic; see §5 |
| `SupervisorDecision` | Non-APPROVE must carry reason codes; `uncalibrated_checks` names controls not currently in force | Veto-only; see §5 |
| `ApprovedOrder` | FLAT is refused (a close instruction, not an order); non-MARKET requires an explicit price | Unbuilt — see §2/§4 |
| `ExecutionResult` | No cross-field invariant enforced beyond type/bounds — this is intentionally a wide "everything the broker told us" record | Unbuilt — see §2/§4 |
| `AccountState` | `login_hash` is a truncated fingerprint of `login`+`server` — the raw login is never persisted through this field | Live read only, one request at a time; never durably stored as-is (see `BrokerAccountSnapshot`) |
| `PositionState` | Rejects `side=FLAT` | Live read; `BrokerPositionSnapshot` is the durable record |
| `BrokerAccountSnapshot` | Never carries the raw login (`account_ref`, same fingerprint as `login_hash`); `position_set_state`/`pending_order_set_state` distinguish a confirmed-empty set from a failed query | F-047, 2026-08-25. Real-terminal validation still pending (F-051) |
| `BrokerPositionSnapshot` / `BrokerPendingOrderSnapshot` | Tied to their parent by `snapshot_id`; a row exists only when the matching set state is `COMPLETE` | F-047; see D-045 for the one known v0 gap (no instrument-spec comparison) |
| `Incident` | A closed incident must record a root cause; `blocks_promotion` is true for any open SEV-0/SEV-1 | Contract exists; no producer yet (§2) |
| `DecisionCapsule` | `provenance_fingerprint` binds market snapshot, feature version, model, risk config and code commit into one digest | Assembled once per decision window by `application/recording.py`; immutable once written |

---

## 7. What this package does not claim

- It does not claim CI has run (tracked separately — review 1.16 §11).
- It does not claim `ApprovedOrder`/`ExecutionResult` have been exercised —
  they cannot be, since nothing constructs them yet.
- It does not claim `BrokerAccountSnapshot`/`BrokerPositionSnapshot`/
  `BrokerPendingOrderSnapshot` have been validated against a real MT5
  terminal (F-051, open).
- It does not claim `Incident` is wired to a real register.
- Tests exist for every invariant named above
  (`tests/unit/test_control_plane_contracts.py`,
  `tests/unit/test_numeric_boundaries.py`,
  `tests/unit/test_trade_intent.py`, and the model-specific suites), but
  per review 1.14 §13's own instruction, their existence is not offered
  here as a substitute for review — this document exists so a human
  reviewer can check the actual invariants against build.md's requirements
  without re-deriving them from the source.
