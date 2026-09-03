# ADR-012 — Owner session policy v1: daily → weekly (D1.5)

**Status:** ACCEPTED — implemented and tested; `order_send`/
`close_all_positions` still unbuilt
**Date:** 2026-09-03
**Drivers:** `review/OWNER_POLICY_V1.md` (owner-approved, 2026-09-02),
owner Shared-Core work order 2026-09-03 item 2 (D1.5); answers
`review/adr/ADR-004-intraday-session-boundary.md` §7's own open question,
"whether a Friday close needs a longer cutoff than a weekday roll"
**Supersedes:** O-003 ("v1 holds nothing overnight"). ADR-004 §1/§6 marked
partially superseded below, not rewritten.
**Implementation:** `src/crumblr/trading_agent/sessions.py`,
`src/crumblr/risk/trading_window.py`, `src/crumblr/risk/policies.py`,
`src/crumblr/risk/flatten_gate.py`, `src/crumblr/application/execution.py`,
`src/crumblr/application/orchestration.py`,
`src/crumblr/application/live_decision.py`,
`src/crumblr/application/flatten_plan.py`, `src/crumblr/domain/models.py`,
`src/crumblr/domain/enums.py`, `config/paper.yaml`

---

## 1. The decision being recorded

The owner's original v1 decision (O-003) was that the platform holds
nothing overnight at all. `OWNER_POLICY_V1.md` replaced that with a weekly
policy:

```text
Monday-Thursday: no daily cutoff/flatten at all - weekday overnight
                 holding is explicitly allowed
Friday:          no new entries from T-15 (15 min before weekly close)
                 must be fully flat by T-5 (5 min before weekly close)
Weekend:         any exposure forbidden
HALT-reset:      human/operator only (already compliant, verified below)
```

`config/paper.yaml`'s offsets move from 60/15 (daily) to 15/5 (Friday
only), owner-approved with this date, recorded as **O-009**.

## 2. The mechanism

### 2.1 One calendar authority: `trading_agent/sessions.py::weekly_close()`

The first design drafted for this slice anchored the Friday-only deadline
on a `trading_day(moment).weekday() == 4` branch inside `phase_at`, plus a
second, independently-derived week-start comparison for the crossed-close
replacement. Both work, but a `Plan`-agent design-review pass rejected the
shape: it is two separate week computations that have to agree with each
other, which is exactly what the owner's own instruction — "one Core
calendar/authority, not duplicated in PAPER_LITE" — rules out.

Resolved instead with a single new function,
`sessions.py::weekly_close(moment) -> datetime`: Friday 17:00
America/New_York ending the trading week `moment`'s trading day belongs
to, derived from `trading_day()` alone. `trading_window.py::phase_at`
compares directly against it with **no weekday branch at all**:

```python
close = weekly_close(moment)
if moment >= close - policy.flatten_offset:
    return SessionPhase.FLATTEN_REQUIRED
if moment >= close - policy.last_entry_offset:
    return SessionPhase.NO_NEW_ENTRIES
return SessionPhase.OPEN
```

Monday's own `weekly_close` sits roughly 4.5 days away, so both offset
comparisons are false and the function falls through to `OPEN` by
arithmetic alone — "Monday-Thursday, no cutoff" is a *consequence* of this
shape, not a special case coded for it. `has_crossed_rollover` (which
compared raw `trading_day()` values — true on any daily rollover, which is
exactly wrong once weekday overnight is permitted) is replaced by
`has_crossed_weekly_close`, defined the same way:

```python
def has_crossed_weekly_close(opened_at_utc, moment) -> bool:
    return weekly_close(opened_at_utc) != weekly_close(moment)
```

### 2.2 `trading_day()`'s weekend-fabrication bug, fixed in scope

`trading_day()` had no weekday awareness: during the closed weekend gap
(Friday 17:00 ET through Sunday 17:00 ET) it fabricated two fictional
trading days ("Saturday", "Sunday") rather than collapsing the gap into
the next real one, Monday. This was not merely theoretical — it directly
caused two live bugs, confirmed before fixing:

- `persistence/flatten.py::flatten_request_id_for()` is keyed on
  `trading_day` alone, and `application/execution.py::flatten_once()` runs
  unconditionally every pass on the service's own wall clock (no
  market-open guard) — the fabrication produced up to two spurious extra
  flatten-request rows across every real weekend.
- `application/orchestration.py::_roll_session` resets the daily-loss
  ledger baseline on every `trading_day` change — the same extra number of
  times across a weekend.

