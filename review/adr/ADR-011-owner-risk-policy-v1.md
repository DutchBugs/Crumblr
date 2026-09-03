# ADR-011 — Owner risk policy v1 (D1.2 + D1.3 + D1.4)

**Status:** ACCEPTED — implemented and tested; `order_send`/
`close_all_positions` still unbuilt
**Date:** 2026-09-03
**Drivers:** `review/OWNER_POLICY_V1.md` (owner-approved, 2026-09-02),
`review/OWNER_WORK_ORDERS_2026-09-02.md` tasks D1.2, D1.3, D1.4
**Supersedes:** O-004 (one EUR/USD exposure at a time). Confirms and
closes the numeric half of D-013 (`config/paper.yaml`'s risk values were
placeholders).
**Implementation:** `src/crumblr/risk/portfolio_risk.py` (new),
`src/crumblr/risk/policies.py`, `src/crumblr/config.py`,
`src/crumblr/domain/enums.py`, `src/crumblr/risk/session.py`,
`src/crumblr/application/execution.py`,
`src/crumblr/application/orchestration.py`,
`src/crumblr/application/live_decision.py`,
`migrations/versions/20260903_d3b2e828b5b0_*.py`,
`tests/unit/test_portfolio_risk.py`, `tests/unit/test_risk_engine.py`,
`tests/replay/test_replay_prototype.py`

---

## 1. The decision being recorded

`OWNER_POLICY_V1.md` replaced two v1 product assumptions the platform had
been built around:

- **Exposure**: not "one EUR/USD position at a time" (O-004) but
  "multiple positions may be open at once, provided total open risk never
  exceeds `max_open_risk`."
- **Numbers**: the four risk fractions move from engineering placeholders
  to owner-approved values — `max_risk_per_trade=0.02`,
  `max_open_risk=0.03`, `max_daily_loss=0.04`, `max_drawdown=0.08`.

This ADR covers the risk-policy third of the resulting work order
(D1.2/D1.3/D1.4). Session policy (D1.5, daily→weekly/weekend-flat) is a
separate, later slice and is untouched here — `risk/trading_window.py`,
`IntradayPolicy`/`IntradayConfig`, `OVERNIGHT_EXPOSURE`, and
`config/paper.yaml`'s `intraday:` block are all byte-identical to before
this change.

## 2. The mechanism

### 2.1 D1.4 first, D1.3 second, D1.2 last — deliberately

Implemented in this order so that lifting the one-exposure cap (D1.3)
never happens before the real portfolio-risk accounting that is supposed
to replace it (D1.4) exists. Reversing the order would have briefly
permitted stacking against a still-fictional, count-based risk budget.
D1.2 (the four numbers themselves) landed last, isolated in its own diff.

### 2.2 Real open-risk accounting replaces a count-based approximation

`PortfolioState.open_risk_fraction` used to be computed at every call site
as `max_risk_per_trade * Decimal(len(open_positions))` — a fiction that
assumed every position risks exactly one trade's worth, regardless of its
actual stop distance, volume, or instrument. The new
`risk/portfolio_risk.py::assess_open_risk()` computes it for real, from
recorded facts: for each position, the signed adverse distance from entry
to stop (`open_price` vs `stop_loss_price`, direction-aware), the
instrument's tick economics, and the position's volume — summed and
divided by live equity.

A new module, not folded into `risk/sizing.py`: `sizing.py` scopes itself
to single-position math over `(Decimal, InstrumentSpec)`, with no
book/account/trust concept — mirroring the ADR-010 precedent of keeping
book-level aggregation (`expected_state.py`) out of the primitive module
(`reconciliation.py`). It reuses `sizing.py`'s existing `realised_risk()`
rather than reimplementing the arithmetic — one authority, more callers.

### 2.3 Fork 1 — entry geometry, not mark-to-market

