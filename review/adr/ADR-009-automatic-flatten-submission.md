# ADR-009 — Automatic flatten submission (core critical path item 7)

**Status:** ACCEPTED — implemented and tested; `close_all_positions`/`order_send` still unbuilt
**Date:** 2026-09-02
**Drivers:** owner decision O-003, `review/adr/ADR-004-intraday-session-boundary.md` §5,
review 1.24 §12.B, reviews 1.25/1.26 §6/1.27 §8, `review/DEVIATIONS.md` D-033
**Supersedes:** nothing. *Partially* closes ADR-004 §5.1/§5.3a/§5.4 and
*partially* resolves D-033 — see §3.
**Implementation:** `src/crumblr/application/execution.py::ExecutionOrchestrator
.flatten_once()`, `src/crumblr/risk/flatten_gate.py`,
`src/crumblr/persistence/flatten.py`, `src/crumblr/application/flatten_plan.py`,
`src/crumblr/domain/models.py::FlattenInstruction`/`FlattenPlan`,
`src/crumblr/domain/enums.py::FlattenEventType`,
`tests/integration/test_execution_flatten.py`

---

## 1. The decision being recorded

`review/DEVIATIONS.md` D-033 states the exact gap: *"the system refuses
new entries outside the trading window and halts when exposure survives
the flatten deadline, but it does not close the position... the halt is
the whole safety story today."* `risk/trading_window.py`'s own module
docstring is explicit about why: *"It does not flatten anything: closing
a position needs the execution path, which is M5... Promising to close is
a promise this system cannot yet keep."*

Confirmed by direct code read before designing: `close_all_positions()`
(`mt5_gateway/execution.py`) is a pure unconditional `ExecutionDisabledError`,
the same as `order_send`. Detection lives in four places, none broker-facing
— `risk/trading_window.py::requires_flat`/`has_crossed_rollover`,
`risk/policies.py::_overnight_breach` (feeds a `HALT` verdict), and the two
`_check_session_boundary` methods in `application/orchestration.py`/
`application/live_decision.py` (trip the kill switch, never call the
broker). The only broker-facing close anywhere in this codebase is
`risk/operator_controls.py::flatten_positions()` — manual-only, and never
called automatically (confirmed by grep before this item).

`ADR-004` §5 is the authoritative scope statement for what M5 must add:

> 1. An automatic flatten at the deadline, distinct from the operator's
>    manual FLATTEN POSITIONS control...
> 2. Behaviour when the flatten fails → retry within a bounded window,
>    then HALT and raise an incident...
> 3. Behaviour when the broker is unavailable near the deadline → HALT
>    before the deadline rather than after it... on reconnection,
>    reconcile first; an unreconciled position book may not be
>    flattened, because flattening what you cannot see is how a hedge
>    becomes a naked position.
> 4. Reconciliation of overnight state at startup → a position
>    discovered at startup whose `opened_at_utc` is in an earlier
>    trading day is an O-003 breach that happened while the system was
>    down. It halts, and it does not clear itself.

This item builds §1, §3a and §4 for real. §2 and §3b are deliberately
deferred — see §3.

## 2. The mechanism

### 2.1 Why a dedicated table pair, not a nullable `capsule_id`

Confirmed by direct schema read: `execution_requests.capsule_id` is
`ForeignKey(..., nullable=False)`, and `execution_events.order_request_id`
FKs into it, also `nullable=False`. A flatten has no `DecisionCapsule` and
no `TradeIntent` behind it — it is policy-driven (a deadline plus observed
exposure), not proposal-driven. Three alternatives were considered and
rejected:

- **Make the FK nullable** — weakens an invariant every existing row
  relies on, and would let a flatten's events pollute
  `ExecutionEventStore.count_events_since(SUBMISSION_STARTED, ...)`,
  FINAL Risk's durable order-frequency budget. A flatten is not a new
  order and must not consume it.
- **Fabricate a placeholder capsule/intent/decision chain** — evidence
  fabrication in the one table whose whole purpose is auditable
  provenance. Not acceptable at any size.
