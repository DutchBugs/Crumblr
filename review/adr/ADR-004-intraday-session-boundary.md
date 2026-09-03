# ADR-004 — Intraday-only trading and the session boundary

**Status:** ACCEPTED — **§1's "no overnight" rule and §6's replay-halts-on-
survival claim superseded 2026-09-03 by
`review/adr/ADR-012-owner-session-policy-v1.md` (D1.5, owner risk policy
v1) — weekday overnight is now permitted; only the approach to the weekly
close is restricted.** The 17:00 America/New_York boundary mechanism
itself (§2-§3) is reused, not replaced — see ADR-012 §2.1.
**Date:** 2026-08-18
**Drivers:** owner decision O-003 (`review/feedback.1.5.md` §1), review finding
F-025 (`review/feedback.1.6.md` §4)
**Supersedes:** nothing
**Implementation:** `src/crumblr/risk/trading_window.py`,
`src/crumblr/risk/policies.py`, `src/crumblr/application/orchestration.py`

---

## 1. The decision being recorded

**Superseded 2026-09-03 — see the Status line above and ADR-012.** The
owner's original v1 decision:

```text
Overnight positions: NOT ALLOWED
```

Review 1.5 §1 attaches a warning to it that is the substance of this ADR:

> Do not silently interpret "intraday" as "close at midnight UTC". The
> operational FX-day/session boundary must be explicitly defined.

Review 1.6 §4 then requires five things to be defined before M5: the last
allowed new-entry time, the mandatory flatten deadline, the session boundary,
the behaviour when a flatten fails, and the behaviour when the broker is
unavailable near the deadline. This document defines all five and states which
are built.

---

## 2. The boundary is 17:00 America/New_York

Not midnight UTC, not midnight local, not a fixed UTC hour.

The FX day rolls at 17:00 New York. That is when swap is charged and when a
position becomes, in the only sense that costs money, an overnight one. A
system that flattens by midnight UTC has already carried the position through
a rollover and paid for it — five to seven hours after the fact, depending on
the season.

Two consequences follow, and both are enforced rather than noted.

**The boundary is not configurable.** It is a market fact. It already exists in
`trading_agent.sessions.trading_day`, which the daily-loss baseline is measured
against, and a second copy of it in YAML would be a second definition that
could drift. The risk day and the session day are the same day by construction.

**The deadlines are offsets, not clock times.** New York and London change
clocks on different dates, so an offset from the boundary follows it through
the changeover while a UTC clock time drifts an hour off it twice a year. In
UTC the boundary is 22:00 in winter and 21:00 in summer; there are tests for
both.

---

## 3. The phases of a trading day

```text
├─────────── OPEN ───────────┼─ NO_NEW_ENTRIES ─┼─ FLATTEN_REQUIRED ─┤ 17:00 NY
                             │                  │                    │
                      last entry cutoff   flatten deadline      session close
```

| Phase | Meaning | Enforced |
|---|---|---|
| `OPEN` | new entries permitted, subject to every other rule | yes |
| `NO_NEW_ENTRIES` | too close to the boundary to open something that would have to be managed out | yes — `SESSION_BLACKOUT` |
| `FLATTEN_REQUIRED` | any remaining exposure must be closed | detection only — see §5 |
| `CLOSED` | the weekend gap; the market is not open at all | yes |

The gap between the two deadlines is deliberate. If flatness were demanded at
the same instant entries stopped, there would be no interval in which an open
position could legitimately be closed.

### Values

The offsets are risk policy and therefore an owner decision. `config/paper.yaml`
currently carries **provisional** values — 60 minutes and 15 minutes — chosen to
be conservative and agreed by nobody. They stand in exactly the position the
risk budgets in D-013 stand in: loadable so the platform is testable, and not
policy until a human says so.

**Owner decision required before M5.** Both numbers, recorded in `status.md` §10.

---

## 4. What is built

- The phase calculation, `risk/trading_window.py`, with the boundary derived
  from the existing trading-day definition.
- Refusal of new entries outside `OPEN`, in the deterministic risk engine, with
  reason code `SESSION_BLACKOUT`.
- Detection of a breach, as `OVERNIGHT_EXPOSURE`, which is a **HALT** and not a
  block. Review 1.6 §4 is explicit that failing to prove flatness must not
  silently become permission to hold overnight, and a block would refuse the
  next trade while leaving the position exactly where it was.
- Two independent ways to detect that breach:
  1. the flatten deadline has passed with exposure still open;
  2. a position's `opened_at_utc` belongs to an earlier trading day.

  The second exists because the first stops being true at the rollover. At
  17:00 New York the phase becomes `OPEN` for the *new* day, so a check that
  asked only about the phase would forgive a position one second after it
  became an overnight one.
- The check runs per tick, not only inside a decision window, because a
  position sits through the boundary during windows where the strategy
  proposes nothing — which is most of them.

---

## 5. What is not built, and why

**The flatten itself.** Closing a position needs the execution path, which is
M5. Building execution behaviour ahead of that gate is what the freeze exists
to prevent, and `risk/operator_controls.py:flatten` has never met a broker.

The asymmetry is deliberate and worth stating plainly: **refusing to open is
safe and ships now; promising to close is a promise this system cannot yet
keep.** A policy that claimed otherwise would be worse than one that says so.

### What M5 must add

```text
1. An automatic flatten at the deadline, distinct from the operator's manual
   FLATTEN POSITIONS control. build.md §8.2 requires the three operator
   controls to stay decoupled; this is a fourth, policy-driven action and must
   not be implemented by reusing the operator's button.

2. Behaviour when the flatten fails
   → retry within a bounded window, then HALT and raise an incident.
   → the position stays open and the system stays halted. A failed flatten is
     never downgraded to a warning.

3. Behaviour when the broker is unavailable near the deadline
   → HALT before the deadline rather than after it, on the reasoning that a
     connection which is already unreliable will not become reliable in the
     last fifteen minutes.
   → on reconnection, reconcile first; an unreconciled position book may not
     be flattened, because flattening what you cannot see is how a hedge
     becomes a naked position.

4. Reconciliation of overnight state at startup
   → a position discovered at startup whose `opened_at_utc` is in an earlier
     trading day is an O-003 breach that happened while the system was down.
     It halts, and it does not clear itself.
```

---

## 6. Consequences

- **Superseded 2026-09-03 — see ADR-012 §2.7/§4.** A replay whose position
  survives the flatten deadline now halts. That is a
  behaviour change to the prototype and it is correct: under O-003 that run was
  doing something it is not permitted to do. Under the weekly policy this is
  narrower — surviving a *weekday* rollover is permitted; only surviving the
  weekly close halts.
- The intraday policy interacts with the killzones. London Open and New York AM
  both sit well inside the trading day, so the cutoff does not shorten either
  in practice — but this stops being true if the killzone windows are ever
  widened, and nothing currently checks the two against each other.
- `SESSION_BLACKOUT` was already in the reason-code vocabulary (build.md §8.1)
  and had no producer. It has one now.

---

## 7. Open questions

| Question | Owner | Needed before |
|---|---|---|
| The two offset values | Project owner | M5 |
| Whether the deadline is per-position or per-book once several instruments exist | Deferred | multi-market work, not v1 |
| Whether a Friday close needs a longer cutoff than a weekday roll | Project owner | M5 — a weekend gap is a different risk from an overnight one |
