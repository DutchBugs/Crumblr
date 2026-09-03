# ADR-015 — Ambiguous-outcome recovery must fail closed on >1 matching positions (Phase B item B4)

**Status:** ACCEPTED — implemented and tested; `order_send`/
`close_all_positions` still unbuilt
**Date:** 2026-09-03
**Drivers:** Owner/reviewer coordination order
`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`, Phase B item B4
**Supersedes:** nothing. Hardens core critical path item 6's own
`_recover_ambiguous_submission` (review 1.20 §10 / review 1.21 §12)
before the Phase B execution slices that will make it live.
**Implementation:** `src/crumblr/application/execution.py`,
`src/crumblr/application/expected_state.py`, `src/crumblr/domain/enums.py`,
`src/crumblr/risk/flatten_gate.py`

---

## 1. The decision being recorded

The owner's own wording: *"Current magic lookup treats `len(matches) > 0`
as 'submitted'. Before real submission, explicitly handle: 0 matching
positions -> no observed position, record the safe determination; 1
matching position -> attributed candidate, continue reconciliation; >1
matching positions -> integrity ambiguity; fail closed / HALT, never
silently accept."*

This is slice 1 of Phase B — deliberately the smallest, most independent
piece, chosen first because (unlike every other Phase B item) it hardens
a code path that is **already live** today, not one gated behind new,
still-to-be-built machinery.

## 2. The mechanism

### 2.1 What was already correct, and what was missing

