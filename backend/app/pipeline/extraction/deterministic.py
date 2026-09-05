"""Stage 2 — deterministic extraction (PRD 8.1).

Regex and gazetteer matching runs *first and always*, and its results are
treated as high-confidence facts.  Wherever a pattern can answer a question, a
pattern is used, because a regex is auditable and a trained model is not
(PRD principle P1).

Confidence assignment:

===================  ==================  ======================================
Target                Method              Confidence (VERIFIED / other source)
===================  ==================  ======================================
Phone                 regex + E.164       0.95 / 0.75
Vehicle plate         regex + gazetteer   0.95
Bank account + IFSC   regex               0.95
Aadhaar (masked)      regex               0.95
IPC / CrPC sections   gazetteer           1.00
Location              gazetteer           0.85
Financial amount      regex               0.95
Structured records    field mapping       0.95 / 0.75
===================  ==================  ======================================

Structured records (a CDR row, a bank transfer, a criminal-history entry) are
also deterministic: the field was written by a machine, so it gets the same
treatment as a regex hit rather than being routed through the NLP model.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import Settings, get_settings
from app.domain.enums import EntityType, SourceConfidence
from app.domain.models import ExtractionCandidate, NormalizedDocument, RelationCandidate
from app.domain.normalize import (
    iter_phones,
    iter_plates,
    normalize_account,
    normalize_aadhaar,
    normalize_ifsc,
    normalize_name,
    normalize_organization,
    normalize_plate,
    normalize_phone,
    parse_amount,
)
from app.logging import get_logger
from app.pipeline.extraction.gazetteers import (
    DISTRICTS,
    IPC_SECTIONS,
    ORG_NAME_TOKENS,
    ORG_SUFFIXES,
)

log = get_logger("crimelink.extract.deterministic")

_IPC_RE = re.compile(
    r"(?:u/s|under section|section|sec\.?|ipc|धारा)\s*(\d{1,3}[A-Za-z]?)(?:\s*(?:,|/|and|&)\s*(\d{1,3}[A-Za-z]?))*",
    re.IGNORECASE,
)
_ALIAS_RE = re.compile(
    r"\b(?:alias|aka|a\.k\.a\.|otherwise known as|@)\s+([A-Z][\w.'\-]+(?:\s+[A-Z][\w.'\-]+){0,3})"
)
_AMOUNT_RE = re.compile(
    r"(?:₹|rs\.?\s*|inr\s*)([0-9][0-9,]*(?:\.\d+)?)|([0-9][0-9,]*(?:\.\d+)?)\s*(?:₹|rs\.?|inr)",
    re.IGNORECASE,
)
_ORG_RE = re.compile(
    r"\b([A-Z][A-Za-z&.\-']*(?:\s+[A-Z][A-Za-z&.\-']*){0,3})\s+"
    r"(?:" + "|".join(re.escape(s) for s in ORG_SUFFIXES) + r")\b"
)


def _base_confidence(source_confidence: SourceConfidence) -> float:
    return 0.95 if source_confidence == SourceConfidence.VERIFIED else 0.75


def _looks_like_organisation(name: str) -> bool:
    """A page/business profile ("Sharma Traders") is a firm, not a person.

    Mislabelling a firm as a person would put it in the fuzzy-match pool and
    offer an investigator a merge that can never be right.
    """
    tokens = [t.lower().rstrip(".") for t in name.split()]
    return any(t in ORG_NAME_TOKENS or t in ORG_SUFFIXES for t in tokens)


def extract_deterministic(
    doc: NormalizedDocument, settings: Settings | None = None
) -> tuple[list[ExtractionCandidate], list[RelationCandidate]]:
    """Run every deterministic extractor over a normalised document."""
    settings = settings or get_settings()
    confidence = _base_confidence(doc.source_confidence)
    staging = doc.source_confidence == SourceConfidence.ANONYMOUS_TIP
    entities: list[ExtractionCandidate] = []
    relations: list[RelationCandidate] = []

    for block in doc.blocks:
        if block.kind == "record":
            e, r = _extract_from_record(doc, block, confidence, staging)
            entities.extend(e)
            relations.extend(r)
        else:
            e, r = _extract_from_text(doc, block, confidence, staging)
            entities.extend(e)
            relations.extend(r)

    log.info(
        "extract.deterministic.done",
        doc_id=doc.doc_id,
        entities=len(entities),
        relations=len(relations),
    )
    return entities, relations


# ---------------------------------------------------------------------------
# Free text
# ---------------------------------------------------------------------------


def _extract_from_text(
    doc: NormalizedDocument, block: Any, confidence: float, staging: bool
) -> tuple[list[ExtractionCandidate], list[RelationCandidate]]:
    entities: list[ExtractionCandidate] = []
    relations: list[RelationCandidate] = []
    text = block.text
    if not text:
        return entities, relations

    def _span(start: int, end: int) -> tuple[int, int]:
        return (block.offset + start, block.offset + end)

    # --- phones -----------------------------------------------------------
    seen_phones: set[str] = set()
    for start, end, e164 in iter_phones(text):
        if e164 in seen_phones:
            continue
        seen_phones.add(e164)
        entities.append(
            ExtractionCandidate(
                entity_type=EntityType.PHONE,
                normalized_value=e164,
                display_value=e164,
                attributes={"number": e164, "source_type": doc.doc_type.lower()},
                confidence=confidence,
                source_doc_id=doc.doc_id,
                case_id=doc.case_id,
                text_span=_span(start, end),
                language=doc.language,
                extractor="deterministic",
                staging=staging,
            )
        )

    # --- vehicle plates ----------------------------------------------------
    seen_plates: set[str] = set()
    for start, end, plate in iter_plates(text):
        if plate in seen_plates:
            continue
        seen_plates.add(plate)
        entities.append(
            ExtractionCandidate(
                entity_type=EntityType.VEHICLE,
                normalized_value=plate,
                display_value=plate,
                attributes={"plate": plate},
                confidence=confidence,
                source_doc_id=doc.doc_id,
                case_id=doc.case_id,
                text_span=_span(start, end),
                language=doc.language,
                extractor="deterministic",
                staging=staging,
            )
        )

    # --- bank accounts and IFSC -------------------------------------------
    for match in re.finditer(r"\b([A-Z]{4}0[A-Z0-9]{6})\b", text.upper()):
        ifsc = normalize_ifsc(match.group(1))
        if ifsc:
            entities.append(
                ExtractionCandidate(
                    entity_type=EntityType.BANK_ACCOUNT,
                    normalized_value=ifsc,
                    display_value=ifsc,
                    attributes={"ifsc": ifsc, "bank": _bank_from_ifsc(ifsc)},
                    confidence=confidence,
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=_span(match.start(), match.end()),
                    language=doc.language,
                    extractor="deterministic",
                    staging=staging,
                )
            )
    for match in re.finditer(r"(?:a/c|account|acct)\s*(?:no\.?|number)?\s*[:#]?\s*(\d{9,18})", text, re.I):
        account = normalize_account(match.group(1))
        if account:
            entities.append(
                ExtractionCandidate(
                    entity_type=EntityType.BANK_ACCOUNT,
                    normalized_value=account,
                    display_value=account,
                    attributes={"number": account},
                    confidence=confidence,
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=_span(match.start(), match.end()),
                    language=doc.language,
                    extractor="deterministic",
                    staging=staging,
                )
            )

    # --- Aadhaar (masked at rest) -----------------------------------------
    for match in re.finditer(r"\b(\d{4})[\s\-]?(\d{4})[\s\-]?(\d{4})\b", text):
        masked = normalize_aadhaar(match.group(0))
        if masked:
            entities.append(
                ExtractionCandidate(
                    entity_type=EntityType.PERSON,
                    normalized_value=masked,
                    display_value=masked,
                    attributes={"aadhaar_masked": masked},
                    confidence=confidence,
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=_span(match.start(), match.end()),
                    language=doc.language,
                    extractor="deterministic",
                    staging=staging,
                )
            )

    # --- IPC sections ------------------------------------------------------
    for match in _IPC_RE.finditer(text):
        for raw in match.groups():
            if not raw:
                continue
            section = raw.upper()
            entities.append(
                ExtractionCandidate(
                    entity_type=EntityType.EVENT,
                    normalized_value=f"IPC_{section}",
                    display_value=f"IPC {section} — {IPC_SECTIONS.get(section, 'Penal provision')}",
                    attributes={
                        "event_type": "CHARGE",
                        "ipc_sections": [section],
                        "legal_reference": True,
                    },
                    confidence=1.0,
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=_span(match.start(), match.end()),
                    language=doc.language,
                    extractor="deterministic",
                    staging=staging,
                )
            )
        break

    # --- locations ---------------------------------------------------------
    lowered = text.lower()
    for name, (lat, lon) in DISTRICTS.items():
        for match in re.finditer(rf"\b{re.escape(name)}\b", lowered):
            entities.append(
                ExtractionCandidate(
                    entity_type=EntityType.LOCATION,
                    normalized_value=name,
                    display_value=name.title(),
                    attributes={
                        "address": name.title(),
                        "location_type": "DISTRICT",
                        "lat": lat,
                        "lon": lon,
                    },
                    confidence=0.85,
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=_span(match.start(), match.end()),
                    language=doc.language,
                    extractor="deterministic",
                    staging=staging,
                )
            )
            break

    # --- organisations -----------------------------------------------------
    for match in _ORG_RE.finditer(text):
        org = normalize_organization(match.group(0))
        if len(org) < 3:
            continue
        entities.append(
            ExtractionCandidate(
                entity_type=EntityType.ORGANIZATION,
                normalized_value=org,
                display_value=match.group(0).strip(),
                attributes={"name": match.group(0).strip(), "org_type": "UNKNOWN"},
                confidence=0.8,
                source_doc_id=doc.doc_id,
                case_id=doc.case_id,
                text_span=_span(match.start(), match.end()),
                language=doc.language,
                extractor="deterministic",
                staging=staging,
            )
        )

    return entities, relations


# ---------------------------------------------------------------------------
# Structured records
# ---------------------------------------------------------------------------


def _extract_from_record(
    doc: NormalizedDocument, block: Any, confidence: float, staging: bool
) -> tuple[list[ExtractionCandidate], list[RelationCandidate]]:
    entities: list[ExtractionCandidate] = []
    relations: list[RelationCandidate] = []
    data: dict[str, Any] = block.data or {}
    kind = data.get("kind")

    def _ent(entity_type: EntityType, value: str, display: str, attrs: dict) -> ExtractionCandidate:
        return ExtractionCandidate(
            entity_type=entity_type,
            normalized_value=value,
            display_value=display,
            attributes=attrs,
            confidence=confidence,
            source_doc_id=doc.doc_id,
            case_id=doc.case_id,
            text_span=block.span,
                origin=block.origin,
            language=doc.language,
            extractor="deterministic",
            staging=staging,
        )

    if kind == "call":
        caller = normalize_phone(str(data.get("caller", "")))
        callee = normalize_phone(str(data.get("callee", "")))
        if not caller or not callee:
            return entities, relations
        entities.append(_ent(EntityType.PHONE, caller, caller, {"number": caller, "source_type": "cdr"}))
        entities.append(_ent(EntityType.PHONE, callee, callee, {"number": callee, "source_type": "cdr"}))
        ts = str(data.get("ts") or "")
        relations.append(
            RelationCandidate(
                source_type=EntityType.PHONE,
                source_value=caller,
                target_type=EntityType.PHONE,
                target_value=callee,
                rel_type="CALLED",
                confidence=confidence,
                attributes={
                    "ts": ts,
                    "duration_s": _as_int(data.get("duration_s")),
                    "direction": str(data.get("direction") or "OUTGOING").upper(),
                    "cell_id": data.get("cell_id"),
                    "imei": data.get("imei"),
                    "call_count": 1,
                    "first_ts": ts,
                    "last_ts": ts,
                },
                source_doc_id=doc.doc_id,
                case_id=doc.case_id,
                text_span=block.span,
                origin=block.origin,
                extractor="deterministic",
                staging=staging,
            )
        )

    elif kind == "transfer":
        from_account = normalize_account(str(data.get("from_account", "")))
        to_account = normalize_account(str(data.get("to_account", "")))
        if not from_account or not to_account:
            return entities, relations
        amount = parse_amount(data.get("amount"))
        ts = str(data.get("ts") or "")
        entities.append(
            _ent(
                EntityType.BANK_ACCOUNT,
                from_account,
                from_account,
                {"number": from_account, "ifsc": data.get("from_ifsc")},
            )
        )
        entities.append(
            _ent(
                EntityType.BANK_ACCOUNT,
                to_account,
                to_account,
                {"number": to_account, "ifsc": data.get("to_ifsc")},
            )
        )
        relations.append(
            RelationCandidate(
                source_type=EntityType.BANK_ACCOUNT,
                source_value=from_account,
                target_type=EntityType.BANK_ACCOUNT,
                target_value=to_account,
                rel_type="TRANSFER_TO",
                confidence=confidence,
                attributes={
                    "amount": amount,
                    "ts": ts,
                    "channel": str(data.get("channel") or "UNKNOWN").upper(),
                    "reference": data.get("reference"),
                },
                source_doc_id=doc.doc_id,
                case_id=doc.case_id,
                text_span=block.span,
                origin=block.origin,
                extractor="deterministic",
                staging=staging,
                # Each transfer stays a discrete, separately-evidenced edge so
                # structuring detection can count and window them.
                discriminator=f"{ts}|{amount}",
            )
        )

    elif kind == "person_record":
        name = str(data.get("name") or "").strip()
        if not name:
            return entities, relations
        aliases = [a for a in (data.get("aliases") or []) if a]
        entities.append(
            _ent(
                EntityType.PERSON,
                normalize_name(name),
                name,
                {
                    "name": name,
                    "aliases": aliases,
                    "role": data.get("role"),
                    "source_type": doc.doc_type.lower(),
                },
            )
        )
        plate = normalize_plate(str(data.get("plate") or ""))
        if plate:
            entities.append(
                _ent(EntityType.VEHICLE, plate, plate, {"plate": plate, "make": data.get("make")})
            )
            relations.append(
                RelationCandidate(
                    source_type=EntityType.PERSON,
                    source_value=normalize_name(name),
                    target_type=EntityType.VEHICLE,
                    target_value=plate,
                    rel_type="OWNS_VEHICLE",
                    confidence=confidence,
                    attributes={"valid_from": data.get("valid_from"), "valid_to": data.get("valid_to")},
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=block.span,
                origin=block.origin,
                    extractor="deterministic",
                )
            )
        phone = normalize_phone(str(data.get("phone") or ""))
        if phone:
            entities.append(
                _ent(EntityType.PHONE, phone, phone, {"number": phone, "source_type": "record"})
            )
            relations.append(
                RelationCandidate(
                    source_type=EntityType.PERSON,
                    source_value=normalize_name(name),
                    target_type=EntityType.PHONE,
                    target_value=phone,
                    rel_type="USES_PHONE",
                    confidence=confidence,
                    attributes={"first_seen": data.get("first_seen"), "last_seen": data.get("last_seen")},
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=block.span,
                origin=block.origin,
                    extractor="deterministic",
                )
            )
        account = normalize_account(str(data.get("account") or ""))
        if account:
            entities.append(
                _ent(
                    EntityType.BANK_ACCOUNT,
                    account,
                    account,
                    {"number": account, "bank_code": data.get("bank_code")},
                )
            )
            relations.append(
                RelationCandidate(
                    source_type=EntityType.PERSON,
                    source_value=normalize_name(name),
                    target_type=EntityType.BANK_ACCOUNT,
                    target_value=account,
                    # The source record names this person as the account
                    # holder, so the honest predicate is OWNS_ACCOUNT — the
                    # same claim the corpus column (holder_person_id) makes.
                    # "Controls" is reserved for third-party control alleged
                    # in intelligence reports, which is a weaker, different
                    # assertion.
                    rel_type="OWNS_ACCOUNT",
                    confidence=confidence,
                    attributes={"bank_code": data.get("bank_code")},
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=block.span,
                    origin=block.origin,
                    extractor="deterministic",
                )
            )
        sections = data.get("ipc_sections") or []
        if sections:
            relations.append(
                RelationCandidate(
                    source_type=EntityType.PERSON,
                    source_value=normalize_name(name),
                    target_type=EntityType.CASE if False else EntityType.EVENT,
                    target_value=f"CONVICTION::{normalize_name(name)}",
                    rel_type="PARTICIPATED_IN",
                    confidence=confidence,
                    attributes={
                        "role": "ACCUSED",
                        "ipc_sections": [str(s) for s in sections],
                        "ts": data.get("case_date"),
                        "event_type": "PRIOR_OFFENCE",
                    },
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=block.span,
                origin=block.origin,
                    extractor="deterministic",
                    discriminator=f"prior::{data.get('case_ref') or ''}::{sections}",
                )
            )

    elif kind == "social_link":
        a = str(data.get("person_a") or "").strip()
        b = str(data.get("person_b") or "").strip()
        if not a or not b:
            return entities, relations
        platform = str(data.get("platform") or "UNKNOWN")
        a_is_org = _looks_like_organisation(a)
        b_is_org = _looks_like_organisation(b)
        a_type = EntityType.ORGANIZATION if a_is_org else EntityType.PERSON
        b_type = EntityType.ORGANIZATION if b_is_org else EntityType.PERSON
        a_value = normalize_organization(a) if a_is_org else normalize_name(a)
        b_value = normalize_organization(b) if b_is_org else normalize_name(b)
        entities.append(_ent(a_type, a_value, a, {"name": a, "source_type": "social_media"}))
        entities.append(_ent(b_type, b_value, b, {"name": b, "source_type": "social_media"}))
        relations.append(
            RelationCandidate(
                source_type=a_type,
                source_value=a_value,
                target_type=b_type,
                target_value=b_value,
                rel_type="LINKED_ON_SOCIAL",
                # Social links are structurally low-confidence; the UI renders
                # them dashed and muted and analytics down-weights them.
                confidence=0.35,
                attributes={"platform": platform, "ts": data.get("ts"), "source_type": "social_media"},
                source_doc_id=doc.doc_id,
                case_id=doc.case_id,
                text_span=block.span,
                origin=block.origin,
                extractor="deterministic",
                staging=staging,
            )
        )

    elif kind == "sighting":
        person = str(data.get("person") or "").strip()
        ts = str(data.get("ts") or "")
        # The "description" column of a corpus sighting row carries the *source*
        # (CCTV / PATROL / ANPR / INTELLIGENCE).  That is a property of the
        # event instance, not its identity: two CCTV sightings are two events.
        source = str(data.get("description") or "SURVEILLANCE").strip() or "SURVEILLANCE"
        source_upper = re.sub(r"[^A-Z0-9]+", "_", source.upper()).strip("_") or "SURVEILLANCE"
        location_name = str(data.get("location") or "").strip()
        # A unique, meaningful display label — without it every CCTV/ANPR/
        # PATROL sighting renders as a bare "CCTV" node and the master graph
        # collapses hundreds of distinct events into identical-looking ones.
        short_ts = ts[:16].replace("T", " ")
        source_label = source_upper.replace("_", " ").title()
        event_label = (
            f"{source_label} · {location_name} · {short_ts}"
            if location_name
            else f"{source_label} · {short_ts}"
        )
        event_key = f"SIGHTING::{normalize_name(person)}::{ts}"
        entities.append(
            _ent(EntityType.PERSON, normalize_name(person), person, {"name": person, "source_type": "surveillance"})
        )
        entities.append(
            _ent(
                EntityType.EVENT,
                event_key,
                event_label,
                {
                    "event_type": source_upper,
                    "timestamp": ts,
                    "description": event_label,
                    "source": source_upper,
                    "source_type": "surveillance",
                },
            )
        )
        relations.append(
            RelationCandidate(
                source_type=EntityType.PERSON,
                source_value=normalize_name(person),
                target_type=EntityType.EVENT,
                target_value=event_key,
                rel_type="PARTICIPATED_IN",
                confidence=confidence,
                attributes={"role": "SUBJECT", "ts": ts, "event_type": source_upper},
                source_doc_id=doc.doc_id,
                case_id=doc.case_id,
                text_span=block.span,
                origin=block.origin,
                extractor="deterministic",
                staging=staging,
            )
        )
        location_name = str(data.get("location") or "").strip()
        if location_name:
            key = location_name.lower()
            coords = DISTRICTS.get(key)
            entities.append(
                _ent(
                    EntityType.LOCATION,
                    key,
                    location_name,
                    {
                        "address": location_name,
                        "location_type": "SIGHTING",
                        **({"lat": coords[0], "lon": coords[1]} if coords else {}),
                    },
                )
            )
            relations.append(
                RelationCandidate(
                    source_type=EntityType.EVENT,
                    source_value=event_key,
                    target_type=EntityType.LOCATION,
                    target_value=key,
                    rel_type="LOCATED_AT",
                    confidence=confidence,
                    attributes={"ts": ts},
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=block.span,
                    origin=block.origin,
                    extractor="deterministic",
                    staging=staging,
                )
            )
        plate = normalize_plate(str(data.get("vehicle_plate") or ""))
        if plate:
            entities.append(_ent(EntityType.VEHICLE, plate, plate, {"plate": plate}))
            relations.append(
                RelationCandidate(
                    source_type=EntityType.PERSON,
                    source_value=normalize_name(person),
                    target_type=EntityType.VEHICLE,
                    target_value=plate,
                    rel_type="OWNS_VEHICLE",
                    confidence=0.6,
                    attributes={"observed_at": ts},
                    source_doc_id=doc.doc_id,
                    case_id=doc.case_id,
                    text_span=block.span,
                    origin=block.origin,
                    extractor="deterministic",
                    staging=staging,
                )
            )

    return entities, relations


def _as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _bank_from_ifsc(ifsc: str) -> str:
    """IFSC positions 1–4 are the bank code."""
    return ifsc[:4].upper()