Fixing this in scope (rather than deferring it, or working around it with
a second calendar helper) was the only way to honor "one calendar
authority": `weekly_close()` is built directly on top of `trading_day()`,
so a broken `trading_day()` would have forced either a duplicate
week-boundary computation or a silently-still-buggy `weekly_close()`.

```python
def trading_day(moment: UtcDatetime) -> date:
    local = moment.astimezone(NEW_YORK)
    if not is_market_open(moment):
        return (local + timedelta(days=(7 - local.weekday()) % 7)).date()
    if local.hour >= WEEK_CLOSE_HOUR_ET:
        return (local + timedelta(days=1)).date()
    return local.date()
```

Zero behavior change for any Monday-Friday moment — the new branch is only
reachable when `is_market_open` is already `False`. Verified monotonic
across the transition (old: Fri→Sat→Sat→Sun→Mon; new: Fri→Mon→Mon→Mon→Mon
— only ever moves a weekend date *forward*), so `risk/session.py`'s
`recorded.trading_day > market_day` halt cannot newly trip across this
deploy.

**Test impact was a fixture-semantics change, not a value edit.**
`tests/conftest.py::FIXED_NOW` is Monday 2026-08-17; two currently-green
tests built "an earlier trading day" as `FIXED_NOW - timedelta(days=1)`
(Sunday, inside the closed gap) and relied on the old fabrication to read
that as a different trading day
(`test_flatten_plan.py::test_a_position_from_an_earlier_trading_day_is_marked_crossed_rollover`,
`test_execution_flatten.py::test_a_position_from_an_earlier_trading_day_is_caught_on_the_first_pass`)
— both moved to a genuine prior-week weekday.

### 2.3 The four duplicated detection sites, consolidated to one

`risk/policies.py`'s own `_overnight_breach`, and inline copies in
`application/orchestration.py`/`application/live_decision.py`'s
`_check_session_boundary` and `application/execution.py::flatten_once`, all
implemented the identical two-legged "past deadline or crossed the
boundary" condition — three of the four by re-inlining it, not calling a
shared function (`ADR-009` §1 already named this duplication as a known
fact). Since this slice already had to touch every one of the four to
change what they call, `_overnight_breach` is renamed to a public
`risk/policies.py::overnight_breach()` and the two application-tier sites
now call it directly, rather than re-inlining a second time under the new
semantics. `execution.py::flatten_once` keeps its own inline shape (it
also needs the individual `past_deadline`/`crossed_weekly_close` legs
separately, for the fingerprint and the persisted plan), but now calls
`has_crossed_weekly_close` by the same name as everyone else.

### 2.4 New: HALT if flat state cannot be confirmed by the deadline

The owner's work order states a requirement the first design draft would
have silently missed: *"If flat state cannot be confirmed by the required
deadline, HALT / surface the incident rather than assuming success."*

Before this change: if the broker's position read was incomplete,
`capture_broker_state` yielded an empty book, and `flatten_once()` hit
`if not positions: return None` with nothing tripped — an unreadable book
was indistinguishable from a genuinely flat one
(`tests/integration/test_execution_flatten.py
::test_an_incomplete_position_book_before_the_deadline_blocks_the_flatten`
still proves this is the correct behavior *before* the deadline). Under
the old daily policy this self-corrected the next day; under the weekly
policy, the same gap at the Friday deadline means a whole unmonitored
weekend could pass silently — precisely the risk class this policy exists
to control.

**Fix, scoped to `flatten_once()`** — the only component with
`BrokerStateObservation`/`SnapshotCompleteness` visibility;
`orchestration.py`/`live_decision.py`'s lighter per-tick checks and
`policies.py::overnight_breach` operate on a plain position tuple with no
completeness signal, so this leg cannot live there without adding a
broker read none of them currently make. Once
`phase_at(now, policy) is FLATTEN_REQUIRED` (at/past the Friday deadline)
and the position read is not `SnapshotCompleteness.COMPLETE`, a new HALT
reason trips **before** the `if not positions: return None` shortcut can
treat the unconfirmed read as flat.

New **`ReasonCode.FLATTEN_STATE_UNKNOWN`** (the `_UNKNOWN`-suffix
fail-closed family — `OPEN_RISK_UNKNOWN`, `RECONCILIATION_UNKNOWN`, etc.),
added to `HALT_REASONS`, and tolerated in `flatten_gate.py
::_TOLERATED_HALT_REASONS` alongside `OVERNIGHT_EXPOSURE` — the flatten
machinery must still be able to *attempt* a commitment despite this
specific halt, or it could never recover once tripped. This is safe rather
than circular: the gate's own condition 5 (`position_book_complete`)
independently still closes the gate whenever the book genuinely is
incomplete, via the existing `ReasonCode.POSITION_BOOK_INCOMPLETE` — the
tolerance only stops the halt from *duplicating* what that leg already
enforces, it does not bypass it.

