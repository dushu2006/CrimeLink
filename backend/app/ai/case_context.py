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

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass(frozen=True)
class CaseContextStats:
    """Deterministic counts for the requested case, never model-generated."""

    case_id: str
    evidence_count: int
    entity_count: int
    person_count: int
    relationship_count: int
    evidence_types_count: int = 0
    evidence_types: list[str] = field(default_factory=list)
    entity_counts_by_type: dict[str, int] = field(default_factory=dict)
    #: Distinct *documents* attached to the case.  Deliberately separate from
    #: ``evidence_count``, which counts every evidence record the case's graph
    #: nodes and relationships reference — a larger set, because one document
    #: can reference many records.  Conflating the two produced answers that
    #: claimed "12 records" in one place and "35" in another.
    document_count: int = 0

    @property
    def document_count_or_evidence(self) -> int:
        return self.document_count or self.evidence_count

    def as_dict(self) -> dict[str, int | str]:
        """Canonical 5-key dictionary preserved for backward compatibility."""
        return {
            "case_id": self.case_id,
            "evidence_count": self.evidence_count,
            "entity_count": self.entity_count,
            "person_count": self.person_count,
            "relationship_count": self.relationship_count,
        }

    def as_detailed_dict(self) -> dict[str, Any]:
        """Extended dictionary with evidence types and entity type breakdowns."""
        return {
            "case_id": self.case_id,
            "evidence_count": self.evidence_count,
            "entity_count": self.entity_count,
            "person_count": self.person_count,
            "relationship_count": self.relationship_count,
            "evidence_types_count": self.evidence_types_count or len(self.evidence_types),
            "evidence_types": list(self.evidence_types),
            "entity_counts_by_type": dict(self.entity_counts_by_type),
            "document_count": self.document_count_or_evidence,
            # Named so a reader cannot confuse the two:
            "evidence_records_referenced": self.evidence_count,
            "case_documents": self.document_count_or_evidence,
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
        evidence_types: set[str] = set()
        for item in self.documents:
            dt = str(item.get("document_type") or item.get("type") or "").strip()
            if dt:
                evidence_types.add(dt.rsplit(".", 1)[-1].upper())
        entity_counts: dict[str, int] = {}
        for item in self.nodes:
            lbl = str(item.get("label") or "Entity")
            if lbl.upper() == "PERSON":
                lbl_key = "people"
            elif lbl.upper() in ("BANKACCOUNT", "ACCOUNT"):
                lbl_key = "accounts"
            elif lbl.upper() in ("PHONE", "PHONENUMBER"):
                lbl_key = "phones"
            elif lbl.upper() in ("VEHICLE",):
                lbl_key = "vehicles"
            elif lbl.upper() in ("LOCATION", "ADDRESS"):
                lbl_key = "locations"
            elif lbl.upper() in ("ORGANIZATION", "COMPANY"):
                lbl_key = "organizations"
            else:
                lbl_key = lbl.lower()
            entity_counts[lbl_key] = entity_counts.get(lbl_key, 0) + 1

        persons = sum(
            1 for item in self.nodes if str(item.get("label", "")).upper() == "PERSON"
        )
        return CaseContextStats(
            case_id=self.case_id,
            evidence_count=len(documents),
            entity_count=len(self.nodes),
            person_count=persons,
            relationship_count=len(self.edges),
            evidence_types_count=len(evidence_types),
            evidence_types=sorted(evidence_types),
            entity_counts_by_type=entity_counts,
        )


@dataclass
class CaseAIContext:
    """Authoritative case-scoped intelligence context.

    Sourced directly from database and graph snapshots. LLMs are never
    allowed to calculate case counts or invent evidence types.
    """

    case_id: str
    case_number: str
    case_title: str
    status: str
    jurisdiction: str
    stats: CaseContextStats
    verified_evidence: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    timeline_summary: dict[str, Any] = field(default_factory=dict)
    suggested_questions: list[str] = field(default_factory=list)
    missing_evidence_types: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_summary_dict(self) -> dict[str, Any]:
        stats_detailed = self.stats.as_detailed_dict()
        # Compute entity_counts_by_type with correct plural labels the frontend understands
        entity_counts_by_type: dict[str, int] = {}
        label_remap = {
            "people": "people",
            "persons": "people",
            "person": "people",
            "accounts": "accounts",
            "account": "accounts",
            "phones": "phones",
            "phone": "phones",
            "vehicles": "vehicles",
            "vehicle": "vehicles",
            "locations": "locations",
            "location": "locations",
            "organizations": "organizations",
            "organization": "organizations",
        }
        for raw_key, count in stats_detailed.get("entity_counts_by_type", {}).items():
            norm = label_remap.get(raw_key.lower(), raw_key.lower())
            entity_counts_by_type[norm] = entity_counts_by_type.get(norm, 0) + int(count or 0)

        # A bound that is missing or empty is reported as N/A -- never as a
        # placeholder string -- so the header's empty-case rendering is exact.
        timeline_dict = {
            "first_recorded": self.timeline_summary.get("first_recorded") or "N/A",
            "latest_recorded": self.timeline_summary.get("latest_recorded") or "N/A",
            "event_count": self.timeline_summary.get("total_events", 0),
        }

        status = self.status.value if hasattr(self.status, "value") else self.status

        return {
            "case_id": self.case_id,
            "case_number": self.case_number,
            "title": self.case_title,
            "case_title": self.case_title,
            "status": status,
            "jurisdiction": self.jurisdiction,
            "stats": {
                # Frontend-compatible field names.  ``documents_indexed`` is
                # the number of distinct case documents (the "verified
                # records" figure), which the builder sets explicitly.
                "documents_indexed": self.stats.document_count_or_evidence,
                "document_count": self.stats.document_count_or_evidence,
                "evidence_count": self.stats.evidence_count,
                "evidence_types_count": (
                    self.stats.evidence_types_count or len(self.stats.evidence_types)
                ),
                "evidence_types": list(self.stats.evidence_types),
                "entities_extracted": self.stats.entity_count,
                "entity_count": self.stats.entity_count,
                "person_count": self.stats.person_count,
                "relationships_mapped": self.stats.relationship_count,
                "relationship_count": self.stats.relationship_count,
                "entity_counts_by_type": entity_counts_by_type,
                "coverage_percent": 100 if self.stats.evidence_count > 0 else 0,
                "confidence_score": 1.0,
            },
            "timeline": timeline_dict,
            "timeline_summary": self.timeline_summary,
            "suggested_questions": list(self.suggested_questions),
            "missing_evidence_types": list(self.missing_evidence_types),
            "evidence_count": self.stats.evidence_count,
            "canonical_entities_count": self.stats.entity_count,
        }


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
    case_id: str | set[str] | list[str],
    *,
    properties: bool = False,
    require_scope: bool = True,
) -> tuple[bool, str]:
    values = _scope_values(item, properties=properties)
    if values:
        if isinstance(case_id, (set, list, tuple)):
            matched = not values.isdisjoint(set(case_id))
        else:
            matched = case_id in values
        return matched, "case_scope_mismatch" if not matched else ""
    # A legacy adapter may return a record from an already case-scoped snapshot
    # without repeating the scope on every row.  It is accepted by the gateway
    # only as a compatibility path; graph/document adapters now stamp scope.
    if not require_scope:
        return True, ""
    return False, "missing_case_provenance"


