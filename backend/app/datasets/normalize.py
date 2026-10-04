"""Canonical entity and relationship extraction from mapped tables.

This is CrimeLink's normalization layer: whatever a source dataset calls its
columns, what comes out of here is always the same shape --

    PERSON --uses_phone--> PHONE
           --owns_vehicle--> VEHICLE
           --owns_account--> ACCOUNT
           --resides_at--> ADDRESS
           --member_of--> ORGANIZATION
           --involved_in--> CASE
    CASE   --has_evidence--> EVIDENCE

Two properties matter more than completeness:

* **Nothing is invented.**  A relationship is emitted only when the source row
  actually states both endpoints.  A person with no owned vehicle simply has no
  ``owns_vehicle`` edge.
* **Everything remembers where it came from.**  Every entity and every
  relationship carries ``provenance`` naming the dataset file, the sheet, and
  the row that produced it, which is what makes
  ``Person -> Relationship -> Evidence -> Source -> exact row`` navigable.

Temporal validity (``valid_from`` / ``valid_to``) is preserved wherever the
source states it, so "who owned this vehicle in March 2024" stays answerable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.datasets import schema_map as sm
from app.datasets.readers import Table
from app.logging import get_logger

log = get_logger("crimelink.datasets.normalize")


# ---------------------------------------------------------------------------
# Canonical relationship vocabulary
# ---------------------------------------------------------------------------

REL_USES_PHONE = "USES_PHONE"
REL_OWNS_VEHICLE = "OWNS_VEHICLE"
REL_OWNS_ACCOUNT = "OWNS_ACCOUNT"
REL_RESIDES_AT = "RESIDES_AT"
REL_MEMBER_OF = "MEMBER_OF"
REL_INVOLVED_IN = "INVOLVED_IN"
REL_HAS_EVIDENCE = "HAS_EVIDENCE"
REL_CALLED = "CALLED"
REL_MESSAGED = "MESSAGED"
REL_TRANSFER_TO = "TRANSFER_TO"
REL_TRANSACTED = "TRANSACTED"
REL_SEEN_AT = "SEEN_AT"
REL_DROVE = "DROVE"
REL_TRAVELLED_TO = "TRAVELLED_TO"
REL_USES_DEVICE = "USES_DEVICE"
REL_USES_EMAIL = "USES_EMAIL"
REL_OWNS_PROPERTY = "OWNS_PROPERTY"
REL_LOCATED_AT = "LOCATED_AT"
REL_ASSOCIATE_OF = "ASSOCIATE_OF"
REL_INVESTIGATES = "INVESTIGATES"
REL_RELATED_TO = "RELATED_TO"
REL_HAS_FIR = "HAS_FIR"
# Explicit derived relations.  A shared identifier is a useful investigative
# lead, but it is not a direct person-to-person association.
REL_SHARED_PHONE = "SHARED_PHONE"
REL_SHARED_ACCOUNT = "SHARED_ACCOUNT"
REL_SHARED_VEHICLE = "SHARED_VEHICLE"
REL_SHARED_LOCATION = "SHARED_LOCATION"
REL_SHARED_IDENTIFIER = "SHARED_IDENTIFIER"

#: Entity types whose written value *is* an identifier, so a reference may
#: claim it as another way of naming the same record.  A phone register's
#: ``+919801000001`` and a note's ``9801000001`` are one phone; a bank
#: statement's ``100000000001`` and the register's ``AC0001`` row are one
#: account.  A person's or organisation's "normalized value" is a display
#: name, and two people called *Ajay Kumar* are two people -- claiming names
#: as identifiers would merge innocent bystanders, so those types are absent.
_IDENTITY_TYPES = frozenset({sm.PHONE, sm.VEHICLE, sm.ACCOUNT})

#: Values a dataset writes where it has no value to give.  A CCTV sheet whose
#: subject column says ``DATA_GAP`` is stating that nobody was identified; a
#: statement filed under ``UNKNOWN`` is stating that the writer does not know.
#: Minting a PERSON named after the marker invents a person the file never
#: described, and a graph that shows them beside real suspects is worse than
#: one that shows nothing.  The vocabulary is recognised in the *value*, never
#: by the file it came from, so any dataset may use these conventions.
_MISSING_MARKERS = frozenset(
    {
        "n/a",
        "n.a.",
        "n.a",
        "not available",
        "not_available",
        "notavailable",
        "no data",
        "no_data",
        "nodata",
        "data gap",
        "data_gap",
        "datagap",
        "unknown",
        "unspecified",
        "unidentified",
        "unavailable",
        "missing",
        "none",
        "null",
        "nil",
        "tbd",
        "tba",
        "redacted",
        "withheld",
        "deleted",
        "anonymous",
        "anon",
        "-",
        "--",
        "---",
        "?",
        "??",
    }
)
#: Words that mark a SCREAMING_SNAKE_CASE token (``DATA_GAP``,
#: ``UNKNOWN_SUBJECT``) as an absence marker rather than a name.  The
#: underscore is required, so an agency that writes a real name in capitals
#: (``RAM SINGH``) is untouched.
_MISSING_SEGMENTS = frozenset(
    {
        "GAP",
        "UNKNOWN",
        "UNSPECIFIED",
        "UNIDENTIFIED",
        "MISSING",
        "UNAVAILABLE",
        "NONE",
        "NULL",
        "NIL",
        "TBD",
        "TBA",
        "REDACTED",
        "WITHHELD",
        "ANON",
        "ANONYMOUS",
        "NA",
    }
)


def is_missing_value(value: Any) -> bool:
    """True when a dataset wrote an absence marker where a value belongs."""
    text = str(value if value is not None else "").strip()
    if not text:
        return True
    if " ".join(text.split()).casefold() in _MISSING_MARKERS:
        return True
    token = text.upper()
    if "_" not in token:
        return False
    segments = token.split("_")
    if not all(segment.isalnum() and segment for segment in segments):
        return False
    return any(segment in _MISSING_SEGMENTS for segment in segments)

#: Columns that identify *which* person a row is about, in priority order.
#: Consulted by the secondary extraction pass so a person referenced as
#: "account holder" or "registered owner" resolves to the same entity as the
#: person table's own row for them.
_PERSON_KEY_FIELDS = (
    "PERSON.id",
    "ACCOUNT.holder_id",
    "VEHICLE.owner_id",
    "SIGHTING.driver",
    "CALL.from_person",
)

#: Canonical relationship -> the graph relationship type it projects onto.
#: Types absent here project as ``ASSOCIATE_OF`` between people and
#: ``MENTIONED_IN`` otherwise, so a new source vocabulary can never crash the
#: graph build.
GRAPH_REL_TYPES: dict[str, str] = {
    REL_USES_PHONE: "USES_PHONE",
    REL_OWNS_VEHICLE: "OWNS_VEHICLE",
    REL_OWNS_ACCOUNT: "OWNS_ACCOUNT",
    REL_RESIDES_AT: "LOCATED_AT",
    REL_MEMBER_OF: "MEMBER_OF",
    REL_INVOLVED_IN: "PARTICIPATED_IN",
    REL_HAS_EVIDENCE: "MENTIONED_IN",
    REL_CALLED: "CALLED",
    REL_MESSAGED: "CALLED",
    REL_TRANSFER_TO: "TRANSFER_TO",
    REL_TRANSACTED: "CONTROLS_ACCOUNT",
    REL_SEEN_AT: "LOCATED_AT",
    REL_DROVE: "OWNS_VEHICLE",
    REL_TRAVELLED_TO: "LOCATED_AT",
    REL_USES_DEVICE: "USES_PHONE",
    REL_USES_EMAIL: "USES_PHONE",
    REL_OWNS_PROPERTY: "LOCATED_AT",
    REL_LOCATED_AT: "LOCATED_AT",
    REL_ASSOCIATE_OF: "ASSOCIATE_OF",
    REL_INVESTIGATES: "PARTICIPATED_IN",
    REL_RELATED_TO: "ASSOCIATE_OF",
    REL_HAS_FIR: "HAS_FIR",
    REL_SHARED_PHONE: "SHARED_PHONE",
    REL_SHARED_ACCOUNT: "SHARED_ACCOUNT",
    REL_SHARED_VEHICLE: "SHARED_VEHICLE",
    REL_SHARED_LOCATION: "SHARED_LOCATION",
    REL_SHARED_IDENTIFIER: "SHARED_IDENTIFIER",
}

#: Free-text relationship words a dataset may use in an edge table.
_REL_WORD_MAP: dict[str, str] = {
    "usesphone": REL_USES_PHONE,
    "hasphone": REL_USES_PHONE,
    "ownsvehicle": REL_OWNS_VEHICLE,
    "ownsvehicles": REL_OWNS_VEHICLE,
    "vehicleowner": REL_OWNS_VEHICLE,
    "ownsaccount": REL_OWNS_ACCOUNT,
    "holdsaccount": REL_OWNS_ACCOUNT,
    "residesat": REL_RESIDES_AT,
    "livesat": REL_RESIDES_AT,
    "addressof": REL_RESIDES_AT,
    "memberof": REL_MEMBER_OF,
    "employedat": REL_MEMBER_OF,
    "worksat": REL_MEMBER_OF,
    "employedby": REL_MEMBER_OF,
    "involvedin": REL_INVOLVED_IN,
    "partyto": REL_INVOLVED_IN,
    "accusedin": REL_INVOLVED_IN,
    "called": REL_CALLED,
    "contacted": REL_CALLED,
    "messaged": REL_MESSAGED,
    "transferto": REL_TRANSFER_TO,
    "paid": REL_TRANSFER_TO,
    "seenat": REL_SEEN_AT,
    "sightedat": REL_SEEN_AT,
    "drove": REL_DROVE,
    "travelledto": REL_TRAVELLED_TO,
    "traveledto": REL_TRAVELLED_TO,
    "usesdevice": REL_USES_DEVICE,
    "usesemail": REL_USES_EMAIL,
    "ownsproperty": REL_OWNS_PROPERTY,
    "locatedat": REL_LOCATED_AT,
    "associateof": REL_ASSOCIATE_OF,
    "knows": REL_ASSOCIATE_OF,
    "sharedphone": REL_SHARED_PHONE,
    "sharesphone": REL_SHARED_PHONE,
    "sharedaccount": REL_SHARED_ACCOUNT,
    "sharesaccount": REL_SHARED_ACCOUNT,
    "sharedvehicle": REL_SHARED_VEHICLE,
    "sharesvehicle": REL_SHARED_VEHICLE,
    "sharedlocation": REL_SHARED_LOCATION,
    "shareslocation": REL_SHARED_LOCATION,
    "relatedto": REL_RELATED_TO,
    "familyof": REL_RELATED_TO,
    "investigates": REL_INVESTIGATES,
    "hasevidence": REL_HAS_EVIDENCE,
}


# ---------------------------------------------------------------------------
# Canonical records
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CanonicalEntity:
    canonical_id: str
    entity_type: str
    # ``display_name`` is presentation data; ``canonical_id`` is immutable
    # identity and must never be used as a fallback display value in AI output.
    # ``name`` remains as a compatibility alias for existing adapters.
    name: str = ""
    display_name: str = ""
    normalized_value: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.name
        if not self.name:
            self.name = self.display_name

    def merge(self, other: "CanonicalEntity") -> None:
        _, _, natural_key = self.canonical_id.partition(":")
        is_placeholder = (
            not self.name
            or self.name.strip() == natural_key.strip()
            or bool(self.attributes.get("stub"))
        )
        if other.name:
            other_is_not_id = other.name.strip() != natural_key.strip()
            if is_placeholder and (other_is_not_id or not self.name):
                self.name = other.name
                self.display_name = other.display_name or other.name
                self.normalized_value = other.normalized_value or self.normalized_value
                self.attributes.pop("stub", None)
            elif not self.name:
                self.name = other.name
        if not self.normalized_value and other.normalized_value:
            self.normalized_value = other.normalized_value
        if not self.display_name:
            self.display_name = self.name or other.display_name
        for key, value in other.attributes.items():
            if key == "stub":
                # Bookkeeping, not data: a reference-only placeholder being
                # folded into the record it names must not mark that record as
                # a stub.
                continue
            if value not in (None, "") and self.attributes.get(key) in (None, ""):
                self.attributes[key] = value
        # Explicit confirmed criminal status must never be lost during merge
        if str(other.attributes.get("criminal_status", "")).strip().lower() in {"confirmed", "convicted", "accused", "chargesheeted", "criminal"}:
            self.attributes["criminal_status"] = other.attributes["criminal_status"]
        if not self.provenance:
            self.provenance = other.provenance


@dataclass(slots=True)
class CanonicalRelationship:
    source_canonical_id: str
    target_canonical_id: str
    rel_type: str
    edge_key: str = ""
    confidence: float = 1.0
    valid_from: str | None = None
    valid_to: str | None = None
    observed_at: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    case_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.edge_key:
            raw = "|".join(
                [
                    self.rel_type,
                    self.source_canonical_id,
                    self.target_canonical_id,
                    self.valid_from or "",
                    self.observed_at or "",
                    str(self.attributes.get("discriminator") or ""),
                ]
            )
            self.edge_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


@dataclass(slots=True)
class NormalizationResult:
    entities: dict[str, CanonicalEntity] = field(default_factory=dict)
    relationships: dict[str, CanonicalRelationship] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: semantic_type -> rows consumed, for the operator-facing report.
    table_stats: dict[str, int] = field(default_factory=dict)

    def add_entity(self, entity: CanonicalEntity) -> str:
        existing = self.entities.get(entity.canonical_id)
        if existing is None:
            self.entities[entity.canonical_id] = entity
        else:
            existing.merge(entity)
        return entity.canonical_id

    def add_relationship(self, rel: CanonicalRelationship) -> None:
        existing = self.relationships.get(rel.edge_key)
        if existing is None:
            self.relationships[rel.edge_key] = rel
            return
        # Aggregate repeats (a phone pair that called 40 times).
        existing.attributes["occurrences"] = int(existing.attributes.get("occurrences", 1)) + 1
        for case_id in rel.case_ids:
            if case_id not in existing.case_ids:
                existing.case_ids.append(case_id)

    def derive_shared_identifier_relationships(self) -> int:
        """Materialize explicit, uncertainty-aware shared-identifier leads.

        Co-use is derived only from direct PERSON -> identifier edges already
        emitted by source records.  It never becomes ``ASSOCIATE_OF`` and the
        derived edge retains the supporting edge keys and source provenance so
        a finding can be traced back to the exact rows that caused it.
        """
        identifier_types = {
            sm.PHONE: REL_SHARED_PHONE,
            sm.ACCOUNT: REL_SHARED_ACCOUNT,
            sm.VEHICLE: REL_SHARED_VEHICLE,
            sm.ADDRESS: REL_SHARED_LOCATION,
            sm.LOCATION: REL_SHARED_LOCATION,
        }
        owners: dict[tuple[str, str], list[CanonicalRelationship]] = {}
        for rel in self.relationships.values():
            if rel.rel_type not in {REL_USES_PHONE, REL_OWNS_ACCOUNT, REL_OWNS_VEHICLE, REL_RESIDES_AT, REL_LOCATED_AT}:
                continue
            source = self.entities.get(rel.source_canonical_id)
            target = self.entities.get(rel.target_canonical_id)
            if not source or source.entity_type != sm.PERSON or not target:
                continue
            shared_type = identifier_types.get(target.entity_type)
            if not shared_type:
                continue
            owners.setdefault((target.entity_type, target.canonical_id), []).append(rel)

        created = 0
        for (identifier_type, identifier_id), supporting in owners.items():
            people = sorted({rel.source_canonical_id for rel in supporting})
            if len(people) < 2:
                continue
            for index, source_id in enumerate(people):
                for target_id in people[index + 1:]:
                    rel_type = identifier_types[identifier_type]
                    evidence_keys = sorted({rel.edge_key for rel in supporting})
                    source_docs = sorted({
                        str((rel.provenance or {}).get("doc_id") or (rel.provenance or {}).get("dataset_file_id"))
                        for rel in supporting
                        if (rel.provenance or {}).get("doc_id") or (rel.provenance or {}).get("dataset_file_id")
                    })
                    self.add_relationship(CanonicalRelationship(
                        source_canonical_id=source_id,
                        target_canonical_id=target_id,
                        rel_type=rel_type,
                        confidence=min(float(rel.confidence or 0.0) for rel in supporting),
                        attributes={
                            "direct_vs_derived": "derived",
                            "shared_identifier_type": identifier_type,
                            "shared_identifier_key": identifier_id,
                            "support_level": "shared_identifier_only",
                            "contradiction_state": "unreviewed",
                            "analytical_basis": "co_use_of_identifier",
                            "supporting_edge_keys": evidence_keys,
                            "supporting_provenance": [rel.provenance for rel in supporting],
                            "source_doc_ids": source_docs,
                            "alternative_explanations": [
                                "Shared identifiers can reflect legitimate common ownership or contact."
                            ],
                        },
                        provenance={
                            "derived_from": evidence_keys,
                            "source_doc_ids": source_docs,
                            "supporting_provenance": [rel.provenance for rel in supporting],
                            "derivation": "shared_identifier",
                        },
                        case_ids=sorted({case_id for rel in supporting for case_id in rel.case_ids}),
                    ))
                    created += 1
        return created

    def counts(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for entity in self.entities.values():
            by_type[entity.entity_type] = by_type.get(entity.entity_type, 0) + 1
        by_rel: dict[str, int] = {}
        for rel in self.relationships.values():
            by_rel[rel.rel_type] = by_rel.get(rel.rel_type, 0) + 1
        return {
            "entities": len(self.entities),
            "relationships": len(self.relationships),
            "entities_by_type": dict(sorted(by_type.items())),
            "relationships_by_type": dict(sorted(by_rel.items())),
            "tables": dict(sorted(self.table_stats.items())),
        }


def cid(entity_type: str, natural_id: str) -> str:
    return f"{entity_type}:{str(natural_id).strip()}"


def _derived_id(entity_type: str, value: str) -> str:
    digest = hashlib.sha1(f"{entity_type}|{value}".encode("utf-8")).hexdigest()[:16]
    return f"{entity_type}:~{digest}"


#: A value that can be read as a telephone number.  Used to decide whether the
#: *numbers* inside it may be treated as another way of naming the same phone;
#: an identifier like ``PH0001`` must not become aliases ``0001``/``PHONE0001``.
_PHONE_LIKE = re.compile(r"^[+]?[\d\s\-().]{8,}$")


def identifier_variants(entity_type: str, value: Any) -> list[str]:
    """Every way a dataset may write the *same* identifier.

    ``+91 98010 00001``, ``9801000001`` and ``+919801000001`` are one phone;
    ``MH 11 AB 1001`` and ``MH11AB1001`` are one vehicle; ``AC-0001`` and
    ``AC0001`` are one account.  Nothing here invents a link: each variant is
    a faithful rewriting of the value the source actually wrote.

    The type decides how far a rewriting may go.  Phone numbers are collapsed
    to their national digits (and re-expanded with the country code) because a
    phone number *is* its digits; account and vehicle identifiers are only
    compacted, because stripping their letters would merge ``AC0001`` with
    account number ``0001``.
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return []
    variants = [text, text.upper()]
    if entity_type == sm.PHONE and _PHONE_LIKE.match(text):
        digits = sm.normalize_phone(text)
        if digits:
            variants.extend([digits, f"+91{digits}"])
    elif entity_type in {sm.VEHICLE, sm.ACCOUNT}:
        compact = sm.normalize_plate(text)
        if compact:
            variants.append(compact)
    elif entity_type == sm.PERSON:
        name = sm.normalize_name(text)
        if name:
            variants.append(name)
    out: list[str] = []
    for variant in variants:
        if variant and variant not in out:
            out.append(variant)
    return out


