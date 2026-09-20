"""Case facts — the ground truth an evaluation question is graded against.

Everything here is read from the same sources the assistant is allowed to use:
the case-scoped documents, the case's own graph nodes, and the case's own graph
edges.  Nothing is hand-written, so a question bank built from a fact pack is
tied to what the corpus actually says rather than to what the evaluator wishes
it said.

Two different notions of "belongs to this case" are kept apart on purpose,
because the assistant's answers depend on the difference:

* **documented** — the name or identifier occurs in this case's own records;
* **graph-linked** — the entity is reachable in this case's graph (its
  ``case_ids`` include this case, or one of its identifiers occurs in this
  case's records), even when no record in this case names it.

An answer that presents the second as the first is over-claiming, and the
graders measure exactly that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: Kinds of case entity the question bank and graders care about.
KIND_PERSON = "PERSON"
KIND_PHONE = "PHONE"
KIND_ACCOUNT = "ACCOUNT"
KIND_VEHICLE = "VEHICLE"
KIND_LOCATION = "LOCATION"

_KIND_BY_LABEL = {
    "PERSON": KIND_PERSON,
    "PHONE": KIND_PHONE,
    "PHONENUMBER": KIND_PHONE,
    "BANKACCOUNT": KIND_ACCOUNT,
    "ACCOUNT": KIND_ACCOUNT,
    "VEHICLE": KIND_VEHICLE,
    "LOCATION": KIND_LOCATION,
    "ADDRESS": KIND_LOCATION,
}

_VALUE_PROPS = (
    "name", "display_name", "full_name", "number", "phone", "msisdn", "phone_number",
    "account_number", "account_no", "plate", "registration", "address", "value",
)

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_MONEY_RE = re.compile(r"(?:Rs\.?|INR|₹)\s?([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_UNIT_AMOUNT_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d{5,})\b")

_INCIDENT_RE = re.compile(r"Incident:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)
_REGISTERED_RE = re.compile(r"Registered:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)
_CRIME_RE = re.compile(r"Crime type:\s*([A-Z_]+)", re.IGNORECASE)
_JURISDICTION_RE = re.compile(r"Jurisdiction:\s*([A-Z\- ]+)", re.IGNORECASE)
_OFFICER_RE = re.compile(r"[Ii]nvestigating officer:?\s*([A-Za-z .]+?)(?:\(|\n|$)")
_WITNESS_RE = re.compile(r"Witness:\s*([A-Za-z .]+?)\s{2,}|Witness:\s*([A-Za-z .]+)\n")
_ACCUSED_RE = re.compile(
    r"Accused persons and roles:\s*([^\n]+?)(?:\s*\(|\n|$)", re.IGNORECASE
)


@dataclass
class CaseFacts:
    """Everything the evaluator knows about one case."""

    case_id: str
    case_number: str
    title: str
    status: str
    documents: list[dict[str, Any]] = field(default_factory=list)
    entities: dict[str, list[str]] = field(default_factory=dict)
    incident_date: str | None = None
    fir_date: str | None = None
    crime_type: str | None = None
    jurisdiction: str | None = None
    occurrence_officer: str | None = None
    witnesses: list[str] = field(default_factory=list)
    leads: list[str] = field(default_factory=list)
    accused: list[str] = field(default_factory=list)
    amounts: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    #: entity value -> {"case_ids": [...], "role": str|None}
    person_meta: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: entity value -> identifiers the case graph attaches to it (phones, plates, …)
    graph_links: dict[str, list[str]] = field(default_factory=dict)
    #: every value that exists in this case's graph (documents or not)
    graph_values: list[str] = field(default_factory=list)
    graph_edges: int = 0
    edge_documents_missing: int = 0

    # ------------------------------------------------------------------ derived
    @property
    def doc_ids(self) -> set[str]:
        return {str(document.get("doc_id")) for document in self.documents}

    @property
    def type_by_doc(self) -> dict[str, str]:
        return {
            str(document.get("doc_id")): str(document.get("document_type") or "").upper()
            for document in self.documents
        }

    @property
    def ids_by_type(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for doc_id, doc_type in self.type_by_doc.items():
            grouped.setdefault(doc_type, []).append(doc_id)
        return grouped

    @property
    def corpus_text(self) -> str:
        return "\n".join(str(document.get("content") or "") for document in self.documents)

    @property
    def people(self) -> list[str]:
        return list(self.entities.get(KIND_PERSON) or [])

    @property
    def phones(self) -> list[str]:
        return list(self.entities.get(KIND_PHONE) or [])

    @property
    def accounts(self) -> list[str]:
        return list(self.entities.get(KIND_ACCOUNT) or [])

    @property
    def vehicles(self) -> list[str]:
        return list(self.entities.get(KIND_VEHICLE) or [])

    @property
    def locations(self) -> list[str]:
        return list(self.entities.get(KIND_LOCATION) or [])

    def docs_of_type(self, *types: str) -> list[str]:
        wanted = {t.upper() for t in types}
        return [d for d, t in self.type_by_doc.items() if t in wanted]

    def document_text(self, doc_id: str) -> str:
        for document in self.documents:
            if str(document.get("doc_id")) == doc_id:
                return str(document.get("content") or "")
        return ""

    def date_span(self) -> tuple[str | None, str | None]:
        if not self.dates:
            return None, None
        return min(self.dates), max(self.dates)

    def mentioned_in_documents(self, name: str) -> bool:
        return str(name or "").lower() in self.corpus_text.lower()

    @property
    def people_in_documents(self) -> list[str]:
        return [name for name in self.people if self.mentioned_in_documents(name)]

    @property
    def people_graph_only(self) -> list[str]:
        """People this case's graph carries but no record in this case names."""
        return [name for name in self.people if not self.mentioned_in_documents(name)]

    def foreign_to(self, other: "CaseFacts") -> list[str]:
        """Values of ``other`` that this case's records and graph never touch."""
        mine_docs = self.corpus_text.lower()
        mine_graph = {str(v).lower() for v in self.graph_values}
        values: list[str] = []
        for kind in (KIND_PERSON, KIND_PHONE, KIND_ACCOUNT, KIND_VEHICLE):
            for value in other.entities.get(kind) or []:
                text = str(value)
                if not text or text.lower() in mine_graph or text.lower() in mine_docs:
                    continue
                values.append(text)
        return values

    def summary(self) -> dict[str, Any]:
        low, high = self.date_span()
        return {
            "case_number": self.case_number,
            "case_id": self.case_id,
            "title": self.title,
            "status": self.status,
            "documents": len(self.documents),
            "evidence_types": sorted(self.ids_by_type),
            "people": self.people[:12],
            "people_graph_only": self.people_graph_only,
            "phones": self.phones[:8],
            "accounts": self.accounts[:8],
            "vehicles": self.vehicles[:8],
            "locations": self.locations[:8],
            "incident_date": self.incident_date,
            "fir_date": self.fir_date,
            "crime_type": self.crime_type,
            "date_span": [low, high],
        }


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text or "")
    if not match:
        return None
    groups = [g for g in match.groups() if g]
    return groups[0].strip() if groups else None


