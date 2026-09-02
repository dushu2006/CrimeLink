"""Reversible application-level pseudonymization.

The privacy architecture is:

* CrimeLink separates authoritative source data from AI reasoning through a
  controlled AI data boundary.
* Relevant graph-derived context is minimized and pseudonymized before being
  sent to AI models.
* The trusted backend retains the mapping and can resolve authorized
  pseudonymous identifiers when presenting results to investigators.

This module implements the reversible map.  It is intentionally NOT a
cryptographic hash: hashing is one-way and would prevent authorized
de-pseudonymization, which is required to surface actual evidence to an
investigator after human review.

Pseudo-IDs look like ``PERSON_023``, ``PHONE_041``, ``VEHICLE_009`` — stable
within the lifetime of a session/request, never reused across unrelated
investigations.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Iterable

from app.domain.enums import EntityType


PREFIX_BY_LABEL: dict[str, str] = {
    EntityType.PERSON.value: "PERSON",
    EntityType.PHONE.value: "PHONE",
    EntityType.VEHICLE.value: "VEHICLE",
    EntityType.LOCATION.value: "LOCATION",
    EntityType.BANK_ACCOUNT.value: "ACCOUNT",
    EntityType.ORGANIZATION.value: "ORG",
    EntityType.EVENT.value: "EVENT",
    "Case": "CASE",
    "Document": "DOC",
}


@dataclass
class PseudonymMap:
    """A bijective map between real provenance keys and pseudonymous IDs.

    The map is scoped to *one investigation context* (one question / one AI
    request).  It is never persisted long-term alongside operational data,
    never logged in full, and never sent to the model.
    """

    _forward: dict[str, str] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def pseudonymize(self, provenance_key: str, label: str | None = None) -> str:
        """Return a stable pseudo-ID for the given provenance key."""
        with self._lock:
            existing = self._forward.get(provenance_key)
            if existing is not None:
                return existing
            prefix = PREFIX_BY_LABEL.get(label or "Person", "NODE")
            n = self._counters.get(prefix, 0) + 1
            self._counters[prefix] = n
            pseudo = f"{prefix}_{n:03d}"
            # ensure uniqueness
            while pseudo in self._reverse:
                n += 1
                self._counters[prefix] = n
                pseudo = f"{prefix}_{n:03d}"
            self._forward[provenance_key] = pseudo
            self._reverse[pseudo] = provenance_key
            return pseudo

    def resolve(self, pseudo: str) -> str | None:
        """Return the real provenance key for a pseudo-ID (authorized only)."""
        return self._reverse.get(pseudo)

    def resolve_many(self, pseudos: Iterable[str]) -> list[str | None]:
        return [self.resolve(p) for p in pseudos]

    def contains(self, provenance_key: str) -> bool:
        return provenance_key in self._forward

    def is_empty(self) -> bool:
        return not self._forward

    def entries(self) -> dict[str, str]:
        """Return a copy of the forward mapping (auditing/debugging only)."""
        return dict(self._forward)

    def __len__(self) -> int:
        return len(self._forward)


def apply_pseudonymization_to_context(
    nodes: list[dict],
    edges: list[dict],
    pmap: PseudonymMap,
) -> tuple[list[dict], list[dict]]:
    """Return (nodes, edges) with provenance keys replaced by pseudo-IDs.

    Input dicts should carry a ``provenance_key`` (or ``id``) and ``label``.
    Sensitive display values (names, plate numbers, phone numbers, addresses,
    account numbers) are REMOVED from the context — only the pseudo-ID, the
    label and the relationship type/weight/timestamps remain.  This enforces
    data minimization alongside pseudonymization.
    """
    safe_nodes: list[dict] = []
    for n in nodes:
        key = n.get("provenance_key") or n.get("id")
        label = n.get("label", "Person")
        pseudo = pmap.pseudonymize(key, label)
        safe_nodes.append({
            "id": pseudo,
            "label": label,
            "confidence": n.get("confidence"),
            "entity_type": n.get("entity_type"),
            "case_id": n.get("case_id"),
        })
    id_to_pseudo = {n["provenance_key"] if "provenance_key" in n else n["id"]: n_out["id"]
                    for n, n_out in zip(nodes, safe_nodes)}
    # also populate from map in case edges reference nodes not included
    for real, pseudo in pmap.entries().items():
        id_to_pseudo.setdefault(real, pseudo)
    safe_edges: list[dict] = []
    for e in edges:
        src_p = id_to_pseudo.get(e.get("source_key") or e.get("source"))
        tgt_p = id_to_pseudo.get(e.get("target_key") or e.get("target"))
        if not src_p or not tgt_p:
            continue
        safe_edges.append({
            "source": src_p,
            "target": tgt_p,
            "rel_type": e.get("rel_type"),
            "confidence": e.get("confidence"),
            "timestamp": e.get("timestamp"),
            "discriminator": e.get("discriminator"),
            "source_doc_ids": e.get("source_doc_ids", []),
        })
    return safe_nodes, safe_edges