def _edge_key(
    rel_type: str,
    source: str,
    target: str,
    valid_from: str | None,
    observed_at: str | None,
    discriminator: Any,
) -> str:
    raw = "|".join(
        [rel_type, source, target, valid_from or "", observed_at or "", str(discriminator or "")]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


class Normalizer:
    """Accumulates canonical records across every table in a dataset."""

    def __init__(self) -> None:
        self.result = NormalizationResult()
        #: natural id -> canonical id, per entity type, for cross-table joins.
        #: Kept for the callers that read it directly (relation tables, FIR
        #: aliasing); every write goes through :meth:`_claim`.
        self._alias: dict[str, str] = {}
        #: ``(entity_type, identifier variant) -> canonical id``.  One row may
        #: know an account as ``AC0001`` while another knows it as
        #: ``100000000001``; both are aliases of one entity, and this is what
        #: makes the second one resolve to the first *whatever order the files
        #: happened to be read in*.
        self._identity: dict[tuple[str, str], str] = {}
        #: Union-find over canonical ids: identifiers discovered later link an
        #: entity that already exists to the one that names it.
        self._parent: dict[str, str] = {}
        self._order: dict[str, int] = {}
        #: True where a canonical id was minted purely to hold a reference --
        #: the placeholder a foreign key creates before the register that
        #: actually describes the thing is read.  A placeholder never wins a
        #: merge against a record the dataset really described.
        self._placeholder: dict[str, bool] = {}
        #: True where the id is the dataset's own identifier for the record
        #: (``AC0001``, ``V001``, ``PH0001``) rather than a value-derived id
        #: (``~hash`` of a number, a plate, a name).  When two records merge,
        #: the dataset's id is the one a reader can look up again; two derived
        #: ids fall back to the earliest.
        self._described: dict[str, bool] = {}
        #: Provenance of the row currently being processed.  Stubs created by
        #: :meth:`_resolve` inherit it, so even an entity that only ever
        #: appears as a foreign key can name the row that referenced it --
        #: which is what guarantee G1 requires of every graph node.
        self._current_prov: dict[str, Any] = {}
        #: ``(entity_type, marker)`` pairs already reported, so a document that
        #: carries the same absence marker on forty rows is explained once.
        self._missing_seen: set[tuple[str, str]] = set()

    # ------------------------------------------------- identity bookkeeping
    def _find(self, canonical_id: str) -> str:
        """Representative of ``canonical_id``'s identity group."""
        parent = self._parent.get(canonical_id)
        if parent is None or parent == canonical_id:
            self._parent.setdefault(canonical_id, canonical_id)
            return canonical_id
        root = self._find(parent)
        self._parent[canonical_id] = root
        return root

    def _link(self, first: str, second: str) -> None:
        """Record that two canonical ids name the same real-world record.

        The representative is the *describing* record when only one of them is
        a placeholder, and the earliest otherwise: the entity a caller gets
        back for ``4801...`` should be the phone register's ``PH0001``, not the
        anonymous node a call log's foreign key happened to create first.
        """
        a, b = self._find(first), self._find(second)
        if a == b:
            return
        placeholder_a = self._placeholder.get(a, False)
        placeholder_b = self._placeholder.get(b, False)
        described_a = self._described.get(a, False)
        described_b = self._described.get(b, False)
        if placeholder_a and not placeholder_b:
            root, child = b, a
        elif placeholder_b and not placeholder_a:
            root, child = a, b
        elif described_b and not described_a:
            root, child = b, a
        elif described_a and not described_b:
            root, child = a, b
        elif self._order.get(b, 1 << 30) < self._order.get(a, 1 << 30):
            root, child = b, a
        else:
            root, child = a, b
        self._parent[child] = root
        self._placeholder[root] = placeholder_a and placeholder_b
        self._described[root] = described_a or described_b

    def _claim(
        self,
        entity_type: str,
        canonical_id: str,
        values: Iterable[Any],
        *,
        placeholder: bool = False,
        described: bool = False,
    ) -> None:
        """Register every way this entity is named, linking duplicates.

        Claiming is what turns "the account table's ``AC0001``" and "the
        ledger's ``100000000001``" into one entity: the second claim finds the
        first already owning the value and links the two rather than creating
        a parallel account nobody owns.
        """
        self._order.setdefault(canonical_id, len(self._order))
        if placeholder:
            self._placeholder.setdefault(canonical_id, True)
        if described:
            self._described.setdefault(canonical_id, True)
        for value in values:
            for variant in identifier_variants(entity_type, value):
                key = (entity_type, variant)
                owner = self._identity.get(key)
                if owner is None:
                    self._identity[key] = canonical_id
                    continue
                self._link(owner, canonical_id)
            if value:
                self._alias[f"{entity_type}|{str(value).strip()}"] = self._find(canonical_id)

    def _lookup(self, entity_type: str, reference: str) -> str | None:
        """The canonical entity ``reference`` already names, if it exists."""
        for variant in identifier_variants(entity_type, reference):
            owner = self._identity.get((entity_type, variant))
            if owner is not None:
                return self._find(owner)
        known = self._alias.get(f"{entity_type}|{str(reference).strip()}")
        return self._find(known) if known else None

    # -------------------------------------------------------------- helpers
    def _provenance(self, source: dict[str, Any], row_number: int | None) -> dict[str, Any]:
        payload = dict(source)
        if row_number is not None:
            payload["row"] = row_number
        return payload

    def _register(
        self,
        entity_type: str,
        natural_id: str | None,
        *,
        name: str = "",
        normalized_value: str = "",
        attributes: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> str | None:
        """Create or refresh an entity, returning its canonical id."""
        key = (natural_id or "").strip()
        if not key and is_missing_value(normalized_value or name):
            self._note_missing(entity_type, str(normalized_value or name))
            return None
        if key:
            canonical = cid(entity_type, key)
        elif normalized_value:
            canonical = _derived_id(entity_type, normalized_value)
        elif name:
            canonical = _derived_id(entity_type, sm.normalize_name(name))
        else:
            return None
        claimed = [key, normalized_value, name] if entity_type in _IDENTITY_TYPES else [key]
        # A record the dataset named keeps its own identifier through a merge;
        # one minted from a value yields to it.
        self._claim(entity_type, canonical, claimed, described=bool(key))
        # The identifiers may already belong to another entity of this type
        # (the ledger met ``100000000001`` before the register named it
        # ``AC0001``): give back the group's representative so every caller
        # behaves as if the two had been one entity from the start.
        canonical = self._find(canonical)
        self.result.add_entity(
            CanonicalEntity(
                canonical_id=canonical,
                entity_type=entity_type,
                name=name or key,
                normalized_value=normalized_value or key,
                attributes={k: v for k, v in (attributes or {}).items() if v not in (None, "")},
                provenance=provenance or {},
            )
        )
        if key:
            self._alias[f"{entity_type}|{key}"] = canonical
        if normalized_value:
            self._alias.setdefault(f"{entity_type}|{normalized_value}", canonical)
        return canonical

    def _note_missing(self, entity_type: str, marker: str) -> None:
        """Record an absence marker once, where the operator can see it.

        The marker is not silently dropped: the value is named in the import
        report, so a file that says "nobody was identified here" is explained
        rather than quietly producing a smaller graph.
        """
        # Case-folded: the log's ``DATA_GAP`` and the display form ``Data Gap``
        # are the same absence, and the operator needs to see it once, spelled
        # the way the file spells it.
        key = (entity_type, marker.casefold())
        if key in self._missing_seen:
            return
        self._missing_seen.add(key)
        prov = self._current_prov or {}
        self.result.warnings.append(
            f"{prov.get('file', '?')} row {prov.get('row', '?')}: "
            f"{entity_type} value {marker!r} is a missing-value marker, not an entity"
        )

    def _resolve(self, entity_type: str, reference: str | None) -> str | None:
        """Resolve a cross-table reference, creating a stub if it is unknown.

        A stub keeps the relationship rather than dropping it: the source row
        genuinely asserts the link, and dropping edges because a lookup table
        was not supplied is how a dataset silently loses half its graph.

        The lookup is identifier-tolerant, so a reference finds the record
        *however that dataset wrote it* -- ``+91 98010 00001`` resolves against
        a register that spells it ``9801000001``.  If the record is only
        defined later (the register is read after the ledger), the stub
        is folded into it by :meth:`reconcile_identifiers` rather than leaving
        two accounts that never meet.
        """
        ref = (reference or "").strip()
        if not ref:
            return None
        if is_missing_value(ref):
            self._note_missing(entity_type, ref)
            return None
        known = self._lookup(entity_type, ref)
        if known:
            return known
        # When resolving a CASE, also check if reference is an aliased FIR
        if entity_type == sm.CASE:
            fir_known = self._alias.get(f"FIR|{ref}")
            if fir_known:
                return self._find(fir_known)
        canonical = cid(entity_type, ref)
        self._claim(entity_type, canonical, [ref], placeholder=True)
        canonical = self._find(canonical)
        if canonical not in self.result.entities:
            self.result.add_entity(
                CanonicalEntity(
                    canonical_id=canonical,
                    entity_type=entity_type,
                    name=ref,
                    normalized_value=ref,
                    attributes={"stub": True},
                    provenance=dict(self._current_prov),
                )
            )
        self._alias[f"{entity_type}|{ref}"] = canonical
        return canonical

    def reconcile_identifiers(self) -> int:
        """Fold entities the dataset's own identifiers show to be one record.

        Two mechanisms meet here, and both run after every table has been read,
        when the whole identifier space is known.

        *Identity groups.*  A ledger row naming ``100000000001`` and a register
        that calls that account ``AC0001`` are one account; a phone written
        ``+91 98010 00001`` in one file and ``9801000001`` in another is one
        phone.  The entity a source *described* wins over the placeholder a
        bare reference created, so the register's ``AC0001`` is what the graph
        keeps, and the ledger's edges are rewired onto it instead of leaving a
        second account nobody owns.

        *Names the dataset later defined.*  A sheet that writes only a name
        creates a person keyed by that name; when a register defines the same
        name with an identifier, the derived record folds into it.

        *Same-key placeholders.*  A cell may hold an identifier the dataset
        defines elsewhere as another type: a ``counterparty`` column containing
        ``PERSON_00379``.  The extractor that met it first created an entity of
        its own type named after the raw value.  Such a placeholder is merged
        into the record that carries the same natural key and a real name.

        Nothing is invented: every merge is between entities the dataset itself
        named with one and the same identifier.

        Returns the number of entities folded away.
        """
        folded = self._apply_remap(self._identity_remap())
        folded += self._apply_remap(self._name_remap())
        folded += self._apply_remap(self._same_key_remap())
        if folded:
            log.info("normalize.identifiers_reconciled", folded=folded)
        return folded

    def _identity_remap(self) -> dict[str, str]:
        """Canonical ids the identity index has linked, as child -> survivor."""
        remap: dict[str, str] = {}
        for canonical_id in list(self.result.entities):
            root = self._find(canonical_id)
            if root != canonical_id:
                remap[canonical_id] = root
        return remap

    def _name_remap(self) -> dict[str, str]:
        """Name-derived people that a keyed record of the same name defines.

        A sheet that only writes ``subject: Amit Joshi`` creates a person keyed
        by the name's hash, because at that moment the dataset had not defined
        him.  When the register is read later and defines ``P008`` as
        *Amit Joshi*, the two are one man: the derived entity folds into the
        described record.  Two *identified* records that happen to share a name
        are deliberately left alone -- the dataset told them apart, and merging
        them would invent a person.
        """
        by_name: dict[str, list[CanonicalEntity]] = {}
        for entity in self.result.entities.values():
            if entity.entity_type != sm.PERSON:
                continue
            name = sm.normalize_name(entity.name or entity.display_name or "")
            if name:
                by_name.setdefault(name.casefold(), []).append(entity)

        remap: dict[str, str] = {}
        for entities in by_name.values():
            derived = [
                e for e in entities if e.canonical_id.split(":", 1)[-1].startswith("~")
            ]
            described = [
                e for e in entities if not e.canonical_id.split(":", 1)[-1].startswith("~")
            ]
            if len(described) != 1 or not derived:
                continue
            for entity in derived:
                remap[entity.canonical_id] = described[0].canonical_id
        return remap

    def _same_key_remap(self) -> dict[str, str]:
        """Placeholders that share a natural key with a described record."""
        by_key: dict[tuple[str, str], list[CanonicalEntity]] = {}
        for entity in self.result.entities.values():
            _entity_type, _, key = entity.canonical_id.partition(":")
            if not key or key.startswith("~"):
                continue
            by_key.setdefault((entity.entity_type, key), []).append(entity)

        remap: dict[str, str] = {}
        for (_etype, key), entities in by_key.items():
            if len(entities) < 2:
                continue
            named = [e for e in entities if (e.name or "").strip() and e.name.strip() != key]
            placeholders = [e for e in entities if (e.name or "").strip() == key]
            if len(named) != 1 or not placeholders:
                # Ambiguous (two real records share an id) or nothing to fold.
                continue
            survivor = named[0]
            for placeholder in placeholders:
                if placeholder.canonical_id == survivor.canonical_id:
                    continue
                if placeholder.entity_type != survivor.entity_type:
                    continue
                remap[placeholder.canonical_id] = survivor.canonical_id
        return remap

    def _apply_remap(self, remap: dict[str, str]) -> int:
        """Merge each folded entity into its survivor and rewire the edges.

        Edge keys are recomputed: two relationships that differed only because
        one endpoint was the placeholder now describe the same connection, and
        aggregate (as repeats of one call pair already do) instead of lingering
        twice under stale keys.
        """
        remap = {
            child: parent
            for child, parent in remap.items()
            if child != parent and parent in self.result.entities
        }
        if not remap:
            return 0
        for child, parent in remap.items():
            folded = self.result.entities.pop(child, None)
            if folded is not None:
                self.result.entities[parent].merge(folded)

        rewired: dict[str, CanonicalRelationship] = {}
        for rel in self.result.relationships.values():
            source = remap.get(rel.source_canonical_id, rel.source_canonical_id)
            target = remap.get(rel.target_canonical_id, rel.target_canonical_id)
            if source == target:
                # The placeholder and its record were the same thing; an edge
                # between them says nothing.
                continue
            if (source, target) != (rel.source_canonical_id, rel.target_canonical_id):
                rel.edge_key = _edge_key(
                    rel.rel_type,
                    source,
                    target,
                    rel.valid_from,
                    rel.observed_at,
                    rel.attributes.get("discriminator"),
                )
                rel.source_canonical_id = source
                rel.target_canonical_id = target
            existing = rewired.get(rel.edge_key)
            if existing is None:
                rewired[rel.edge_key] = rel
                continue
            existing.attributes["occurrences"] = int(
                existing.attributes.get("occurrences", 1)
            ) + int(rel.attributes.get("occurrences", 1))
            for case_id in rel.case_ids:
                if case_id not in existing.case_ids:
                    existing.case_ids.append(case_id)
        self.result.relationships = rewired

        for alias_key, canonical in list(self._alias.items()):
            if canonical in remap:
                self._alias[alias_key] = remap[canonical]
        return len(remap)

    #: A value that is nothing but a code: ``CP_01``, ``PARTY-3``, ``ACCT_0007``,
    #: ``100000000001``.  Such a value may be an identifier the dataset defines
    #: somewhere else, and resolving it is the point; but if no record in the
    #: dataset defines it, calling it an organisation asserts something no row
    #: ever said.
    _OPAQUE_CODE = re.compile(r"^(?:[A-Za-z]{1,8}[-_/]?\d{1,15}|\d{6,20})$")

    def _reference_entity(self, value: str, prov: dict) -> str | None:
        """Resolve a bare reference to whatever the dataset says it is.

        An identifier defined elsewhere resolves to the record that defines it
        -- ``CP_01`` is the person whose ``person_id`` is ``CP_01``, an
        ``INV-2024-11`` is the property it names.  A value that reads as a name
        (it has spaces, or letters not followed by a code) is the business the
        transaction names.  Anything else is an unresolved code: it is reported
        rather than dressed up as an organisation, which is how a reference
        like ``CP_01`` used to become a company with no employees and no links.
        """
        ref = (value or "").strip()
        if not ref:
            return None
        for candidate_type in sm.ENTITY_TYPES:
            known = self._lookup(candidate_type, ref)
            if known:
                return known
        if " " not in ref and self._OPAQUE_CODE.match(ref):
            self.result.warnings.append(
                f"Unresolved reference {ref!r}: no record in this dataset defines it, "
                "so it was left untyped rather than recorded as an organisation"
            )
            return None
        return self._register(
            sm.ORGANIZATION, None, name=ref, normalized_value=ref.upper(),
            attributes={"role": "counterparty"}, provenance=prov,
        )

    def _register_person(
        self,
        person_id: str | None,
        name: str,
        *,
        attributes: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> str | None:
        """Register a person, resolving a name the dataset already knows.

        A CCTV sheet's ``subject`` column or a witness statement names somebody
        the case file already defines.  Minting a second PERSON for that name
        is how one suspect becomes three nodes, none of them connected.  The
        name is only used when a person with exactly that name already exists;
        it never merges two records the dataset told apart by identifier.
        """
        raw = str(name or "").strip()
        if raw and not person_id and is_missing_value(raw):
            # Report the file's own text, not the display form: "DATA_GAP" is
            # what the operator will search for in the export.
            self._note_missing(sm.PERSON, raw)
            return None
        normalized = sm.normalize_name(name) if name else ""
        if not person_id and normalized:
            known = self._lookup(sm.PERSON, normalized)
            if known:
                return known
        return self._register(
            sm.PERSON,
            person_id,
            name=normalized,
            normalized_value=normalized,
            attributes=attributes,
            provenance=provenance,
        )

    def _row_case_ids(self, row: dict, m: dict) -> list[str]:
        """The cases this row states it belongs to.

        Scoping an edge is not the same as membership: a transfer that names
        ``C101`` belongs to that case's file, and belongs on its graph, but the
        accounts are not case members because money moved between them.
        """
        out: list[str] = []
        for field in ("CASE.id", "CASE.number"):
            column = m.get(field)
            if not column:
                continue
            value = str(row.get(column, "") or "").strip()
            if not value:
                continue
            case_cid = self._resolve(sm.CASE, value)
            if case_cid and case_cid not in out:
                out.append(case_cid)
        return out

    def _relate(
        self,
        source: str | None,
        target: str | None,
        rel_type: str,
        *,
        confidence: float = 1.0,
        valid_from: str | None = None,
        valid_to: str | None = None,
        observed_at: str | None = None,
        attributes: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        case_ids: list[str] | None = None,
    ) -> None:
        if not source or not target or source == target:
            return
        self.result.add_relationship(
            CanonicalRelationship(
                source_canonical_id=source,
                target_canonical_id=target,
                rel_type=rel_type,
                confidence=confidence,
                valid_from=valid_from,
                valid_to=valid_to,
                observed_at=observed_at,
                attributes={k: v for k, v in (attributes or {}).items() if v not in (None, "")},
                provenance=provenance or {},
                case_ids=list(case_ids or []),
            )
        )

    # ------------------------------------------------------------- entry point
    def ingest_table(
        self,
        table: Table,
        mapping: sm.TableMapping,
        source: dict[str, Any],
    ) -> int:
        """Normalize one table. Returns the number of rows that produced records."""
        handler = _HANDLERS.get(mapping.semantic_type, Normalizer._generic_table)
        mapped = mapping.mapped
        used = 0
        for row_number, row in table.iter_rows():
            provenance = self._provenance(source, row_number)
            self._current_prov = provenance
            try:
                if handler(self, row, mapped, provenance):
                    used += 1
                # A table has one primary handler, but a real-world export is
                # rarely about one thing: a "phone list" carries the owner's
                # name and address, a vehicle register carries the owner. The
                # secondary pass picks those up so nothing stated in the data
                # is lost merely because the table was classified as something
                # else. It is idempotent -- entities merge by canonical id.
                self._enrich_row(row, mapped, provenance)
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop a dataset
                self.result.warnings.append(
                    f"{source.get('file', '?')} row {row_number}: {type(exc).__name__}: {exc}"
                )
        self.result.table_stats[mapping.semantic_type] = (
            self.result.table_stats.get(mapping.semantic_type, 0) + used
        )
        self._current_prov = {}
        return used

    # ------------------------------------------------------------- handlers
    def _enrich_row(self, row: dict, m: dict, prov: dict) -> None:
        """Register entities a wide row asserts but its handler did not read.

        Denormalised sheets are the norm outside of textbook schemas: one row
        naming a person, their number, their address and their car. Whichever
        handler owns the table, all four are real and all four belong in the
        graph.

        Relationships are only drawn where the row is *unambiguous*. A CDR row
        holds two phones and a transaction row two accounts; guessing which
        one a named person owns would manufacture evidence, so those rows
        contribute their entities and no edges.
        """
        if (m.get("OFFICER.id") or m.get("OFFICER.name")) and not m.get("PERSON.id"):
            # An officer roster's names are officers; the OFFICER handler owns
            # them. Minting a parallel PERSON for each would double every
            # investigator in the graph.
            return
        if m.get("LOCATION.id") and not m.get("PERSON.id"):
            return
        if m.get("ORGANIZATION.id") and not m.get("PERSON.id"):
            return
        if m.get("EVENT.id") and not m.get("PERSON.id"):
            return

        # A row may identify its person by any of several reference columns.
        # Using the reference the table actually carries -- rather than always
        # deriving a key from the name -- is what keeps "the holder of account
        # X" and "person P00042" one entity instead of two.
        person_id = ""
        for field in _PERSON_KEY_FIELDS:
            column = m.get(field)
            if not column:
                continue
            value = str(row.get(column, "") or "").strip()
            if value:
                person_id = value
                break
        person_keyed = any(m.get(field) for field in _PERSON_KEY_FIELDS)
        raw_name = str(row.get(m.get("PERSON.name", ""), "") or "").strip() or " ".join(
            filter(
                None,
                [
                    str(row.get(m.get("PERSON.first_name", ""), "") or "").strip(),
                    str(row.get(m.get("PERSON.last_name", ""), "") or "").strip(),
                ],
            )
        ).strip()
        if not raw_name and not person_id:
            return
        if person_keyed and not person_id:
            # The table identifies people by reference, and this row's
            # reference is blank. Inventing an identity from a stray name
            # would create a duplicate of somebody already in the dataset.
            return

        name = sm.normalize_name(raw_name) if raw_name else ""
        if name:
            pid = self._register_person(person_id or None, name, provenance=prov)
        else:
            # The row references a person but does not name them. Resolving
            # produces a placeholder that yields to the real record when the
            # person table is read; registering here would instead pin the
            # identifier in as the person's "name" and the console would show
            # PERSON_00010 where "Vikas Sahu" belongs.
            pid = self._resolve(sm.PERSON, person_id)
        if not pid:
            return

        # Two of a kind in one row means the row is about a pair, not about a
        # person's own property.
        ambiguous_phone = bool(m.get("CALL.from_phone") or m.get("CALL.to_phone"))
        ambiguous_account = bool(
            m.get("TRANSACTION.from_account") or m.get("TRANSACTION.to_account")
        )

        number = str(row.get(m.get("PHONE.number", ""), "") or "").strip()
        if number and not ambiguous_phone:
            normalized = sm.normalize_phone(number)
            phone = self._register(
                sm.PHONE,
                str(row.get(m.get("PHONE.id", ""), "") or "").strip() or None,
                name=number,
                normalized_value=normalized or number,
                provenance=prov,
            )
            self._relate(pid, phone, REL_USES_PHONE, provenance=prov)

        email = str(row.get(m.get("EMAIL.address", ""), "") or "").strip()
        if email:
            handle = self._register(
                sm.EMAIL,
                str(row.get(m.get("EMAIL.id", ""), "") or "").strip() or None,
                name=email,
                normalized_value=email.lower(),
                provenance=prov,
            )
            self._relate(pid, handle, REL_USES_EMAIL, provenance=prov)

        line1 = str(row.get(m.get("ADDRESS.line1", ""), "") or "").strip()
        if line1:
            parts = [
                line1,
                str(row.get(m.get("ADDRESS.locality", ""), "") or "").strip(),
                str(row.get(m.get("ADDRESS.city", ""), "") or "").strip(),
                str(row.get(m.get("ADDRESS.state", ""), "") or "").strip(),
            ]
            label = ", ".join(part for part in parts if part)
            address = self._register(
                sm.ADDRESS,
                str(row.get(m.get("ADDRESS.id", ""), "") or "").strip() or None,
                name=label,
                normalized_value=label.upper(),
                provenance=prov,
            )
            self._relate(pid, address, REL_RESIDES_AT, provenance=prov)

        registration = str(row.get(m.get("VEHICLE.registration", ""), "") or "").strip()
        if registration:
            vehicle = self._register(
                sm.VEHICLE,
                str(row.get(m.get("VEHICLE.id", ""), "") or "").strip() or None,
                name=registration,
                normalized_value=sm.normalize_plate(registration),
                provenance=prov,
            )
            self._relate(pid, vehicle, REL_OWNS_VEHICLE, provenance=prov)

        account = str(row.get(m.get("ACCOUNT.number", ""), "") or "").strip()
        if account and not ambiguous_account:
            handle = self._register(
                sm.ACCOUNT,
                str(row.get(m.get("ACCOUNT.id", ""), "") or "").strip() or None,
                name=account,
                normalized_value=sm.normalize_account(account),
                provenance=prov,
            )
            self._relate(pid, handle, REL_OWNS_ACCOUNT, provenance=prov)

        organization = str(row.get(m.get("ORGANIZATION.name", ""), "") or "").strip()
        if organization:
            org = self._register(
                sm.ORGANIZATION,
                str(row.get(m.get("ORGANIZATION.id", ""), "") or "").strip() or None,
                name=organization,
                normalized_value=organization.upper(),
                provenance=prov,
            )
            self._relate(pid, org, REL_MEMBER_OF, provenance=prov)

    def _persons(self, row: dict, m: dict, prov: dict) -> bool:
        name = row.get(m.get("PERSON.name", ""), "") or " ".join(
            filter(None, [row.get(m.get("PERSON.first_name", ""), ""), row.get(m.get("PERSON.last_name", ""), "")])
        )
        person_id = row.get(m.get("PERSON.id", ""), "")
        if not name and not person_id:
            return False
        raw_crm_status = row.get(m.get("PERSON.criminal_status", ""), "")
        if not raw_crm_status and str(row.get(m.get("PERSON.status", ""), "")).lower() in {"confirmed", "convicted", "accused", "chargesheeted"}:
            raw_crm_status = str(row.get(m.get("PERSON.status", ""), "")).strip()
        if not raw_crm_status and row.get("status") and (row.get("criminal_record_id") or m.get("CRIMINAL_RECORD.id")):
            if str(row.get("status", "")).strip().lower() in {"confirmed", "convicted", "accused", "chargesheeted"}:
                raw_crm_status = str(row.get("status", "")).strip()

        attributes = {
            "date_of_birth": sm.normalize_date(row.get(m.get("PERSON.dob", ""), "")) or row.get(m.get("PERSON.dob", ""), ""),
            "gender": row.get(m.get("PERSON.gender", ""), ""),
            "city": row.get(m.get("ADDRESS.city", ""), ""),
            "state": row.get(m.get("ADDRESS.state", ""), ""),
            "occupation": row.get(m.get("PERSON.occupation", ""), ""),
            "risk_role": row.get(m.get("PERSON.risk_role", ""), ""),
            "aadhaar": row.get(m.get("PERSON.aadhaar", ""), ""),
            "pan": row.get(m.get("PERSON.pan", ""), ""),
            # Stated criminal status travels with the record so downstream
            # surfaces can echo it; nothing here infers it.
            "criminal_status": raw_crm_status,
        }
        pid = self._register_person(person_id, name, attributes=attributes, provenance=prov)
        # An address stated inline on a person row is a real, evidenced link.
        line1 = row.get(m.get("ADDRESS.line1", ""), "")
        if pid and line1:
            parts = [line1, row.get(m.get("ADDRESS.city", ""), ""), row.get(m.get("ADDRESS.state", ""), "")]
            label = ", ".join(p for p in parts if p)
            aid = self._register(
                sm.ADDRESS, row.get(m.get("ADDRESS.id", ""), "") or None,
                name=label, normalized_value=label.upper(), provenance=prov,
            )
            self._relate(pid, aid, REL_RESIDES_AT, provenance=prov)
        return bool(pid)

    def _name_variants(self, row: dict, m: dict, prov: dict) -> bool:
        person = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), "") or row.get("person_id", ""))
        alias = row.get(m.get("PERSON.alias", ""), "") or row.get("alias", "")
        canonical_name = row.get(m.get("PERSON.name", ""), "") or row.get("canonical_name", "")
        if not person or not alias:
            return False
        entity = self.result.entities.get(person)
        if entity is not None:
            _, _, natural_key = entity.canonical_id.partition(":")
            if canonical_name and (not entity.name or entity.name.strip() == natural_key.strip()):
                entity.name = canonical_name.strip()
                entity.normalized_value = sm.normalize_name(canonical_name) or canonical_name
                entity.attributes.pop("stub", None)
            aliases = list(entity.attributes.get("aliases") or [])
            if alias not in aliases:
                aliases.append(alias)
            entity.attributes["aliases"] = aliases
            entity.attributes.setdefault("alias_source", row.get(m.get("COMMON.source", ""), "") or row.get("source_type", ""))
        return True

    def _phones(self, row: dict, m: dict, prov: dict) -> bool:
        number = row.get(m.get("PHONE.number", ""), "")
        phone_id = row.get(m.get("PHONE.id", ""), "")
        if not number and not phone_id:
            return False
        normalized = sm.normalize_phone(number) if number else ""
        pid = self._register(
            sm.PHONE, phone_id, name=number or phone_id, normalized_value=normalized or phone_id,
            attributes={
                "status": row.get(m.get("PHONE.status", ""), ""),
                "subscriber_type": row.get(m.get("PHONE.subscriber_type", ""), ""),
                "imei": row.get(m.get("PHONE.imei", ""), ""),
            },
            provenance=prov,
        )
        owner = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), "")) if m.get("PERSON.id") else None
        self._relate(owner, pid, REL_USES_PHONE, provenance=prov)
        return bool(pid)

    def _vehicles(self, row: dict, m: dict, prov: dict) -> bool:
        registration = row.get(m.get("VEHICLE.registration", ""), "")
        vehicle_id = row.get(m.get("VEHICLE.id", ""), "")
        if not registration and not vehicle_id:
            return False
        vid = self._register(
            sm.VEHICLE, vehicle_id, name=registration or vehicle_id,
            normalized_value=sm.normalize_plate(registration) if registration else vehicle_id,
            attributes={
                "make_model": row.get(m.get("VEHICLE.make_model", ""), ""),
                "fuel": row.get(m.get("VEHICLE.fuel", ""), ""),
                "registered_state": row.get(m.get("VEHICLE.state", ""), ""),
                "color": row.get(m.get("VEHICLE.color", ""), ""),
            },
            provenance=prov,
        )
        owner_ref = row.get(m.get("VEHICLE.owner_id", ""), "") or row.get(m.get("PERSON.id", ""), "")
        owner = self._resolve(sm.PERSON, owner_ref) if owner_ref else None
        if owner:
            self._relate(
                owner, vid, REL_OWNS_VEHICLE,
                valid_from=sm.normalize_date(row.get(m.get("COMMON.valid_from", ""), "")),
                valid_to=sm.normalize_date(row.get(m.get("COMMON.valid_to", ""), "")),
                provenance=prov,
            )
        return bool(vid)

    def _vehicle_ownership(self, row: dict, m: dict, prov: dict) -> bool:
        vid = self._resolve(sm.VEHICLE, row.get(m.get("VEHICLE.id", ""), ""))
        owner = self._resolve(sm.PERSON, row.get(m.get("VEHICLE.owner_id", ""), "") or row.get(m.get("PERSON.id", ""), ""))
        if not vid or not owner:
            return False
        valid_from = sm.normalize_date(row.get(m.get("COMMON.valid_from", ""), ""))
        valid_to = sm.normalize_date(row.get(m.get("COMMON.valid_to", ""), ""))
        self._relate(
            owner, vid, REL_OWNS_VEHICLE,
            valid_from=valid_from, valid_to=valid_to,
            attributes={"current": not valid_to},
            provenance=prov,
        )
        return True

    def _accounts(self, row: dict, m: dict, prov: dict) -> bool:
        number = row.get(m.get("ACCOUNT.number", ""), "")
        account_id = row.get(m.get("ACCOUNT.id", ""), "")
        if not number and not account_id:
            return False
        aid = self._register(
            sm.ACCOUNT, account_id, name=number or account_id,
            normalized_value=sm.normalize_account(number) if number else account_id,
            attributes={
                "bank_name": row.get(m.get("ACCOUNT.bank_name", ""), ""),
                "branch": row.get(m.get("ACCOUNT.branch", ""), ""),
                "account_type": row.get(m.get("ACCOUNT.type", ""), ""),
            },
            provenance=prov,
        )
        holder_ref = row.get(m.get("ACCOUNT.holder_id", ""), "") or row.get(m.get("PERSON.id", ""), "")
        holder = self._resolve(sm.PERSON, holder_ref) if holder_ref else None
        self._relate(holder, aid, REL_OWNS_ACCOUNT, provenance=prov)
        return bool(aid)

    def _transactions(self, row: dict, m: dict, prov: dict) -> bool:
        amount = sm.normalize_amount(row.get(m.get("TRANSACTION.amount", ""), ""))
        when = sm.normalize_date(row.get(m.get("TRANSACTION.timestamp", ""), "")) or sm.normalize_date(
            row.get(m.get("COMMON.observed_at", ""), "")
        )
        txn_id = row.get(m.get("TRANSACTION.id", ""), "")
        from_ref = row.get(m.get("TRANSACTION.from_account", ""), "") or row.get(m.get("ACCOUNT.id", ""), "")
        to_ref = row.get(m.get("TRANSACTION.to_account", ""), "")
        person_ref = row.get(m.get("PERSON.id", ""), "")
        counterparty = row.get(m.get("TRANSACTION.counterparty", ""), "")

        attributes = {
            "amount": amount,
            "transaction_type": row.get(m.get("TRANSACTION.type", ""), ""),
            "description": row.get(m.get("TRANSACTION.description", ""), ""),
            "record_id": txn_id,
            "discriminator": txn_id,
        }
        emitted = False
        source_account = self._resolve(sm.ACCOUNT, from_ref) if from_ref else None
        if to_ref:
            target_account = self._resolve(sm.ACCOUNT, to_ref)
            self._relate(source_account, target_account, REL_TRANSFER_TO, observed_at=when,
                         attributes=attributes, provenance=prov,
                         case_ids=self._row_case_ids(row, m))
            emitted = True
        elif counterparty and source_account:
            other = self._reference_entity(counterparty, prov)
            if other:
                self._relate(source_account, other, REL_TRANSFER_TO, observed_at=when,
                             attributes=attributes, provenance=prov,
                             case_ids=self._row_case_ids(row, m))
                emitted = True
        if person_ref and source_account:
            holder = self._resolve(sm.PERSON, person_ref)
            self._relate(holder, source_account, REL_OWNS_ACCOUNT, provenance=prov)
            emitted = True
        return emitted

    def _cdr(self, row: dict, m: dict, prov: dict) -> bool:
        when = sm.normalize_date(row.get(m.get("CALL.timestamp", ""), "")) or sm.normalize_date(
            row.get(m.get("COMMON.observed_at", ""), "")
        )
        duration = row.get(m.get("CALL.duration", ""), "")
        from_person = row.get(m.get("CALL.from_person", ""), "")
        to_person = row.get(m.get("CALL.to_person", ""), "")
        from_phone = row.get(m.get("CALL.from_phone", ""), "")
        to_phone = row.get(m.get("CALL.to_phone", ""), "")
        phone_ref = row.get(m.get("PHONE.id", ""), "") or row.get(m.get("PHONE.number", ""), "")

        attributes = {
            "duration_seconds": duration,
            "call_type": row.get(m.get("CALL.type", ""), ""),
            "cell": row.get(m.get("CALL.cell", ""), ""),
        }
        emitted = False
        case_ids = self._row_case_ids(row, m)
        if from_phone and to_phone:
            a = self._resolve(sm.PHONE, from_phone)
            b = self._resolve(sm.PHONE, to_phone)
            self._relate(a, b, REL_CALLED, observed_at=when, attributes=attributes,
                         provenance=prov, case_ids=case_ids)
            emitted = True
        if from_person and to_person:
            a = self._resolve(sm.PERSON, from_person)
            b = self._resolve(sm.PERSON, to_person)
            self._relate(a, b, REL_CALLED, observed_at=when, attributes=attributes,
                         provenance=prov, case_ids=case_ids)
            emitted = True
        if from_person and phone_ref:
            self._relate(
                self._resolve(sm.PERSON, from_person),
                self._resolve(sm.PHONE, phone_ref),
                REL_USES_PHONE, provenance=prov, case_ids=case_ids,
            )
            emitted = True
        return emitted

    def _sms(self, row: dict, m: dict, prov: dict) -> bool:
        when = sm.normalize_date(row.get(m.get("COMMON.observed_at", ""), ""))
        from_person = row.get(m.get("CALL.from_person", ""), "")
        to_person = row.get(m.get("CALL.to_person", ""), "")
        if not from_person or not to_person:
            return self._cdr(row, m, prov)
        self._relate(
            self._resolve(sm.PERSON, from_person),
            self._resolve(sm.PERSON, to_person),
            REL_MESSAGED, observed_at=when,
            attributes={"category": row.get(m.get("COMMON.notes", ""), "")},
            provenance=prov,
        )
        phone_ref = row.get(m.get("PHONE.id", ""), "")
        if phone_ref:
            self._relate(
                self._resolve(sm.PERSON, from_person),
                self._resolve(sm.PHONE, phone_ref),
                REL_USES_PHONE, provenance=prov,
            )
        return True

    def _addresses(self, row: dict, m: dict, prov: dict) -> bool:
        parts = [
            row.get(m.get("ADDRESS.line1", ""), ""),
            row.get(m.get("ADDRESS.locality", ""), ""),
            row.get(m.get("ADDRESS.city", ""), ""),
            row.get(m.get("ADDRESS.state", ""), ""),
        ]
        label = ", ".join(p for p in parts if p)
        address_id = row.get(m.get("ADDRESS.id", ""), "")
        if not label and not address_id:
            return False
        return bool(
            self._register(
                sm.ADDRESS, address_id, name=label or address_id,
                normalized_value=label.upper() or address_id,
                attributes={
                    "city": row.get(m.get("ADDRESS.city", ""), ""),
                    "state": row.get(m.get("ADDRESS.state", ""), ""),
                    "postal_code": row.get(m.get("ADDRESS.postal_code", ""), ""),
                },
                provenance=prov,
            )
        )

    def _locations(self, row: dict, m: dict, prov: dict) -> bool:
        name = row.get(m.get("LOCATION.name", ""), "") or row.get(m.get("PERSON.name", ""), "") or row.get("name", "") or row.get("location_name", "")
        location_id = row.get(m.get("LOCATION.id", ""), "") or row.get("location_id", "")
        if not name and not location_id:
            return False
        return bool(
            self._register(
                sm.LOCATION, location_id, name=name or location_id,
                normalized_value=(name or location_id).upper(),
                attributes={
                    "city": row.get(m.get("ADDRESS.city", ""), ""),
                    "state": row.get(m.get("ADDRESS.state", ""), ""),
                    "location_type": row.get(m.get("LOCATION.type", ""), ""),
                    "latitude": row.get(m.get("LOCATION.lat", ""), ""),
                    "longitude": row.get(m.get("LOCATION.lon", ""), ""),
                },
                provenance=prov,
            )
        )

    def _organizations(self, row: dict, m: dict, prov: dict) -> bool:
        name = row.get(m.get("ORGANIZATION.name", ""), "") or row.get(m.get("PERSON.name", ""), "") or row.get("name", "") or row.get("org_name", "") or row.get("company_name", "")
        org_id = row.get(m.get("ORGANIZATION.id", ""), "") or row.get("organization_id", "") or row.get("org_id", "")
        if not name and not org_id:
            return False
        return bool(
            self._register(
                sm.ORGANIZATION, org_id, name=name or org_id,
                normalized_value=(name or org_id).upper(),
                attributes={
                    "organization_type": row.get(m.get("ORGANIZATION.type", ""), ""),
                    "city": row.get(m.get("ADDRESS.city", ""), ""),
                    "state": row.get(m.get("ADDRESS.state", ""), ""),
                    "status": row.get(m.get("ORGANIZATION.status", ""), ""),
                    "incorporated": row.get(m.get("ORGANIZATION.incorporated", ""), ""),
                },
                provenance=prov,
            )
        )

    def _employment(self, row: dict, m: dict, prov: dict) -> bool:
        person = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), ""))
        org = self._resolve(sm.ORGANIZATION, row.get(m.get("ORGANIZATION.id", ""), ""))
        if not person or not org:
            return False
        self._relate(
            person, org, REL_MEMBER_OF,
            valid_from=sm.normalize_date(row.get(m.get("COMMON.valid_from", ""), "")),
            valid_to=sm.normalize_date(row.get(m.get("COMMON.valid_to", ""), "")),
            attributes={"role": row.get(m.get("COMMON.role", ""), "")},
            provenance=prov,
        )
        return True

    def _cases(self, row: dict, m: dict, prov: dict) -> bool:
        case_id = row.get(m.get("CASE.id", ""), "")
        case_number = row.get(m.get("CASE.number", ""), "") or case_id
        if not case_id and not case_number:
            return False
        # If this row names an FIR, alias it to the canonical case
        fir_no = row.get(m.get("FIR.number", ""), "") or row.get("fir_no", "")
        fir_id = row.get(m.get("FIR.id", ""), "") or row.get("fir_id", "")
        if case_id:
            case_cid = cid(sm.CASE, case_id)
            if fir_id:
                self._alias[f"FIR|{fir_id}"] = case_cid
                self._alias[f"CASE|{fir_id}"] = case_cid
            if fir_no:
                self._alias[f"FIR|{fir_no}"] = case_cid
                self._alias[f"CASE|{fir_no}"] = case_cid

        case_type = row.get(m.get("CASE.type", ""), "")
        unit = row.get(m.get("CASE.unit", ""), "")
        title = row.get(m.get("CASE.title", ""), "") or " — ".join(
            p for p in [case_type.title() if case_type else "", unit] if p
        ) or case_number
        return bool(
            self._register(
                sm.CASE, case_id or case_number, name=title,
                normalized_value=(case_number or case_id).upper(),
                attributes={
                    "case_number": case_number,
                    "case_type": case_type,
                    "opened_date": sm.normalize_date(row.get(m.get("CASE.opened", ""), "")) or row.get(m.get("CASE.opened", ""), ""),
                    "police_unit": unit,
                    "raw_status": row.get(m.get("CASE.status", ""), ""),
                },
                provenance=prov,
            )
        )

    def _fir(self, row: dict, m: dict, prov: dict) -> bool:
        fir_id = str(row.get(m.get("FIR.id", ""), "") or row.get("fir_id", "")).strip()
        fir_no = str(row.get(m.get("FIR.number", ""), "") or row.get("fir_no", "")).strip() or fir_id
        case_id = str(row.get(m.get("CASE.id", ""), "") or row.get("case_id", "")).strip()
        if not fir_id and not fir_no:
            return False
        if not fir_id:
            fir_id = fir_no

        if case_id:
            case_cid = self._resolve(sm.CASE, case_id)
            self._alias[f"FIR|{fir_id}"] = case_cid
            self._alias[f"CASE|{fir_id}"] = case_cid
            if fir_no:
                self._alias[f"FIR|{fir_no}"] = case_cid
                self._alias[f"CASE|{fir_no}"] = case_cid

            fid = self._register(
                sm.FIR, fir_id, name=fir_no, normalized_value=fir_no.upper(),
                attributes={
                    "fir_number": fir_no,
                    "case_id": case_id,
                    "registration_date": sm.normalize_date(row.get(m.get("FIR.date", ""), "")) or row.get(m.get("FIR.date", ""), ""),
                    "police_station": row.get(m.get("CASE.unit", ""), ""),
                    "status": row.get(m.get("PERSON.status", ""), "") or row.get(m.get("CASE.status", ""), ""),
                },
                provenance=prov,
            )
            self._relate(case_cid, fid, REL_HAS_FIR, provenance=prov, case_ids=[case_cid])
        else:
            fid = self._register(
                sm.FIR, fir_id, name=fir_no, normalized_value=fir_no.upper(),
                provenance=prov,
            )

        complainant = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), ""))
        if complainant and case_id:
            case_cid = self._resolve(sm.CASE, case_id)
            self._relate(complainant, case_cid, REL_INVOLVED_IN, attributes={"role": "Complainant"}, provenance=prov, case_ids=[case_cid])
        return bool(fid)

    def _case_entities(self, row: dict, m: dict, prov: dict) -> bool:
        raw_case = row.get(m.get("CASE.id", ""), "") or row.get(m.get("CASE.number", ""), "") or row.get("case_id", "")
        case = self._resolve(sm.CASE, raw_case)
        person = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), ""))
        if not case or not person:
            return False
        role = row.get(m.get("COMMON.role", ""), "") or row.get("case_role", "")
        self._relate(
            person, case, REL_INVOLVED_IN,
            attributes={"role": role},
            provenance=prov, case_ids=[case],
        )
        return True

    def _evidence(self, row: dict, m: dict, prov: dict) -> bool:
        evidence_id = row.get(m.get("EVIDENCE.id", ""), "")
        if not evidence_id:
            return False
        eid = self._register(
            sm.EVIDENCE, evidence_id, name=evidence_id,
            normalized_value=evidence_id.upper(),
            attributes={
                "evidence_type": row.get(m.get("EVIDENCE.type", ""), ""),
                "collected_date": sm.normalize_date(row.get(m.get("EVIDENCE.collected", ""), "")) or row.get(m.get("EVIDENCE.collected", ""), ""),
                "status": row.get(m.get("EVIDENCE.status", ""), ""),
            },
            provenance=prov,
        )
        case = self._resolve(sm.CASE, row.get(m.get("CASE.id", ""), "")) if m.get("CASE.id") else None
        self._relate(case, eid, REL_HAS_EVIDENCE, provenance=prov, case_ids=[case] if case else [])
        return bool(eid)

    def _properties(self, row: dict, m: dict, prov: dict) -> bool:
        property_id = row.get(m.get("PROPERTY.id", ""), "")
        if not property_id:
            return False
        pid = self._register(
            sm.PROPERTY, property_id, name=property_id,
            normalized_value=property_id.upper(),
            attributes={
                "property_type": row.get(m.get("PROPERTY.type", ""), ""),
                "declared_value": row.get(m.get("PROPERTY.value", ""), ""),
                "declared_value_inr": sm.normalize_amount(row.get(m.get("PROPERTY.value", ""), "")),
                "transaction_date": sm.normalize_date(row.get(m.get("PROPERTY.transaction_date", ""), "")) or row.get(m.get("PROPERTY.transaction_date", ""), ""),
            },
            provenance=prov,
        )
        address = self._resolve(sm.ADDRESS, row.get(m.get("ADDRESS.id", ""), "")) if m.get("ADDRESS.id") else None
        self._relate(pid, address, REL_LOCATED_AT, provenance=prov)
        owner = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), "")) if m.get("PERSON.id") else None
        self._relate(owner, pid, REL_OWNS_PROPERTY, provenance=prov)
        return bool(pid)

    def _sightings(self, row: dict, m: dict, prov: dict) -> bool:
        # A log may name the vehicle by its plate rather than by the register's
        # internal id (``vehicle`` column vs ``vehicle_id``); both are the same
        # vehicle, and a sighting whose vehicle cannot be resolved is dropped.
        vehicle_ref = row.get(m.get("VEHICLE.id", ""), "") or row.get(
            m.get("VEHICLE.registration", ""), ""
        )
        vehicle = self._resolve(sm.VEHICLE, vehicle_ref)
        location = self._resolve(sm.LOCATION, row.get(m.get("LOCATION.id", ""), "") or row.get(m.get("LOCATION.name", ""), ""))
        when = sm.normalize_date(row.get(m.get("COMMON.observed_at", ""), ""))
        if not vehicle:
            return False
        case_ids = self._row_case_ids(row, m)
        self._relate(
            vehicle, location, REL_SEEN_AT, observed_at=when,
            attributes={"source": row.get(m.get("COMMON.source", ""), "")},
            provenance=prov, case_ids=case_ids,
        )
        driver = row.get(m.get("SIGHTING.driver", ""), "")
        if driver:
            self._relate(
                self._resolve(sm.PERSON, driver), vehicle, REL_DROVE,
                observed_at=when, provenance=prov, case_ids=case_ids,
            )
        return True

    def _travel(self, row: dict, m: dict, prov: dict) -> bool:
        person = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), ""))
        destination = row.get(m.get("TRAVEL.destination", ""), "")
        if not person or not destination:
            return False
        location = self._register(
            sm.LOCATION, None, name=destination, normalized_value=destination.upper(),
            provenance=prov,
        )
        self._relate(
            person, location, REL_TRAVELLED_TO,
            observed_at=sm.normalize_date(row.get(m.get("COMMON.observed_at", ""), "")),
            attributes={
                "mode": row.get(m.get("TRAVEL.mode", ""), ""),
                "origin": row.get(m.get("TRAVEL.origin", ""), ""),
            },
            provenance=prov,
        )
        return True

    def _devices(self, row: dict, m: dict, prov: dict) -> bool:
        device_id = row.get(m.get("DEVICE.id", ""), "")
        imei = row.get(m.get("PHONE.imei", ""), "")
        if not device_id and not imei:
            return False
        did = self._register(
            sm.DEVICE, device_id, name=imei or device_id,
            normalized_value=imei or device_id,
            attributes={"device_type": row.get(m.get("DEVICE.type", ""), ""), "imei": imei},
            provenance=prov,
        )
        owner = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), "")) if m.get("PERSON.id") else None
        self._relate(owner, did, REL_USES_DEVICE, provenance=prov)
        return bool(did)

    def _emails(self, row: dict, m: dict, prov: dict) -> bool:
        address = row.get(m.get("EMAIL.address", ""), "")
        if not address:
            return False
        eid = self._register(
            sm.EMAIL, row.get(m.get("EMAIL.id", ""), "") or None, name=address,
            normalized_value=address.lower(), provenance=prov,
        )
        owner = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), "")) if m.get("PERSON.id") else None
        self._relate(owner, eid, REL_USES_EMAIL, provenance=prov)
        return bool(eid)

    def _officers(self, row: dict, m: dict, prov: dict) -> bool:
        name = row.get(m.get("OFFICER.name", ""), "")
        officer_id = row.get(m.get("OFFICER.id", ""), "")
        if not name and not officer_id:
            return False
        return bool(
            self._register(
                sm.OFFICER, officer_id, name=sm.normalize_name(name) or officer_id,
                normalized_value=sm.normalize_name(name) or officer_id,
                attributes={
                    "rank": row.get(m.get("OFFICER.rank", ""), ""),
                    "unit": row.get(m.get("OFFICER.unit", ""), ""),
                },
                provenance=prov,
            )
        )

    def _events(self, row: dict, m: dict, prov: dict) -> bool:
        event_id = str(row.get(m.get("EVENT.id", ""), "") or row.get("event_id", "")).strip()
        event_type = str(row.get(m.get("EVENT.type", ""), "") or row.get("event_type", "")).strip()
        event_time = str(row.get(m.get("EVENT.time", ""), "") or row.get("event_time", "")).strip()
        summary = str(row.get(m.get("EVENT.summary", ""), "") or row.get("summary", "")).strip()
        case_id = str(row.get(m.get("CASE.id", ""), "") or row.get("case_id", "")).strip()
        location_id = str(row.get(m.get("LOCATION.id", ""), "") or row.get("location_id", "")).strip()

        if not event_id and not summary:
            return False
        if not event_id:
            event_id = f"EVT_{abs(hash(summary + event_time))}"

        eid = self._register(
            sm.EVENT, event_id, name=summary or event_id,
            normalized_value=(summary or event_id).upper(),
            attributes={
                "event_type": event_type,
                "timestamp": sm.normalize_date(event_time) or event_time,
                "description": summary,
                "location_id": location_id,
            },
            provenance=prov,
        )
        if case_id:
            case_cid = self._resolve(sm.CASE, case_id)
            self._relate(case_cid, eid, REL_HAS_EVIDENCE, provenance=prov, case_ids=[case_cid])
        if location_id:
            loc_cid = self._resolve(sm.LOCATION, location_id)
            self._relate(eid, loc_cid, REL_LOCATED_AT, provenance=prov)
        return bool(eid)

    def _relationship_edges(self, row: dict, m: dict, prov: dict) -> bool:
        source_ref = str(row.get(m.get("COMMON.source_ref", ""), "") or row.get("from_id", "")).strip()
        target_ref = str(row.get(m.get("COMMON.target_ref", ""), "") or row.get("to_id", "")).strip()
        raw_rel = str(row.get(m.get("COMMON.relationship", ""), "") or row.get("relationship_type", "")).strip()
        if not source_ref or not target_ref:
            return False
        rel_type = _REL_WORD_MAP.get(sm.norm(raw_rel))
        if not rel_type:
            # An unrecognized relationship is a review item, not an invented
            # ASSOCIATE_OF edge. This is especially important for shared-
            # identifier vocabulary from external exports.
            self.result.warnings.append(
                f"Unrecognized relationship {raw_rel!r}; edge was not created"
            )
            return False

        # Determine source entity type using structured from_type or alias lookup
        raw_source_type = row.get(m.get("COMMON.source_type", ""), "") or row.get("from_type", "")
        source_type = _clean_entity_type(raw_source_type)
        if not source_type:
            for etype in sm.ENTITY_TYPES:
                if f"{etype}|{source_ref}" in self._alias:
                    source_type = etype
                    break
        if not source_type:
            source_type = _infer_type(source_ref)

        # Determine target entity type using structured to_type or alias lookup
        raw_target_type = row.get(m.get("COMMON.target_type", ""), "") or row.get("to_type", "")
        target_type = _clean_entity_type(raw_target_type)
        if not target_type:
            for etype in sm.ENTITY_TYPES:
                if f"{etype}|{target_ref}" in self._alias:
                    target_type = etype
                    break
        if not target_type:
            target_type = _infer_type(target_ref)

        case_id = str(row.get(m.get("CASE.id", ""), "") or row.get("case_id", "")).strip()
        case_ids = [self._resolve(sm.CASE, case_id)] if case_id else []

        source = self._resolve(source_type, source_ref)
        target = self._resolve(target_type, target_ref)
        self._relate(
            source, target, rel_type,
            valid_from=sm.normalize_date(row.get(m.get("COMMON.valid_from", ""), "")),
            valid_to=sm.normalize_date(row.get(m.get("COMMON.valid_to", ""), "")),
            attributes={"declared_relationship": raw_rel},
            provenance=prov,
            case_ids=case_ids,
        )
        return True

    def _generic_table(self, row: dict, m: dict, prov: dict) -> bool:
        """Best-effort handling of a table we could not classify.

        Any canonical entity fields that *did* resolve still produce entities,
        so an unfamiliar CSV contributes what it genuinely contains instead of
        being discarded.
        """
        emitted = False
        for handler in (self._persons, self._phones, self._vehicles, self._accounts,
                        self._organizations, self._locations):
            try:
                if handler(row, m, prov):
                    emitted = True
            except Exception:  # noqa: BLE001
                continue
        return emitted


