# ADR-008 — Ambiguous-outcome recovery (core critical path item 6)

**Status:** ACCEPTED — implemented and tested; `order_send` still unbuilt
**Date:** 2026-09-01
**Drivers:** review 1.20 §10, review 1.21 §12, ADR-003 §6, review
1.25/1.26 §6/1.27 §8 (all list "ambiguous-outcome recovery" as Dev-1
core critical path item 6)
**Supersedes:** nothing.
**Implementation:**
`src/crumblr/application/execution.py::ExecutionOrchestrator._recover_ambiguous_submission()`,
`src/crumblr/domain/enums.py::ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED`,
`tests/integration/test_execution_orchestrator.py::TestEndToEnd
::test_a_stalled_submission_is_recovered_not_reprocessed`,
`::test_a_matching_broker_position_resolves_as_submitted`

---

## 1. The decision being recorded

Reviews 1.20 §10 / 1.21 §12 give the exact required procedure:
"timeout / crash / lost response → DO NOT blindly resubmit → query
durable request state → reconcile broker state → determine whether the
request already took effect." ADR-003 §6: "An ambiguous outcome
resolves to reconcile, never to retry the action." ADR-007 (item 5)
named this item as its own direct successor: "item 6, whenever it is
built, should call `mt5_magic_number()` directly."

**The concrete gap this closes**: once an `order_request_id` is
claimed, every later `run_once()` pass for it returned `None`
unconditionally (`application/execution.py`, pre-existing), regardless
of how far the first pass got. A process crash in the narrow window
between `SUBMISSION_STARTED` committing to PostgreSQL (item 3) and the
run finishing left that request permanently, silently stuck — never
revisited, never resolved. This was not hypothetical: it was the
codified, tested behaviour
(`test_a_second_run_once_does_not_reprocess_an_already_claimed_capsule`),
confirmed by direct read before this change, and remains unaffected by
it — that test's capsule never reaches `SUBMISSION_STARTED`, so the new
recovery check falls through to the same `None` it already asserted.

`SUBMISSION_STARTED` is the only event that can be "the last thing that
happened" to a claimed request without the request having reached a
real terminal outcome — every other path (`INELIGIBLE`/`GATE_CLOSED`/
`RECONCILIATION_BLOCKED`/`FINAL_RISK_BLOCKED`/`ORDER_CHECK_REJECTED`/
`SUBMISSION_GATE_BLOCKED`) is already a genuine terminal refusal, not an
ambiguity. So "was `SUBMISSION_STARTED` the last durable event for this
request" is a complete, sufficient detector on its own — no time-based
staleness or timeout machinery is needed alongside it.

## 2. The mechanism

`ExecutionOrchestrator._recover_ambiguous_submission()` runs exactly
when the request-claim gate reports an already-claimed request
(`claim.claimed is False`) — previously a bare `return None`, now a
call into this method instead. It reads the request's full durable
event history (`ExecutionEventStore.events_for()`); if the last event
is anything other than `SUBMISSION_STARTED`, it returns `None`
immediately — already resolved, or never reached that state, nothing to
recover.

Otherwise it derives `mt5_magic_number(order_request_id)` — reusing
item 5's function directly, exactly as ADR-007 anticipated, so both the
original submission-time payload and this recovery pass compute the
identical number independently, nothing persisted separately to keep
in sync — and searches `self._adapter.positions()` (already a real,
reachable read; `OrderCheckMt5Gateway.positions()` delegates to the
reader, no new MT5 capability grant) for a position whose `magic`
matches. The determination (`submitted: bool`, match count, matching
ticket numbers, the searched magic number) is appended durably as a new
event, `AMBIGUOUS_OUTCOME_RESOLVED`.

**Idempotent by construction.** Once this event is appended,
`events[-1]` is no longer `SUBMISSION_STARTED` on the next pass, so
recovery never re-runs and never re-reads the broker for an
already-resolved request — verified in
`test_a_stalled_submission_is_recovered_not_reprocessed` via a call
counter on the fake broker's `positions_get`, not only on final state.
Content-conflict hardening (item 4) applies automatically through the
existing `_append()` path — nothing new to wire.

### Why a new event type, not `RECONCILED`

`RECONCILED` stays reserved for its original M5 purpose — post-fill
reconciliation (core critical path item 8), confirming a *known* fill
against expected state. This item answers a different question:
whether an *unclear* submission happened at all. Reusing `RECONCILED`
here would collide with item 8's own later need for it, and would make
the event log ambiguous about which of two different procedures
produced a given entry.

### Why "last event is `SUBMISSION_STARTED`", not a staleness/timeout check

A crash can happen an arbitrary amount of time before this method runs
— the platform has no way to know when, and no reason to wait before
checking. The detector is structural (what state was left behind), not
temporal (how long it has been left behind). This also keeps the
mechanism deterministic and trivially testable: two `run_once()` calls
in immediate succession already exercise the real trigger condition,
with no clock manipulation required.

### Scope: open positions only, not pending orders

Confirmed by research before implementation: `magic` is not tracked at
any layer for pending orders in this codebase today —
`PendingOrderState`, `BrokerPendingOrderSnapshot`, and the schema behind
`ReadOnlyMt5Gateway.pending_orders()` carry no such field, unlike
`positions()`, which is magic-aware end to end. A submitted
`EntryType.LIMIT` order sitting pending, not yet filled, would not be
found by this check.

This is named here deliberately, not silently absorbed into "recovery
works." Extending pending-order tracking to carry `magic` is real,
separate infrastructure work comparable in size to item 5 itself, and
is out of scope for this item. See `review/DEVIATIONS.md` D-049 for the
formal record of this gap.

## 3. What this does not do

**`order_send` remains completely unbuilt and unreachable.**
`OrderCheckMt5Gateway.order_send` is still the same unconditional raise
(`ExecutionDisabledError`); the `Mt5Module` Protocol still excludes it
from its surface. Nothing in `_recover_ambiguous_submission()` calls or
approaches it, and nothing in this method decides to attempt a
resubmission on any outcome — ADR-003 §6's "never to retry the action"
holds exactly as stated. Because no code path in this platform can
today produce a real `order_send` call, recovery will — provably, in
every real case this platform can currently produce — always conclude
`submitted=False`. That is not a weakness of this item; it is the same
"real mechanism, structurally inert until its caller exists" discipline
every prior item on this list has used, and it is what makes this
mechanism safe to ship ahead of `order_send` itself.

**No automatic action is taken on a `submitted=True` result.** Recording
the determination durably is this item's whole job. Deciding what an
operator, or a later automated step, does with that determination is
out of scope — consistent with "resolves to reconcile, never to retry
the action."

**No pending-order/magic support** — named above and in
`review/DEVIATIONS.md` D-048, not built here.

## 4. Consequences

- `review/FEEDBACK.md`'s "core submission-safety phase" tracking
  updates — item 6 done, three items remain (automatic flatten
  submission, post-fill reconciliation, broker-side SL verification).
- Every claimed request that crashes between `SUBMISSION_STARTED` and a
  real terminal event is now recovered on the very next `run_once()`
  pass, rather than staying silently stuck forever.
- `review/DEVIATIONS.md` gains D-049, recording the pending-order/magic
  scope gap as a real, acknowledged boundary.
- Item 7 (automatic flatten submission) and item 8 (post-fill
  reconciliation) can build on `AMBIGUOUS_OUTCOME_RESOLVED`'s payload
  shape if they need to distinguish "this request was never ambiguous"
  from "this request was ambiguous and resolved to not-submitted" —
  neither currently does, but the event is available for either to
  consume.
