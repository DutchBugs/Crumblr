# ADR-014 — Broker-side stop-loss verification (core critical path item 9)

**Status:** ACCEPTED — implemented and tested; `order_send`/
`close_all_positions` still unbuilt
**Date:** 2026-09-03
**Drivers:** Owner Shared-Core work order 2026-09-03, item 4 (core
critical path item 9); `review/feedback.1.26.md:262`
**Supersedes:** nothing. Fulfils the future-owner reference
`ReasonCode.OPEN_RISK_UNKNOWN`'s own docstring names (`review/adr
/ADR-011-owner-risk-policy-v1.md`) and the "natural consumer" reference
`review/adr/ADR-010-post-fill-reconciliation.md:334-335` makes to
`tickets_by_request`.
**Implementation:** `src/crumblr/application/expected_state.py`,
`src/crumblr/application/reconciliation.py`,
`src/crumblr/application/execution.py`, `src/crumblr/domain/enums.py`,
`src/crumblr/risk/flatten_gate.py`

---

## 1. The decision being recorded

The owner's own wording: *"Broker truth moet aantonen dat een geopende
positie daadwerkelijk de verwachte beschermende SL heeft; ontbrekende/
afwijkende protection fail-closed; sluit aan op reconciliation en
ambiguous-outcome recovery."* The canonical original requirement
(`review/feedback.1.26.md:262`): *"Verify broker-side SL after a fill;
absence/mismatch fails closed and escalates."*

This is the last item on the owner's original execution-safety punch
list. `ReasonCode.OPEN_RISK_UNKNOWN`'s own docstring (D1.4, ADR-011)
already named item 9 as "the correctly-scoped future owner" of
escalating a position whose protective-stop geometry cannot be trusted;
this ADR is that escalation.

## 2. The mechanism

### 2.1 What already existed, reused without change

- `ApprovedOrder.stop_loss_price: Price` is a required field — every
  order this platform ever approves carries a real intended stop.
- `execution.py::_start_submission()` already persists the full
  `ApprovedOrder` (including `stop_loss_price`) as `SUBMISSION_STARTED`'s
  durable payload.
- `PositionState.stop_loss_price: Price | None` and
  `BrokerPositionSnapshot.stop_loss_price: Price | None` already exist
  and are already durably captured every broker-state pass; MT5's
  `sl=0.0` already decodes to `None` (`mt5_gateway/readonly.py`).
- `expected_state.py::DerivedExposure.tickets_by_request` (item 8) maps
  each determined, submitted request to its attributed tickets —
  exactly the input `ADR-010` names as item 9's natural consumer.
- `execution.py::reconcile_once()` already, in one pass, derives
  exposure, captures broker state once, and loops over each pending
  request whose attributed tickets are all currently open before
  appending `RECONCILED`. This is the hook point item 9 uses — the same
  observation, the same derived exposure, zero extra broker read.

### 2.2 The new derivation: what SL did the platform intend

`DerivedExposure` gains `expected_stop_loss_by_request: Mapping[UUID,
Decimal]`, populated by a new `_stop_loss_price_of()` helper in
`expected_state.py` that mirrors the existing `_entry_type_of()` idiom
exactly: scan a request's own event history for `SUBMISSION_STARTED`'s
payload and read `stop_loss_price` out of it
(`Decimal(str(payload["stop_loss_price"]))`).

A request absent from this mapping — rather than present with some
fallback — means its intended stop could not be established from
durable history. `ApprovedOrder.stop_loss_price` being a required field
means this should be unreachable in practice; it is handled fail-closed
anyway, not asserted away. Deliberately **not** folded into
`undetermined_reasons` (which feeds the whole-book MATCHED/MISMATCHED/
UNKNOWN verdict) — see §2.4.

### 2.3 The new comparison: `verify_protective_stops`

`application/reconciliation.py` gains a pure sibling function to the
existing `_position_mismatches()`, deliberately **not** feeding
`reconcile()` or `ExpectedState` at all:

```python
def verify_protective_stops(
    positions: tuple[PositionState, ...],
    *,
    attributed: frozenset[int],
    expected_stop_loss_price: Decimal | None,
) -> tuple[ProtectiveStopIssue, ...]:
```

For each ticket in `attributed` (every one of which is guaranteed to
have a matching `PositionState` by construction — `reconcile_once()`'s
own `attributed - open_tickets` check already skips a request otherwise):
`expected_stop_loss_price is None` or the observed
`PositionState.stop_loss_price is None` both report
`PROTECTIVE_STOP_MISSING`; a non-`None` mismatch reports
`PROTECTIVE_STOP_MISMATCH`. Exact `Decimal` equality, no tolerance —
matching this module's existing comparison style (canonical symbol,
ticket-set membership); the broker reports back exactly what this
platform's own sizing already respects to the instrument's tick size.
Every issue is collected, never short-circuited on the first.

### 2.4 The escalation is deliberately separate from `reconcile()`'s own verdict

`review/DEVIATIONS.md` D-051 gap 3 records that whether a *generic*
book-level `MISMATCHED` should itself halt is a deliberately deferred,
separate policy question — `config.SupervisorConfig
.halt_on_reconciliation_mismatch` remains unconsumed. Folding SL checks
into `_position_mismatches()`'s existing `reasons` (which feeds
`reconcile()`'s own MATCHED/MISMATCHED verdict) would have silently
resolved that deferred question as a side effect of this item. Instead:

- `domain/enums.py` gains `ReasonCode.PROTECTIVE_STOP_MISSING` and
  `PROTECTIVE_STOP_MISMATCH`, each documented as this item's own,
  narrowly-scoped escalation.
- `execution.py::reconcile_once()` calls `verify_protective_stops()`
  inline, right where the existing per-request `RECONCILED` loop already
  confirms `attributed` tickets are all open, and — if any issue is
  found — calls a new `_trip_protective_stop_issue()`, mirroring
  `_trip_overnight_exposure()`/`_trip_flatten_state_unknown()`'s exact
  idempotent-trip shape (`tripped_by="reconciliation_driver"`).
- The existing `RECONCILED` event is still appended exactly as before —
  the whole-book MATCHED/MISMATCHED semantics are untouched. Its payload
  gains one new field, `protective_stop_issues` (ticket, reason,
  expected, observed), for audit visibility — a dict payload addition,
  not a schema-breaking change.

`build.md` §8.2 already lists "reconciliation mismatch" as an Automatic
HALT trigger. Item 9 fulfils that trigger for this one specific,
well-defined case; the generic case remains exactly as deferred as
D-051 left it (amended in place, not superseded — see
`review/DEVIATIONS.md`). Item 9 therefore introduces **no new deviation**
from `build.md` — it narrows an existing one.

### 2.5 Flattening must still be able to resolve this halt

`risk/flatten_gate.py::_TOLERATED_HALT_REASONS` already tolerates
`OVERNIGHT_EXPOSURE`/`FLATTEN_STATE_UNKNOWN` — halts whose safe
resolution is becoming flat, not a further risk. The same reasoning
applies here even more directly: a position whose protection cannot be
trusted is exactly the position flattening exists to close. Both new
reason codes were added to `_TOLERATED_HALT_REASONS` for the same
purpose `OPEN_RISK_UNKNOWN`'s own docstring names — avoiding a
permanent, un-remediable brick the moment this halt trips. (`close_all
_positions` itself remains unbuilt, D-050, so no real flatten can
execute yet either way — this is forward-looking correctness, not
something exercised by real position closure today.)

### 2.6 Scope: Core only, structurally inert today

Confirmed by grep: `application/paper_lite.py` never references
`ExecutionOrchestrator`, `reconcile_once`, or
`AMBIGUOUS_OUTCOME_RESOLVED` — it is a fully separate execution stack.
Item 9 is scoped to Core's `ExecutionOrchestrator` only, with zero
PAPER_LITE interaction, matching items 6-8 and D1.5's own precedent.

Structural inertness: `tickets_by_request`/`determined_request_ids` are
only ever populated from a durable `AMBIGUOUS_OUTCOME_RESOLVED` with
`submitted=True`, which nothing can write today —
`OrderCheckMt5Gateway.order_send` unconditionally raises. Item 9 is
therefore provably inert today, same as items 6-8/D1.5 — proven directly
by `tests/integration/test_execution_reconciliation.py
::TestStillInert::test_protective_stop_verification_never_fires_via_the_ordinary_run_once_path`.

## 3. What this does not do

- Does not touch `_position_mismatches()`, `ExpectedState`, or
  `reconcile()`'s own MATCHED/MISMATCHED/UNKNOWN verdict.
- Does not wire `SupervisorConfig.halt_on_reconciliation_mismatch` —
  still unconsumed, still a separate future decision (D-051 gap 3,
  amended not resolved).
- Does not build the `OrderState` transition-validation state machine
  (D-051 gap 1) — still deferred, still only meaningful once transitions
  can be broker-driven.
- Does not touch PAPER_LITE.
- No tolerance/epsilon on the price comparison.
- No `order_send`/`close_all_positions`/`feedback_2_0_approved` change.

## 4. Consequences

- `review/DEVIATIONS.md` D-051 gap 3 amended in place (not superseded)
  to record the narrowed scope.
- `status.md` records this as a Dev-1 Shared-Core deliverable (item 4 of
  4 in the 2026-09-03 work order) — the owner already specified the fix
  in the work order itself, so no new O-number is needed.
- No `review/INTEGRATION_NOTICES.md` entry: no cross-track call-site
  signature changes (`derive_expected_exposure()`'s only caller is
  `execution.py` itself; the new `DerivedExposure` field is additive
  with a safe default).