def _node_value(node: dict[str, Any]) -> str | None:
    properties = node.get("properties") or {}
    for prop in _VALUE_PROPS:
        value = properties.get(prop)
        if value in (None, "", []):
            continue
        return str(value).strip()
    return None


def build_case_facts(
    *,
    case_id: str,
    case_number: str,
    title: str,
    status: str,
    documents: list[dict[str, Any]],
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
) -> CaseFacts:
    """Assemble a fact pack from the case's own documents, graph nodes and edges."""
    entities: dict[str, list[str]] = {
        kind: [] for kind in (KIND_PERSON, KIND_PHONE, KIND_ACCOUNT, KIND_VEHICLE, KIND_LOCATION)
    }
    person_meta: dict[str, dict[str, Any]] = {}
    value_by_key: dict[str, str] = {}
    graph_values: list[str] = []

    for node in nodes or ():
        # The store reports "Person"/"BankAccount"; the persisted snapshot uses
        # "PERSON"/"BANK_ACCOUNT" — normalise so both paths agree.
        label = re.sub(r"[^A-Z]", "", str(node.get("label") or "").upper())
        kind = _KIND_BY_LABEL.get(label)
        if not kind:
            continue
        value = _node_value(node)
        if not value:
            continue
        if value not in entities[kind]:
            entities[kind].append(value)
        if value not in graph_values:
            graph_values.append(value)
        key = str(node.get("provenance_key") or "")
        if key:
            value_by_key[key] = value
        if kind == KIND_PERSON:
            properties = node.get("properties") or {}
            person_meta[value] = {
                "case_ids": [str(c) for c in (properties.get("case_ids") or [])],
                "role": properties.get("role"),
            }

    graph_links: dict[str, list[str]] = {}
    edge_documents_missing = 0
    for edge in edges or ():
        source = value_by_key.get(str(edge.get("source_key") or ""))
        target = value_by_key.get(str(edge.get("target_key") or ""))
        if not source or not target:
            continue
        if not (edge.get("source_doc_ids") or edge.get("source_doc_id")):
            edge_documents_missing += 1
        if source in person_meta and target not in graph_links.setdefault(source, []):
            graph_links[source].append(target)
        if not edge.get("source_doc_ids") and not edge.get("source_doc_id"):
            continue
        if target not in graph_values:
            graph_values.append(target)

    corpus = "\n".join(str(document.get("content") or "") for document in documents)
    fir_text = ""
    for document in documents:
        if str(document.get("document_type") or "").upper() == "FIR":
            fir_text = str(document.get("content") or "")
            break
    charge_text = ""
    for document in documents:
        if str(document.get("document_type") or "").upper() == "CHARGE_SHEET":
            charge_text = str(document.get("content") or "")
            break

    dates = sorted(set(_DATE_RE.findall(corpus)))
    amounts = sorted(set(
        str(value).replace(",", "") for value in
        (_MONEY_RE.findall(corpus) + _UNIT_AMOUNT_RE.findall(corpus))
    ))

    accused: list[str] = []
    accused_raw = _first(_ACCUSED_RE, charge_text) or ""
    if accused_raw:
        head = accused_raw.split("—")[0]
        for candidate in re.split(r",|\band\b", head):
            name = candidate.strip(" .")
            if 2 <= len(name.split()) <= 3 and name[0:1].isupper():
                accused.append(name)

    witnesses: list[str] = []
    for match in _WITNESS_RE.finditer(fir_text or corpus):
        name = (match.group(1) or match.group(2) or "").strip()
        if name:
            witnesses.append(name)

    leads = []
    for match in re.finditer(r"Lead:\s*([A-Za-z .]+)", corpus):
        name = match.group(1).strip()
        if name and name not in leads:
            leads.append(name)

    return CaseFacts(
        case_id=case_id,
        case_number=case_number,
        title=title,
        status=status,
        documents=documents,
        entities=entities,
        incident_date=_first(_INCIDENT_RE, corpus) or _first(_DATE_RE, fir_text),
        fir_date=_first(_REGISTERED_RE, fir_text),
        crime_type=_first(_CRIME_RE, fir_text),
        jurisdiction=(_first(_JURISDICTION_RE, fir_text) or "").strip() or None,
        occurrence_officer=_first(_OFFICER_RE, fir_text),
        witnesses=witnesses,
        leads=leads,
        accused=accused,
        amounts=amounts,
        dates=dates,
        person_meta=person_meta,
        graph_links=graph_links,
        graph_values=graph_values,
        graph_edges=len(edges or ()),
        edge_documents_missing=edge_documents_missing,
    )


def shared_names(facts: CaseFacts, others: list[CaseFacts]) -> dict[str, list[str]]:
    """Names this case shares with another case (the contamination vector)."""
    mine = {name.lower() for name in facts.people}
    shared: dict[str, list[str]] = {}
    for other in others:
        common = [
            name for name in other.people
            if name.lower() in mine and name.lower() != "unconfirmed"
        ]
        if common:
            shared[other.case_number] = sorted(set(common))
    return shared