- **A dedicated `flatten_requests`/`flatten_events` pair** (chosen) —
  structurally parallel to `execution_requests`/`execution_events`
  (same immutable-request-plus-append-only-events shape, same
  claim-is-the-insert pattern, same content-conflict hardening from item
  4), but with no capsule/intent FK at all. The absence is the honest
  schema-level statement that a flatten has no proposal.

New Alembic migration off head `d4b6e2f81a37` (confirmed via `alembic
heads` before creating it, and confirmed with Dev 2 that no migration
was in flight, per instruction §8's traffic-control rule).

**Keyed on the trading day, not the observed book — deliberately finer than
the policy occurrence since ADR-012 (D1.5).** The idempotency
key is `(environment, canonical_symbol, trading_day)` — one flatten
commitment per trading day per symbol, ever. Since owner risk policy v1's
weekly session redesign (`review/adr/ADR-012-owner-session-policy-v1.md`
§2.5), the *policy* occurrence this key protects is weekly, not daily —
kept at trading-day granularity anyway, because a weekend-spanning breach
that survives Friday deserves its own fresh Monday-dated commitment record
(fresh evidence the breach is *still* unresolved) rather than being folded
into a resolved-or-blocked Friday one, and it needs no schema change.
Keying on the observed
position book would mint a new key every time a position's volume changed
between passes, which is a resubmission mechanism ADR-003 §6 forbids. The
request's `fingerprint` covers the *policy* (offsets, deadline), so an
edited policy mid-day is caught as a real conflict; the observed book goes
in the *event* payload instead — mirroring how `SUBMISSION_STARTED`
carries the complete `ApprovedOrder` while `execution_requests.fingerprint`
carries the approval chain.

### 2.2 Why `FLATTEN_SUBMISSION_STARTED`, not `CLOSED`/`RECONCILED`/`SUBMISSION_STARTED`

A **separate** `FlattenEventType` enum, not new members on
`ExecutionEventType` — different tables, different identity spaces;
sharing one vocabulary would let a flatten event carry an order-only name
or vice versa, silently.

The commitment event itself is `FLATTEN_SUBMISSION_STARTED`, not:

- **`CLOSED`** (reserved for M5) — answers "this position's lifecycle
  ended", post-fill closure, item 8's territory. ADR-008 §2 already set
  this precedent for `RECONCILED`; the same reasoning applies verbatim.
- **`RECONCILED`** — item 8's, for the same reason.
- **`SUBMISSION_STARTED`** — two concrete reasons, not a naming
  preference: (1) `agent_gateway::ProposalWithdrawal` treats
  `SUBMISSION_STARTED` as the withdrawal-cutoff boundary for an
  agent-proposed order; a policy-driven flatten is not a proposal and
  must never be agent-withdrawable. (2) `ExecutionEventStore
  .count_events_since(SUBMISSION_STARTED, ...)` is FINAL Risk's durable
  order-frequency budget; a flatten is not a new order and must not
  consume it — the same reasoning as §2.1's table-separation argument,
  one layer up.

### 2.3 Why the reconciliation leg requires "not `UNKNOWN`", never "`MATCHED`"

The subtlest decision in this item, stated loudly here and in
`risk/flatten_gate.py`'s own module docstring. The only expectation this
platform can currently form is `ExpectedState.flat()` — every observed
open position reports as *"unexpected open position"*. A flatten is
*triggered by* an open position, so reconciliation against `flat()` is
**always** `MISMATCHED` at the exact moment a flatten is needed. Requiring
`MATCHED` — the naive copy of `submission_gate.py`'s own leg — would make
the flatten gate unconditionally closed exactly when a flatten is needed.

ADR-004 §5.3's real safety property is *observability* — "flattening what
you cannot see is how a hedge becomes a naked position" — not agreement.
`ReconciliationStatus.UNKNOWN` is precisely the codified "cannot see" (a
missing, stale, or incomplete snapshot); `MISMATCHED`-because-a-position-
genuinely-exists is the expected, informative state and must not close
this gate. `tests/unit/test_flatten_gate.py
::test_a_mismatched_reconciliation_does_not_close_the_gate` exists
specifically so a future reader who notices the asymmetry with
`submission_gate.py` and "fixes" it gets a red test and a pointer to this
reasoning, not a silent regression.

### 2.4 Why an `OVERNIGHT_EXPOSURE`-only halt does not close the gate

The existing detection path (`risk/policies.py::_overnight_breach`, both
`_check_session_boundary` methods) already trips `OVERNIGHT_EXPOSURE` on
the identical condition this item exists to resolve. A plain "not halted"
leg would make the flatten gate permanently closed by the very condition
it is meant to answer — becoming flat is the *safe resolution* of an
overnight-exposure halt, not a further risk to refuse.

`risk/flatten_gate.py::evaluate_flatten_gate` therefore contributes
`SYSTEM_HALTED` only when `kill_switch.active_reasons -
{OVERNIGHT_EXPOSURE}` is non-empty. A halt for any *other* reason
(drawdown, reconciliation mismatch, manual, MT5 connection failure) still
closes the gate — those say the platform's picture of the world is
untrustworthy, exactly ADR-004 §5.3's warning, and it applies to a
flatten exactly as it does to any other action.

`ExecutionOrchestrator._trip_overnight_exposure()` is called *after* the
gate decision on every path, so a halt this very pass causes can never be
the halt that tolerates itself on this same pass — the tolerance only
ever applies starting the *next* pass, once the halt is already durably on
record.

### 2.5 Why `FlattenInstruction`, not `ApprovedOrder`

`ApprovedOrder` cannot express a close: it raises on `Side.FLAT` ("FLAT is
a close instruction") and requires `intent_id`/`intent_risk_decision_id`/
`supervisor_decision_id`/`stop_loss_price`/`expires_at_utc` — none of
which a policy-driven close has an honest value for, since there was no
proposal behind it. `FlattenInstruction` (`domain/models.py`) is the
flatten's own contract: no stop loss (a close does not need one), no
expiry (the deadline has already passed), `volume` always the broker's
own reported position size — never risk-sized, the single largest
semantic difference from an entry order.

`FlattenPlan` wraps one `FlattenInstruction` per targeted position under
one occurrence. Per-position instructions under a per-book request is
deliberate: ADR-004 §7 defers "per-position vs per-book deadline, once
several instruments exist" as an open owner question, and this shape
keeps both answers reachable without a future schema change.

### 2.6 Why not `OperatorControls.flatten_positions()`

ADR-004 §5.1 requires the automatic flatten to be "a fourth, policy-driven
action" that "must not be implemented by reusing the operator's button."
`risk/operator_controls.py`'s own module docstring: *"None of them can be
invoked by the system on its own behalf."* `application/execution.py`
never imports `operator_controls` — asserted mechanically by
`tests/integration/test_execution_flatten.py
::test_the_operator_flatten_control_is_never_reached`.

### 2.7 The driver: `ExecutionOrchestrator.flatten_once()`

A new public method, called from the top of `run_once()`, independent of
the capsule loop — a flatten must fire when there is exposure, regardless
of whether any capsule exists (`run_once()` otherwise does nothing when
`CapsuleStore.read_all()` returns empty). Its own outcome type
(`FlattenAttemptOutcome`) is deliberately not merged into `run_once()`'s
return tuple: a flatten has no `capsule_id`, and every existing assertion
on that return value stays untouched.

**Durable state is checked before any broker read** — the same order
ADR-008 already established for `_recover_ambiguous_submission` ("query
durable request state -> reconcile broker state"). Today's flatten
occurrence's identity (`environment`, `canonical_symbol`, `trading_day`)
is fully determined by the clock and config alone, with no broker read
needed to derive it. A pass that already reached a terminal outcome for
it — `FLATTEN_GATE_BLOCKED` or `FLATTEN_OUTCOME_RESOLVED` — returns
immediately without touching the broker at all. Only a still-open
`FLATTEN_SUBMISSION_STARTED` commitment, or no occurrence claimed yet,
reads the broker.

`intraday.enabled=False` (every existing test's `platform_config()`, but
**not** `config/paper.yaml`, which has always shipped `enabled: true` —
this ADR overstated that at the time it was written; corrected in
`review/adr/ADR-012-owner-session-policy-v1.md` §2/§4) short-circuits
before any of this — which is what keeps this item provably inert for the
pre-existing, capsule-focused test suite: confirmed by re-running
`test_execution_orchestrator.py`'s full 14-test file unchanged before and
after this item, identical result both times.

`_resolve_flatten_outcome()` is the item-6-shaped idempotent recovery:
runs only when the occurrence's last durable event is
`FLATTEN_SUBMISSION_STARTED`. Unlike `_recover_ambiguous_submission`
(which searches broker positions by `mt5_magic_number`), this reads the
target tickets recorded in the commitment event's own payload and checks
which are still open — simpler and more direct, since the targets were
already named by ticket. Idempotent by construction: once
`FLATTEN_OUTCOME_RESOLVED` is appended, the next pass's durable-state
check returns immediately, with no broker read.

## 3. What this does not do — and scope against ADR-004 §5, explicitly

**Historical note, true as of this ADR's original writing, no longer true
today — see `review/adr/ADR-020-real-flatten-close.md` (Phase B item B5).**
At the time this ADR was written, `close_all_positions`/`order_send` were
completely unbuilt and unreachable, and `_resolve_flatten_outcome()`
provably always concluded every target was still open
(`flattened=False`) — the same "real mechanism, structurally inert until
its caller exists" discipline every prior item on this list (2, 3, 5, 6)
used. B5 built the real per-ticket close: `OrderCheckMt5Gateway`'s two
methods are still the same unconditional raises, but `DemoOrderSendMt5Gateway`
(a separate, real, still-unwired-for-entries adapter) now has a real
`close_position`/`close_all_positions`, and `_resolve_flatten_outcome()`'s
successor (`_attempt_and_resolve_flatten()`) can genuinely reach
`flattened=True` — only when a real `FlattenCloseSink` is explicitly
constructed and injected and `flatten_submission_enabled` reads `True`,
still false in every shipped config today.

**Nothing here clears, downgrades, or shortens a halt.** A halt this item
causes stays in force exactly as any other halt does — until an operator
resets it.

| ADR-004 §5 item | This slice | Why |
|---|---|---|
| 1. Automatic flatten, distinct from the manual control | **In scope**, up to the commitment point. **The close itself: done — Phase B item B5, `review/adr/ADR-020-real-flatten-close.md`** | Structurally distinct: separate module, tables, event enum, config flag; §2.6. B5 adds the real per-ticket close behind the same `flatten_submission_enabled` flag |
| 2. Retry-then-HALT on a failed flatten | **Done — Phase B item B5** | Real closes now exist, so real retry logic has something genuine to retry against. `FLATTEN_OUTCOME_RESOLVED`'s durable state machine (this ADR) is exactly what B5's retry-then-HALT attaches to — no redesign needed |
| 3a. Reconcile before flattening | **In scope** | Gate legs — §2.3 |
| 3b. HALT before the deadline if the broker is unavailable | **Deferred — D-050** | Needs a periodic pre-deadline connectivity watch, a different kind of mechanism belonging near `LiveReader`'s cadence, not a gate evaluated at the deadline. What ships: unavailable-at-the-deadline still closes the gate (`POSITION_BOOK_INCOMPLETE`) and halts — the "after it" half. Still not built as of B5 |
| 4. Startup reconciliation of overnight state | **In scope** | `has_crossed_rollover` fires on the very first pass of a fresh process regardless of history; `KillSwitch` already guarantees no auto-clear |

ADR-004 §7's two open owner questions (per-position vs per-book deadline,
a longer Friday cutoff) are untouched and stay open — this item's shape
keeps both reachable without forcing an answer now.

## 4. Consequences

- `review/DEVIATIONS.md` D-033 moves to `PARTIALLY RESOLVED`; new D-050
  records the three explicitly deferred pieces (the close itself, §5.2's
  retry, §5.3b's pre-deadline connectivity watch).
- `review/FEEDBACK.md`'s "core submission-safety phase" tracking updates
  — item 7 done, two remain (post-fill reconciliation, broker-side SL
  verification).
- Item 8 (post-fill reconciliation) may consume `FLATTEN_SUBMISSION_STARTED`'s
  payload shape if it needs to distinguish a flatten-driven close from an
  entry-driven one; it does not currently need to.
- A new Alembic head; Dev 2 notified in advance per instruction §8, and
  the `INTEGRATION_NOTICES.md` entries record the head and the additive
  domain-model change.
