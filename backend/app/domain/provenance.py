"""Deterministic provenance keys — the backbone of idempotent processing (PRD 9.3).

``provenance_key = SHA256(case_id | doc_id | entity_type | normalized_value)``

Why this matters
----------------
The naive approach ("MERGE on name") is unsafe: re-processing a document that
crashed half-way would either duplicate a person or — far worse — silently
merge two *different* people who happen to share a common Indian name.

With a provenance key derived from the *document* that produced the entity:

* re-processing the same document N times is mathematically guaranteed to
  converge on the same graph state (no duplicates, no lost edges);
* two different documents mentioning the same name produce two different keys,
  hence two nodes, which are then routed to the human entity-resolution queue
  instead of being auto-merged.

The key produced here is the *candidate* key.  Entity resolution (stage 4) may
decide that a candidate should be folded into an existing node that carries a
different key; that decision is recorded and the injector writes to whatever
key resolution returned.  Either way, the mapping is a pure function of the
graph state, so idempotency is preserved.
"""

from __future__ import annotations

import hashlib
from typing import Iterable

SEP = "|"


def sha256_hex(*parts: str | bytes | int | float | None) -> str:
    """Stable SHA-256 over the given parts (``None`` is encoded as empty)."""
    h = hashlib.sha256()
    for part in parts:
        if part is None:
            h.update(b"\x00")
        elif isinstance(part, bytes):
            h.update(part)
        else:
            h.update(str(part).encode("utf-8"))
        h.update(SEP.encode("utf-8"))
    return h.hexdigest()


def candidate_key(
    case_id: str,
    doc_id: str,
    entity_type: str,
    normalized_value: str,
) -> str:
    """Deterministic key of an *extraction candidate* within a document."""
    return sha256_hex(case_id, doc_id, entity_type, normalized_value)


def edge_key(rel_type: str, source_pk: str, target_pk: str, *discriminators: str) -> str:
    """Deterministic key of a graph edge.

    ``discriminators`` separate edges that must stay distinct (e.g. two money
    transfers between the same account pair) from edges that are aggregated
    (e.g. every call between a phone pair collapses into one ``CALLED`` edge).
    """
    return sha256_hex(rel_type, source_pk, target_pk, *discriminators)


def content_hash(data: bytes) -> str:
    """SHA-256 of an uploaded document — the chain-of-custody fingerprint."""
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj: object) -> str:
    """Whitespace-stable, key-sorted JSON used for audit hash chaining.

    Audit rows must hash identically no matter which driver or locale produced
    them, so the serialisation is fully deterministic.
    """
    import json

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def chain_hash(prev_hash: str, canonical_row: str) -> str:
    """``SHA-256(prev_row_hash || canonical_row)`` (PRD 12.2)."""
    h = hashlib.sha256()
    h.update(prev_hash.encode("utf-8"))
    h.update(canonical_row.encode("utf-8"))
    return h.hexdigest()


GENESIS_HASH = "0" * 64


def join_keys(values: Iterable[str]) -> str:
    return ",".join(sorted(values))
