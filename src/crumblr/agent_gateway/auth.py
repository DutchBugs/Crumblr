"""Interim agent authentication — a shared-secret credential, not the final

cryptographic identity `THREAT_MODEL_AGENT_GATEWAY.md` §4.1/§7 (AG-001)
describes (mTLS/SPIFFE-shaped, matching `AgentIdentity.service_identity`'s
own naming). Building real mTLS is out of scope for this pass; what this
module gives instead is a real, testable, fail-closed boundary — a secret
only Crumblr and the registered agent know, hashed at rest, checked in
constant time — so Step B is not authenticating on "an `agent_id` was
supplied" alone. Swapping this for certificate-based auth later changes
`AgentGateway.authenticate`'s implementation only; every caller and every
fail-closed test around it stays the same shape.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_SALT_BYTES = 16


def hash_credential(secret: str) -> str:
    """`salt_hex:digest_hex` — a fresh random salt per call, so two agents

    (or two rotations of the same agent's secret) never produce the same
    stored hash even if they somehow chose the same secret."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.sha256(salt + secret.encode("utf-8")).hexdigest()
    return f"{salt.hex()}:{digest}"


def verify_credential(*, secret: str, stored_hash: str) -> bool:
    """Constant-time comparison — a credential check must not leak timing

    information about how much of the secret matched."""
    try:
        salt_hex, digest_hex = stored_hash.split(":", 1)
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    candidate = hashlib.sha256(salt + secret.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate, digest_hex)
