# ADR-020 — Real per-ticket close/flatten (Phase B item B5)

**Status:** ACCEPTED — implemented and tested; unreachable in every shipped
config (`flatten_submission_enabled=False`) and with no adapter constructed
by any real caller
**Date:** 2026-09-04
**Drivers:** Owner/reviewer coordination order
`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`, Phase B item B5: *"A
system that can open but cannot reliably close is not canary-ready."*
Required: policy-driven Friday flatten, operator-requested flatten,
retry/recovery semantics for an ambiguous close, confirmation from fresh
broker state, durable outcome/audit.
**Supersedes:** nothing. Extends ADR-009 (automatic flatten submission,
core critical path item 7), which explicitly deferred "the close itself"
and "retry-then-HALT on a failed flatten" as D-050.
**Implementation:** `src/crumblr/mt5_gateway/execution.py`
(`build_close_order_request`), `src/crumblr/mt5_gateway/demo_execution.py`
(`close_position`, real `close_all_positions`), `src/crumblr/application
/execution_outcome.py` (`close_result_fully_closed`), `src/crumblr
/application/execution.py` (`FlattenCloseSink`, `_attempt_and_resolve_flatten`,
`_trip_flatten_close_failed`), `src/crumblr/risk/flatten_gate.py`
(`FLATTEN_CLOSE_FAILED` joins `_TOLERATED_HALT_REASONS`),
`src/crumblr/domain/enums.py` (`ReasonCode.FLATTEN_CLOSE_FAILED`),
`src/crumblr/domain/models.py` (`ExecutionResult.intent_id` widened to
`UUID | None`)

---

## 1. The decision being recorded

Before this item, `ExecutionOrchestrator.flatten_once()` could detect a
required flatten, gate it, and durably commit to one
(`FLATTEN_SUBMISSION_STARTED`, carrying a `FlattenPlan` of per-ticket
`FlattenInstruction`s) — but nothing ever closed anything.
`_resolve_flatten_outcome()`'s own pre-existing docstring said so
explicitly: *"Because `close_all_positions` stays unreachable, this will
provably always conclude every target is still open."* This item builds
the real mechanism behind that same already-existing, already-false
`flatten_submission_enabled` flag — the eleventh of `risk/flatten_gate.py`'s
eleven conditions — exactly the "build the real, tested capability ahead
of its own activation" discipline every prior Phase-B slice used.

Separately, `risk/operator_controls.py::OperatorControls.flatten_positions()`
(build.md §8.2, F-008) already called `self._broker.close_all_positions(reason=...)`
— fully wired end to end, waiting only on a real implementation.
`OperatorControls` itself is constructed nowhere in `src/`/`scripts/`
(confirmed by grep before this item began), so making that call real does
not make the operator control live-reachable — the same "real but
unconnected" discipline `DemoOrderSendMt5Gateway.order_send` already
established.

## 2. `close_position` is a new, separate capability — not a widened `close_all_positions`

MT5 has no dedicated "close" API call; a close *is* an opposite-side
`order_send` that additionally names the target `position` (ticket)
explicitly. `build_close_order_request()` mirrors
`build_market_order_request()`'s shape but always sets
`"position": instruction.ticket` — on a hedging account this is what
tells the broker which specific ticket to act on. The B5 work order's own
requirement — *"must target the existing ticket and must not accidentally
open an opposite hedge/new position"* — is exactly what this field
prevents: closing by symbol/side alone would leave the broker to net or
open ambiguously against whichever position(s) happen to exist on that
symbol.

