# ADR-007 — `order_send` idempotence: MT5 magic-number derivation (F-007-adjacent, core critical path item 5)

**Status:** ACCEPTED — implemented and tested; `order_send` still unbuilt
**Date:** 2026-09-01
**Drivers:** `build.md` §7 invariants 2/8, review 1.20 §10, review 1.21 §12,
review 1.25/1.26 §6/1.27 §8 (all list "order_send idempotence" as Dev-1
core critical path item 5)
**Supersedes:** nothing. `mt5_gateway/port.py::order_send()`'s docstring
already stated the requirement this ADR gives a mechanism for.
**Implementation:** `src/crumblr/domain/hashing.py::mt5_magic_number()`,
`src/crumblr/domain/models.py::ApprovedOrder.magic_number`,
`tests/unit/test_control_plane_contracts.py::TestApprovedOrder`

---

## 1. The decision being recorded

`build.md` §7 invariant 2: "every mutation has an idempotency key."
Invariant 8: "a reconnect never silently replays an order."
`mt5_gateway/port.py::order_send()`'s own docstring already requires
implementations to be "idempotent on `order.order_request_id`:
resubmitting the same request must never create a second position" —
but no mechanism for achieving that has ever existed in this codebase.

**What was already built and does not need rebuilding**: the
durable-identity half. `order_request_id` is a content-derived key
(`uuid5(..., f"crumblr:order:{intent.decision_hash}")`), atomically
claimed before any broker interaction (`ExecutionRequestStore.claim()`),
and the platform durably records its commitment to attempt submission
(`ExecutionEventType.SUBMISSION_STARTED`, core critical path item 3,
content-conflict-hardened as of item 4). Review 1.20 §10's required
recovery order names "persist request identity" as its first step — all
of that already exists.

**What was missing**: the MT5-visible half. MT5's `order_send()` has no
native idempotency-key concept — `order_request_id` (a UUID) means
nothing to the broker. Confirmed by direct search before writing any
code: nothing anywhere in this repo has ever populated an MT5 order
request's `magic` field, `ApprovedOrder` has no `magic`/`comment` field,
and the only working `order_send` implementation
(`mt5_gateway/simulated.py::SimulatedBroker`) uses an in-process Python
dict as its idempotence mechanism — real for tests, but would not
survive an actual process restart, which is exactly the scenario
idempotence exists to protect against.

Without a broker-visible identifier, the *next* item on this list
(ambiguous-outcome recovery — "query durable request state → reconcile
broker state → determine whether the request already took effect")
cannot be built at all: reconciliation needs something to search broker
state *by*.

## 2. The mechanism

`domain/hashing.py::mt5_magic_number(order_request_id: UUID) -> int`:
a deterministic function of `order_request_id` alone, via
`fingerprint({"order_request_id": str(order_request_id)})`, taking the
first 8 hex characters (32 bits) of the digest and masking to 31 bits
(`& 0x7FFFFFFF`).

`ApprovedOrder.magic_number` (`domain/models.py`) is a `@computed_field`
calling this function — not a new required field, no change needed at
any existing `ApprovedOrder(...)` construction site, and it automatically
appears in `model_dump(mode="json")` output. Concretely: `SUBMISSION_STARTED`'s
already-shipped "complete `ApprovedOrder` content" payload (item 3) now
carries the magic number for free, with zero change to the code that
builds that payload — the durable commitment record durably shows the
exact broker-facing identifier a future submission would carry.

### Why 31 bits, not the schema's full 64-bit `BigInteger` width

No real Pepperstone/MT5 terminal evidence exists for this field's actual
constraints, and none can be gathered without submitting a real order —
exactly what this platform must not yet do. Rather than assume a wider
range is safe, this deliberately picks a narrower, universally-compatible
one: always non-negative, fits both signed and unsigned 32-bit
interpretations regardless of how a given broker/API layer treats the
field. The same "decode from observation, never hardcode an MT5
assumption" discipline `review/DEVIATIONS.md` D-037 already established
for filling-mode/trade-mode decoding, applied here to a field no
observation has ever been possible for.

### Collision risk

~2.1 billion possible values (31 bits). Collision risk across this
platform's realistic order volume is negligible — the same acceptance
already applied to `AccountState.login_hash`'s narrower 64-bit-to-16-hex-char
truncation in the same file's neighborhood, for the same reason: this is
an identity-correlation aid, not a security boundary, and the input
space (one value per distinct, already-unique `order_request_id`) is
far smaller than the output space.

### Why a standalone function, not only a model property

Item 6 (ambiguous-outcome recovery, not yet built) will need to call the
*identical* derivation to know what magic value to search broker
positions/orders for during reconciliation. Placing it in
`domain/hashing.py` makes it a reusable utility both sides share, rather
than something reconciliation code would import `ApprovedOrder` only to
reach.

## 3. What this does not do

**`order_send` remains completely unbuilt and unreachable.**
`OrderCheckMt5Gateway.order_send` (`mt5_gateway/execution.py`) is still
the same unconditional raise (`ExecutionDisabledError`), touching
neither its argument nor MT5. The `Mt5Module` Protocol
(`mt5_gateway/client.py`) still deliberately excludes `order_send` from
its surface — "so there is nothing here for a future caller to reach for
by accident." `OrderCheckMt5Gateway.order_check()`'s real, reachable
request dict is unchanged — this ADR does not add `magic`/`comment` to
it; that is a reasonable future consistency step, deliberately not taken
here, since touching an already-real broker-call site is a separate,
more deliberate decision than adding an inert derived field.

No reconciliation or broker-query logic uses this magic number yet —
that is item 6's job. This item only builds the derivation both sides
will eventually share, so that when item 6 is built, it has something
real to search for.

## 4. Consequences

- `review/FEEDBACK.md`'s "core submission-safety phase" tracking updates
  — item 5 done, four items remain (ambiguous-outcome recovery,
  automatic flatten submission, post-fill reconciliation, broker-side SL
  verification).
- Every `ApprovedOrder`-derived payload persisted from this point
  forward (currently: `SUBMISSION_STARTED`'s payload) carries a
  `magic_number` field. This is additive to `model_dump()` output — no
  existing exact-payload-equality test anywhere in the repo asserts a
  closed set of `ApprovedOrder` keys, confirmed by grep before shipping.
- Item 6, whenever it is built, should call `mt5_magic_number()` directly
  rather than deriving its own value — the whole point of this ADR is
  that both sides compute the same number independently from the same
  `order_request_id`, with nothing persisted separately to keep in sync.
