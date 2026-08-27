# ADR-001 — Execution-time risk revalidation

**Status:** ACCEPTED · algorithm implemented 2026-08-27, not yet wired into a live orchestrator
**Date:** 2026-08-17
**Raised by:** review finding F-007 (`review/feedback.1.0.md`)
**Amended:** 2026-08-17 by review finding F-011 (`review/feedback.1.1.md`) —
see *Monetary risk is recomputed at the executable price* below
**Required before:** M5 — paper execution
**Supersedes:** nothing

---

## Context

Today the risk engine runs once, on the intent, and its verdict is carried
forward to `order_send` unchanged. Between those two moments the world can
move. Concretely, any of these can change after approval and before submission:

```text
price and spread            equity
open positions              total open risk
kill-switch state           instrument specification
account identity            market-data freshness
```

The gap is small in a replay, where the next bar arrives only when the loop
asks for it. It is not small against a real broker, where `order_check` and
`order_send` are network calls with latency, retries and reconnects, and where
the intent may have expired while waiting.

The failure this admits is specific: an order that was legitimately approved
gets submitted into conditions under which it would have been refused. Nothing
in the current design detects that.

## Decision

Insert a **second, deterministic risk check immediately before `order_send`**,
against state read at that moment.

```text
Trading Agent
      ↓
Intent risk check          ← sizing happens here
      ↓
Supervisor                 ← may veto or halt; may not approve past a block
      ↓
FINAL execution-time check ← same rules, current state
      ↓
order_check                ← broker's own validation
      ↓
order_send
```

Four constraints govern it.

**1. It may only be equal or more restrictive.** The final check re-runs the
same rule set against fresher state. It can refuse an order the first check
approved; it can never approve one the first check refused, and it can never
increase an approved volume. If the two disagree, the more restrictive answer
wins — which, given the rules are identical, means the fresher one.

**2. The supervisor cannot overrule a deterministic block.** The order of the
stages is not cosmetic. A supervisor approval is permission to proceed to the
risk gate, not permission to pass it. Should a future statistical or LLM layer
ever produce an approval, it still cannot reach `order_send` around this check.

**3. An expired intent cannot execute.** Expiry is evaluated at the final
check, against the clock at that moment, not against the clock when the intent
was written.

**4. Both decisions are persisted.** The decision capsule records the initial
risk decision *and* the execution-time one. A trade refused at the final gate
is evidence about latency and market conditions, and discarding it would hide
exactly the behaviour this check exists to catch.

### Monetary risk is recomputed at the executable price

*Added by review finding F-011.*

Re-running validation against the intent's reference price would satisfy the
letter of "check again" while missing the case it exists for. Volume is fixed,
but **the money that volume puts at risk is not** — it is a function of the
distance from the current executable price to the stop, and that distance moves
with the market.

Concretely: a BUY approved with the ask at 1.0850 and the stop at 1.0830 risks
20 pips of distance. If the ask has moved to 1.0862 while the order queued, the
same volume now risks 32 pips — a 60% increase in monetary exposure, with no
change to the number the supervisor approved. Nothing in the first check would
catch it, because the first check never saw that price.

The final gate therefore computes:

```text
entry basis   = current ask for a BUY, current bid for a SELL
                (the side the order will actually execute against)
risk distance = |entry basis − approved stop|
monetary risk = risk distance ÷ tick size × tick value × approved volume
```

and blocks when that exceeds the configured budget against **current** equity.

The inputs it must read fresh, not inherit:

```text
current bid/ask              configured max slippage/deviation
current spread               intent expiry
approved stop (fixed)        current symbol specification
approved volume (fixed)      current portfolio exposure
current equity
```

The rule in one line:

> Sizing is not recomputed. Monetary risk is recomputed against current
> execution-state assumptions. The volume either stands or the order is
> blocked; it may never increase.

A favourable move that *reduces* risk therefore leaves the order untouched. The
gate is one-directional: it can refuse, never enlarge.

