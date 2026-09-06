"""Small durable content-addressed registry for research-plane artifacts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import TypeVar

from crumblr.domain.models import Contract
from crumblr.research.hashing import research_fingerprint

Artifact = TypeVar("Artifact", bound=Contract)


class ArtifactConflictError(ValueError):
    """The same immutable identity was reused with different content."""


class JsonlResearchArtifactRegistry:
    """Append-only local registry suitable for shadow/research operation.

    The wire contracts remain storage-neutral. A PostgreSQL adapter can replace
    this class without changing the Trainer integration.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._records: dict[tuple[str, str], dict[str, object]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            key = (str(record["kind"]), str(record["identity"]))
            existing = self._records.get(key)
            if existing is not None and existing["fingerprint"] != record["fingerprint"]:
                raise ArtifactConflictError(f"conflicting durable artifact: {key}")
            self._records[key] = record

    def register(self, *, kind: str, identity: str, artifact: Contract) -> bool:
        payload = artifact.model_dump(mode="json")
        content_fingerprint = research_fingerprint(payload)
        record: dict[str, object] = {
            "kind": kind,
            "identity": identity,
            "fingerprint": content_fingerprint,
            "payload": payload,
        }
        key = (kind, identity)
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing["fingerprint"] != content_fingerprint:
                    raise ArtifactConflictError(
                        f"{kind} {identity} already exists with different content"
                    )
                return False
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._records[key] = record
            return True

    def get(self, *, kind: str, identity: str, contract: type[Artifact]) -> Artifact | None:
        with self._lock:
            record = self._records.get((kind, identity))
        if record is None:
            return None
        return contract.model_validate(record["payload"])

    def count(self, *, kind: str | None = None) -> int:
        with self._lock:
            if kind is None:
                return len(self._records)
            return sum(record_kind == kind for record_kind, _ in self._records)
