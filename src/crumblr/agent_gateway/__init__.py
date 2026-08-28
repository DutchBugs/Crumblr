"""External-agent trust boundary (ADR-005, review/EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md).

Step A: the contracts in `contracts.py` are the reviewable shapes an
external agent's identity, assignment, context and proposals take.

Step B (`gateway.py`, `auth.py`, `stores.py`, `errors.py`, `events.py`):
the Agent Gateway itself — identity/credential authentication, server-side
assignment authorization, context-hash binding, idempotent proposal/
NO_TRADE claiming, fail-closed audit trail. Still no mapping from an
accepted `TradeProposal` to a platform-owned `TradeIntent` (AG-006,
`review/AGENT_FEEDBACK.md` — blocked on a shared-contract handshake with
Dev 1) and no wire transport — everything is proven by calling
`AgentGateway` directly, from tests. Nothing in `src/` outside this
package imports from it yet.
"""

from __future__ import annotations