The stop-distance geometry is anchored to `open_price` (what the position
was authorized against), not `current_price` (what it is worth right
now). Four reasons: (a) *structural* — `SimulatedBroker.positions()`
never sets `current_price` anywhere (confirmed by grep, zero matches); a
`current_price`-anchored rule combined with the required fail-closed-on-
`None` leg would permanently block every trade after the first,
reintroducing exactly what D1.3 removes. (b) *semantic* — the 3% figure
is an allocation budget ("how much has Crumblr committed"), not a
mark-to-market exposure report; entry geometry is exactly the sum of what
`size_position()` itself already authorized. (c) *no incentive loop* —
mark-to-market geometry would shrink toward zero as a losing position
approaches its stop (freeing budget exactly when the book is most
impaired) and grow past entry-risk on a winner (over-reporting a position
that can no longer lose its authorized amount); entry geometry has
neither failure mode. (d) *still responsive to real risk reduction* — a
stop moved to breakeven gives `open_price − stop_loss_price = 0`,
correctly freeing that position's full budget.

Stated plainly: this is an allocation figure, not a mark-to-market P&L
number, and must never be presented as one.

`_adverse_distance()` computes the signed distance rather than reusing
`TradeIntent`'s `abs()`-based `_stop_distance` — `PositionState`, unlike
`TradeIntent`, carries no validator guaranteeing the stop sits on the
protective side, so `abs()` could misreport a locked-in profit as risk. A
stop at or beyond breakeven contributes exactly zero, never a negative
number, and never raises.

### 2.4 Fork 2 — live equity, not session-start equity

`assess_open_risk()` takes `equity` from the caller, and every call site
threads live `AccountState.equity` (or `SimulatedBroker.equity`), not a
cached session-start figure. The decisive argument is dimensional, not
stylistic: `evaluate()` computes
`projected_open_risk = portfolio.open_risk_fraction + intent
.requested_risk_fraction`, and `requested_risk_fraction` is converted to
money via `size_position(equity=account.equity, ...)` a few lines later —
using two different equity denominators for the two terms of that sum
would be a correctness defect, not a style choice. Secondarily: after a
loss, a fixed-currency open risk is a *larger* share of what remains —
live equity tightens the budget exactly then, where session-start equity
would understate exposure precisely when the account is impaired, which
is the permissive direction a safety limit must never move. Every call
site hoists one account/position read and shares it between the
`PortfolioState` construction and the risk assessment, so the number
stays a pure function of one recorded observation.

### 2.5 Fail-closed: unestablished, never a substituted maximum

`OpenRiskAssessment.fraction`/`risk_amount` are `Decimal | None`, `None`
together — never a partial or fabricated number. Any position this
platform cannot honestly value (no instrument spec for its
`broker_symbol`, or no protective stop) makes the **whole** assessment
unestablished, not just that ticket's contribution — a partial number
would misrepresent a partial book as a complete one. The result still
names which tickets and why (`UntrustedPosition.reason`), for operator
visibility.

`PortfolioState.open_risk_fraction` widens from `Decimal` (defaulting to
`ZERO` — itself a fail-open hazard) to `Decimal | None` with no default:
every construction site must now say something. `None` feeds a new
**`ReasonCode.OPEN_RISK_UNKNOWN`** (grouped with the existing F-002
family — `RECONCILIATION_UNKNOWN`, `SAFETY_STATE_UNKNOWN` — the same
"absence of evidence is not evidence of safety" discipline this codebase
already applies elsewhere).

**BLOCK, not HALT — deliberately.** The refusal's job (stop new risk
stacking on top of an unquantifiable position) is fully achieved by a
BLOCK. The platform cannot currently *close* the offending position
either way (`close_all_positions` stays unbuilt, D-050), so a HALT would
be a permanent brick with no in-system remediation — worse than the
BLOCK it would replace. There is already a correctly-scoped future owner
for the system-level judgement this implies: **item 9, broker-side
stop-loss verification**, named here explicitly as the escalation path
rather than duplicated. A later change to BLOCK-vs-HALT here must be a
deliberate decision, not a side effect — pinned by
`tests/unit/test_risk_engine.py
::test_an_unestablished_open_risk_is_a_block_not_a_halt`.

### 2.6 D1.3 — O-004 withdrawn, `max_open_positions` reclassified