def validate_case_context(
    case_id: str | Iterable[str],
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

    if isinstance(case_id, (set, list, tuple)):
        requested_set = {str(c).strip() for c in case_id if str(c).strip()}
        if not requested_set:
            raise ValueError("A case-scoped context requires a non-empty case_id")
        primary_id = next(iter(requested_set))
        requested: str | set[str] = requested_set
    else:
        primary_id = str(case_id or "").strip()
        if not primary_id:
            raise ValueError("A case-scoped context requires a non-empty case_id")
        requested = primary_id

    result = CaseContextValidation(case_id=primary_id)
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


def _edge_identity(edge: dict[str, Any]) -> tuple[str, ...]:
    """Identity of one relationship, whichever key spelling the source used."""
    return (
        "edge",
        str(edge.get("source_key") or edge.get("source") or ""),
        str(edge.get("target_key") or edge.get("target") or ""),
        str(edge.get("rel_type") or ""),
    )


def _event_identity(ev: dict[str, Any], index: int) -> tuple[str, ...]:
    """Identity of one timeline event, aligned with the node/edge scans."""
    kind = str(ev.get("type") or "")
    if kind == "edge":
        return _edge_identity(ev)
    if kind == "node" and ev.get("key"):
        return ("node", str(ev["key"]))
    key = ev.get("event_key") or ev.get("key") or ev.get("provenance_key")
    return ("event", str(key) if key else f"#{index}")


def _event_in_case_scope(
    ev: dict[str, Any],
    allowed_keys: set[str],
    allowed_edges: set[tuple[tuple[str, ...], str]],
    case_id: str,
) -> bool:
    """Whether a timeline event belongs to the validated case context.

    A node event must be one of the validated nodes; an edge event must be
    one of the validated relationships; anything else must carry this case's
    own provenance.  This is the same boundary the counts obey, applied to
    the dates.
    """
    kind = str(ev.get("type") or "")
    if kind == "node":
        return str(ev.get("key") or "") in allowed_keys
    if kind == "edge":
        # Identity plus stamp: parallel relationships between the same two
        # nodes are told apart by the timestamp the event was built from.
        return (_edge_identity(ev), str(ev.get("timestamp") or "")) in allowed_edges
    key = str(ev.get("event_key") or ev.get("key") or ev.get("provenance_key") or "")
    if key and key in allowed_keys:
        return True
    matched, _reason = _matches_case(ev, case_id)
    if matched:
        return True
    props = ev.get("properties")
    if isinstance(props, dict):
        matched, _reason = _matches_case(props, case_id)
        return matched
    return False


def _timeline_sort_key(ts: str) -> tuple[int, str]:
    """Chronological sort key for one recorded timestamp.

    ISO-8601 values (with or without a zone, ``Z`` or offset, date-only) are
    compared as instants; anything unparseable sorts after them by its text,
    so an odd stamp can never displace a real one as the first/latest bound.
    """
    s = str(ts).strip()
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return (1, s)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (0, parsed.astimezone(timezone.utc).isoformat())


def format_readable_date(ts: str | datetime | None) -> str:
    """Format an ISO date string or datetime into human readable form (e.g. Jan 12, 2026)."""
    if not ts:
        return "Unknown"
    s = str(ts).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y")
    except Exception:
        pass
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        try:
            mon = months[int(m.group(2))]
            return f"{mon} {int(m.group(3))}, {m.group(1)}"
        except Exception:
            pass
    return s[:10]


def generate_case_suggested_questions(
    *,
    case_number: str,
    case_title: str,
    entities: Iterable[dict[str, Any]] = (),
    evidence_types: Iterable[str] = (),
    relationships: Iterable[dict[str, Any]] = (),
    events: Iterable[dict[str, Any]] = (),
) -> list[str]:
    """Dynamically generate investigative suggested questions from actual case data.

    These are generated from authoritative case evidence types, entities,
    and relationships rather than hardcoded generic questions.
    """
    questions: list[str] = []

    # Check entities for key people/accused
    persons: list[tuple[str, bool]] = []
    for e in entities:
        lbl = str(e.get("label") or e.get("_label") or "").upper()
        if lbl == "PERSON":
            props = e.get("properties") or e
            name = props.get("name") or props.get("full_name") or e.get("name")
            if name and name not in ("?", "Unknown", "None"):
                role_upper = str(props.get("role") or "").upper()
                is_crim = bool(
                    props.get("is_criminal")
                    or props.get("criminal_status") in ("CONFIRMED", "ACCUSED", "CHARGESHEETED", "CONVICTED")
                    or "ACCUSED" in role_upper
                    or "SUSPECT" in role_upper
                )
                persons.append((name, is_crim))

    ev_types = {str(t).upper() for t in evidence_types}
    rel_types = {str(r.get("rel_type") or r.get("relationship_type") or "").upper() for r in relationships}

    # 1. Key people question
    questions.append("Who are the key people in this case?")

    # 2. Suspect/target specific connection
    target_person: str | None = None
    for name, is_crim in persons:
        if is_crim:
            target_person = name
            break
    if not target_person and persons:
        target_person = persons[0][0]

    # Theme from case title
    theme = "the alleged events"
    t_lower = case_title.lower()
    if "tender" in t_lower or "bribery" in t_lower:
        theme = "the tender process"
    elif "fraud" in t_lower or "scam" in t_lower:
        theme = "the alleged fraud"
    elif "extortion" in t_lower:
        theme = "the extortion scheme"
    elif "theft" in t_lower or "robbery" in t_lower:
        theme = "the robbery incident"
    elif "murder" in t_lower or "homicide" in t_lower:
        theme = "the incident"

    if target_person:
        questions.append(f"What evidence connects {target_person} to {theme}?")
    else:
        questions.append(f"What evidence connects the primary entities to {theme}?")

    # 3. Financial relationships
    has_financial = any(
        "BANK" in t or "FINANCIAL" in t or "TRANSACTION" in t or "STATEMENT" in t
        for t in ev_types
    )
    has_transfer_rel = any(
        "TRANSFER" in r or "TRANSACTION" in r or "PAYMENT" in r
        for r in rel_types
    )
    has_bank_entity = any(
        str(e.get("label", "")).upper() in ("BANKACCOUNT", "ACCOUNT")
        for e in entities
    )
    if has_financial or has_transfer_rel or has_bank_entity:
        questions.append("Show the financial relationships in this case.")

    # 4. Timeline / sequence
    if events or any("CDR" in t or "CALL" in t or "CCTV" in t or "STATEMENT" in t for t in ev_types):
        questions.append(f"What happened before and after {theme}?")

    # 5. Multi-source corroboration
    if len(ev_types) >= 2 or len(relationships) >= 3:
        questions.append("Which relationships are supported by multiple evidence sources?")

    # 6. Contradictions / verification
    questions.append("Are there contradictions in the available evidence?")

    return questions[:6]


def build_case_ai_context(
    *,
    case_id: str,
    case_number: str,
    case_title: str,
    status: str = "OPEN",
    jurisdiction: str = "METRO-CENTRAL",
    nodes: Iterable[dict[str, Any]] = (),
    edges: Iterable[dict[str, Any]] = (),
    documents: Iterable[dict[str, Any]] = (),
    events: Iterable[dict[str, Any]] = (),
    known_evidence_types: Iterable[str] = ("FIR", "CALL_RECORD", "BANK_STATEMENT", "CCTV", "WITNESS_STATEMENT", "FORENSIC_REPORT"),
) -> CaseAIContext:
    """Build a complete, authoritative CaseAIContext for a case."""
    validation = validate_case_context(
        case_id, nodes=nodes, edges=edges, documents=documents, require_scope=True
    )
    # ``CaseContextStats`` is frozen; ``validation.stats`` builds a fresh
    # instance on every access.  Assigning ``document_count`` on it raised
    # ``FrozenInstanceError`` and took the whole ``/ai/cases/{id}/context``
    # endpoint down, which is what left the CASE INTELLIGENCE header on its
    # zero/N/A fallback.  Derive the completed stats immutably instead.
    stats = replace(validation.stats, document_count=len(validation.documents))

    # The timeline is subject to the same case boundary as the counts.  The
    # caller's ``events`` are usually built from the *unvalidated* snapshot,
    # so an event is kept only when it belongs to a validated node or
    # relationship (or carries this case's provenance itself); otherwise a
    # cross-case or unscoped record with a timestamp could move the header's
    # first/latest bounds even though it contributes to no other metric.
    allowed_keys = {
        str(item.get("provenance_key") or item.get("id"))
        for item in validation.nodes
        if item.get("provenance_key") or item.get("id")
    }
    allowed_edges = {
        (_edge_identity(edge), str(edge.get("timestamp") or "")) for edge in validation.edges
    }
    scoped_events = [
        ev for ev in events
        if isinstance(ev, dict) and _event_in_case_scope(ev, allowed_keys, allowed_edges, case_id)
    ]

    # Dated records: (record identity, timestamp).  Identity keeps a record
    # that reaches us twice -- once as an event and once in the direct scan
    # below -- from being counted twice, while distinct stamps on one record
    # (first/last seen) still both count.
    dated: set[tuple[tuple[str, ...], str]] = set()

    def _record(identity: tuple[str, ...], ts: Any) -> None:
        text = str(ts).strip() if ts else ""
        if text and not text.startswith("Timestamp unavailable"):
            dated.add((identity, text))

    for index, ev in enumerate(scoped_events):
        _record(_event_identity(ev, index), ev.get("timestamp") or ev.get("at") or ev.get("date"))
    for doc in validation.documents:
        _record(
            ("document", str(doc.get("doc_id") or doc.get("filename") or "")),
            doc.get("date") or doc.get("created_at") or doc.get("recorded_at"),
        )
    for edge in validation.edges:
        _record(_edge_identity(edge), edge.get("timestamp"))
    for node in validation.nodes:
        props = node.get("properties") or {}
        for k in ("first_ts", "last_ts", "timestamp"):
            if props.get(k):
                _record(("node", str(node.get("provenance_key") or node.get("id") or "")), props[k])

    # Order chronologically, not lexicographically: the same case mixes
    # ``...Z``, ``...+00:00``, naive and date-only stamps, and a string sort
    # of those can pick the wrong bound.
    clean_ts = sorted((ts for _identity, ts in dated), key=_timeline_sort_key)
    first_ts = clean_ts[0] if clean_ts else None
    latest_ts = clean_ts[-1] if clean_ts else None

    # ``None`` means "no dated record in this case"; the summary renders it
    # as N/A so a genuinely empty case is displayed honestly.
    timeline_summary = {
        "first_recorded": format_readable_date(first_ts) if first_ts else None,
        "latest_recorded": format_readable_date(latest_ts) if latest_ts else None,
        "first_recorded_raw": first_ts,
        "latest_recorded_raw": latest_ts,
        "total_events": len(dated),
    }

    # Missing evidence types
    available_types = set(stats.evidence_types)
    missing_types = [
        str(t).upper()
        for t in known_evidence_types
        if str(t).upper() not in available_types
    ]

    suggested = generate_case_suggested_questions(
        case_number=case_number,
        case_title=case_title,
        entities=validation.nodes,
        evidence_types=stats.evidence_types,
        relationships=validation.edges,
        events=scoped_events,
    )

    # Provenance map
    prov: dict[str, list[str]] = {}
    for n in validation.nodes:
        k = n.get("provenance_key") or n.get("id")
        p = (n.get("properties") or {}).get("source_doc_ids") or []
        if k:
            prov[str(k)] = [str(x) for x in p]

    return CaseAIContext(
        case_id=case_id,
        case_number=case_number,
        case_title=case_title,
        status=status,
        jurisdiction=jurisdiction,
        stats=stats,
        verified_evidence=validation.documents,
        entities=validation.nodes,
        relationships=validation.edges,
        events=scoped_events,
        timeline_summary=timeline_summary,
        suggested_questions=suggested,
        missing_evidence_types=missing_types,
        provenance=prov,
    )

