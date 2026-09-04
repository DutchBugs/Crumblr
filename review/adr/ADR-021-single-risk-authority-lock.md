# ADR-021 — One serialized risk-ledger authority across pipelines (AG-012 / Phase C)

**Status:** ACCEPTED 2026-09-04 — Dev 2 acked the revised §2.1 design
(cross-session, both rounds), with one precision correction to the
supporting claim (fixed in place, see §2.1's parenthetical). Dev 1's
side (`risk/session.py`, `persistence/risk_session.py`,
`application/live_decision.py`, `application/execution.py`) implemented
below; Dev 2 builds `agent_gateway/decision_path.py`'s side once this
lands on `main`.
**Date:** 2026-09-04
**Drivers:** `review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md` Phase C,
"Dev 1 + Dev 2 — close AG-012 at the execution convergence point";
`review/AGENT_FEEDBACK.md` AG-012 (Dev 2's original finding);
`review/AGENT_STATUS.md` §0n (Dev 2's design analysis, not adopted at the
time — this ADR adopts it); cross-session coordination with Dev 2,
2026-09-04.
**Supersedes:** nothing.
**Implementation (proposed):** `risk/session.py` (`RiskLedgerLock`
Protocol), `persistence/risk_session.py` (concrete
`pg_advisory_xact_lock` implementation; `load_latest`/`save` gain an
optional `connection` parameter), `application/live_decision.py`
(`LiveDecisionOrchestrator.decide_once()` redesigned), `application
/execution.py` (`ExecutionOrchestrator._process()`'s FINAL Risk read
joins the same lock), `agent_gateway/decision_path.py` (Dev 2's side —
not this ADR's implementation, but its interface contract).

---

## 1. The problem, precisely

Three call sites read and/or write `risk_session_states` (the durable
daily-loss/drawdown ledger, keyed implicitly by canonical symbol — this
platform trades exactly one instrument today) at the time this ADR was
first drafted — a fourth was found afterward, see the amendment below the
table:

| Call site | Owner | Reads | Writes | Caches in memory? |
|---|---|---|---|---|
| `LiveDecisionOrchestrator.decide_once()` (intent-time Risk, internal strategies) | Dev 1 | `load_latest()`, once per process lifetime or trading-day rollover | `save()`, only on a cycle that reaches a risk-`PASS` decision | **Yes** — `self._ledger`, mutated in memory every cycle via `EquityLedger.update()`, only periodically flushed |
| `agent_gateway/decision_path.py::evaluate_agent_trade_intent()` (intent-time Risk, external-agent proposals) | Dev 2 | `load_latest()`, fresh every call (AG-012's existing interim mitigation) | never | No — already re-derives every call |
| `ExecutionOrchestrator._process()` (FINAL Risk, execution-time revalidation) | Dev 1 | `load_latest()`, fresh every pass (review 1.23 F-058) | never | No — already re-derives every pass |
| `PaperLiteOrchestrator._recover_risk_session()`/`_persist_risk_session()` | Dev 3 | `load_latest()`, unlocked | `save()`, unlocked, from `_outcome()` — on essentially every decision outcome, not a rarely-flushed cache | No caching, but **not locked either** — see amendment |

> **Amendment, 2026-09-04 (same day, before Dev 2's `decision_path.py`
> side landed).** Dev 2 found this fourth party while checking every real
> caller of `evaluate_agent_trade_intent()` before wiring their own side
> through — not covered by this ADR's original three-party framing.
> `application/paper_lite.py::PaperLiteOrchestrator` (Dev-3-owned,
> `scripts/paper_lite.py` constructs it with a real
> `PostgresRiskSessionStore(engine)`, not a test-only in-memory one) reads
> and writes `risk_session_states` through neither `RiskLedgerLock` nor
> any other synchronization — a genuine, verified (confirmed by direct
> read of `application/paper_lite.py` lines 736-795 and
> `scripts/paper_lite.py`, not accepted on report alone) two-step
> read-then-later-write race against whichever other party writes the
> same row in between, independent of caching duration.
>
> **This ADR's stated guarantee is therefore currently narrower than
> written** — "serializes... across every process that reads or writes
> it" is true for the three parties in the table above, not yet for
> PAPER_LITE. Not fixed here: `paper_lite.py`'s own recover/persist
> design is Dev-3-owned territory, and whether/how it should acquire
> `RiskLedgerLock` is an architectural call for whoever owns that file —
> bundling a fix into this ADR's own mechanical-signature-widening pass
> would be exactly the kind of unilateral cross-track patch §2.1 already
> declined to make for a *different* reason (needing both sides'
> agreement on a *design*, not just a signature). Tracked as a Dev-2-filed
> `AG-###` finding in `review/AGENT_FEEDBACK.md`; not escalated to a
> project-wide `F-###`/reviewer finding, for the same reason the original
> AG-012 race wasn't: PAPER_LITE's own broker is `SimulatedBroker` only,
> `order_send` is unreachable from it exactly as from every other
> pipeline, so this is a Phase-C-completeness gap, not a live safety one.
> **Whoever picks this up next should re-read this table fresh rather
> than trust it as exhaustive** — a third caller was already missed once.

**The race is specifically between rows 1 and 2.** Two processes —
`LiveDecisionOrchestrator` for internal strategies,
`evaluate_agent_trade_intent()` for external-agent proposals — each form
an opinion about "how much of today's loss/drawdown budget remains" from
the same durable record, but only one of them (`LiveDecisionOrchestrator`)
holds a long-lived in-memory copy that can drift from what the other one
just wrote. A lost-update race: both could independently observe
"budget available" against views that, combined, no longer reflect
reality.

**This is not a live safety gap today** (per AG-012's own finding,
reaffirmed here): `order_send` is unreachable from any pipeline
regardless, so no real position can ever result from an inconsistent
intent-time judgment — `ExecutionOrchestrator`'s FINAL Risk (row 3)
independently re-derives everything from real broker state immediately
before any future broker write, and does not trust either pipeline's
intent-time opinion at all. This ADR closes the race because the owner
work order names it as a Phase-C precondition before
agent-driven submission can ever be treated as real — not because
today's shadow-mode operation is unsafe.

**Terminology note:** the owner work order's Phase C section calls this
"the final-Risk-to-broker-side-effect critical section." That phrasing
describes the *effect* being protected (nothing reaches a broker on an
inconsistent budget view), not literally only this codebase's own
`FINAL Risk` term-of-art (`ExecutionOrchestrator`'s execution-time
revalidation, row 3, already race-free). This ADR's actual scope is the
intent-time race (rows 1-2); row 3 is included for completeness (§5) but
was never itself racy.

## 2. Design: one Postgres advisory transaction lock, symbol-keyed

Reuses `persistence/agent_gateway.py::lock_assignment()`'s exact proven
primitive (`pg_advisory_xact_lock(hashtext(:key))`, transaction-scoped,
released automatically at commit/rollback, requires an open transaction)
— not a new mechanism, the same one AG-007 already validated in
production-shaped tests.

**Key:** `hashtext('risk-ledger:' || canonical_symbol)`. Symbol-only, not
account-scoped — matches how `PortfolioState`/`risk_session_states` are
already keyed, and this platform's current single-instrument scope. (No
objection from Dev 2 on this point.)

### 2.1 `RiskLedgerLock` — new Protocol, `risk/session.py`

**Revised after the first draft — see the note at the end of this
section.** Alongside the existing `RiskSessionStore` Protocol in the same
file (the established precedent: a Protocol in `risk/`, its concrete
Postgres implementation in `persistence/`):

```python
class RiskLedgerLock(Protocol):
    @contextmanager
    def held(self, canonical_symbol: str) -> Iterator[Connection]: ...
```

**The lock owns opening the transaction and yields the connection out** —
not a required-`connection` parameter the caller supplies. Reasoning:
`LiveDecisionOrchestrator`/`decision_path.py`/`ExecutionOrchestrator`
themselves hold only narrow Protocol-typed dependencies today
(`RiskSessionStore`, `MarketDataSource`, etc.) and none of them owns a
raw `Engine` directly — introducing one into an orchestrator class purely
so it could open a transaction to satisfy a required-`connection`
parameter would be a real, unwanted architectural inconsistency. (Precise
claim, corrected after Dev 2's own re-check: `application/recording.py::RunRecorder`
— already an injected collaborator of both orchestrators — *does* hold
an `Engine` privately and already batches multiple store writes under one
transaction the same shape `PostgresRiskLedgerLock` uses below, e.g.
`self._journal.append(event, connection=connection)` /
`self._capsules.seal(capsule, connection=connection)`. The lock is
deliberately its own class rather than routed through `RunRecorder`'s
engine — conflating "audit recording" with "risk-ledger mutual
exclusion" in one object gains nothing, and the two spans don't even
overlap: recover→evaluate happens before any recording, `seal()` happens
after. The accurate claim is "no orchestrator *class* owns an `Engine`
directly," not "nothing in reach of one does.") Instead, the concrete
`PostgresRiskLedgerLock` holds the `Engine`
(constructed once at the script/composition-root level, the same way
`PostgresRiskSessionStore(engine)` already is) and opens one transaction
per `held()` call, acquires the advisory lock inside it, then yields the
connection so `RiskSessionStore.load_latest(connection=...)`/`.save(...,
connection=...)` (§2.2) can run inside that same transaction/lock scope.

**Note on the first draft:** an earlier version of this section had
`held()` take a required external `connection: Connection` parameter,
mirroring `lock_assignment()`'s own signature literally. Dev 2 acked that
version specifically ("requiring it explicitly is arguably better...
fail loudly rather than silently lock-and-release inside a throwaway
transaction") before this gap was noticed during implementation:
`lock_assignment()`'s callers (`AgentGateway`) already hold an `Engine`
and open their own `transaction()` for other reasons — `LiveDecisionOrchestrator`
does not, so requiring a connection there had no natural source. This
revision changes who opens the transaction; it does not change the
locking guarantee (still one `pg_advisory_xact_lock` per critical
section, still released at commit/rollback) or the symbol-keyed
derivation (§2.3). Flagged back to Dev 2 for a second, quick ack before
implementation, since it changes the exact call shape `decision_path.py`
will eventually use.

### 2.2 `RiskSessionStore.load_latest()` / `.save()` gain an optional `connection`

```python
def load_latest(self, *, connection: Connection | None = None) -> SessionRecord: ...
def save(self, state: RiskSessionState, *, connection: Connection | None = None) -> None: ...
```

Exactly the `if connection is not None: ... else: with self._engine.begin() as own_connection: ...`
shape `FlattenRequestStore`/`FlattenEventStore`/`ExecutionRequestStore`/
`ExecutionEventStore` already use throughout `persistence/`. Source-compatible:
every existing caller that doesn't pass `connection` keeps today's exact
behaviour (its own transaction, unlocked) — this widening does not by
itself change anything for a caller that ignores it.

### 2.3 `PostgresRiskLedgerLock` — concrete implementation, `persistence/risk_session.py`

```python
class PostgresRiskLedgerLock:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def held(self, canonical_symbol: str) -> Iterator[Connection]:
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"risk-ledger:{canonical_symbol}"},
            )
            yield connection
```

No explicit unlock (matches `lock_assignment()` — released automatically
when the `with self._engine.begin()` block commits or rolls back on
exit). Constructed once per process, holding the `Engine` — the same
composition-root pattern `PostgresRiskSessionStore(engine)` already
uses, so a script wires `PostgresRiskLedgerLock(engine)` alongside it.

## 3. `LiveDecisionOrchestrator` — the real behavioural change

`decide_once()` currently recovers the ledger **once** (on the first
call, or on a trading-day rollover) and holds/mutates
`self._ledger: EquityLedger | None` in memory across every subsequent
cycle, persisting back (`_persist_session()`) only on a cycle that
reaches a full risk-`PASS` decision. This is a deliberate optimization
(`_persist_session()`'s own docstring: "a database call per bar for a
value that mostly has not moved" is avoided) — and exactly the caching
AG-012 identified as the source of the race, since a long-lived in-memory
copy can never observe a concurrent writer's update mid-lifetime.

**Proposed replacement**, every `decide_once()` cycle (not only on
first call/rollover):

```text
with self._risk_ledger_lock.held(canonical_symbol) as connection:
  session_record = self._session_store.load_latest(connection=connection)
  recovery = session.recover_session(session_record, live_equity=..., ...)
  self._ledger = recovery.ledger          # same object shape as today
  (loss-gate checks, session-boundary checks — unchanged, read self._ledger)
  ... strategy evaluation, intent-time Risk (policies.evaluate) ...
  state = session.snapshot(self._ledger, ...)
  self._session_store.save(state, connection=connection)
# exiting the `with` block commits, releasing the lock
```

`recover_session()` already takes "the worse of the recorded vs. live
account state" (§1's table footnote) — re-deriving fresh every cycle is
not a correctness regression on its own; the actual fix is making the
*persist* side happen every cycle too, inside the same lock, so a
concurrent reader (decision_path.py) is never more than one cycle behind
reality. Today's persistence only happens on a risk-`PASS` cycle — a real,
separate staleness gap this ADR also closes as a side effect (worth
naming, not folded silently into "the AG-012 fix" without saying so).

**What does not change:** `EquityLedger`'s own API (`update()`,
`start_new_session()`, the drawdown/session-loss properties
`_check_loss_gates()` reads) — `self._ledger` is still a normal
in-process object for the duration of one `decide_once()` call, it is
just no longer *retained* across calls. The loss-gate and
session-boundary checks, the decision-window idempotence machinery
(F-054, a separate durable store, untouched), and everything after
intent-time Risk are unaffected.

**Cost:** one lock acquisition + one `SELECT` + one `INSERT` per
`decide_once()` cycle, instead of only on risk-`PASS` cycles. At M5
cadence (one decision per closed 5-minute bar) this is negligible —
nowhere near a hot loop, and the exact same real-PostgreSQL round-trip
`ExecutionOrchestrator`'s FINAL Risk already pays every pass today.

## 4. `agent_gateway/decision_path.py` — lock-for-read-consistency only

**Dev 2's correction, adopted:** this side's critical section is
`recover→evaluate`, not `recover→evaluate→persist` — `decision_path.py`
never calls `.save()` (confirmed: `RiskSessionState` only changes on
realized P&L/drawdown movement, which needs a fill, and this path
structurally cannot reach `order_send`). The lock's job here is purely to
prevent this read from landing mid-way through `LiveDecisionOrchestrator`'s
read-modify-write cycle (§3) — not to arbitrate two writers. This ADR
does not prescribe `decision_path.py`'s exact code (Dev-2-owned,
built once this interface is settled) — only the shared contract:
`with self._risk_ledger_lock.held(canonical_symbol) as connection: record = self._session_store.load_latest(connection=connection)`,
using the same `PostgresRiskLedgerLock`/key derivation as §2.3 so the two
sides cannot independently drift.

## 5. `ExecutionOrchestrator` FINAL Risk — included for completeness, not because it was racy

`_process()`'s own `session_recovery = recover_session(self._session_store.load_latest(), ...)`
is already fresh every pass, with no in-memory caching — it was never
part of the race §1 describes. Two reasons to still wrap its read in the
same lock:

1. **It is the literal closest thing to "the final-Risk-to-broker-side-effect
   critical section"** the owner work order's own wording names — a
   future real `order_send` needs *this* read inside the lock, not only
   the earlier intent-time reads, for the guarantee to hold all the way
   to the broker boundary.
2. **Low marginal cost.** `_process()`'s read is already a plain
   `load_latest()` call with no write-back — adding
   `RiskLedgerLock.held(...)` around it is a small, low-risk addition to
   an already-correct call site, not a redesign.

`_process()` does not call `.save()` either (confirmed by re-reading —
no `self._session_store.save(...)` call anywhere in
`application/execution.py`), so like §4 this is lock-for-read-consistency
only.

## 6. What this does not do

- Does not touch `order_send`/`close_all_positions`/any broker-mutating
  call — those remain exactly as unreachable as before this ADR.
- Does not change `EquityLedger`'s own semantics or `risk/policies.py::evaluate()`'s
  signature.
- Does not merge `LiveDecisionOrchestrator` and `decision_path.py` into
  one process (§0n's "option 1," explicitly not chosen — the lock
  achieves the same safety property with a narrower blast radius and
  reuses an already-proven primitive, per Dev 2's own original framing).
- Does not address `review/DEVIATIONS.md` D-051 gap 2 (`live_decision.py`'s
  `flat()` forward hazard once `order_send` works) — unrelated, separate
  gap.

## 7. Consequences

- `RiskSessionStore` Protocol widens (source-compatible addition) —
  shared-contract territory (`risk/session.py`), logged in
  `review/INTEGRATION_NOTICES.md`.
- No new migration — reuses `risk_session_states` unchanged; the lock
  itself is a session-level Postgres primitive, not a table.
- **Implemented and tested, Dev-1 side.** `tests/unit/test_live_decision.py::TestADR021RiskLedgerPersistsEveryCycle`
  (2 tests — a NO_TRADE cycle now persists and acquires the lock exactly
  once; a second, independent orchestrator instance sharing only the
  durable store observes the first's persisted state, proving
  `self._ledger` is no longer retained across calls).
  `tests/integration/test_risk_ledger_lock.py::TestPostgresRiskLedgerLockSerializesUnderRealConcurrency`
  (2 tests, real PostgreSQL, real threads — a second holder never starts
  before the first releases; two different symbols' locks do not block
  each other, proving the key is genuinely per-symbol). Full existing
  `test_live_decision.py`/`test_execution_orchestrator.py`/
  `test_execution_flatten.py` suites re-run unchanged and still green —
  the redesign did not alter any externally-observable decision/outcome,
  only the ledger's recovery/persistence cadence.
- `ExecutionOrchestrator`'s constructor gains `risk_ledger_lock`
  (required); `LiveDecisionOrchestrator`'s constructor gains
  `risk_ledger_lock` (required) — both source-breaking (not optional),
  matching how `recover_session()` gaining required `max_daily_loss`/
  `max_drawdown` (PL-006) was handled: every real construction site
  (`application/bootstrap.py::DurableRuntime`/`build_durable_runtime`,
  `scripts/live_decision.py`, `scripts/run_execution_preflight_evidence.py`,
  and every test builder) updated in the same change, not left broken for
  later.
- Once Dev 2's `decision_path.py` side lands: `review/AGENT_FEEDBACK.md`
  AG-012 closes for real (option 1 from its own row, "single shared
  risk-evaluation authority," superseding the interim option-2
  mitigation currently in `decision_path.py`).
- **PAPER_LITE's own unlocked access remains open** (§1's amendment) —
  a real, if currently non-safety-critical, gap between this ADR's
  stated guarantee and what is actually locked. Whoever owns
  `application/paper_lite.py` next should read §1's amendment before
  assuming the guarantee is complete.
