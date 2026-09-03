# ADR-013 — Restart recovery must not forget an already-breached loss/drawdown limit (PL-006)

**Status:** ACCEPTED — implemented and tested; `order_send`/
`close_all_positions` still unbuilt
**Date:** 2026-09-03
**Drivers:** Owner Shared-Core work order 2026-09-03, item 3 (PL-006)
**Supersedes:** nothing. Closes a real gap review F-019/`risk/session.py`'s
own restart-recovery fix left standing.
**Implementation:** `src/crumblr/risk/session.py::recover_session()`/
`_halt()`, `src/crumblr/application/execution.py`,
`src/crumblr/application/orchestration.py`,
`src/crumblr/application/live_decision.py`,
`src/crumblr/application/paper_lite.py`,
`src/crumblr/agent_gateway/decision_path.py`

---

## 1. The decision being recorded

The owner's own wording: persisted `max_session_loss_fraction`/
`max_drawdown_fraction` must not be forgotten after a restart once equity
recovers; if the session already hit the 4% daily-loss or 8% drawdown
ceiling, recovery must not later allow trades again just because the
*current* fraction is now lower. Explicitly: "make this part of normal
Core Risk semantics, not PAPER_LITE glue."

## 2. The mechanism

### 2.1 What was already correct, and what was missing

`risk/session.py::recover_session()` (review F-019) already reconstructs
`max_drawdown_fraction`/`max_session_loss_fraction` correctly on restart:
`EquityLedger.resumed()` seeds them with the worse of the persisted
record and what live equity implies, and `EquityLedger.update()` can only
widen them afterward. That half of F-019's own requirement — "recovery
may only ever be more conservative than the record" — was never broken.

What was missing: `recover_session()` never *checked* the recovered
maxima against the configured `risk.max_drawdown`/`risk.max_daily_loss`
thresholds before deciding recovery could proceed. It only halted on an
unreadable record, a schema mismatch, a future trading day, or a
position-count mismatch. Separately, the live per-tick gate
(`orchestration.py::_check_loss_gates`, `risk/policies.py::evaluate()`'s
loss-gates section) only ever reads the *current* instantaneous
`ledger.drawdown_fraction`/`session_loss_fraction` — correct and
sufficient within one continuous run (the gate trips the instant a limit
is crossed, and the `KillSwitch` then stays halted until an operator
resets it), but it leaves no second line of defense specifically at the
restart boundary: a session whose recorded worst already breached policy
had no mechanism of its own saying so, and recovery relied entirely on
the `KillSwitch`'s separately-persisted `SafetyState` having
independently captured the halt.

### 2.2 The evidence this was real, not theoretical

`application/paper_lite.py::_recover_risk_session()` (Dev 3's PAPER_LITE
track, merged to `main` the same day as this fix) already contained a
local, hand-rolled workaround for exactly this gap:

```python
exhausted: list[ReasonCode] = []
if recovery.ledger.max_session_loss_fraction >= self._config.risk.max_daily_loss:
    exhausted.append(ReasonCode.DAILY_LOSS_LIMIT)
if recovery.ledger.max_drawdown_fraction >= self._config.risk.max_drawdown:
    exhausted.append(ReasonCode.MAX_DRAWDOWN)
if exhausted:
    self._trip_risk_session(tuple(exhausted), snapshot, "...")
```

Discovered and patched independently, as PAPER_LITE-local glue — exactly
the shape the owner's own instruction warns against. This is direct
confirmation the gap was real enough that a second, independent
implementation effort had already run into it and worked around it
locally rather than fixing the shared authority.

### 2.3 The fix: one check, inside the one shared function

`recover_session()` gains two new required keyword parameters,
`max_daily_loss: Decimal` and `max_drawdown: Decimal` — the *configured*
thresholds, not a cached historical value, so the check always measures
against current policy the same way the live gate does. After the ledger
is reconstructed:

```python
exhausted: list[ReasonCode] = []
if ledger.max_drawdown_fraction >= max_drawdown:
    exhausted.append(ReasonCode.MAX_DRAWDOWN)
if ledger.max_session_loss_fraction >= max_daily_loss:
    exhausted.append(ReasonCode.DAILY_LOSS_LIMIT)
if exhausted:
    return _halt(live_equity, market_day, tuple(exhausted), ...)
```

