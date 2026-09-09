"""Local Host Admin: a localhost-only administration surface for this one

Windows host's runtime secrets and non-secret settings.

Deliberately separate from `crumblr.dashboard` (read-only observability,
`fastapi` GET routes only) and from every production decision path. Nothing
in this package imports `crumblr.risk`, `crumblr.persistence.safety_state`,
or `crumblr.agent_gateway.gateway` -- see `tests/unit/test_local_admin_boundary.py`
for the structural proof, not only the intent recorded here.
"""

from __future__ import annotations
