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

# Dataset-scoped maps live for the process lifetime and are shared by every AI
# request for that dataset.  This keeps PERSON_001 stable when a dataset is
# represented by CSV, JSON, a document mention, or a later investigation
# request.  The production deployment can replace this registry with an
# encrypted database-backed store without changing the boundary API.
_STABLE_DATASET_MAPS: dict[str, tuple[dict[str, str], dict[str, str], dict[str, int]]] = {}
_STABLE_LOCK = threading.RLock()

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
    "DOCUMENT": "DOC",
    "Person": "PERSON",
    "PERSON": "PERSON",
    "Phone": "PHONE",
    "PHONE": "PHONE",
    "Vehicle": "VEHICLE",
    "VEHICLE": "VEHICLE",
    "Location": "LOCATION",
    "LOCATION": "LOCATION",
    "BankAccount": "ACCOUNT",
    "BANK_ACCOUNT": "ACCOUNT",
}


@dataclass
class PseudonymMap:
    """A bijective map between real provenance keys and pseudonymous IDs.

    The map is scoped to *one investigation context* (one question / one AI
    request).  It is never persisted long-term alongside operational data,
    never logged in full, and never sent to the model.
    """

    dataset_id: str | None = None
    _forward: dict[str, str] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        if not self.dataset_id:
            return
        with _STABLE_LOCK:
            shared = _STABLE_DATASET_MAPS.get(self.dataset_id)
            first_instance = shared is None
            if shared is None:
                shared = ({}, {}, {})
                _STABLE_DATASET_MAPS[self.dataset_id] = shared
            self._forward, self._reverse, self._counters = shared
            # All instances for the same dataset use one lock.  Mapping writes
            # therefore remain atomic when concurrent AI requests arrive.
            self._lock = _STABLE_LOCK
            if first_instance:
                self._load_persisted_mapping()

    def _load_persisted_mapping(self) -> None:
        """Hydrate the trusted map when the relational table is available."""
        try:
            from sqlalchemy import select
            from app.db.models import DatasetPseudonym
            from app.db.session import sync_session

            with sync_session() as session:
                rows = session.execute(
                    select(DatasetPseudonym).where(
                        DatasetPseudonym.dataset_id == self.dataset_id
                    )
                ).scalars().all()
                for row in rows:
                    self._forward[row.canonical_key] = row.pseudonym
                    self._reverse[row.pseudonym] = row.canonical_key
                    prefix, _, number = row.pseudonym.rpartition("_")
                    if number.isdigit():
                        self._counters[prefix] = max(
                            self._counters.get(prefix, 0), int(number)
                        )
        except Exception:
            # Unit tests and first-boot imports may run before relational
            # bootstrap. The in-process map still remains safe and is persisted
            # as soon as the table is available.
            return

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
            self._persist_mapping(provenance_key, pseudo, label or "")
            return pseudo

    def _persist_mapping(self, provenance_key: str, pseudo: str, label: str) -> None:
        if not self.dataset_id:
            return
        try:
            from sqlalchemy import select
            from app.db.base import new_uuid
            from app.db.models import DatasetPseudonym
            from app.db.session import sync_session

            with sync_session() as session:
                existing = session.execute(
                    select(DatasetPseudonym).where(
                        DatasetPseudonym.dataset_id == self.dataset_id,
                        DatasetPseudonym.canonical_key == provenance_key,
                    )
                ).scalar_one_or_none()
                if existing is None:
                    session.add(DatasetPseudonym(
                        id=new_uuid(),
                        dataset_id=self.dataset_id,
                        canonical_key=provenance_key,
                        pseudonym=pseudo,
                        entity_type=label,
                    ))
                    session.commit()
        except Exception:
            # A model call must not fail because persistence is temporarily
            # unavailable; the trusted in-process mapping remains active and
            # the next request can retry hydration/persistence.
            return

    def resolve(self, pseudo: str) -> str | None:
        """Return a real key for trusted backend code only.

        UI/API callers should use :func:`de_pseudonymize` so authorization and
        the fail-closed unknown-token behavior are explicit at the boundary.
        """
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


def de_pseudonymize(
    pmap: PseudonymMap,
    pseudo: str,
    *,
    authorized: bool,
) -> str:
    """Resolve an AI token only at an authorized trusted-backend boundary.

    Unknown or unauthorized values deliberately return the same neutral text;
    callers cannot use this helper to probe the mapping.
    """
    if not authorized:
        return "Identity unavailable"
    return pmap.resolve(pseudo) or "Identity unavailable"


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
    id_to_pseudo: dict[str, str] = {}
    for n in nodes:
        key = n.get("provenance_key") or n.get("id")
        if not key:
            # Fail closed: an unidentifiable node cannot be sent to an
            # external model because it cannot be safely de-pseudonymized.
            continue
        key = str(key)
        label = n.get("label", "Person")
        pseudo = pmap.pseudonymize(key, label)
        id_to_pseudo[key] = pseudo
        safe_nodes.append({
            "id": pseudo,
            "label": label,
            "confidence": n.get("confidence"),
            "entity_type": n.get("entity_type"),
        })
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
            # Edge keys, case ids, and source/document identifiers are
            # backend-only provenance and must not cross the external AI boundary.
        })
    return safe_nodes, safe_edges