def _clean_entity_type(raw_type: Any) -> str | None:
    if not raw_type:
        return None
    cleaned = str(raw_type).strip().upper()
    mapping = {
        "PERSON": sm.PERSON,
        "INDIVIDUAL": sm.PERSON,
        "PHONE": sm.PHONE,
        "TELEPHONE": sm.PHONE,
        "MOBILE": sm.PHONE,
        "VEHICLE": sm.VEHICLE,
        "ACCOUNT": sm.ACCOUNT,
        "BANK_ACCOUNT": sm.ACCOUNT,
        "LOCATION": sm.LOCATION,
        "ADDRESS": sm.ADDRESS,
        "ORGANIZATION": sm.ORGANIZATION,
        "COMPANY": sm.ORGANIZATION,
        "CASE": sm.CASE,
        "FIR": sm.FIR,
        "EVIDENCE": sm.EVIDENCE,
        "DEVICE": sm.DEVICE,
        "EMAIL": sm.EMAIL,
        "PROPERTY": sm.PROPERTY,
        "OFFICER": sm.OFFICER,
        "EVENT": sm.EVENT,
    }
    return mapping.get(cleaned)


def _infer_type(reference: str) -> str:
    """Guess the entity type of a bare natural id like ``PHONE_00232`` or ``P001``."""
    ref = str(reference).strip()
    prefix_underscore = ref.split("_", 1)[0].upper()
    table = {
        "PERSON": sm.PERSON, "PER": sm.PERSON,
        "PHONE": sm.PHONE, "PH": sm.PHONE,
        "VEH": sm.VEHICLE, "VEHICLE": sm.VEHICLE,
        "ACCT": sm.ACCOUNT, "ACC": sm.ACCOUNT, "ACCOUNT": sm.ACCOUNT, "BA": sm.ACCOUNT,
        "ADDR": sm.ADDRESS, "ADDRESS": sm.ADDRESS,
        "LOC": sm.LOCATION, "LOCATION": sm.LOCATION, "L": sm.LOCATION,
        "ORG": sm.ORGANIZATION, "ORGANIZATION": sm.ORGANIZATION, "O": sm.ORGANIZATION,
        "CASE": sm.CASE, "C": sm.CASE,
        "FIR": sm.FIR,
        "EVID": sm.EVIDENCE, "EVIDENCE": sm.EVIDENCE,
        "EVT": sm.EVENT, "EVENT": sm.EVENT,
        "DEV": sm.DEVICE, "DEVICE": sm.DEVICE,
        "EMAIL": sm.EMAIL,
        "PROP": sm.PROPERTY, "PROPERTY": sm.PROPERTY,
        "OFF": sm.OFFICER, "OFFICER": sm.OFFICER,
    }
    if prefix_underscore in table:
        return table[prefix_underscore]

    # Try alphanumeric pattern without underscore (P001, PH001, VH001, BA001, L001, O001, C101)
    m = re.match(r"^([A-Z]{1,4})\d+$", ref.upper())
    if m:
        pfx = m.group(1)
        alphanumeric_table = {
            "P": sm.PERSON,
            "PH": sm.PHONE,
            "VH": sm.VEHICLE,
            "BA": sm.ACCOUNT,
            "L": sm.LOCATION,
            "O": sm.ORGANIZATION,
            "C": sm.CASE,
            "EVT": sm.EVENT,
            "FIR": sm.FIR,
        }
        if pfx in alphanumeric_table:
            return alphanumeric_table[pfx]

    return sm.PERSON