`DemoOrderSendMt5Gateway.close_position(instruction: FlattenInstruction)`
reuses `order_send`'s exact demo-only guard (`self.account()` first,
unconditionally) and response-decoding shape — factored into one shared
`_decode_order_send_result()` rather than duplicated, since both are the
same underlying MT5 call differing only in the request dict's content.
`close_all_positions(*, reason)` (build.md §8.2's own contract) is built
on top of `close_position()`: reads `positions()` fresh, closes each one
independently (one ticket's failure never blocks the rest), returns the
tickets that actually closed. This single change makes
`OperatorControls.flatten_positions()` real with zero edits to
`operator_controls.py` itself.

`cancel_pending_orders` stays refused: no pending-order support exists
anywhere in this platform (MARKET-only canary, the same boundary
`_recover_ambiguous_submission`'s own docstring already names), so there
is nothing for it to honestly act on.

## 3. `ExecutionResult.intent_id` widens to `UUID | None`

A close has no `TradeIntent` behind it (the same reasoning
`FlattenInstruction`'s own docstring already gives for why it is not
`ApprovedOrder`) — `close_position()` has no honest, non-fabricated value
to supply. Confirmed by grep before widening: no code anywhere reads
`ExecutionResult.intent_id` back out (every existing reference is a
*construction*-site `intent_id=intent.intent_id` on unrelated models), so
this is a safe, additive nullable-widening — the same kind of change
already made once this session for `PortfolioState.open_risk_fraction`
(owner risk policy v1, D1.4). `ExecutionResult.order_request_id` — also
`UUID`, non-nullable — carries `instruction.flatten_request_id` for a
close: there is no order request behind it either, and the flatten
occurrence is the only identity a close attempt is genuinely scoped to.

## 4. The append-once persistence constraint shapes the retry design

`persistence/flatten.py::flatten_event_id_for()` derives purely from
`(flatten_request_id, event_type)` — each `FlattenEventType` can be
appended **at most once, ever**, per occurrence. A second, differently-
content append of the same type raises `FlattenEventConflictError`. This
rules out "append one event per retry attempt" and is why the design
below never appends a durable event for an *attempt* — only for a
genuinely terminal outcome.

## 5. `_commit_flatten` stays exactly as it was — the real close happens one pass later

The obvious-looking design — attempt the close inline, immediately after
appending `FLATTEN_SUBMISSION_STARTED`, in the same `_commit_flatten`
call — was tried first and reverted. It collapsed the commit pass and the
resolve pass into one call stack, which would have meant a real broker
write sharing a call stack with the gate decision that just authorized
it, and would have broken every existing `test_execution_flatten.py`
assertion that a fully-approved config's *first* `flatten_once()` call
stops at `FLATTEN_SUBMISSION_STARTED` (proven by
`fake.close_all_positions_calls == 0` / `fake.order_send_calls == 0`
immediately after that call, still true and unchanged by this item).

Instead: `_commit_flatten()` is **unmodified** — it still only appends
`FLATTEN_SUBMISSION_STARTED` and returns. The real close is attempted only
by `_attempt_and_resolve_flatten()`, reached exclusively through
`_resolve_flatten_outcome()` on the *next* `flatten_once()` pass (the
same recovery branch ADR-009 already built for the inert case) — a full
pass boundary always separates "durably commit" from "the one place a
real broker write can happen," mirroring the two-step shape
`_recover_ambiguous_submission` already established for entries (item 6).

## 6. `_attempt_and_resolve_flatten()` — attempt, re-observe, then decide

Given each position (`still_open`, cross-referenced against the freshly-
observed broker state already in hand):

1. If nothing is still open (already closed, by this attempt or
   externally, before this pass even started) — resolve `flattened=True`
   immediately, no broker write attempted.
2. If something is still open **and** a real `FlattenCloseSink` was
   explicitly constructed and injected (`self._flatten_close_adapter`,
   `None` in every existing caller/test) **and**
   `flatten_submission_enabled` reads `True` right now (re-checked fresh,
   not trusted from commit time — the same belt-and-suspenders discipline
   `order_send`/`close_position` already apply to the account guard):
   attempt `close_position()` for each still-open instruction, catching
   any exception per-ticket so one bad ticket never blocks the rest, then
   take **one fresh broker observation** and recompute `still_open` from
   *that* — never trusting the raw `order_send`-shaped response alone
   for the final verdict (the same "confirm from fresh broker state"
   discipline `_process()`'s own FINAL Risk read already uses, review
   1.22 F-058).
3. If that re-observation shows everything closed: append
   `FLATTEN_OUTCOME_RESOLVED(flattened=True, ...)` — terminal.
4. If a genuine attempt was made and a residual remains: trip
   `ReasonCode.FLATTEN_CLOSE_FAILED` (`_trip_flatten_close_failed`, the
   same idempotent-trip shape as `_trip_overnight_exposure`/
   `_trip_flatten_state_unknown`/`_trip_protective_stop_issue`) and
   **append nothing durable-terminal** — `FLATTEN_SUBMISSION_STARTED`
   stays the last event, so the *next* pass retries automatically, with a
   fresh observation, no artificial retry counter, and never a blind
   resubmission of an already-closed ticket (only currently-still-open
   targets are ever attempted). This is D-050's deferred "retry-then-HALT
   on a failed flatten," closed by this item.
5. If no attempt was possible (no adapter, or the flag reads false right
   now — every shipped config, today): append
   `FLATTEN_OUTCOME_RESOLVED(flattened=False, ...)` immediately — the
   exact terminal call this method's pre-B5 predecessor always made,
   unchanged, and still what every existing (adapter-less) integration
   test asserts.

`risk/flatten_gate.py::_TOLERATED_HALT_REASONS` gains
`FLATTEN_CLOSE_FAILED` for the identical reason `OVERNIGHT_EXPOSURE`/
`FLATTEN_STATE_UNKNOWN`/the two protective-stop reasons/
`SUBMISSION_INTEGRITY_AMBIGUOUS` are already there: becoming flat is the
safe resolution of this halt, not a further risk, so the mechanism must
be able to retry past a halt it caused itself.

**A real observed effect, not a bug:** in practice, `_trip_overnight_exposure`
(called unconditionally on every pass with open positions, *before* any
close is attempted — see that call site's own comment) will almost always
trip the kill switch's global `active_reasons` to `OVERNIGHT_EXPOSURE`
*first*, in the same pass, before `_trip_flatten_close_failed` ever runs —
`KillSwitch.trip()` is a no-op once already halted, so the more specific
reason rarely wins that slot. The specific reason is not lost: it is
always present on the `FlattenAttemptOutcome.reason_codes` this method
returns and the durable event history's own resolution — the global kill
switch's job is only "halt or not," not "every contributing reason,"
and this item does not change that.

## 7. `flatten_once()`'s overnight-exposure trip moves earlier

Both `flatten_once()` call sites that invoke resolve/commit used to call
`self._trip_overnight_exposure(positions, now)` *after* the resolve/commit
call, using the pre-attempt `positions` snapshot. Once a real close can
happen *inside* that call, that ordering would let a successful close
resolve every remaining position and then have the stale, pre-attempt
`positions` list immediately re-trip `OVERNIGHT_EXPOSURE` moments after a
clean resolution — misreporting exposure a close just fixed. Both call
sites now trip first, from the same pass's own fresh-before-any-attempt
read, then resolve/commit. `KillSwitch.trip()`'s idempotence means this
reordering changes nothing for the pre-B5 case where nothing ever closes.

## 8. What this does not do

- **Partial-volume close handling.** `close_result_fully_closed()`
  classifies `OrderState.PARTIALLY_FILLED` as *not* fully closed,
  deliberately conservative — the remaining volume stays open on the same
  ticket, and only the caller's own fresh re-observation (never this
  classification alone) makes the final call. A named, deliberate scope
  limit for the current single-ticket, full-volume canary shape, not a
  silently-absorbed gap.
- **The pre-deadline connectivity watch.** D-050's other deferred piece —
  a periodic pre-deadline connectivity watch belonging near `LiveReader`'s
  own cadence, not a gate evaluated at the deadline — is untouched by
  this item.
- **Wiring a real `FlattenCloseSink` into any script.** Exactly like
  `DemoOrderSendMt5Gateway` itself (Phase B item B1), nothing in `src/`
  or `scripts/` constructs one — this item is real, tested, and
  genuinely unreachable until a later slice wires it in, gated the whole
  time by `flatten_submission_enabled` staying false in every shipped
  config.
- **Entries.** `application/execution.py` never names
  `DemoOrderSendMt5Gateway` by name — `FlattenCloseSink` is a narrow
  structural Protocol instead — so
  `tests/unit/test_demo_order_send_gateway.py::TestNotWiredIntoTheOrchestrator`'s
  mechanical `inspect.getsource` proof is unchanged and still passes. A
  real entry `order_send` still needs Phase C/AG-012's shared execution/
  Risk authority, the account pin (B7, done) and the canary permit (B8,
  done) actually wired together — none of that is this item's scope.

## 9. Consequences

- No new migration — `flatten_events.payload` is already a free-form
  column; the new terminal payloads (`closed_tickets`, real `flattened`
  values) fit the existing shape.
- `review/DEVIATIONS.md` D-050 narrows to just the still-deferred
  connectivity watch (§8).
- `review/adr/ADR-009-automatic-flatten-submission.md` §3's scope table
  updates: item 1 ("the close itself") and item 2 ("retry-then-HALT") both
  move from deferred to in-scope, here.
- `review/INTEGRATION_NOTICES.md` gets one entry:
  `ReasonCode.FLATTEN_CLOSE_FAILED` is additive-only in `domain/enums.py`
  (shared-contract territory); `ExecutionResult.intent_id`'s nullable
  widening in `domain/models.py` is confirmed source-compatible (grep:
  nothing reads it back).
- This is the last unshipped Phase-B slice (B6 — removing the flat-book
  reconciliation assumption — is explicitly deferred by the owner work
  order until continuous-DEMO promotion, not part of the first canary).
  Phase C (external Supervisor + one serialized shared Risk authority,
  joint with Dev 2) is next.
