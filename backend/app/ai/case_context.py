"""Authoritative case-scoped context validation for Case RAG.

The graph and document adapters are responsible for doing the primary case
filter.  This module is the second, deterministic boundary immediately before
retrieval ranking and prompt construction.  It deliberately knows nothing
about an LLM: an item either has provenance for the requested case or it is
removed from the context.

A canonical entity can legitimately occur in several cases.  ``case_ids`` on a
node therefore means *membership*, not that every source record for that node
belongs to every case.  Relationships are stricter: the relationship itself
must carry ``case_ids``/``case_scope`` for the requested case.  Endpoint
membership is not sufficient provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class CaseContextStats:
    """Deterministic counts for the requested case, never model-generated."""

    case_id: str
    evidence_count: int
    entity_count: int
    person_count: int
    relationship_count: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "case_id": self.case_id,
            "evidence_count": self.evidence_count,
            "entity_count": self.entity_count,
            "person_count": self.person_count,
            "relationship_count": self.relationship_count,
        }


@dataclass
class CaseContextValidation:
    """Validated context plus diagnostic counts for structured logging."""

    case_id: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    documents: list[dict[str, Any]] = field(default_factory=list)
    filtered_out: list[dict[str, str]] = field(default_factory=list)

    @property
    def filtered_out_count(self) -> int:
        return len(self.filtered_out)

    @property
    def stats(self) -> CaseContextStats:
        documents = {
            str(item.get("doc_id"))
            for item in self.documents
            if item.get("doc_id")
        }
        persons = sum(
            1 for item in self.nodes if str(item.get("label", "")).upper() == "PERSON"
        )
        return CaseContextStats(
            case_id=self.case_id,
            evidence_count=len(documents),
            entity_count=len(self.nodes),
            person_count=persons,
            relationship_count=len(self.edges),
        )


def _scope_values(item: dict[str, Any], *, properties: bool = False) -> set[str]:
    """Read all supported case provenance spellings from one record."""

    source = item.get("properties", {}) if properties else item
    if not isinstance(source, dict):
        return set()
    values: set[str] = set()
    for key in ("case_ids", "case_scope"):
        raw = source.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.update(str(value) for value in raw if value not in (None, ""))
        elif raw not in (None, ""):
            values.add(str(raw))
    for key in ("case_id",):
        raw = source.get(key)
        if raw not in (None, ""):
            values.add(str(raw))
    return values


def _matches_case(
    item: dict[str, Any],
    case_id: str,
    *,
    properties: bool = False,
    require_scope: bool = True,
) -> tuple[bool, str]:
    values = _scope_values(item, properties=properties)
    if values:
        return case_id in values, "case_scope_mismatch" if case_id not in values else ""
    # A legacy adapter may return a record from an already case-scoped snapshot
    # without repeating the scope on every row.  It is accepted by the gateway
    # only as a compatibility path; graph/document adapters now stamp scope.
    if require_scope:
        return False, "missing_case_provenance"
    return True, ""


def validate_case_context(
    case_id: str,
    *,
    nodes: Iterable[dict[str, Any]] = (),
    edges: Iterable[dict[str, Any]] = (),
    documents: Iterable[dict[str, Any]] = (),
    require_scope: bool = True,
) -> CaseContextValidation:
    """Return only records that can be used for ``case_id``.

    ``require_scope`` defaults to strict mode: every item must carry explicit
    case provenance.  The optional compatibility override is available only to
    callers that have already established an equivalent case-scoped boundary;
    the live gateway never uses it.
    """

    requested = str(case_id or "").strip()
    if not requested:
        raise ValueError("A case-scoped context requires a non-empty case_id")

    result = CaseContextValidation(case_id=requested)
    for item in nodes:
        if not isinstance(item, dict):
            result.filtered_out.append({"kind": "node", "reason": "invalid_item"})
            continue
        ok, reason = _matches_case(
            item, requested, properties=True, require_scope=require_scope
        )
        if ok:
            result.nodes.append(item)
        else:
            result.filtered_out.append(
                {
                    "kind": "node",
                    "id": str(item.get("provenance_key") or item.get("id") or ""),
                    "reason": reason,
                }
            )

    allowed_nodes = {
        str(item.get("provenance_key") or item.get("id"))
        for item in result.nodes
        if item.get("provenance_key") or item.get("id")
    }
    for item in edges:
        if not isinstance(item, dict):
            result.filtered_out.append({"kind": "relationship", "reason": "invalid_item"})
            continue
        ok, reason = _matches_case(item, requested, require_scope=require_scope)
        source = str(item.get("source_key") or item.get("source") or "")
        target = str(item.get("target_key") or item.get("target") or "")
        if ok and (source not in allowed_nodes or target not in allowed_nodes):
            ok, reason = False, "endpoint_outside_case_context"
        if ok:
            result.edges.append(item)
        else:
            result.filtered_out.append(
                {
                    "kind": "relationship",
                    "id": f"{source}->{target}:{item.get('rel_type', '')}",
                    "reason": reason,
                }
            )

    for item in documents:
        if not isinstance(item, dict):
            result.filtered_out.append({"kind": "document", "reason": "invalid_item"})
            continue
        ok, reason = _matches_case(item, requested, require_scope=require_scope)
        if ok:
            result.documents.append(item)
        else:
            result.filtered_out.append(
                {
                    "kind": "document",
                    "id": str(item.get("doc_id") or item.get("filename") or ""),
                    "reason": reason,
                }
            )

    return result


def case_scope_ids(item: dict[str, Any]) -> set[str]:
    """Public helper used by graph adapters when filtering edge provenance."""

    return _scope_values(item)