_HANDLERS = {
    "PERSON_TABLE": Normalizer._persons,
    "NAME_VARIANTS": Normalizer._name_variants,
    "PHONE_TABLE": Normalizer._phones,
    "VEHICLE_TABLE": Normalizer._vehicles,
    "VEHICLE_OWNERSHIP": Normalizer._vehicle_ownership,
    "ACCOUNT_TABLE": Normalizer._accounts,
    "TRANSACTIONS": Normalizer._transactions,
    "CDR": Normalizer._cdr,
    "SMS": Normalizer._sms,
    "ADDRESS_TABLE": Normalizer._addresses,
    "LOCATION_TABLE": Normalizer._locations,
    "ORGANIZATION_TABLE": Normalizer._organizations,
    "EMPLOYMENT": Normalizer._employment,
    "CASE_TABLE": Normalizer._cases,
    "FIR_TABLE": Normalizer._fir,
    "CASE_ENTITIES": Normalizer._case_entities,
    "EVIDENCE_REGISTER": Normalizer._evidence,
    "EVENT_TABLE": Normalizer._events,
    "PROPERTY_TABLE": Normalizer._properties,
    "VEHICLE_SIGHTINGS": Normalizer._sightings,
    "TRAVEL": Normalizer._travel,
    "DEVICE_TABLE": Normalizer._devices,
    "EMAIL_TABLE": Normalizer._emails,
    "OFFICER_TABLE": Normalizer._officers,
    "RELATIONSHIP_EDGES": Normalizer._relationship_edges,
}