`_recover_ambiguous_submission()` runs whenever a claimed request's last
durable event is `SUBMISSION_STARTED` with nothing after it — the one
state a process crash between that commitment and a real broker response
could leave behind (item 6's own design). It reads broker positions by
this platform's own computed `magic` number and durably records what it
found via `AMBIGUOUS_OUTCOME_RESOLVED`.

The 0-match and 1-match cases were, and remain, correct: zero matches
means "not submitted, zero exposure"; exactly one match means "the
attributed candidate, continue reconciliation." What was missing: `>1`
matches was folded into the same `len(matches) > 0` branch as exactly
one match, durably recording `submitted=True` with **every** matching
ticket attributed to the one request. A single MARKET order this
platform ever submits can produce at most one resulting position — no
retry logic exists that could legitimately produce two — so two or more
positions sharing one magic number is never a legitimate outcome; it
signals a magic-number collision or corrupted broker/platform state.
Silently attributing all of them to one request would have let
`reconcile_once()`'s `RECONCILED` loop and `verify_protective_stops()`
(item 9) reason about tickets that may not actually belong to this
request at all — exactly the "silently accept" failure mode `build.md`
§20's own stated default forbids: *"No new exposure. Reconcile first."*

### 2.2 The fix

`_recover_ambiguous_submission()` now branches on `len(matches) > 1`
**before** computing `submitted`:

```python
if len(matches) > 1:
    self._append(
        order_request_id, ExecutionEventType.AMBIGUOUS_OUTCOME_RESOLVED, now,
        payload={
            "magic_number": magic,
            "integrity_ambiguity": True,
            "matching_position_count": len(matches),
            "matching_tickets": [p.ticket for p in matches],
        },
    )
    self._trip_submission_integrity_ambiguous(order_request_id, matches, now)
    return ExecutionAttemptOutcome(...)

submitted = len(matches) == 1
# ... existing 0-match/1-match payload/append/return, unchanged
```

The `>1` payload deliberately carries **no `submitted` key at all** —
distinct from a genuinely malformed payload (missing/absent field by
accident), not reusing that wording. `_trip_submission_integrity_ambiguous()`
mirrors `_trip_overnight_exposure()`/`_trip_protective_stop_issue()`'s
exact idempotent-trip shape (`if not self._kill_switch.is_halted:`,
`tripped_by="ambiguous_recovery_driver"`), escalating via a new
`ReasonCode.SUBMISSION_INTEGRITY_AMBIGUOUS`.

`application/expected_state.py::derive_expected_exposure()` gains a new
check, ahead of the existing "missing or malformed" branch, that
recognizes `payload.get("integrity_ambiguity")` and records a distinct,
honest reason (`"... cannot safely attribute any of them to this
request (integrity ambiguity, not a malformed payload)"`) — the request
is left out of `tickets_by_request`/`determined_request_ids`, the same
shape every other undetermined branch already uses. This makes
`ExpectedState.undetermined_reasons` non-empty, so `reconcile()` returns
`UNKNOWN` (never `MATCHED`) — a second, independent, already-existing
line of defense against any *new* submission for as long as the
ambiguity stands, on top of the kill-switch HALT itself.

### 2.3 Same D-051 gap 3 / build.md §8.2 alignment as item 9

`SUBMISSION_INTEGRITY_AMBIGUOUS` is a dedicated, narrowly-scoped halt
producer, exactly like `PROTECTIVE_STOP_MISSING`/`PROTECTIVE_STOP_MISMATCH`
(item 9, ADR-014) — it does not touch `reconcile()`'s own generic
MATCHED/MISMATCHED verdict or `SupervisorConfig
.halt_on_reconciliation_mismatch` (still unconsumed, `review/DEVIATIONS.md`
D-051 gap 3, unchanged by this slice). `build.md` §8.2 already lists
"reconciliation mismatch" as an Automatic HALT trigger and §20 states the
"no new exposure, reconcile first" default for ambiguous situations —
this fulfils both for this one specific, well-defined case. No new
deviation from `build.md` is introduced.

### 2.4 Flatten tolerance

`risk/flatten_gate.py::_TOLERATED_HALT_REASONS` gains
`SUBMISSION_INTEGRITY_AMBIGUOUS`, joining `PROTECTIVE_STOP_MISSING`/
`PROTECTIVE_STOP_MISMATCH`/`OVERNIGHT_EXPOSURE`/`FLATTEN_STATE_UNKNOWN`:
flattening closes whatever the broker actually reports, regardless of
which request a position is or isn't attributable to, so becoming flat
is still the safe resolution even when attribution itself is in doubt —
excluding it would brick the one remediation path once Phase B item B5
makes real per-ticket closing possible.

## 3. A note on "provably inert," precisely

Unlike items 6-9/D1.5 (provably inert in their *entirety* — nothing in
those slices could ever fire today), this slice touches a method whose
0-match branch is **already live**: every request that reaches
`SUBMISSION_STARTED` today is durably resolved as `submitted=False` on
the very next pass, since `order_send` never runs and a real MT5
position bearing this platform's magic number can never exist. This
slice does not change that existing, already-exercised behaviour —
verified directly by running the pre-existing 0-match/1-match tests
unchanged. Only the new `>1`-match branch is provably unreachable today,
for the same structural reason (`OrderCheckMt5Gateway.order_send`
unconditionally raises), proven directly by
`tests/integration/test_execution_orchestrator.py::TestEndToEnd
::test_order_send_and_close_all_positions_are_never_called` (unchanged)
and by the fact that no code path anywhere can cause `FakeMt5.open_positions`
to reflect a real submission outside of a test explicitly constructing
that fixture by hand.

## 4. What this does not do

- Does not touch the 0-match or 1-match payload shapes or behaviour.
- Does not build B1/B2/B3/B5/B6/B7/B8 (the rest of Phase B) — separate,
  later slices.
- Does not add pending-order ambiguity handling — `magic` is not tracked
  for pending orders anywhere today (existing, named gap in the
  method's own docstring); first canary is MARKET-only per the work
  order itself.
- No `order_send`/`close_all_positions`/`feedback_2_0_approved` change.

## 5. Consequences

- No new `review/DEVIATIONS.md` entry — this implements an owner
  requirement directly and aligns with, rather than departs from,
  `build.md` §8.2/§20 (see §2.3 above).
- `status.md` records this as a Dev-1 Phase-B, slice-1 deliverable — no
  new O-number needed, same reasoning as ADR-013/ADR-014.
- No `review/INTEGRATION_NOTICES.md` entry: no cross-track call-site
  signature changes.