`risk/policies.py::MAX_EXPOSURES_PER_SYMBOL` and the `SYMBOL_EXPOSURE_
EXISTS`-appending block are deleted outright, along with the `exposures`
local that fed both it and the overnight-breach guard
(`if exposures and _overnight_breach(...)`) — that guard is now
unconditional (`_overnight_breach` already returns `False` on an empty
book, so dropping the `exposures and` prefix is a strict no-op except
that it also now considers positions in other symbols, which the old
guard's same-symbol-only counting was structurally blind to; this can
only produce more correct refusals, never fewer).

`ReasonCode.SYMBOL_EXPOSURE_EXISTS` is **kept, not deleted** —
`ReasonCode` values are reconstructed from persisted rows via
`ReasonCode(code)` in three stores (`persistence/execution.py`,
`persistence/flatten.py`, `persistence/safety_state.py`); deleting the
member would make any historical row carrying it undecodable. Its
docstring is rewritten to state plainly that it is retired and that no
code path emits it any more.

`RiskConfig.max_open_positions` gains a docstring reclassifying it as an
operational circuit-breaker ceiling — not a trading rule, and not the
owner's portfolio budget (that is `max_open_risk`, enforced against
measured risk). Shipped value moves from `1` to **`10`**, chosen rather
than arbitrary: every registered strategy requests a fixed 0.5% per trade
today, so the 3% budget itself binds at 6 concurrent positions under
current behaviour — the ceiling sits above that so it can never silently
become a strategy rule again, while staying low enough that hitting it is
still worth an operator's attention. Recorded in `review/DEVIATIONS.md`
as an engineering-audit item (D-053) with a named revisit trigger: if any
strategy ever requests below roughly 0.3% per trade, this ceiling should
be reconsidered.

`tests/unit/test_one_exposure_policy.py` is deleted in full — every
assertion in it is now false as production behaviour. Its load-bearing
end-to-end proof is inverted rather than silently dropped: the deleted
file's `TestTheReplayNeverStacks` (a 400-bar replay asserting no window
ever held more than one position) becomes
`tests/replay/test_replay_prototype.py::TestMultiplePositionsPermitted`,
asserting the opposite property.

### 2.7 The replay proof, and why it is not driven through the strategy

The plan called for proving "multiple positions occur, and no window's
measured open risk ever exceeded the budget" end to end through a full
agent replay — the inversion of the deleted test's own method. In
practice, `trading_agent/baseline.py::_decide()`'s own
`already_positioned` guard refuses to pyramid the *same* direction
regardless of what the risk gateway now permits, so a second concurrent
position can only occur through a direction reversal while the first is
still open. That is rare enough on a synthetic random walk (confirmed:
even a 4000-bar run with loss gates widened far past the owner's own
values produced 23 fills and never stacked) that reliably forcing it
would mean seed-hunting or loosening gates until one turns up —
tuning-against-synthetic-data in substance, which `CLAUDE.md` §4 rules
out, even without touching `baseline_v1` itself.

**Resolved:** `TestMultiplePositionsPermitted` drives
`SimulatedBroker.order_send()` directly — the same real broker/
`PositionState` machinery `TestIdempotency` above it already exercises —
rather than through the agent/strategy pipeline. It proves two things
end to end through real execution: a broker book can hold two concurrent
positions when two distinct orders are submitted, and the resulting real
`PositionState` book, valued by `assess_open_risk()` against the broker's
own reported equity, sits inside the owner's `max_open_risk`. What is
under test is the risk gateway's and broker's willingness to allow and
correctly value multiple positions, not the strategy's ability to
produce them on random data — the strategy was never the thing O-004
constrained.

The three acceptance examples in `OWNER_WORK_ORDERS_2026-09-02.md`
(1.0%+2.0%=3.0% passes, 1.1%+2.0%=3.1% blocks, several small positions
under budget pass regardless of count) are proved directly at the
`risk/policies.py::evaluate()` level in
`tests/unit/test_risk_engine.py::TestExposureLimits`, verbatim.

### 2.8 Persistence

