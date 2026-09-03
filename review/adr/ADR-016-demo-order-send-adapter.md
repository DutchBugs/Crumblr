# ADR-016 — A real, but separate and unwired, DEMO `order_send` adapter (Phase B items B1+B2)

**Status:** ACCEPTED — implemented and tested; not constructed or
referenced anywhere in `application/execution.py`; `order_send` remains
structurally unreachable from the live orchestrator
**Date:** 2026-09-03
**Drivers:** Owner/reviewer coordination order
`review/OWNER_WORK_ORDERS_DEMO_CANARY_2026-09-03.md`, Phase B items B1
("keep the current non-sending adapter permanently obvious… add a
separate, explicitly named DEMO mutation capability/adapter") and B2
("implement exactly one real submission side effect")
**Supersedes:** nothing. Extends Phase 4/ADR-001's `OrderCheckMt5Gateway`
without modifying it.
**Implementation:** `src/crumblr/mt5_gateway/execution.py`,
`src/crumblr/mt5_gateway/client.py`,
`src/crumblr/mt5_gateway/demo_execution.py` (new)

---

## 1. The decision being recorded

B1's own wording: *"Prefer leaving `OrderCheckMt5Gateway` as an
order-check-only adapter. Add a separate, explicitly named DEMO mutation
capability/adapter rather than turning an old non-sending type into a
sending type invisibly. The real mutation adapter must refuse any
non-DEMO account/environment even if a caller is wrong."*

B2 asks for the one real submission side effect, in a specific required
order that includes two steps this platform does not have yet: "acquire
one shared execution/Risk authority (Phase C / AG-012)" and "atomically
claim one-shot canary permit." **Scope decision, made with the user
before implementation:** rather than inventing throwaway placeholder
gates for those two missing pieces, this slice stops exactly where the
chain already stops today — a fully real, tested, standalone
`order_send` adapter exists, but nothing in `ExecutionOrchestrator`
holds a reference to it. `_process()` is bit-identical to before this
slice; `_start_submission()` still appends `SUBMISSION_STARTED` and
returns. Wiring the real call site in is deferred to a later slice, once
the account pin (B7), the permit (B8) and AG-012 actually exist.

## 2. The mechanism

### 2.1 `DemoOrderSendMt5Gateway` — composition, not modification

`mt5_gateway/demo_execution.py::DemoOrderSendMt5Gateway` wraps an
`OrderCheckMt5Gateway` instance by composition — every read and
`order_check` call delegates unchanged. It adds exactly one real
capability: `order_send`. `cancel_pending_orders`/`close_all_positions`
still delegate to the wrapped gateway's own refusing implementations
(Phase B item B5's scope, not this one's). This is D-036's original
"execution is a separate adapter, not a modification of the read-only
one" precedent applied one layer further.

### 2.2 The demo-only guard needed no new mechanism

`order_check()` was confirmed, by direct read, to do **no** demo/account
verification of its own — that guard lives in `ReadOnlyMt5Gateway
._verify_account()`, run only when `.account()` is called.
`order_send()` calls `self.account()` as its very first step, before
building or sending anything. This is enough: `_verify_account` already
raises `AccountGuardError` on a non-demo account, wrong server, wrong
login, wrong currency or wrong leverage — the same, already-tested
mechanism every other real call in this codebase trusts. B1's "must
refuse any non-DEMO account even if a caller is wrong" requirement is
satisfied by correct sequencing, not new code.

### 2.3 One shared request builder — and a deliberate change to `order_check`

`build_market_order_request()` (new, `mt5_gateway/execution.py`) is
extracted from `order_check()`'s own request-dict-building lines and
reused by both `order_check` and the new `order_send`. This is not
merely DRY: it is a correctness property. `order_check`'s entire purpose
is to validate the *exact* request `order_send` would submit — two
similar-but-not-identical dict literals would mean `order_check` was
never actually checking what matters.

**Deliberate, called-out behaviour change:** the shared builder includes
`"magic": order.magic_number` — a field `order_check`'s own request
never carried before. `ApprovedOrder.magic_number` (ADR-007, core
critical path item 5) is a computed field the entire ambiguous-recovery
and reconciliation chain (items 6-9) depends on to find a resulting
broker position after a real send. Omitting it from `order_send` was
never an option; including it in `order_check` too, rather than
special-casing it out, is what makes the two calls genuinely equivalent.
`magic` is additive and does not affect margin/rejection semantics.
Verified, not merely asserted safe: `test_mt5_execution_gateway.py`'s
existing `order_check` tests were extended to assert the field's
presence, and a new `DemoOrderSendMt5Gateway` test drives both calls
against the identical order and asserts the two resulting request dicts
are equal.

### 2.4 `Mt5Module` gains `order_send`/`TRADE_RETCODE_DONE_PARTIAL`

Pure Protocol-surface declarations. `load_mt5_module()` `cast()`s the
real `MetaTrader5` package to this Protocol — the real package already
has both; nothing about the real terminal's own capability changes.
Every fake `Mt5Module` implementation across the test suite
(`test_mt5_readonly_gateway.py`, `test_mt5_probe.py`,
`test_live_reader.py`, `test_mt5_execution_gateway.py`,
`tests/integration/_execution_fixtures.py`) was updated to satisfy the
wider Protocol — the read-only-scoped fakes gained an assertion-raising
`order_send`, matching their existing `order_check` idiom exactly; the
execution-scoped fakes (which already had a real or
assertion-raising `order_send`) gained only the new retcode constant.

### 2.5 `state` classification is intentionally modest

`order_send()`'s `ExecutionResult.state` is a best-effort three-way
split (`FILLED`/`PARTIALLY_FILLED`/`REJECTED` by retcode), matching
`order_check`'s own DONE-vs-not-DONE precision level — not a full
`OrderState` transition model. Phase B item B3 ("normalize definite
broker outcomes honestly": rejection, accepted/submitted, broker
acknowledgement, full fill, partial fill, transport exception/timeout as
distinct durable, orchestrator-level semantics) is the separate, later
slice that does that work. This method's job is a real, honest,
correctly-shaped adapter call — not the durable event model built on top
of it. `mt5_position_ticket` is deliberately left `None`: determining
which resulting broker position belongs to a request is exactly what
the existing magic-number-based ambiguous-recovery mechanism does one
layer up, not something this adapter should guess at from the
`order_send` response alone.

### 2.6 Idempotency is a system property, not an adapter one

`BrokerPort.order_send`'s own docstring requires: *"resubmitting the
same request must never create a second position."* This adapter does
not implement any deduplication itself — it does not need to. The
property is satisfied at the orchestrator level: `_start_submission()`
is called at most once per `order_request_id` (B2's own "no automatic
retry" instruction — once `SUBMISSION_STARTED` exists, uncertainty goes
to broker-state recovery, never a resubmit), so `order_send` itself is
never called twice for the same request by construction of the caller,
not the callee.

## 3. build.md §7 gateway invariants — which apply today, which are deferred

Of build.md's ten numbered gateway invariants: (1) credentials-scope and
(2) idempotency-key-per-mutation are concretely satisfied by this slice
(`magic` = `order.magic_number`, always). (3) pre-order risk validation,
(4) order_check preceding order_send, (6) reconciliation after every
mutation, (7) unknown-state-halts and (8) reconnect-never-replays are
all properties of the *full wired chain* B2's later slice builds — not
exercised yet, since nothing calls this adapter in production. (5)
"every `order_send` result is persisted" is **not yet true** and is not
claimed to be: this slice's `order_send` returns a real `ExecutionResult`
value, but nothing durably records it, because nothing calls it. This is
explicitly deferred to the slice that wires the real call site in, not
silently skipped.

## 4. What this does not do

- Does not touch `application/execution.py::ExecutionOrchestrator` —
  no new construction site, no type-hint change, no call to
  `DemoOrderSendMt5Gateway.order_send`. Verified directly:
  `tests/unit/test_demo_order_send_gateway.py
  ::TestNotWiredIntoTheOrchestrator` asserts the orchestrator's own
  source contains no reference to the new class at all.
- Does not build Phase B items B3 (durable outcome normalization), B5
  (real per-ticket close), B7 (account pin), B8 (canary permit), or
  Phase C (AG-012, shared execution/Risk authority) — separate, later
  slices.
- Does not add any deduplication/idempotency logic inside the adapter
  (§2.6).
- Does not change `tests/integration/_execution_fixtures.py::FakeMt5`'s
  own behaviour — its `order_send` still raises
  `AssertionError("order_send must never be called")`, still true,
  since the orchestrator still never calls it.
- No shipped config change; `submission_enabled`/`feedback_2_0_approved`
  stay `false` everywhere and are irrelevant to this slice regardless,
  since nothing routes through the new adapter from the orchestrator.

## 5. Consequences

- No new `review/DEVIATIONS.md` entry — checked against `build.md` §7's
  gateway invariants (see §3 above) before concluding this: nothing here
  contradicts the spec, it simply has not yet exercised the invariants
  that only apply once the chain is wired end to end.
- `status.md` records this as a Dev-1 Phase-B, slice-2 deliverable — no
  new O-number needed, same reasoning as prior Phase-B/item-9 ADRs.
- `review/INTEGRATION_NOTICES.md`: no cross-track call-site signature
  changes to Core application code. Dev 2 notified informationally that
  `Mt5Module`'s Protocol surface grew, in case any fake/mock of it
  exists on their side (confirmed by grep: `agent_gateway/` does not
  touch `mt5_gateway/` at all today, so this is precautionary).