### 2.5 Idempotency key: kept per-trading-day, deliberately

`ADR-009` §2.1 said the flatten request is "keyed on the policy
occurrence... one flatten commitment per trading day per symbol, ever."
Under a weekly policy the *policy* occurrence is weekly, but the key stays
at `trading_day` granularity — a weekend-spanning breach that survives
Friday deserves its own fresh Monday-dated commitment record (fresh
evidence the breach is *still* unresolved) rather than being silently
folded into a resolved-or-blocked Friday one, and it requires no schema
change. `ADR-009` §2.1's sentence is amended to say this explicitly rather
than continuing to assert a now-inaccurate "per policy occurrence" framing.

**Deployment-ordering consequence, not a code change**: the flatten
request's fingerprint includes the session policy's offsets. Landing the
60/15→15/5 config change while an environment has a live, unresolved
`flatten_requests` row for the current trading day would raise
`FlattenRequestConflictError` on the next pass — working as designed (a
real policy edit mid-occurrence should conflict, not silently reinterpret)
— true today only in the sense that no environment has ever had a real
fill, so no live row can exist to conflict with.

### 2.6 Naming: kept, drift documented rather than silently left

`IntradayPolicy`/`IntradayConfig` and the `intraday:` YAML key keep their
names despite "Intraday" now materially overstating what they mean (the
policy fires once a week, not daily) — a rename would touch every call
site's imports and the operator-facing config key for zero behavior
change. Precedent: D1.4 reclassified `RiskConfig.max_open_positions` via
its docstring rather than renaming it when its meaning changed; the same
treatment applies here. Every docstring on the touched types was rewritten
to state the weekly semantics honestly instead.

`ReasonCode.OVERNIGHT_EXPOSURE` keeps its name for a different, forced
reason: `ReasonCode` values are reconstructed from persisted rows
(`ReasonCode(code)` in `persistence/execution.py`/`flatten.py`
/`safety_state.py`), the same constraint that forced
`SYMBOL_EXPOSURE_EXISTS` to be retained in D1.3 rather than renamed.

`FlattenInstruction`/`FlattenPlan.crossed_rollover` **was** renamed, to
`crossed_weekly_close` — a genuinely different case from the two above.
The first-draft design assumed this field was safe to rename because it
is not persisted; a `Plan`-agent review caught that this was wrong:
`FLATTEN_SUBMISSION_STARTED` events persist `plan.model_dump(mode="json")`
in full, so the rename changes a real audit-payload key. Renamed anyway,
for the same honesty reason the `Intraday*` docstrings were rewritten —
but recorded explicitly rather than silently: historical rows carry
`crossed_rollover`, rows from this change onward carry
`crossed_weekly_close`. Nothing re-validates the persisted payload back
into a typed model today (`_resolve_flatten_outcome` reads it via raw dict
access), so nothing breaks — an auditor reading old and new rows side by
side is the only audience that needs to know why the key differs, and now
can from the field's own docstring in `domain/models.py`.

### 2.7 `_roll_session`'s daily-loss reset stays daily, deliberately

The owner's redesign is the *session/flatten* policy, not the daily-loss
limit — `application/orchestration.py::_roll_session` still resets the
`EquityLedger` baseline on every `trading_day` change (unchanged, still
daily), not on `weekly_close`. Stated explicitly here and in the
function's own docstring because a reader could otherwise assume "weekly
policy" meant the loss limit went weekly too; it did not, and nothing in
the owner's work order asked for that.

## 3. What this does not do

- **PL-006** (persisted loss/drawdown-fraction restart-recovery hardening)
  and **item 9** (broker-side SL verification) — the owner's own items 3
  and 4, sequenced after this one. Confirmed the `trading_day()` fix
  cannot newly trip `risk/session.py::recover_session()`'s
  `recorded.trading_day > market_day` halt (§2.2), but nothing else in
  that module is touched.
- **Market holidays** — genuinely unmodelled anywhere in `sessions.py`
  (no Good Friday, no broker-specific early close), and a weekly policy
  pinned to Friday 17:00 NY makes this matter more than the old daily
  policy did (a holiday used to mean one quiet day; now it could mean a
  flatten deadline pointed at a moment the broker is already shut). New
  open question, §7 below — not fixed here.