**Required tests before M5**

1. approved BUY, ask moves away from the stop until the fixed volume exceeds
   budget → BLOCK
2. approved SELL, the mirror case → BLOCK
3. favourable move that lowers risk → original volume retained, not increased
4. spread widens past the configured limit between the checks → BLOCK
5. symbol specification changes between the checks → BLOCK pending
   re-registration
6. intent expires between the checks → BLOCK
7. kill switch trips between the checks → BLOCK
8. property: the final gate never approves what the first refused, and never
   returns a volume above the approved one

## Consequences

**Accepted costs**

- The rule set runs twice per order. It is deterministic and cheap; the
  duplication is deliberate rather than something to optimise away.
- Sizing is *not* recomputed. The approved volume is fixed at the first check
  and only ever re-validated, never re-derived — otherwise a change in equity
  between the two points could silently produce a different order than the one
  the supervisor approved.
- Some orders will be refused after approval. That rate is a metric worth
  watching, not a defect: a rising one means the gap between decision and
  execution is growing.

**What this does not solve**

- It narrows the window; it does not close it. Between the final check and the
  broker's fill there is still latency no local check can cover. That residual
  is what `order_check`, idempotency and reconciliation are for.
- It says nothing about positions already open. That is the separate concern in
  finding F-008.

## Implementation notes

- The check belongs in `risk/policies.py`, reusing `evaluate` rather than a
  parallel implementation. Two rule sets that were meant to be identical and
  drifted apart would be worse than having only one.
- It needs a fresh `MarketSnapshot`, `AccountState` and position list read at
  call time — not the ones captured with the intent.
- A new reason code distinguishes "refused at the final gate" from "refused up
  front", because the two mean different things operationally.
- Tests must include: state changing between the two checks such that the
  second refuses; an intent expiring between them; the kill switch tripping
  between them; and the property that the final check never approves what the
  first refused.

## Status of implementation

**Algorithm implemented 2026-08-27** (Phase 4 slice 2, non-sending execution
engineering — `review/PHASE4_PLAN_REVIEW_GO_WITH_TWEAKS.md`):
`risk/policies.py::revalidate_fixed_volume_at_execution_time`. Reuses
`evaluate()` verbatim for every check it already performs (constraint from
*Implementation notes* above); adds only the executable-price-based stop
repricing and the fixed-volume-vs-fresh-budget comparison. New reason code
`ReasonCode.EXECUTION_TIME_RISK_BLOCK` distinguishes a final-gate refusal
from an intent-time one, appended alongside the specific reason(s) —
satisfying *Implementation notes*' "a new reason code distinguishes refused
at the final gate from refused up front."

Of the eight *Required tests before M5*, seven are covered directly by
`tests/unit/test_risk_engine.py::TestExecutionTimeRevalidation` (a
`test_adr001_N_*` test per item): 1 (BUY, ask moves away, BLOCK), 2 (SELL,
mirror case, BLOCK), 3 (favourable move, volume unchanged not increased),
4 (spread widens, BLOCK), 6 (intent expires, BLOCK), 7 (kill switch trips,
refused), 8 (property: never returns a volume other than `None` or the
approved one). Item 5 (symbol specification changes between the checks →
BLOCK pending re-registration) is **not** this function's responsibility in
the Phase-4 design: FINAL Risk only runs after the caller's reconciliation
step has already confirmed `MATCHED` against the pinned instrument-spec
baseline (F-055) — a spec change surfaces there, before FINAL Risk is ever
reached, not as a check duplicated inside it.

**Not yet done:** wiring this function into a live caller. No
`ExecutionOrchestrator` exists yet — the fresh synchronous
broker/market observation, the persisted snapshot, the reconciliation step
immediately before this check, and the `ApprovedOrder`/`order_check` steps
immediately after it are later slices of the same Phase-4 plan. This ADR
stays open until that wiring exists and the eight required tests are
exercised end to end, not only at the function level.
