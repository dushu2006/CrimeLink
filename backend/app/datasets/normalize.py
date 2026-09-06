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
from dataclasses import dataclass, field
from typing import Any

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
    name: str = ""
    normalized_value: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def merge(self, other: "CanonicalEntity") -> None:
        if not self.name and other.name:
            self.name = other.name
        elif other.name and self.attributes.get("stub") and not other.attributes.get("stub"):
            # A stub is a placeholder created from a bare cross-reference
            # ("Z900"). The moment the real record turns up it wins: showing an
            # investigator an identifier where a name exists is a bug.
            self.name = other.name
            self.normalized_value = other.normalized_value or self.normalized_value
            self.attributes.pop("stub", None)
        if not self.normalized_value and other.normalized_value:
            self.normalized_value = other.normalized_value
        for key, value in other.attributes.items():
            if value not in (None, "") and self.attributes.get(key) in (None, ""):
                self.attributes[key] = value
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


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


class Normalizer:
    """Accumulates canonical records across every table in a dataset."""

    def __init__(self) -> None:
        self.result = NormalizationResult()
        #: natural id -> canonical id, per entity type, for cross-table joins.
        self._alias: dict[str, str] = {}
        #: Provenance of the row currently being processed.  Stubs created by
        #: :meth:`_resolve` inherit it, so even an entity that only ever
        #: appears as a foreign key can name the row that referenced it --
        #: which is what guarantee G1 requires of every graph node.
        self._current_prov: dict[str, Any] = {}

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
        if key:
            canonical = cid(entity_type, key)
        elif normalized_value:
            canonical = _derived_id(entity_type, normalized_value)
        elif name:
            canonical = _derived_id(entity_type, sm.normalize_name(name))
        else:
            return None
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

    def _resolve(self, entity_type: str, reference: str | None) -> str | None:
        """Resolve a cross-table reference, creating a stub if it is unknown.

        A stub keeps the relationship rather than dropping it: the source row
        genuinely asserts the link, and dropping edges because a lookup table
        was not supplied is how a dataset silently loses half its graph.
        """
        ref = (reference or "").strip()
        if not ref:
            return None
        known = self._alias.get(f"{entity_type}|{ref}")
        if known:
            return known
        canonical = cid(entity_type, ref)
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
        """Fold placeholder entities into the record the dataset already has.

        A cell can hold an identifier the dataset defines elsewhere: a
        ``counterparty`` column containing ``PERSON_00379``, a ``party`` column
        holding an ``ORG_0031``. The extractor that met it first had no way to
        know, so it created an entity of its own type named after the raw
        value -- which is how an organisation called "PERSON_00379" ends up on
        the Organizations page.

        This pass runs after every table has been read, when the whole id space
        is known. A placeholder (an entity whose name is nothing but its own
        natural key, so the data never asserted an identity for it) is merged
        into a *differently typed* entity carrying that same natural key and a
        real name, and its relationships are rewired. Nothing is invented: the
        merge happens only because the dataset itself used that identifier for
        a record it did describe.

        Returns the number of entities folded away.
        """
        by_key: dict[str, list[CanonicalEntity]] = {}
        for entity in self.result.entities.values():
            entity_type, _, key = entity.canonical_id.partition(":")
            if not key or key.startswith("~"):
                continue
            by_key.setdefault(key, []).append(entity)

        remap: dict[str, str] = {}
        for key, entities in by_key.items():
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
                survivor.merge(placeholder)
                remap[placeholder.canonical_id] = survivor.canonical_id

        if not remap:
            return 0

        for canonical_id in remap:
            self.result.entities.pop(canonical_id, None)

        rewired: dict[str, CanonicalRelationship] = {}
        for rel in self.result.relationships.values():
            source = remap.get(rel.source_canonical_id, rel.source_canonical_id)
            target = remap.get(rel.target_canonical_id, rel.target_canonical_id)
            if source == target:
                # The placeholder and its record were the same thing; an edge
                # between them says nothing.
                continue
            if (source, target) != (rel.source_canonical_id, rel.target_canonical_id):
                rel.source_canonical_id = source
                rel.target_canonical_id = target
            rewired.setdefault(rel.edge_key, rel)
        self.result.relationships = rewired

        for alias_key, canonical in list(self._alias.items()):
            if canonical in remap:
                self._alias[alias_key] = remap[canonical]

        log.info("normalize.identifiers_reconciled", folded=len(remap))
        return len(remap)

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
            pid = self._register(
                sm.PERSON,
                person_id or None,
                name=name,
                normalized_value=name,
                provenance=prov,
            )
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
        attributes = {
            "date_of_birth": sm.normalize_date(row.get(m.get("PERSON.dob", ""), "")) or row.get(m.get("PERSON.dob", ""), ""),
            "gender": row.get(m.get("PERSON.gender", ""), ""),
            "city": row.get(m.get("ADDRESS.city", ""), ""),
            "state": row.get(m.get("ADDRESS.state", ""), ""),
            "occupation": row.get(m.get("PERSON.occupation", ""), ""),
            "risk_role": row.get(m.get("PERSON.risk_role", ""), ""),
            "aadhaar": row.get(m.get("PERSON.aadhaar", ""), ""),
            "pan": row.get(m.get("PERSON.pan", ""), ""),
        }
        pid = self._register(
            sm.PERSON, person_id, name=sm.normalize_name(name),
            normalized_value=sm.normalize_name(name), attributes=attributes, provenance=prov,
        )
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
        person = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), ""))
        alias = row.get(m.get("PERSON.alias", ""), "")
        if not person or not alias:
            return False
        entity = self.result.entities.get(person)
        if entity is not None:
            aliases = list(entity.attributes.get("aliases") or [])
            if alias not in aliases:
                aliases.append(alias)
            entity.attributes["aliases"] = aliases
            entity.attributes.setdefault("alias_source", row.get(m.get("COMMON.source", ""), ""))
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
                         attributes=attributes, provenance=prov)
            emitted = True
        elif counterparty and source_account:
            org = self._register(
                sm.ORGANIZATION, counterparty, name=counterparty,
                normalized_value=counterparty.upper(), attributes={"role": "counterparty"},
                provenance=prov,
            )
            self._relate(source_account, org, REL_TRANSFER_TO, observed_at=when,
                         attributes=attributes, provenance=prov)
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
        if from_phone and to_phone:
            a = self._resolve(sm.PHONE, from_phone)
            b = self._resolve(sm.PHONE, to_phone)
            self._relate(a, b, REL_CALLED, observed_at=when, attributes=attributes, provenance=prov)
            emitted = True
        if from_person and to_person:
            a = self._resolve(sm.PERSON, from_person)
            b = self._resolve(sm.PERSON, to_person)
            self._relate(a, b, REL_CALLED, observed_at=when, attributes=attributes, provenance=prov)
            emitted = True
        if from_person and phone_ref:
            self._relate(
                self._resolve(sm.PERSON, from_person),
                self._resolve(sm.PHONE, phone_ref),
                REL_USES_PHONE, provenance=prov,
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
        name = row.get(m.get("LOCATION.name", ""), "")
        location_id = row.get(m.get("LOCATION.id", ""), "")
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
        name = row.get(m.get("ORGANIZATION.name", ""), "")
        org_id = row.get(m.get("ORGANIZATION.id", ""), "")
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

    def _case_entities(self, row: dict, m: dict, prov: dict) -> bool:
        case = self._resolve(sm.CASE, row.get(m.get("CASE.id", ""), "") or row.get(m.get("CASE.number", ""), ""))
        person = self._resolve(sm.PERSON, row.get(m.get("PERSON.id", ""), ""))
        if not case or not person:
            return False
        self._relate(
            person, case, REL_INVOLVED_IN,
            attributes={"role": row.get(m.get("COMMON.role", ""), "")},
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
        vehicle = self._resolve(sm.VEHICLE, row.get(m.get("VEHICLE.id", ""), ""))
        location = self._resolve(sm.LOCATION, row.get(m.get("LOCATION.id", ""), "") or row.get(m.get("LOCATION.name", ""), ""))
        when = sm.normalize_date(row.get(m.get("COMMON.observed_at", ""), ""))
        if not vehicle:
            return False
        self._relate(
            vehicle, location, REL_SEEN_AT, observed_at=when,
            attributes={"source": row.get(m.get("COMMON.source", ""), "")},
            provenance=prov,
        )
        driver = row.get(m.get("SIGHTING.driver", ""), "")
        if driver:
            self._relate(
                self._resolve(sm.PERSON, driver), vehicle, REL_DROVE,
                observed_at=when, provenance=prov,
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

    def _relationship_edges(self, row: dict, m: dict, prov: dict) -> bool:
        source_ref = row.get(m.get("COMMON.source_ref", ""), "")
        target_ref = row.get(m.get("COMMON.target_ref", ""), "")
        raw_rel = row.get(m.get("COMMON.relationship", ""), "")
        if not source_ref or not target_ref:
            return False
        rel_type = _REL_WORD_MAP.get(sm.norm(raw_rel), REL_RELATED_TO)
        source = self._resolve(_infer_type(source_ref), source_ref)
        target = self._resolve(_infer_type(target_ref), target_ref)
        self._relate(
            source, target, rel_type,
            valid_from=sm.normalize_date(row.get(m.get("COMMON.valid_from", ""), "")),
            valid_to=sm.normalize_date(row.get(m.get("COMMON.valid_to", ""), "")),
            attributes={"declared_relationship": raw_rel},
            provenance=prov,
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


def _infer_type(reference: str) -> str:
    """Guess the entity type of a bare natural id like ``PHONE_00232``."""
    prefix = str(reference).split("_", 1)[0].upper()
    table = {
        "PERSON": sm.PERSON, "PER": sm.PERSON, "P": sm.PERSON,
        "PHONE": sm.PHONE, "PH": sm.PHONE,
        "VEH": sm.VEHICLE, "VEHICLE": sm.VEHICLE,
        "ACCT": sm.ACCOUNT, "ACC": sm.ACCOUNT, "ACCOUNT": sm.ACCOUNT,
        "ADDR": sm.ADDRESS, "ADDRESS": sm.ADDRESS,
        "LOC": sm.LOCATION, "LOCATION": sm.LOCATION,
        "ORG": sm.ORGANIZATION, "ORGANIZATION": sm.ORGANIZATION,
        "CASE": sm.CASE,
        "EVID": sm.EVIDENCE, "EVIDENCE": sm.EVIDENCE,
        "DEV": sm.DEVICE, "DEVICE": sm.DEVICE,
        "EMAIL": sm.EMAIL,
        "PROP": sm.PROPERTY, "PROPERTY": sm.PROPERTY,
        "OFF": sm.OFFICER, "OFFICER": sm.OFFICER,
    }
    return table.get(prefix, sm.PERSON)


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
    "CASE_ENTITIES": Normalizer._case_entities,
    "EVIDENCE_REGISTER": Normalizer._evidence,
    "PROPERTY_TABLE": Normalizer._properties,
    "VEHICLE_SIGHTINGS": Normalizer._sightings,
    "TRAVEL": Normalizer._travel,
    "DEVICE_TABLE": Normalizer._devices,
    "EMAIL_TABLE": Normalizer._emails,
    "OFFICER_TABLE": Normalizer._officers,
    "RELATIONSHIP_EDGES": Normalizer._relationship_edges,
}
