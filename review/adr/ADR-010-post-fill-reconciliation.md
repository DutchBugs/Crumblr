# ADR-010 — Post-fill reconciliation (core critical path item 8)

**Status:** ACCEPTED — implemented and tested; `order_send`/`close_all_positions` still unbuilt
**Date:** 2026-09-02
**Drivers:** review 1.16 §7-8, review 1.26 §6 item 8 ("derive post-fill
expected state from durable platform execution history and reconcile it
against broker truth"), reviews 1.24/1.25/1.27's definition-of-done
bundles, ADR-008 §2, ADR-009 §2.2
**Supersedes:** nothing. Fulfils the instruction `application/
reconciliation.py::ExpectedState.flat()`'s own docstring has carried
since it was written.
**Implementation:** `src/crumblr/application/expected_state.py`,
`src/crumblr/application/reconciliation.py::ExpectedState
.from_durable_exposure()`, `src/crumblr/application/execution.py
::ExecutionOrchestrator.reconcile_once()`, `src/crumblr/domain/enums.py
::ExecutionEventType.RECONCILED`, `tests/integration/
test_execution_reconciliation.py`

---

## 1. The decision being recorded

`review/feedback.1.26.md` §6 item 8: *"Derive post-fill expected state
from durable platform execution history and reconcile it against broker
truth."* Origin, review 1.16 §7-8: *"Once execution exists, expected
state must come from the platform's durable execution/order history...
Otherwise reconciliation becomes: compare MT5 to MT5. That detects
nothing."*

`ExpectedState.expected_position_tickets`/`expected_pending_order_ids`
have existed since F-047 and have never been populated by any caller —
the only constructor was `flat()`, whose own docstring has said since it
was written: *"Once `order_send` exists, build the expectation from the
platform's own durable order/position history instead."* The comparator
(`reconcile()`) did not need to change shape — it was already whole-book,
ticket-keyed, fail-closed. What was missing was the expectation
*producer*.

Review 1.21 uniquely qualifies this item as *"post-execution
reconciliation design"* — the only item on the whole critical path ever
described as design-only. This ADR's answer: the design is buildable for
real, because the derivation reads durable history the platform already
has and needs no broker capability it lacks, so this item builds it
rather than only specifying it — the same discipline items 2-7 already
used.

**The honest limit, stated up front.** Because `order_send`/
`close_all_positions` stay unreachable, no committed request or flatten
occurrence can ever have resulted in a real position. This mechanism's
output is therefore **provably identical to `flat()`'s** in every
deployment this platform can currently produce — sharper inertness than
any prior item on this list. Items 6 and 7 still *emit a new event* even
in their fully-inert branch; item 8 emits nothing at all when nothing has
ever been committed. Proven mechanically, not merely asserted:
`tests/unit/test_expected_state.py::test_an_empty_history_is_exactly_flat`
asserts `ExpectedState.from_durable_exposure(guard, DerivedExposure
.empty()) == ExpectedState.flat(guard)`, byte for byte.

## 2. The mechanism

### 2.1 Why the derivation is pure and lives outside `reconciliation.py`

`application/expected_state.py` is a new, database-free module —
`flatten_plan.py`'s "no second opinion" discipline applied one layer
further. It classifies already-read event history into a
`DerivedExposure`; `reconciliation.py`'s own module docstring promises
*"This reads only PostgreSQL through a `BrokerStateSource`"* — keeping
all store reads in the driver (`ExecutionOrchestrator.reconcile_once()`)
preserves that promise literally rather than quietly widening it.

### 2.2 Why `RECONCILED`, and why not the other four reserved members

Two prior ADRs pre-committed `ExecutionEventType.RECONCILED` to this
item: ADR-008 §2 (*"stays reserved for its original M5 purpose —
post-fill reconciliation (core critical path item 8), confirming a known
fill against expected state"*) and ADR-009 §2.2 (*"item 8's, for the same
reason"*). Both are honoured exactly as written.

`SUBMITTED`/`BROKER_ACK`/`FILLED`/`CLOSED` each assert a broker fact — a
real submission, acknowledgement, fill, or lifecycle end — that no code
path in this platform can produce. Emitting any of them would be
evidence fabrication in the one table whose purpose is auditable
provenance, the same objection ADR-009 §2.1 raised against fabricating a
placeholder capsule, one layer down. `RECONCILED`'s literal claim — *"the
platform compared its durable expectation against observed broker truth,
and here is the verdict"* — is true of an action the platform genuinely
performs today, including truthfully about a still-flat expectation. It
is the only one of the five whose claim does not depend on a fill having
happened.

No new table is needed (contrast ADR-009 §2.1, where a flatten's lack of
an `order_request_id` forced a dedicated table pair): `RECONCILED` is an
`ExecutionEventType`, FK'd to `execution_requests.order_request_id`, and
item 8's subject is always an already-claimed request that has one.

**`RECONCILED` can exist at most once per request, ever.**
`event_id_for` derives from `(order_request_id, event_type)` alone — a
second `append()` with different content raises
`ExecutionEventConflictError` (item 4's hardening). It is therefore a
once-per-request terminal determination, not a per-pass heartbeat —
matching this member's own position in `OrderState`'s build.md §19
machine (after `FILLED`, before `CLOSED`). Its payload carries
determination content only — `expected_position_tickets`,
`observed_open_tickets`, `closed_tickets`, `expected_pending_order_ids`,
`book_status` — deliberately no timestamp, no snapshot id, mirroring
`AMBIGUOUS_OUTCOME_RESOLVED`'s own precedent exactly: what lets a
concurrent double-check converge instead of raising a false conflict.

### 2.3 The flatten-gate fork

Item 7's `risk/flatten_gate.py` leg ("not `UNKNOWN`, never `MATCHED`")
was justified in ADR-009 §2.3 on the premise *"the only expectation this
platform can currently form is `flat()`."* Item 8 makes that premise
false — it cannot be left standing, since a load-bearing justification
resting on a premise the same codebase has since falsified is exactly
what `review/DEVIATIONS.md` exists to prevent.

**Resolved: `flatten_once()` keeps passing `flat()`; only the gate's
*justification* changes.** The leg's real justification never actually
depended on `flat()`: a flatten is triggered by an open position past its
deadline; under *any* expectation, that position is either attributed
(`MATCHED`) or not (`MISMATCHED`) — requiring `MATCHED` would refuse to
flatten precisely the positions the platform did *not* put there (opened
by hand, by another EA, by an earlier deployment), and an unattributable
position past the deadline is *more* alarming than an attributed one, not
less. ADR-004 §5.3's real safety property is observability ("flattening
what you cannot see"), not agreement — expectation-independent, and
survives item 8 verbatim.

Switching `flatten_once()` to the derived expectation would be all-cost,
no-benefit today: the derived expectation is provably either identical to
`flat()` (no committed submission) or `flat()` plus an undetermined
reason (an unrelated request stuck at `SUBMISSION_STARTED`) — which can
only *newly close* the flatten gate, never newly open it, at the one
moment (the deadline) this platform cares most about not stalling.
ADR-004 §5.3's "reconcile before flattening" requirement is already fully
satisfied by the *observed*-side legs (`position_book_complete`, snapshot
freshness, `reconcile()`'s own completeness checks) — identical under
either expectation — so keeping `flat()` does not weaken §5.3 by even one
leg.

`risk/flatten_gate.py`'s module docstring is rewritten to this
expectation-independent argument (not "only `flat()` is possible"), with
a pointer here. `tests/unit/test_flatten_gate.py
::test_a_mismatched_reconciliation_does_not_close_the_gate`'s **docstring
only** changes — its assertions were already correct and stay unchanged.
D-051 records the deferral with an explicit trigger condition: revisit
only once `order_send`/`close_all_positions` are reachable, together with
D-050's retry machinery — not before.

### 2.4 Why reusing item 6's `matching_tickets` is not "comparing MT5 to MT5"

Review 1.16 §8's objection targets an expectation built from the *latest*
snapshot, which moves with the observation and detects nothing. A ticket
frozen into an append-only, immutable, content-conflict-hardened event
(item 4's hardening) is a different object — a durable commitment that
never changes again. `AMBIGUOUS_OUTCOME_RESOLVED.payload
["matching_tickets"]` (item 6) is the *only* durable source of a real
ticket anywhere in this codebase today; item 8 consumes that attribution
rather than re-deriving it, so there is one attribution mechanism, not
two that could disagree.

If the broker later stops reporting an expected ticket, reconciliation
reports *"expected open position missing"* — a detection `flat()` is
structurally incapable of making. That is the whole point of this item.

The tickets are broker tickets (`frozenset[int]`), not magic numbers — a
magic number is not a ticket (`mt5_magic_number` derives a 31-bit value
from `order_request_id`; one magic can in principle cover several
positions) and cannot substitute for `expected_position_tickets`, which
`_position_mismatches` compares directly against `position.ticket`.

### 2.5 The exposure mapping

`application/expected_state.py::_EXPOSURE_BY_EVENT` is a total mapping
over every `ExecutionEventType` member — guarded by
`test_every_execution_event_type_has_a_declared_exposure_meaning`, so a
future enum addition without an exposure decision fails a test instead of
silently reporting zero exposure. Every genuine terminal refusal (ADR-008
§1's own list) expects nothing. `SUBMISSION_STARTED` alone is
undetermined — the one genuinely ambiguous state, ADR-008's own argument,
reused directly. `AMBIGUOUS_OUTCOME_RESOLVED` is determined either way
(`submitted=False` → no exposure; `submitted=True` → its
`matching_tickets`). **`RECONCILED` is deliberately
`NOT_EXPOSURE_RELEVANT`** — treating it as "the last event" would make a
request's own derivation poison itself the pass after `reconcile_once()`
writes a verdict for it; the classifier looks past it to the last
genuinely exposure-relevant event underneath. The four still-reserved
members are mapped `UNDETERMINED` (unemittable today, so unreachable) so
a future item that emits one without updating this table fails closed
rather than silently reporting zero exposure for a state it does not yet
know how to read.

**Pending orders (D-049), promoted from a note to an enforced fail-closed
leg.** `magic` is not tracked for pending orders at any layer (D-049).
Rather than silently reporting an empty `expected_pending_order_ids` — a
lie for a non-`MARKET` entry — any determined/undetermined request whose
`SUBMISSION_STARTED` payload names an entry type other than `MARKET`
contributes an undetermined reason citing D-049. Six lines that turn
D-049 from a paragraph into a runtime property.

**Flatten interaction.** `FLATTEN_SUBMISSION_STARTED` never by itself
removes a ticket from the expectation — D-050's own "Watch for" warns
directly against reading a commitment as an outcome. An unresolved
commitment's targets become undetermined reasons instead (the flatten's
own ambiguity, one layer up). `FLATTEN_OUTCOME_RESOLVED`'s
`closed_tickets` are removed; `still_open_tickets` stay expected —
removal is essential, or a durably-closed position would stay falsely
expected forever.

### 2.6 The driver and its placement

`ExecutionOrchestrator.reconcile_once()` is called from the **bottom** of
`run_once()` — after the capsule loop, unlike `flatten_once()`'s
top-of-pass placement. Reconciliation is the last stage of build.md's
pipeline ("Execution service executes. Reconciliation verifies."), and
running it after the loop lets `_recover_ambiguous_submission` (which
runs inside the loop) resolve a `SUBMISSION_STARTED` ambiguity in the
*same pass*, before reconciliation asks about it — confirmed directly:
`tests/integration/test_execution_orchestrator.py
::test_a_stalled_submission_is_recovered_not_reprocessed` and
`::test_a_matching_broker_position_resolves_as_submitted` both now
observe `RECONCILED` following `AMBIGUOUS_OUTCOME_RESOLVED` in the very
same `run_once()` call. Its own outcome type
(`ReconciliationAttemptOutcome`) is deliberately not merged into
`run_once()`'s return tuple, same reasoning as `flatten_once()`'s.

Not hooked into `_process()`: that method is per-capsule and gated by
eligibility, and a request that already reached `SUBMISSION_STARTED`
never re-enters its body (the claim gate diverts it to
`_recover_ambiguous_submission` and returns) — post-fill reconciliation
of an already-committed request is structurally unreachable from there.
`activation_watermark=None` in every shipped config also means
`_process()`'s body is provably never reached in the normal live
pipeline (D-047) — hanging item 8 off it would make the mechanism
unreachable for a second, unrelated reason.

**Durable state checked before any broker read, twice over.** First:
`request_ids_with_event(SUBMISSION_STARTED)` — the complete,
exhaustively-proven candidate set — returns immediately if empty, before
any broker read at all; provably empty in every shipped config today.
Second: if every candidate already carries `RECONCILED`, this also
returns before reading the broker, mirroring `flatten_once()`'s own
"already resolved, no further broker read" branch
(`test_a_second_pass_does_not_re_reconcile_an_already_reconciled_request`).

### 2.7 The read seam and why it is bounded by state, not time

`ExecutionEventStore.request_ids_with_event()` and `FlattenEventStore
.occurrence_histories()` are new. D-047's "fine for now" framing (the
activation watermark being unset makes an equivalent scan provably cheap
forever in shipped configs) does **not** transfer here: `execution_events`
grows with every capsule refusal in any deployment that runs the
orchestrator, and `reconcile_once()` runs every pass. Instead, the
candidate set is bounded *by construction*: it is exactly `{requests that
ever reached SUBMISSION_STARTED}` — a theorem from §2.5's exhaustive
mapping, not a heuristic — provably empty in every shipped config today,
and bounded forever by real order volume once non-empty. A `since`
parameter exists on the seam but the driver deliberately passes `None`:
time-bounding would defeat the mechanism's purpose — a position lost
track of weeks ago is exactly the drift item 8 exists to catch.

One index-only migration, off head `cc35e55b3f92`
(`ix_execution_events_type_time` on `(event_type, occurred_at_utc)`),
serves the new seam and retroactively serves `count_events_since()`'s
existing unindexed filter, which FINAL Risk already calls every
`_process()` pass.

## 3. What this does not do

**`order_send` and `close_all_positions` remain completely unbuilt and
unreachable.** Both are still the same unconditional
`ExecutionDisabledError`. Nothing in `reconcile_once()` or anything it
calls approaches either — verified structurally by
`tests/integration/test_execution_reconciliation.py
::test_no_broker_fact_event_is_ever_emitted`, which scans
`application/execution.py`'s own source for `SUBMITTED`/`BROKER_ACK`/
`FILLED`/`CLOSED` literals.

**This is a sharper honesty statement than items 6 or 7 needed to make,
and it is worth saying plainly.** ADR-008's recovery always concludes
`submitted=False`; ADR-009's resolution always concludes `flattened=False`
— but both still *emit an event that did not exist before*. Item 8 emits
nothing at all in a deployment with no committed submission — its
inertness is total, not merely one-branch.

**What is nonetheless genuinely new, and would run the moment one
`SUBMISSION_STARTED` exists**: (a) a whole-book reconciliation that runs
every pass, independent of any capsule — today, with no eligible capsule,
`_process()` never runs and this orchestrator performs no reconciliation
at all, so drift would be invisible unless an operator ran
`scripts/reconcile.py` by hand; (b) an expectation derived from durable
history, which can report *"expected open position missing"* — a
detection `flat()` is structurally incapable of; (c) a fail-closed
expected-side `UNKNOWN` (`undetermined_reasons`), which did not
previously exist at all.

**Explicitly out of scope, named rather than silently folded in or
ignored** (see D-051 for the full record):

- No `OrderState` transition-validation state machine — confirmed nothing
  anywhere validates a build.md §19 transition. Real, `order_send`-
  independent, but not what item 8's own reviewer sentence asks for.
- No persisted `ExecutionResult` —
  `PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md` already rejected that model;
  `execution_events` already is the persistence answer.
- `_process()`'s and `live_decision.py`'s own `flat()` call sites stay
  `flat()`. `_process()` is a pre-submission eligibility check for one
  capsule, not a question about the platform's own committed exposure —
  switching it would let another request's ambiguity block an unrelated
  capsule at the preflight gate. `live_decision.py` is deliberately
  MT5-free, and its expectation is cached once at construction, a shape a
  derived expectation cannot fit. **Forward hazard**: once `order_send`
  works, `live_decision`'s `flat()` becomes wrong by construction — every
  legitimately-open platform position will read as unexpected in a tier
  whose reconciliation feeds a trust judgement. A distinct, future
  cross-tier problem, not this item's to solve.
- `config.SupervisorConfig.halt_on_reconciliation_mismatch` stays
  unconsumed — confirmed by grep: set in `config/base.yaml`, defined in
  `config.py`, read nowhere in `src/`. Wiring a fifth kill-switch producer
  onto a flag nothing has ever consumed is an unrequested behaviour
  change; item 8's reviewer sentence says "reconcile", not "halt".
- No `close_all_positions`, no `order_send`, no retry — D-050's three
  deferrals untouched.

## 4. Consequences

- `review/DEVIATIONS.md` D-033 stays `PARTIALLY RESOLVED` (item 7's own
  scope; item 8 does not touch the flatten's own close). New **D-051**
  records the three items named above. D-049 gains a cross-reference (now
  enforced as a runtime leg, §2.5). D-050 gains a cross-reference (item 8
  consumes `FLATTEN_OUTCOME_RESOLVED`'s payload, §2.5).
- `review/FEEDBACK.md`'s "core submission-safety phase" tracking updates
  — item 8 done, one item remains (broker-side SL verification, item 9).
- Item 9 is a natural consumer of `RECONCILED`'s `tickets_by_request`-
  derived payload shape: an SL-verification pass wants to know which
  tickets are attributed to which request, which this item's derivation
  already answers.
- A new Alembic head (`03df83b062a6`), coordinated with Dev 2 in advance
  per instruction §8; a new `INTEGRATION_NOTICES.md` entry for the
  additive `ExpectedState.undetermined_reasons` field.
