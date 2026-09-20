"""Evidence Boundary — the single bounded context the LLM is allowed to reason over.

Everything the model sees passes through a bounded, case-scoped evidence
object.  The LLM cannot reach outside this object, cannot see other cases,
and cannot invent facts that aren't traceable to something inside it.

The boundary contains:
- deterministic case metadata (status, title, dates)
- relevant entities (people, phones, accounts, vehicles, locations)
- relevant relationships (with provenance)
- relevant documents (capped and sanitized)
- timeline events relevant to the question
- authoritative counts (so the model never recounts)
- detected contradictions (deterministic pre-pass)
- provenance mapping
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass
class EvidenceBoundary:
    """The bounded case-scoped evidence package for one AI request."""

    case_id: str
    case_number: str
    case_title: str
    case_status: str
    jurisdiction: str
    question: str
    intent: str
    response_style: str
    detail: str
    #: Entities the question explicitly named (resolved against case data),
    #: each as {"label": ..., "name": ...} — used to scope the answer.
    entity_labels: list[dict[str, str]] = field(default_factory=list)
    # Authoritative counts (deterministic — never recount from the subset)
    case_stats: dict[str, Any] = field(default_factory=dict)
    # Structured entities
    persons: list[dict[str, Any]] = field(default_factory=list)
    phones: list[dict[str, Any]] = field(default_factory=list)
    accounts: list[dict[str, Any]] = field(default_factory=list)
    vehicles: list[dict[str, Any]] = field(default_factory=list)
    locations: list[dict[str, Any]] = field(default_factory=list)
    organizations: list[dict[str, Any]] = field(default_factory=list)
    # Relationships keyed by rel_type groups
    relationships: list[dict[str, Any]] = field(default_factory=list)
    # Relevant document records, truncated to char budget
    documents: list[dict[str, Any]] = field(default_factory=list)
    # Timeline events (sorted chronologically)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    # Deterministic analytics
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    # Document IDs available for this case (for citation validation)
    available_document_ids: list[str] = field(default_factory=list)
    included_document_ids: list[str] = field(default_factory=list)
    #: Metadata for *every* document on the case (no content). A "what files do
    #: we have" question is about the whole case file, not just the subset that
    #: retrieval happened to rank into the context budget.
    case_file_inventory: list[dict[str, Any]] = field(default_factory=list)
    # Provenance: id -> list of source doc ids
    provenance: dict[str, list[str]] = field(default_factory=dict)
    # Metadata about the evidence types present
    available_evidence_types: list[str] = field(default_factory=list)
    missing_evidence_types: list[str] = field(default_factory=list)
    # Temporal buckets (if timeline requested)
    temporal_buckets: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Retrieval diagnostics
    retrieval: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ utils
    def all_entity_names(self) -> list[str]:
        out = []
        for bucket in (self.persons, self.phones, self.accounts, self.vehicles,
                       self.locations, self.organizations):
            for item in bucket:
                nm = item.get("name") or item.get("display_name")
                if nm:
                    out.append(nm)
        return out

    def citation_is_valid(self, doc_id: str) -> bool:
        return str(doc_id) in {str(d) for d in self.available_document_ids}


# ---------------------------------------------------------------------------
# Building an evidence boundary from retrieved data.
# ---------------------------------------------------------------------------


def _display_name_for_node(n: dict[str, Any], account_owners: dict[str, str],
                            key_to_name: dict[str, str]) -> str:
    props = n.get("properties") or n
    lbl = str(n.get("label") or "Entity").upper()
    key = str(n.get("provenance_key") or n.get("id") or "")
    if lbl == "PERSON":
        return props.get("name") or props.get("full_name") or key_to_name.get(key) or key
    if lbl in ("BANKACCOUNT", "ACCOUNT"):
        # The dataset uses `bank`, not `bank_name`; prefer the human label the
        # entity was stored with so an answer reads "Kotak Mahindra Bank a/c
        # 8916" rather than a bare account number.
        explicit = props.get("name")
        if explicit and str(explicit).strip():
            return str(explicit).strip()
        acc = props.get("account_number") or key_to_name.get(key) or key
        bank = props.get("bank") or props.get("bank_name")
        owner_key = account_owners.get(key) or account_owners.get(key.split(":")[-1])
        owner = key_to_name.get(owner_key) if owner_key else None
        parts = []
        if bank:
            parts.append(str(bank))
        parts.append(f"a/c {acc}")
        if owner:
            parts.append(f"(owner: {owner})")
        return " ".join(parts)
    if lbl == "VEHICLE":
        plate = props.get("plate") or key_to_name.get(key) or key
        mm = props.get("make_model")
        return f"{mm} [{plate}]" if mm else str(plate)
    if lbl in ("PHONE", "PHONENUMBER"):
        num = props.get("number") or props.get("phone") or key_to_name.get(key) or key
        return str(num)
    if lbl in ("LOCATION", "ADDRESS"):
        addr = props.get("address") or props.get("city") or key_to_name.get(key) or key
        return str(addr)
    if lbl in ("ORGANIZATION", "COMPANY"):
        nm = props.get("name") or props.get("company_name") or key_to_name.get(key) or key
        return str(nm)
    if lbl == "EVENT":
        return str(
            props.get("name") or props.get("title")
            or props.get("event_type") or "Event"
        )
    return props.get("name") or key_to_name.get(key) or key


def build_evidence_boundary(
    *,
    case_id: str,
    case_number: str,
    case_title: str,
    case_status: str,
    jurisdiction: str,
    question: str,
    plan: Any,
    nodes: Iterable[dict[str, Any]],
    edges: Iterable[dict[str, Any]],
    documents: Iterable[dict[str, Any]],
    all_case_document_ids: Iterable[str] = (),
    all_case_documents: Iterable[dict[str, Any]] = (),
    timeline: Iterable[dict[str, Any]] = (),
    available_evidence_types: Iterable[str] = (),
    missing_evidence_types: Iterable[str] = (),
    case_stats: dict[str, Any] | None = None,
    temporal_buckets: dict[str, list[dict[str, Any]]] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
) -> EvidenceBoundary:
    """Categorize retrieved items into a clean EvidenceBoundary."""

    nodes_list = list(nodes)
    edges_list = list(edges)
    docs_list = list(documents)
    timeline_list = list(timeline)

    # Build key → name and account-owner maps
    key_to_name: dict[str, str] = {}
    for n in nodes_list:
        key = str(n.get("provenance_key") or n.get("id") or "")
        props = n.get("properties") or n
        name = (
            props.get("name")
            or props.get("full_name")
            or props.get("account_number")
            or props.get("number")
            or props.get("plate")
            or props.get("address")
            or props.get("city")
        )
        if key and name:
            key_to_name[key] = str(name)
            short = key.split(":")[-1]
            if short:
                key_to_name[short] = str(name)

    account_owners: dict[str, str] = {}
    for e in edges_list:
        if str(e.get("rel_type", "")).upper() == "OWNS_ACCOUNT":
            s = str(e.get("source_key") or e.get("source") or "")
            t = str(e.get("target_key") or e.get("target") or "")
            if s and t:
                account_owners[t] = s
                if ":" in t:
                    account_owners[t.split(":")[-1]] = s

    persons: list[dict[str, Any]] = []
    phones: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []
    vehicles: list[dict[str, Any]] = []
    locations: list[dict[str, Any]] = []
    organizations: list[dict[str, Any]] = []

    seen_keys: set[str] = set()
    for n in nodes_list:
        key = str(n.get("provenance_key") or n.get("id") or "")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        props = n.get("properties") or n
        label = str(n.get("label") or props.get("label") or "").upper()
        display = _display_name_for_node(n, account_owners, key_to_name)
        if display in ("?", "Unknown", "None", ""):
            continue
        raw_refs = props.get("source_doc_ids")
        if not raw_refs and props.get("source_doc_id"):
            raw_refs = [props.get("source_doc_id")]
        entry: dict[str, Any] = {
            "key": key,
            "name": display,
            "label": label,
            "role": props.get("role"),
            "source_doc_ids": [str(r) for r in (raw_refs or []) if r],
        }
        # include a couple of useful attributes when present
        if label == "PERSON":
            entry["criminal_status"] = props.get("criminal_status")
            persons.append(entry)
        elif label in ("PHONE", "PHONENUMBER"):
            phones.append(entry)
        elif label in ("BANKACCOUNT", "ACCOUNT"):
            accounts.append(entry)
        elif label == "VEHICLE":
            vehicles.append(entry)
        elif label in ("LOCATION", "ADDRESS"):
            locations.append(entry)
        elif label in ("ORGANIZATION", "COMPANY"):
            organizations.append(entry)

    # Clean up relationships for the boundary — keep display names and provenance.
    # Endpoints keep their OWN display name (accounts stay "SBI 5010..."), so a
    # person→account edge never collapses into a person→[themselves] self-loop.
    cleaned_relationships: list[dict[str, Any]] = []
    seen_rel_keys: set[tuple[str, str, str, str]] = set()
    for e in edges_list:
        s_key = str(e.get("source_key") or e.get("source") or "")
        t_key = str(e.get("target_key") or e.get("target") or "")
        s_name = key_to_name.get(s_key) or key_to_name.get(s_key.split(":")[-1]) or s_key
        t_name = key_to_name.get(t_key) or key_to_name.get(t_key.split(":")[-1]) or t_key
        rel_type = str(e.get("rel_type") or "")
        dedupe_key = (s_key, t_key, rel_type, str(e.get("timestamp") or ""))
        if dedupe_key in seen_rel_keys:
            continue
        seen_rel_keys.add(dedupe_key)
        rel = {
            "source": s_name,
            "target": t_name,
            "source_key": s_key,
            "target_key": t_key,
            "rel_type": e.get("rel_type"),
            "confidence": e.get("confidence", 1.0),
            "source_doc_ids": [str(d) for d in (e.get("source_doc_ids") or []) if d],
            "timestamp": e.get("timestamp"),
        }
        if not rel["source_doc_ids"] and e.get("source_doc_id"):
            rel["source_doc_ids"] = [str(e["source_doc_id"])]
        for numeric in ("amount", "call_count", "duration"):
            if e.get(numeric) is not None:
                rel[numeric] = e[numeric]
        cleaned_relationships.append(rel)

    # Documents
    cleaned_docs: list[dict[str, Any]] = []
    included_doc_ids: list[str] = []
    for d in docs_list:
        did = str(d.get("doc_id") or "")
        if not did:
            continue
        included_doc_ids.append(did)
        cleaned_docs.append({
            "doc_id": did,
            "filename": d.get("filename") or did,
            "document_type": (d.get("document_type") or "DOCUMENT").upper(),
            "content": (d.get("content") or "")[:4000],
            "source_doc_id": d.get("source_document_id") or d.get("source_doc_id"),
            "recorded_at": d.get("date") or d.get("created_at") or d.get("recorded_at"),
        })

    # Provenance map
    provenance: dict[str, list[str]] = {}
    for n in nodes_list:
        k = str(n.get("provenance_key") or n.get("id") or "")
        props = n.get("properties") or {}
        src = props.get("source_doc_ids") or ([props.get("source_doc_id")] if props.get("source_doc_id") else [])
        if k and src:
            provenance[k] = [str(x) for x in src if x]

    available_doc_ids = [str(d) for d in all_case_document_ids if d]
    if not available_doc_ids:
        # fallback to the docs we know about
        available_doc_ids = sorted({did for did in included_doc_ids})

    # Metadata-only inventory of the whole case file (never document content).
    inventory: list[dict[str, Any]] = []
    for d in all_case_documents or ():
        did = str(d.get("doc_id") or "")
        if not did:
            continue
        inventory.append({
            "doc_id": did,
            "filename": str(d.get("filename") or did),
            "document_type": str(d.get("document_type") or "DOCUMENT").rsplit(".", 1)[-1].upper(),
        })
    if not inventory:
        inventory = [
            {"doc_id": d["doc_id"], "filename": d["filename"],
             "document_type": d["document_type"]}
            for d in cleaned_docs
        ]

    # Replace provenance keys in timeline events with display names. A timeline
    # that reads "person:2 → person:1" tells an investigator nothing.
    def _name(value: Any) -> Any:
        if value in (None, ""):
            return value
        key = str(value)
        return key_to_name.get(key) or key_to_name.get(key.split(":")[-1]) or key

    timeline_list = [
        {**ev, "source": _name(ev.get("source")), "target": _name(ev.get("target"))}
        if ev.get("type") == "edge" or "source" in ev or "target" in ev
        else ev
        for ev in timeline_list
    ]

    # Temporal buckets
    if temporal_buckets is None:
        from app.ai.retrieval import bucket_temporal_phases
        temporal_buckets = bucket_temporal_phases(timeline_list)

    return EvidenceBoundary(
        case_id=case_id,
        case_number=case_number,
        case_title=case_title,
        case_status=case_status,
        jurisdiction=jurisdiction,
        question=question,
        intent=getattr(plan, "intent", "GENERAL"),
        response_style=getattr(plan, "response_style", "natural"),
        detail=getattr(plan, "detail", "standard"),
        entity_labels=[
            {"label": str(e.get("label") or "PERSON"), "name": str(e.get("name") or "")}
            for e in getattr(plan, "entity_labels", []) or []
            if e.get("name")
        ],
        case_stats=case_stats or {},
        persons=persons,
        phones=phones,
        accounts=accounts,
        vehicles=vehicles,
        locations=locations,
        organizations=organizations,
        relationships=cleaned_relationships,
        documents=cleaned_docs,
        timeline=timeline_list,
        contradictions=contradictions or [],
        available_document_ids=available_doc_ids,
        included_document_ids=sorted(set(included_doc_ids)),
        case_file_inventory=inventory,
        provenance=provenance,
        available_evidence_types=list(available_evidence_types),
        missing_evidence_types=list(missing_evidence_types),
        temporal_buckets=temporal_buckets,
    )


#: Attribute names on a graph node whose values are personal data and must be
#: replaced wherever they appear in retrieved free text.
_PII_ATTRIBUTES = (
    "name", "full_name", "alias", "number", "phone", "phone_number",
    "account_number", "plate", "plate_number", "address", "email",
    "voter_id", "aadhaar", "pan",
)


def _raw_pii_terms(nodes: Iterable[dict[str, Any]], pmap: Any) -> dict[str, str]:
    """Build {real value → pseudonym} for every personal value in the case.

    Used to scrub retrieved document text: a name does not stop being personal
    data just because it appears inside a paragraph rather than a graph field.
    """
    terms: dict[str, str] = {}
    for node in nodes or ():
        if not isinstance(node, dict):
            continue
        props = node.get("properties") or {}
        key = str(node.get("provenance_key") or node.get("id") or "")
        label = str(node.get("label") or "Entity")
        if not key:
            continue
        pseudo = pmap.pseudonymize(key, label)
        for attribute in _PII_ATTRIBUTES:
            value = props.get(attribute)
            if isinstance(value, str) and len(value.strip()) >= 3:
                terms[value.strip()] = pseudo
    return terms


def _scrub(text: str, terms: dict[str, str]) -> str:
    """Replace real personal values with pseudonyms, longest match first."""
    if not text or not terms:
        return text
    out = str(text)
    for real in sorted(terms, key=len, reverse=True):
        if real and real in out:
            out = re.sub(re.escape(real), terms[real], out, flags=re.IGNORECASE)
    return out


def pseudonymize_boundary(
    boundary: EvidenceBoundary,
    pmap: Any,
    *,
    nodes: Iterable[dict[str, Any]] | None = None,
) -> EvidenceBoundary:
    """Return a copy of ``boundary`` safe to hand to an external model.

    Entity display names are replaced with their stable pseudonyms, retrieved
    document text is scrubbed of real personal values, and the backend-only
    fields (provenance keys, entity→document links) are dropped.  Document
    identifiers survive, because the model has to be able to cite the record a
    claim rests on and a document id is not personal data.

    The identity mapping itself never enters the returned object — it stays in
    ``pmap``, inside CrimeLink.
    """
    import copy as _copy

    clone = _copy.deepcopy(boundary)

    def _alias(key: str, label: str | None) -> str:
        return pmap.pseudonymize(key, label)

    pii_terms = _raw_pii_terms(nodes, pmap) if nodes is not None else {}

    # Real display name → pseudonym, so relationships still name the same
    # entities the model sees in the entity list (otherwise it cannot reason
    # about who is connected to whom).
    name_to_pseudo: dict[str, str] = {}

    for bucket in (clone.persons, clone.phones, clone.accounts, clone.vehicles,
                   clone.locations, clone.organizations):
        for entry in bucket:
            key = str(entry.get("key") or "")
            real_name = str(entry.get("name") or "")
            if not key:
                continue
            pseudo = _alias(key, entry.get("label"))
            entry["name"] = pseudo
            if real_name:
                name_to_pseudo[real_name] = pseudo
            entry.pop("key", None)
            entry.pop("source_doc_ids", None)

    def _swap(value: Any) -> Any:
        text = str(value or "")
        if text in name_to_pseudo:
            return name_to_pseudo[text]
        # Account/vehicle displays are "BANK 1234 (owner: X)" style — rewrite
        # any real name embedded inside them.
        for real, pseudo in name_to_pseudo.items():
            if real and real in text:
                text = text.replace(real, pseudo)
        return text

    for rel in clone.relationships:
        rel["source"] = _swap(rel.get("source"))
        rel["target"] = _swap(rel.get("target"))
        rel.pop("source_key", None)
        rel.pop("target_key", None)
        # Keep source_doc_ids: they are the citations the answer must carry and
        # a document id is not personal data.

    for entry in clone.entity_labels:
        entry["name"] = _swap(entry.get("name"))

    # Retrieved document text is the other place identities hide. Scrub it with
    # the same pseudonyms so the answer stays reproducible after restoration.
    for doc in clone.documents:
        if doc.get("content"):
            doc["content"] = _scrub(str(doc["content"]), pii_terms)
        for field in ("filename", "source_doc_id"):
            if doc.get(field):
                doc[field] = _scrub(str(doc[field]), pii_terms)

    # Timeline entries name people too.
    for ev in clone.timeline:
        for field in ("source", "target"):
            if ev.get(field):
                ev[field] = _swap(ev[field])
    for phase in (clone.temporal_buckets or {}).values():
        for ev in phase:
            for field in ("source", "target"):
                if isinstance(ev, dict) and ev.get(field):
                    ev[field] = _swap(ev[field])

    clone.case_title = _scrub(str(clone.case_title or ""), pii_terms) or clone.case_title
    clone.provenance = {}
    clone.retrieval = {}
    return clone


def pluralize(word: str, count: int) -> str:
    """Return '1 word' / 'N words' using correct English pluralization."""
    if count == 1:
        return f"1 {word}"
    irregular = {
        "person": "people",
        "People": "People",
    }
    if word in irregular:
        return f"{count} {irregular[word]}"
    if word.endswith("y") and word[-2:].lower() not in ("ey", "ay", "oy"):
        return f"{count} {word[:-1]}ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return f"{count} {word}es"
    return f"{count} {word}s"
