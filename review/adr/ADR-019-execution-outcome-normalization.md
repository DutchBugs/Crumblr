# ADR-019 — Normalize definite broker outcomes, narrowed to MARKET-only (Phase B item B3)

**Status:** ACCEPTED — implemented and tested; not called by
`ExecutionOrchestrator`
**Date:** 2026-09-03
**Drivers:** Owner/reviewer coordination order
`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`, Phase B item B3;
`build.md` §19's own `OrderState` machine (`SUBMITTED → ACKNOWLEDGED →
PARTIAL/FILLED/REJECTED → RECONCILED → CLOSED`), already implemented as
`domain/enums.py::OrderState` — this item builds the journal-vocabulary
counterpart for the states that had none yet.
**Supersedes:** nothing.
**Implementation:** `src/crumblr/domain/enums.py`
(`ExecutionEventType.REJECTED`), `src/crumblr/application
/expected_state.py`, `src/crumblr/application/execution_outcome.py`
(new)

---

## 1. The decision being recorded

B3's own six-outcome list: rejection; accepted/submitted; broker
acknowledgement; full fill; partial fill; transport exception/timeout/
ambiguous response. *"Update `derive_expected_exposure()` so any
newly-emitted real event has an honest exposure meaning... Never
fabricate `AMBIGUOUS_OUTCOME_RESOLVED` for an ordinary definite
success."*

Same scope discipline as every prior Phase B slice: this item builds a
real, tested, pure normalization function — nothing calls it, because
nothing in `ExecutionOrchestrator` calls `order_send` yet (Phase
C/AG-012 doesn't exist).

## 2. `ExecutionEventType` is a narrower vocabulary than `OrderState` by design

`ExecutionEventType`'s own module docstring already draws this
distinction: *"`OrderState` [build.md §19] names the full state
machine; this is the narrower event vocabulary for how a request got
there, or didn't."* `OrderState` already has `SUBMITTED`, `ACKNOWLEDGED`,
`PARTIALLY_FILLED`, `FILLED`, `REJECTED` as distinct states — but
`ExecutionEventType`'s own reserved-for-M5 set was only ever
`SUBMITTED`/`BROKER_ACK`/`FILLED`/`CLOSED` (no `REJECTED`, no separate
partial-fill member), and even the existing four are already a
narrowing (no `ACKNOWLEDGED`/`RECONCILED`-shaped duplication —
`RECONCILED` in this enum means something different, item 8's own
post-fill verdict, not the `OrderState` transition). This item narrows
further, deliberately, rather than mechanically mirroring `OrderState`
1:1 into the journal.

## 3. Why the six-outcome list narrows to two real event types

- **Full/partial fill → one `ExecutionEventType.FILLED`.** A MARKET IOC
  `order_send` response is a single synchronous retcode
  (`DemoOrderSendMt5Gateway.order_send()`, Phase B item B1, already
  decodes it to exactly `FILLED`/`PARTIALLY_FILLED`/`REJECTED`). Full
  vs. partial is a payload distinction (`requested_volume` vs.
  `executed_volume`), not a reason to duplicate the event type — the
  same narrowing choice already made for `ACKNOWLEDGED` above.
- **Rejection → new `ExecutionEventType.REJECTED`.** The one genuinely
  missing member. Deliberately **not** represented via
  `AMBIGUOUS_OUTCOME_RESOLVED{submitted=False}`, even though the
  exposure *meaning* is identical (zero exposure, no ticket): that
  event's own name and docstring claim a *post-hoc, broker-position-
  search* determination of an unclear outcome (items 6/B4) — a
  definite, synchronous rejection response was never ambiguous, and
  naming it that way would misdescribe what actually happened. This is
  the literal case B3's own "never fabricate `AMBIGUOUS_OUTCOME_RESOLVED`
  for an ordinary definite success" warns against, applied to rejection
  too, not just success.
- **`SUBMITTED`/`BROKER_ACK` stay reserved, not built.** A MARKET IOC
  order has no separate "acked, not yet filled" phase the way a pending
  LIMIT/STOP order would. Building emission logic for a phase that
  cannot occur in the MARKET-only first canary would be speculative —
  deferred until pending-order support exists, the same boundary
  `_recover_ambiguous_submission`'s own docstring already names (`magic`
  is not tracked for pending orders; first canary is MARKET-only).
- **Transport exception/timeout/ambiguous response needs no new code at
  all.** If `order_send` raises or times out, `normalize_execution_result`
  is simply never reached — `SUBMISSION_STARTED` stays the last durable
  event, exactly the state `_recover_ambiguous_submission()` (items
  6/B4) already exists to resolve via broker-position search. B2's own
  required order already says so: *"No automatic retry... once
  `SUBMISSION_STARTED` exists, uncertainty goes to broker-state
  recovery."* Nothing to build.

## 4. `FILLED`'s own exposure meaning is deliberately unchanged

`_EXPOSURE_BY_EVENT[FILLED]` stays `UNDETERMINED` — not a gap this item
leaves behind, but the honest state of affairs. `DemoOrderSendMt5Gateway
.order_send()` deliberately never sets `ExecutionResult
.mt5_position_ticket` (`review/adr/ADR-016-demo-order-send-adapter.md`
§2.5): the synchronous response cannot honestly claim to know which
resulting broker position is "the" one this request created.
Attributing a ticket remains the *existing* magic-number search's job
(`_recover_ambiguous_submission`) — this item does not widen that
method's trigger condition to also react to `FILLED`. That widening
touches the exact method Phase B item B4 just hardened this same
session, has no real caller to test it against today, and belongs with
the eventual wiring/AG-012 slice that actually needs it — not this one,
which stays purely additive and does not modify any existing
safety-critical recovery logic.

## 5. `REJECTED`'s exposure meaning is unconditionally zero

`_EXPOSURE_BY_EVENT[REJECTED] = DETERMINED`. `derive_expected_exposure()`
gains a direct branch mirroring the existing `AMBIGUOUS_OUTCOME_RESOLVED
{submitted=False}` case exactly — no ticket search, no stop-loss
tracking (there is no position to protect). Unlike `FILLED`, a
rejection carries no attribution ambiguity at all: no position was ever
created, so there is nothing further to determine.

## 6. What this does not do

- Does not call `normalize_execution_result()` from
  `application/execution.py` — verified directly by a structural
  `inspect.getsource` guard test, mirroring B1+B2's own idiom.
- Does not widen `_recover_ambiguous_submission()`'s trigger condition
  (§4).
- Does not build `SUBMITTED`/`BROKER_ACK` emission (§3).
- Does not build anything for transport exceptions/timeouts (§3) —
  already correctly handled.
- Does not touch B5, or Phase C/AG-012.

## 7. Consequences

- No new `review/DEVIATIONS.md` entry — `build.md` §19's own
  `OrderState` machine already anticipates `REJECTED` as a real state;
  this item builds its journal-vocabulary counterpart, narrowed for good
  reason (§2-3), not a departure from the spec.
- `execution_events.event_type` is a plain `String(64)` column
  (`persistence/schema.py`) — adding `ExecutionEventType.REJECTED`
  needed **no migration**.
- `status.md` records this as a Dev-1 Phase-B deliverable — no new
  O-number needed, same reasoning as every prior Phase-B ADR.
- No `review/INTEGRATION_NOTICES.md` entry — no cross-track call-site
  changes; `application/execution.py` is untouched.
