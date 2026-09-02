"""Ports (structural interfaces) for the persistence tier.

The ports exist so that the pipeline, analytics and API layers never import a
driver.  Swapping Neo4j for the embedded graph — or MinIO for the local
filesystem — is a configuration change, not a code change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class GraphPayload:
    """Cytoscape.js-compatible subgraph payload (PRD 10)."""

    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "truncated": self.truncated,
        }


@dataclass(slots=True)
class ObjectMeta:
    key: str
    size: int
    content_type: str = "application/octet-stream"
    etag: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GraphStore(Protocol):
    """Write-once-with-evidence, read-many graph interface."""

    backend_name: str

    # ---------------------------------------------------------------- writes
    def upsert_nodes(self, nodes: list[Any]) -> int:
        """Insert or refresh nodes keyed by ``provenance_key``."""

    def upsert_edges(self, edges: list[Any]) -> int:
        """Insert or aggregate edges keyed by their deterministic edge key."""

    def ensure_case_node(self, case_id: str, case_number: str, jurisdiction_id: str) -> None:
        """Create/refresh the ``(:Case)`` anchor node for a case."""

    # ----------------------------------------------------------------- reads
    def get_node(self, provenance_key: str) -> Any | None: ...

    def get_nodes(self, provenance_keys: list[str]) -> dict[str, Any]: ...

    def expand(
        self,
        root_key: str,
        rel_types: list[str] | None = None,
        depth: int = 1,
        limit: int = 300,
    ) -> GraphPayload: ...

    def search(
        self,
        query: str,
        labels: list[str] | None = None,
        case_id: str | None = None,
        limit: int = 50,
    ) -> list[Any]: ...

    def snapshot(self, case_id: str, include_inactive: bool = False) -> Any: ...

    def timeline(
        self,
        case_id: str,
        from_ts: str | None = None,
        to_ts: str | None = None,
        participant: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def stats(self) -> dict[str, Any]: ...

    # ---------------------------------------------------- entity resolution
    def find_by_hard_identifier(
        self, entity_type: str, normalized_value: str, case_id: str | None = None
    ) -> str | None:
        """Return the canonical key of an existing node sharing a hard identifier."""

    def candidate_persons(self, case_id: str, exclude_key: str | None = None) -> list[Any]: ...

    def add_potential_alias(
        self, source_key: str, target_key: str, queue_id: str, similarity: float
    ) -> None: ...

    def tombstone_reject(self, source_key: str, target_key: str, resolved_by: str) -> None: ...

    def has_tombstone(self, source_key: str, target_key: str) -> bool: ...

    def merge_persons(
        self, keep_key: str, absorb_key: str, actor_id: str, queue_id: str | None = None
    ) -> Any:
        """Re-route every edge from *absorb_key* onto *keep_key*.

        Reversible: the absorbed node is deactivated, not deleted, and a
        ``MERGED_INTO`` marker records where its edges went (PRD 9.2).
        """

    def unmerge_persons(self, kept_key: str, absorbed_key: str, actor_id: str) -> Any:
        """Undo a merge using the ``MERGED_INTO`` marker (wrongful merges are
        the worst failure mode, so every merge must be reversible)."""

    # --------------------------------------------------------------- caching
    def invalidate_cache(self, case_id: str) -> None: ...


@runtime_checkable
class ObjectStore(Protocol):
    """Write-once object storage for original documents and derived artifacts."""

    backend_name: str

    def put(self, bucket: str, key: str, data: bytes, content_type: str = "application/octet-stream") -> ObjectMeta: ...

    def get(self, bucket: str, key: str) -> bytes: ...

    def stat(self, bucket: str, key: str) -> ObjectMeta | None: ...

    def exists(self, bucket: str, key: str) -> bool: ...

    def presigned_url(self, bucket: str, key: str, expires_s: int) -> str: ...

    def list_keys(self, bucket: str, prefix: str = "") -> list[str]: ...
