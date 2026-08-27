"""External-agent trust boundary (ADR-005, review/EXTERNAL_AGENT_ARCHITECTURE_GUIDE.md).

Step A only: the contracts in `contracts.py` are the first-draft, reviewable
shapes an external Agent Gateway will eventually validate against. Nothing
in `src/` outside this package imports from it yet — there is no Gateway
service, no auth, no mapping from a `TradeProposal` to a platform-owned
`TradeIntent`. That is Step B, a separate, later pass.
"""

from __future__ import annotations