- **No rename of `IntradayPolicy`/`IntradayConfig`/`intraday:`** — §2.6.
- **No change to the daily-loss ledger's reset cadence** — §2.7.
- **No `close_all_positions`/`order_send`/`feedback_2_0_approved`
  change** — every file this slice touches is session/risk/flatten
  detection logic, nowhere near a submission call
  (`git diff | grep order_send`/`close_all_positions` shows only
  pre-existing definitions/refusals).
- **`agent_gateway/`** — zero edits. `agent_gateway/decision_path.py`
  inherits both changed semantics (`SESSION_BLACKOUT`/`OVERNIGHT_EXPOSURE`
  meaning, and the fixed `trading_day()` weekend behavior) with no code
  change on that side; see `review/INTEGRATION_NOTICES.md`.

## 4. Consequences

- `review/DEVIATIONS.md` gains entries for the `trading_day()` fix (a
  real pre-existing bug, not new scope) and the `crossed_rollover` →
  `crossed_weekly_close` persisted-payload shape change.
- `review/adr/ADR-004-intraday-session-boundary.md` §1's "Overnight
  positions: NOT ALLOWED" and §6's "a replay whose position survives the
  flatten deadline now halts" are marked superseded by this ADR, pointer
  only — not rewritten, the same treatment ADR-011 gave O-004.
- `review/adr/ADR-009-automatic-flatten-submission.md` §2.1 amended per
  §2.5 above. §2.7's "every shipped config's default" inertness claim
  ("`intraday.enabled=False`... every shipped config's default") was
  already false before this change — `config/paper.yaml` has always
  shipped `enabled: true`, and `config/base.yaml` carries no `intraday:`
  section at all — corrected here rather than left standing.
- **Replay behavior changed, measured rather than assumed.** The
  reference 1500-bar `baseline_v1` replay `status.md` already records as
  producing 44 `SESSION_BLACKOUT` refusals under the old daily policy now
  produces **zero** `SESSION_BLACKOUT`/`OVERNIGHT_EXPOSURE` refusals under
  the owner's widened risk numbers (D1.2-D1.4) and this weekly session
  policy together — `MAX_DRAWDOWN` (the 8% ceiling) now halts the run
  before it accumulates enough trades to ever reach a Friday trading day
  under the seeded synthetic conditions. Confirmed by direct replay run,
  not assumed; the full `tests/replay/` suite (30 tests) passes unchanged
  — none of its assertions were numerically tied to the old daily
  boundary. ADR-004 §6 already set the precedent that a replay-behavior
  change from a genuine policy change is recorded, not treated as a
  regression.
- A new owner decision, **O-009**, recorded in `status.md` for the
  session-policy numbers (15/5 minutes, Friday-only).

## 5. Verification

- `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy` —
  clean.
- `tests/unit/test_trading_window.py` (44 tests, substantially rewritten —
  see below), `tests/unit/test_flatten_plan.py` (7),
  `tests/integration/test_execution_flatten.py` (11, two new: the
  before/at-deadline halves of the `FLATTEN_STATE_UNKNOWN` HALT) all pass.
- `tests/replay/` (30 tests) passes unchanged.
- Full suite, solo, against `crumblr_test_dev1`.
- Determinism: `scripts/run_replay.py --bars 600` run twice, stdout-only
  MD5 identical.

## 6. Tests

`tests/unit/test_trading_window.py` needed a substantial rewrite, not a
patch — its `WINTER`/`SUMMER` constants are both Tuesdays (chosen
originally to catch a fixed-UTC-hour boundary), and under the weekly
policy a Tuesday has no deadlines at all, so most of the file's assertions
(the phase parametrize table, `TestEntriesAndFlatness`,
`TestCrossingTheRollover`'s "breach does not expire at the rollover"
centerpiece, most of `TestTheRiskEngineEnforcesIt`) asserted on a premise
the new policy removes. Replaced with `WINTER_FRIDAY`/`SUMMER_FRIDAY`
constants (keeping the DST-pair argument, since the deadline arithmetic
now measures from Friday's own close), a new `TestCrossingTheWeeklyClose`
class, and explicit new affirmative tests proving weekday overnight is
now permitted — nothing in the old file tested that, because it used to
be forbidden.

## 7. Open questions

| Question | Owner | Needed before |
|---|---|---|
| Market holidays (Good Friday, broker-specific early closes) — unmodelled anywhere in `sessions.py`, and a weekly policy pinned to Friday 17:00 NY is more exposed to this gap than the old daily one was | Project owner / engineering | Before a real DEMO canary crosses a holiday week |
| Per-position vs per-book deadline, once several instruments exist | Deferred (ADR-004 §7, unchanged) | Multi-market work, not v1 |