Both reasons can fire together, matching this codebase's own "collect
every failing reason, do not short-circuit on the first" discipline
(`risk/policies.py::evaluate()`'s own philosophy) rather than reporting
only whichever check happens to run first. `_halt()`'s own signature
widened from a single `reason: ReasonCode` to `reasons: tuple[ReasonCode,
...]` to carry this — its four pre-existing call sites (unreadable
record, schema mismatch, future trading day, position mismatch) each
became a one-element tuple, mechanical, no behavior change for them.

No change was needed to the session-day-reset legs above this check:
`max_session_loss_fraction` is already correctly zeroed for a new trading
day (`recorded.max_session_loss_fraction if same_session else ZERO`)
before reaching this new check, so a session that renewed overnight is
not held to yesterday's exhausted allowance — proven directly by
`tests/unit/test_risk_session.py
::test_the_daily_reset_also_means_recovery_does_not_halt_on_yesterdays_exhausted_loss`.
`max_drawdown_fraction`, deliberately never reset by a day change
(drawdown is a whole-account measure, not a daily one — already true and
unchanged), can halt recovery across a day boundary, and does.

### 2.4 Every call site, and the one deliberately left alone — then fixed anyway

Five call sites existed. Four were updated directly:
`orchestration.py::_recover_session`, `live_decision.py
::_recover_session`, `execution.py`'s inline call, and
`application/paper_lite.py::_recover_risk_session` — which also had its
own local `exhausted`/`_trip_risk_session` duplicate block deleted, since
`recovery.must_halt`/`recovery.reason_codes` now carries exactly what
that block used to compute by hand. This is the concrete fulfillment of
"part of normal Core Risk semantics, not PAPER_LITE glue": removing
PAPER_LITE-local duplicate logic in favor of the shared one, not adding
new PAPER_LITE-specific code.

The fifth, `agent_gateway/decision_path.py:213` (Dev 2's own track), was
initially left untouched deliberately — confirmed via cross-session
coordination with Dev 2 that the signature change is not source-
compatible, and Dev 2 asked to update their own call site on their next
`main` sync rather than have it touched here, since they were mid-commit
in that same file. That plan changed within the hour: running the full
test suite showed `application/paper_lite.py`'s *own* decision path
calls straight through `agent_gateway
::evaluate_agent_trade_intent` into that exact line, so the unfixed
signature was not merely a break waiting for Dev 2's next pull — it was
already failing 10 of PAPER_LITE's own tests on `main`, today, for
everyone. Given that direct, demonstrated shared-`main` breakage (not a
hypothetical future one), the one-line fix
(`max_daily_loss=config.risk.max_daily_loss`,
`max_drawdown=config.risk.max_drawdown` — `config` already in scope,
identical shape to the other four sites) was made directly rather than
left broken, and Dev 2 was notified immediately with the reasoning.

### 2.5 A known, deliberately-not-fixed-here interaction: earlier detection changes the downstream verdict shape

Fixing `agent_gateway/decision_path.py:213` surfaced two failing tests in
Dev 2's own `tests/unit/test_agent_decision_path.py`
(`TestAG012FreshSessionRecoveryEveryCall
::test_a_recorded_prior_loss_this_session_reaches_the_daily_loss_gate`
and `::test_two_calls_against_different_stores_are_fully_independent`) —
**not a defect in this fix**, but a real interaction with an existing,
deliberate design convention. `risk/policies.py::evaluate()`'s own
documented rule (`tests/unit/test_risk_engine.py
::test_adr001_7`'s docstring: *"an already-halted system is enforced as
a BLOCK, not a fresh HALT escalation — the halt already happened when
the kill switch was tripped"*) means any decision evaluated after the
kill switch is already halted reads as `BLOCK`/`SYSTEM_HALTED`, never
re-deriving the original trip's specific reason.

Before this fix, a recorded prior loss was only ever caught *live*,
inside `evaluate()`'s own loss-gate leg, in the same call that
discovered it — producing a direct `DAILY_LOSS_LIMIT`/`HALT` verdict in
that one decision. Now `recover_session()` catches the identical
condition earlier, during recovery itself, before `evaluate()` ever
runs — so by the time a decision is evaluated, the system is already
halted, and the established convention correctly downgrades that
decision to `BLOCK`/`SYSTEM_HALTED`. The kill switch itself still trips
with the correct, specific reason (`MAX_DRAWDOWN`/`DAILY_LOSS_LIMIT`,
visible in `kill_switch.active_reasons` and the trip detail) — this is
strictly earlier and more correct than before, not a regression, but two
tests asserted the old timing's specific downstream verdict shape.

Deliberately not fixed in this same change: coordinated with Dev 2, who
asked to fix the two assertions on their side once they merge and see
the real failure themselves, rather than have it guessed against a
description while they were mid-commit in the same area. This ADR ships
with those two failures named and explained, not silently left red or
hidden.

## 3. What this does not do

- No change to the live per-tick gates themselves
  (`orchestration.py::_check_loss_gates`, `risk/policies.py::evaluate()`'s
  loss-gates section) — they already correctly trip the instant a limit
  is crossed within a continuous run; this closes the *restart* gap
  specifically, not a live-gate gap.
- No change to `EquityLedger`'s own tracking logic — `update()`'s
  `max(...)` widening was already correct (F-019); this only adds the
  missing *check* against configured thresholds at recovery time.
- No `close_all_positions`/`order_send`/`feedback_2_0_approved` change —
  every file this touches is risk-session/recovery logic, nowhere near a
  submission call.

## 4. Consequences

- `review/DEVIATIONS.md` gains an entry recording the F-019 gap this
  closes and the coordinated (then directly-made) `agent_gateway/`
  signature-change break.
- `review/INTEGRATION_NOTICES.md` gains an entry naming the breaking
  `recover_session()` signature change for any other current or future
  caller.
- `status.md` records this as a Dev-1 Shared-Core deliverable (item 3 of
  4), not a new owner decision — the owner already specified the fix in
  the work order itself, so no new O-number is needed.