`RiskSessionState.open_risk_fraction` widens to `Decimal | None`;
`to_payload()` is explicitly `None`-aware rather than risking the literal
string `"None"`. One migration
(`20260903_d3b2e828b5b0`, off head `03df83b062a6`) makes
`risk_session_states.open_risk_fraction` nullable — confirmed a single
Alembic head both before and after, with no collision against Dev 2's
`agent/contracts` branch. `recover_session()` never reads this field
(confirmed by direct read); recovery behaviour is unaffected, and its
only role remains audit. The `open_position_count`/`open_risk_fraction`
coupling that held since this table was written breaks for the first
time — a stop moved to breakeven now changes risk with no count change —
so `_persist_session`'s dedupe key now tracks
`open_risk_fraction is None` (an establishment-status flip, safety-
relevant) rather than the raw fraction (which would restore a
write-per-bar) or nothing at all.

## 3. What this does not do

- **D1.5 (session policy: daily → weekly/weekend-flat)** — a separate,
  later slice. `trading_window.py`, `IntradayPolicy`/`IntradayConfig`,
  `OVERNIGHT_EXPOSURE`, and `config/paper.yaml`'s `intraday:` block are
  untouched.
- **D1.6 (HALT reset human-only)** — already compliant, confirmed by
  direct read of `KillSwitch.reset()`; verification only, no code change.
- **Item 9 (broker-side stop-loss verification) / D1.8 (Settings seam)**
  — separate, later work. Item 9 is named above as the future escalation
  path for `OPEN_RISK_UNKNOWN`, not built here.
- **`agent_gateway/decision_path.py`'s own count-based approximation
  (Dev 2's D2.2)** — confirmed zero edits under `src/crumblr/agent_gateway/`
  in this slice (`PortfolioState.open_risk_fraction`'s widened type is
  source-compatible with its existing explicit-kwarg call site). Flagged
  in `review/INTEGRATION_NOTICES.md`: `agent_gateway/market_context.py
  ::AgentPlatformState.open_risk_fraction: RiskFraction | None` uses
  `RiskFraction`'s `gt=0` constraint, so a flat book and an *unestablished*
  assessment both have to serialize as `None` today — two genuinely
  different Core states collapsing into one agent-visible value. Dev 2's
  to reconcile as part of D2.2, not fixed here.
- **No `close_all_positions`/`order_send`/`feedback_2_0_approved` change**
  — every file this slice touches is risk/config/persistence logic;
  confirmed nowhere near a submission call
  (`git diff | grep order_send`/`close_all_positions` shows only a
  docstring mention naming D-050, no new call).
- **`risk.approved_config_version` stays unset** — a separate, later
  owner act, not implied by shipping these numbers.

## 4. Consequences

- `review/DEVIATIONS.md` D-013 moves to `PARTIALLY RESOLVED` (the four
  owner fractions are now confirmed; the three engineering-chosen fields
  — `max_orders_per_hour`, `max_open_positions`, `min_stop_distance_points`
  — remain their own, separate gap). New **D-053** (the `10` ceiling,
  with its revisit trigger). New **D-054** (the `OPEN_RISK_UNKNOWN`
  BLOCK-not-HALT choice and the residual `AgentPlatformState`
  three-states-into-two-slots gap). Numbered D-053/D-054 rather than the
  original plan's D-052/D-053 — Dev 2 shipped their own D-052 first
  (`agent/contracts` commit `bf49549`); coordinated directly and
  confirmed no collision.
- `status.md` gains **O-008**, recorded twice: the four owner numbers,
  and multiple-positions-allowed/O-004-withdrawn (superseding, not
  editing, the existing O-004 rows).
- `review/INTEGRATION_NOTICES.md` gains three entries: the new
  `ReasonCode.OPEN_RISK_UNKNOWN`/`SYMBOL_EXPOSURE_EXISTS` retirement;
  `PortfolioState.open_risk_fraction`'s widened type; the new
  `risk/portfolio_risk.py::assess_open_risk` seam and the
  `AgentPlatformState` flag for Dev 2's D2.2.
- A new Alembic head (`d3b2e828b5b0`), coordinated with Dev 2 in advance.
- Dev 2 notified once `assess_open_risk` shipped, to wire
  `PortfolioSnapshot.open_risk_fraction` against it on their own track.
