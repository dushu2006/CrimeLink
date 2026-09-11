"""Source schema -> canonical CrimeLink schema.

The point of this module: CrimeLink must accept a dataset whose columns are
called ``cust_name`` / ``mob_no`` / ``acct_no`` just as readily as one whose
columns are called ``full_name`` / ``phone_number`` / ``account_number``.

Three signals decide a mapping, in this order:

1. **Header aliases** -- a large alias table per canonical field, matched on a
   normalized form of the header (case-, space- and punctuation-insensitive).
2. **Value patterns** -- when a header is unhelpful (``col_3``), the *values*
   are inspected: 10-digit Indian mobile numbers, ``AP21CE9967`` plates, 12-digit
   Aadhaar, ``ABCDE1234F`` PAN, ISO dates, currency amounts.
3. **Table shape** -- the combination of mapped fields decides the semantic
   type of the table (a table with ``from_phone`` + ``to_phone`` + ``duration``
   is CDR, regardless of what the file is called).

Every mapping carries a confidence.  Below ``REVIEW_THRESHOLD`` the mapping is
still applied but reported to the operator for confirmation, because refusing
an unfamiliar CSV outright is worse than importing it with a visible caveat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

#: Mappings at or above this score are considered settled.
CONFIDENT_THRESHOLD = 0.90
#: Below this the operator is asked to confirm the column mapping.
REVIEW_THRESHOLD = 0.70


def norm(name: str) -> str:
    """Normalized comparison form of a column name."""
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


# ---------------------------------------------------------------------------
# Canonical entity types
# ---------------------------------------------------------------------------

PERSON = "PERSON"
PHONE = "PHONE"
VEHICLE = "VEHICLE"
ACCOUNT = "ACCOUNT"
ADDRESS = "ADDRESS"
LOCATION = "LOCATION"
ORGANIZATION = "ORGANIZATION"
CASE = "CASE"
EVIDENCE = "EVIDENCE"
DEVICE = "DEVICE"
EMAIL = "EMAIL"
PROPERTY = "PROPERTY"
DOCUMENT = "DOCUMENT"
OFFICER = "OFFICER"
TRANSACTION = "TRANSACTION"
CALL = "CALL"

ENTITY_TYPES: tuple[str, ...] = (
    PERSON, PHONE, VEHICLE, ACCOUNT, ADDRESS, LOCATION, ORGANIZATION, CASE,
    EVIDENCE, DEVICE, EMAIL, PROPERTY, DOCUMENT, OFFICER, TRANSACTION, CALL,
)

#: How a canonical entity type maps onto a graph node label.  Types absent from
#: this map are event-like and become edges/attributes rather than nodes.
GRAPH_LABELS: dict[str, str] = {
    PERSON: "Person",
    PHONE: "Phone",
    VEHICLE: "Vehicle",
    ACCOUNT: "BankAccount",
    ADDRESS: "Location",
    LOCATION: "Location",
    PROPERTY: "Location",
    ORGANIZATION: "Organization",
    OFFICER: "Person",
    DEVICE: "Phone",
    EMAIL: "Phone",
    EVIDENCE: "Event",
    DOCUMENT: "Event",
}


# ---------------------------------------------------------------------------
# Canonical fields and their aliases
#
# Key = "<ENTITY>.<field>", value = the header spellings that mean it.
# ---------------------------------------------------------------------------

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    # --- identity ---------------------------------------------------------
    "PERSON.id": ("person_id", "personid", "individual_id", "subject_id", "party_id", "suspect_id", "cust_id", "customer_id", "citizen_id"),
    "PERSON.name": ("full_name", "name", "person_name", "subject", "subject_name", "suspect", "suspect_name", "accused", "accused_name", "cust_name", "customer_name", "holder_name", "party_name", "individual", "person"),
    "PERSON.first_name": ("first_name", "firstname", "given_name", "fname"),
    "PERSON.last_name": ("last_name", "lastname", "surname", "family_name", "lname"),
    "PERSON.dob": ("dob", "date_of_birth", "birth_date", "birthdate", "dateofbirth"),
    "PERSON.gender": ("gender", "sex"),
    "PERSON.aadhaar": ("aadhaar", "aadhar", "aadhaar_number", "uid", "uidai", "aadhaar_no"),
    "PERSON.pan": ("pan", "pan_number", "pan_no", "permanent_account_number"),
    "PERSON.occupation": ("occupation", "profession", "job", "designation_civil"),
    "PERSON.risk_role": ("risk_role", "role_in_network", "classification", "risk", "risk_category"),
    "PERSON.status": ("person_status", "subject_status"),
    "PERSON.criminal_status": (
        "criminal_status",
        "criminal_history",
        "conviction_status",
        "criminal_record",
        "record_status",
    ),
    "PERSON.alias": ("alias", "aliases", "aka", "nickname", "surface_name", "known_as", "variant_name"),
    # --- telecom ----------------------------------------------------------
    "PHONE.id": ("phone_id", "phoneid", "msisdn_id", "sim_id", "number_id"),
    "PHONE.number": ("phone", "phone_number", "phoneno", "phone_no", "mobile", "mobile_no", "mobile_number", "msisdn", "contact", "contact_no", "contact_number", "subscriber_number", "cell", "cell_number", "telephone", "tel", "caller", "callee", "a_party", "b_party"),
    "PHONE.status": ("phone_status", "sim_status", "line_status"),
    "PHONE.subscriber_type": ("subscriber_type", "connection_type", "plan_type", "prepaid_postpaid"),
    "PHONE.imei": ("imei", "device_imei", "handset_imei"),
    # --- vehicles ---------------------------------------------------------
    "VEHICLE.id": ("vehicle_id", "vehicleid", "veh_id"),
    "VEHICLE.registration": ("registration", "registration_number", "registration_no", "vehicle_no", "vehicle_number", "plate", "plate_number", "number_plate", "license_plate", "licence_plate", "vehicle_registration", "reg_no", "regno", "rc_number"),
    "VEHICLE.make_model": ("make_model", "makemodel", "model", "make", "vehicle_model", "vehicle_make", "vehicle_type"),
    "VEHICLE.fuel": ("fuel", "fuel_type"),
    "VEHICLE.state": ("registered_state", "registration_state", "rto_state"),
    "VEHICLE.owner_id": ("owner_person_id", "owner_id", "registered_owner_id"),
    "VEHICLE.owner_name": ("owner", "owner_name", "registered_owner"),
    "VEHICLE.color": ("color", "colour", "vehicle_color"),
    # --- finance ----------------------------------------------------------
    "ACCOUNT.id": ("account_id", "accountid", "acct_id", "bank_account_id"),
    "ACCOUNT.number": ("account_number", "account_no", "acct_no", "acctno", "accountnumber", "bank_account", "bank_account_number", "iban", "beneficiary_account", "payer_account"),
    "ACCOUNT.bank_name": ("bank_name", "bank", "bankname", "institution", "bank_code"),
    "ACCOUNT.branch": ("branch", "branch_city", "branch_name", "ifsc", "ifsc_code"),
    "ACCOUNT.type": ("account_type", "acct_type"),
    "ACCOUNT.holder_id": ("holder_person_id", "holder_id", "account_holder_id", "owner_person_id"),
    "ACCOUNT.holder_name": ("account_holder", "holder", "holder_name", "beneficiary_name"),
    # --- transactions -----------------------------------------------------
    "TRANSACTION.id": ("txn_id", "transaction_id", "trans_id", "txnid", "reference_no", "utr"),
    "TRANSACTION.timestamp": ("txn_date", "transaction_date", "txn_timestamp", "value_date", "posting_date", "date_of_transaction"),
    "TRANSACTION.amount": ("amount", "txn_amt", "transaction_amount", "amount_inr", "amt", "value", "credit_amount", "debit_amount"),
    "TRANSACTION.type": ("transaction_type", "txn_type", "type_of_transaction", "dr_cr", "mode"),
    "TRANSACTION.from_account": ("from_account_id", "from_account", "payer_account", "debit_account", "source_account", "sender_account", "remitter_account"),
    "TRANSACTION.to_account": ("to_account_id", "to_account", "beneficiary_account_id", "credit_account", "destination_account", "receiver_account"),
    "TRANSACTION.counterparty": ("counterparty", "counter_party", "merchant", "vendor", "payee", "beneficiary"),
    "TRANSACTION.description": ("description", "narration", "particulars", "remarks", "purpose"),
    # --- communications ---------------------------------------------------
    "CALL.id": ("call_id", "cdr_id", "callid", "record_id_call"),
    "CALL.timestamp": ("call_date", "call_time", "call_timestamp", "start_time", "datetime", "date_time"),
    "CALL.from_person": ("from_person", "caller_id", "from_party", "calling_party", "a_party_id"),
    "CALL.to_person": ("to_person", "callee_id", "to_party", "called_party", "b_party_id"),
    "CALL.from_phone": ("from_phone", "from_phone_id", "calling_number", "caller_number", "a_number", "originating_number"),
    "CALL.to_phone": ("to_phone", "to_phone_id", "called_number", "callee_number", "b_number", "terminating_number"),
    "CALL.duration": ("duration", "duration_sec", "duration_seconds", "call_duration", "dur", "seconds"),
    "CALL.type": ("call_type", "direction", "call_direction"),
    "CALL.cell": ("cell_id", "cell_location_id", "tower_id", "cell_site", "lac"),
    # --- places -----------------------------------------------------------
    "ADDRESS.id": ("address_id", "addressid", "addr_id"),
    "ADDRESS.line1": ("line1", "address", "address_line1", "street", "addr", "premises", "address_line_1"),
    "ADDRESS.locality": ("locality", "area", "neighbourhood", "neighborhood", "line2", "address_line_2"),
    "ADDRESS.city": ("city", "town", "district"),
    "ADDRESS.state": ("state", "province", "region"),
    "ADDRESS.postal_code": ("postal_code", "pincode", "pin_code", "zip", "zipcode", "postcode"),
    "LOCATION.id": ("location_id", "locationid", "loc_id", "place_id"),
    "LOCATION.name": ("location_name", "location", "place", "place_name", "site", "landmark"),
    "LOCATION.type": ("location_type", "place_type"),
    "LOCATION.lat": ("latitude", "lat"),
    "LOCATION.lon": ("longitude", "lon", "lng", "long"),
    # --- organizations ----------------------------------------------------
    "ORGANIZATION.id": ("org_id", "organization_id", "organisation_id", "company_id", "entity_id"),
    "ORGANIZATION.name": ("org_name", "organization", "organisation", "organization_name", "company", "company_name", "firm", "business_name", "employer"),
    "ORGANIZATION.type": ("org_type", "organization_type", "business_type", "sector", "industry"),
    "ORGANIZATION.status": ("org_status", "company_status"),
    "ORGANIZATION.incorporated": ("incorporated", "incorporation_date", "registered_on", "date_of_incorporation"),
    # --- cases ------------------------------------------------------------
    "CASE.id": ("case_id", "caseid", "crime_id", "fir_id"),
    "CASE.number": ("case_number", "case_no", "fir_number", "fir_no", "crime_number", "cr_no"),
    "CASE.type": ("case_type", "crime_type", "offence", "offense", "offence_type", "nature_of_crime"),
    "CASE.opened": ("opened_date", "registered_date", "date_registered", "case_date", "reported_on", "date_opened", "incident_date"),
    "CASE.unit": ("police_unit", "police_station", "station", "unit", "investigating_unit", "ps"),
    "CASE.status": ("case_status", "investigation_status", "status_of_case"),
    "CASE.title": ("case_title", "title", "subject_of_case"),
    # --- evidence / documents --------------------------------------------
    "EVIDENCE.id": ("evidence_id", "exhibit_id", "item_id", "evidenceid"),
    "EVIDENCE.type": ("evidence_type", "exhibit_type", "item_type"),
    "EVIDENCE.collected": ("collected_date", "seizure_date", "date_collected", "recovered_on"),
    "EVIDENCE.status": ("evidence_status", "custody_status", "exhibit_status"),
    "DOCUMENT.id": ("document_id", "doc_id", "file_id"),
    "DOCUMENT.type": ("document_type", "doc_type"),
    "DOCUMENT.path": ("file_path", "path", "filename", "file_name", "document_path"),
    # --- misc entities ----------------------------------------------------
    "DEVICE.id": ("device_id", "handset_id", "deviceid"),
    "DEVICE.type": ("device_type", "handset_type", "make_device"),
    "EMAIL.id": ("email_id", "emailid"),
    "EMAIL.address": ("email", "email_address", "mail", "e_mail", "mailid"),
    "PROPERTY.id": ("property_id", "propertyid", "asset_id"),
    "PROPERTY.type": ("property_type", "asset_type", "land_type"),
    "PROPERTY.value": ("declared_value", "property_value", "valuation", "assessed_value", "market_value"),
    "PROPERTY.transaction_date": ("transaction_date", "registration_date", "purchase_date", "sale_date"),
    "OFFICER.id": ("officer_id", "io_id", "badge_id"),
    "OFFICER.name": ("officer_name", "officer", "investigating_officer", "io_name"),
    "OFFICER.rank": ("rank", "designation"),
    "OFFICER.unit": ("officer_unit", "posting", "unit_name"),
    # --- generic temporal / relational ------------------------------------
    "COMMON.valid_from": ("valid_from", "start_date", "from_date", "effective_from", "since"),
    "COMMON.valid_to": ("valid_to", "end_date", "to_date", "effective_to", "until"),
    "COMMON.observed_at": ("date", "timestamp", "observed_at", "seen_at", "datetime", "event_date", "time"),
    "COMMON.role": ("role", "relationship_role", "capacity", "involvement"),
    "COMMON.source": ("source", "source_type", "reported_by", "origin"),
    "COMMON.relationship": ("relationship", "relation", "rel_type", "link_type", "edge_type"),
    "COMMON.source_ref": ("source_id", "from_id", "src_id", "subject_ref"),
    "COMMON.target_ref": ("target_id", "to_id", "dst_id", "object_ref"),
    "COMMON.notes": ("notes", "summary", "comment", "observation", "details"),
    "COMMON.record_id": ("record_id", "row_id", "serial", "sr_no", "s_no", "id"),
    # --- travel / sightings ----------------------------------------------
    "TRAVEL.id": ("travel_id", "trip_id", "journey_id"),
    "TRAVEL.mode": ("mode", "travel_mode", "transport_mode"),
    "TRAVEL.origin": ("origin", "from_city", "departure", "source_city"),
    "TRAVEL.destination": ("destination", "to_city", "arrival", "destination_city"),
    "SIGHTING.id": ("sighting_id", "observation_id", "anpr_id"),
    "SIGHTING.driver": ("observed_driver", "driver", "driver_id", "occupant"),
}

#: Reverse index: normalized alias -> canonical field.  Built once.
_ALIAS_INDEX: dict[str, str] = {}
for _canonical, _aliases in FIELD_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_INDEX.setdefault(norm(_alias), _canonical)
    _ALIAS_INDEX.setdefault(norm(_canonical.split(".", 1)[1]), _canonical)


# ---------------------------------------------------------------------------
# Near-miss header matching
# ---------------------------------------------------------------------------
#
# Exact aliases only ever cover headings somebody thought of in advance.  Real
# exports are written by people: "NAME OF PERSON", "Residential Address",
# "Mobile No. (Primary)".  None of those are in the alias list and none of them
# are ambiguous to a human, so matching on *tokens* rather than on the whole
# string closes most of the gap without inventing meanings.
#
# The rule is deliberately conservative: every token of the alias must appear
# in the column heading. "person_name" matches "NAME OF PERSON" because both
# its tokens are present; it does not match "person_id", and nothing matches on
# a coincidental substring, because "no" inside "notes" is not a token.

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

#: Single-token aliases shorter than this are too generic to match loosely --
#: with these few exceptions, which are unambiguous wherever they appear.
_MIN_LOOSE_TOKEN = 5
_DISTINCTIVE_SHORT = frozenset({"imei", "imsi", "ifsc", "aadhaar", "upi", "vin"})


def tokens_of(name: str) -> frozenset[str]:
    return frozenset(part for part in _TOKEN_SPLIT.split(str(name).lower()) if part)


def _build_alias_tokens() -> list[tuple[frozenset[str], str, str]]:
    out: list[tuple[frozenset[str], str, str]] = []
    seen: set[tuple[frozenset[str], str]] = set()
    for canonical, aliases in FIELD_ALIASES.items():
        candidates = list(aliases) + [canonical.split(".", 1)[1]]
        for alias in candidates:
            alias_tokens = tokens_of(alias)
            if not alias_tokens:
                continue
            if len(alias_tokens) == 1:
                only = next(iter(alias_tokens))
                if len(only) < _MIN_LOOSE_TOKEN and only not in _DISTINCTIVE_SHORT:
                    continue
            key = (alias_tokens, canonical)
            if key in seen:
                continue
            seen.add(key)
            out.append((alias_tokens, canonical, alias))
    # Longest aliases first: "person_name" must win over "name" on a heading
    # that contains both.
    out.sort(key=lambda item: (-len(item[0]), -len(item[2])))
    return out


_ALIAS_TOKENS = _build_alias_tokens()


def match_by_tokens(column: str) -> tuple[str, float] | None:
    """Best token-subset alias match for *column*, if any is safe enough.

    Returns ``(canonical_field, confidence)``. Confidence stays below the
    "confident" threshold so a table mapped this way is still surfaced for
    review -- a good guess is reported as a guess.
    """
    column_tokens = tokens_of(column)
    if not column_tokens:
        return None
    for alias_tokens, canonical, _alias in _ALIAS_TOKENS:
        if alias_tokens <= column_tokens:
            # Two matching tokens ("name of person") is far stronger evidence
            # than one ("residential address"), and is scored accordingly.
            return (canonical, 0.88 if len(alias_tokens) > 1 else 0.76)
    return None


# ---------------------------------------------------------------------------
# Value patterns -- used when the header alone is not decisive
# ---------------------------------------------------------------------------

_PATTERNS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("PERSON.aadhaar", re.compile(r"^\d{12}$"), 0.80),
    ("PERSON.pan", re.compile(r"^[A-Z]{5}\d{4}[A-Z]$"), 0.95),
    ("PHONE.imei", re.compile(r"^\d{15}$"), 0.85),
    ("PHONE.number", re.compile(r"^(?:\+?91[\-\s]?)?[6-9]\d{9}$"), 0.90),
    ("VEHICLE.registration", re.compile(r"^[A-Z]{2}\s?\d{1,2}\s?[A-Z]{1,3}\s?\d{4}$"), 0.92),
    ("EMAIL.address", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"), 0.95),
    ("ACCOUNT.branch", re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$"), 0.95),  # IFSC
    ("ACCOUNT.number", re.compile(r"^\d{11,18}$"), 0.60),
    ("COMMON.observed_at", re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$"), 0.70),
)


def detect_by_values(values: Iterable[Any]) -> tuple[str | None, float]:
    """Infer a canonical field from a column's values alone."""
    samples = [str(v).strip() for v in values if str(v or "").strip()]
    if len(samples) < 3:
        return (None, 0.0)
    samples = samples[:100]
    best: tuple[str | None, float] = (None, 0.0)
    for canonical, pattern, weight in _PATTERNS:
        hits = sum(1 for s in samples if pattern.match(s.upper() if canonical.startswith(("VEHICLE", "PERSON.pan", "ACCOUNT.branch")) else s))
        ratio = hits / len(samples)
        if ratio >= 0.75:
            score = weight * ratio
            if score > best[1]:
                best = (canonical, round(score, 3))
    return best


# ---------------------------------------------------------------------------
# Value agreement -- a header name is a claim, the values are the evidence
# ---------------------------------------------------------------------------

_DATE_LIKE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}([ T].*)?$|^\d{1,2}[-/]\d{1,2}[-/]\d{4}$")
_NUMERIC_LIKE = re.compile(r"^[-+]?[\d,]*\.?\d+$")
_ID_PREFIX = re.compile(r"^([A-Za-z][A-Za-z0-9]{1,14}[_\-])[A-Za-z0-9]{2,}$")

#: Fields whose values are self-evidently checkable. A column headed
#: ``person_id`` holding dates is not a person id, whatever the header says --
#: real exports have shifted headers, renamed columns and stale templates, and
#: trusting the header blindly is how a People page fills up with dates.
_AGREEMENT_DATE = ("observed_at", "timestamp", "date", "dob", "valid_from", "valid_to")
_AGREEMENT_NUMBER = ("amount", "duration", "value", "count")


def _sample(values: Iterable[Any], size: int = 60) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            out.append(text)
        if len(out) >= size:
            break
    return out


def column_agreement(canonical: str, values: Iterable[Any]) -> float | None:
    """How well a column's values support the field a header claimed.

    Returns ``None`` when the field has no checkable shape (a free-text name,
    a description) -- absence of evidence is not a contradiction.
    """
    samples = _sample(values)
    if len(samples) < 3:
        return None
    field_name = canonical.split(".", 1)[-1].lower()
    # Judge by what the normalizers can actually parse, not by a narrow regex:
    # "27.87 lakh" is an amount and "16/03/2024" is a date.
    dates = sum(1 for s in samples if normalize_date(s))
    numbers = sum(1 for s in samples if normalize_amount(s) is not None)
    total = len(samples)

    if field_name in _AGREEMENT_DATE:
        return dates / total
    if field_name in _AGREEMENT_NUMBER:
        return numbers / total
    if canonical == "EMAIL.address":
        return sum(1 for s in samples if "@" in s) / total
    if canonical == "PHONE.number":
        return sum(1 for s in samples if len(_NON_DIGITS.sub("", s)) >= 7) / total
    if field_name in {"id", "case_id", "person_id", "account_id", "vehicle_id"} or field_name.endswith("_id"):
        # An identifier is whatever the dataset says it is -- but it is not a
        # date, and it is not a currency amount.
        return 1.0 - (dates / total)
    if field_name in {"name", "full_name", "org_name", "title"}:
        return 1.0 - ((dates + numbers) / total)
    return None


#: Below this, the values contradict the header outright and the header loses.
AGREEMENT_FLOOR = 0.5

#: Two columns of the same key in one row are a directed pair: a call has a
#: caller and a callee, a transfer a source and a destination. Headerless
#: exports lose those labels, but the shape still says what they are.
_PAIRED_FIELDS: dict[str, tuple[str, str]] = {
    "PERSON.id": ("CALL.from_person", "CALL.to_person"),
    "PHONE.id": ("CALL.from_phone", "CALL.to_phone"),
    "ACCOUNT.id": ("TRANSACTION.from_account", "TRANSACTION.to_account"),
}

#: Categorical columns never legitimately hold another table's primary keys.
_CATEGORICAL_FIELDS = frozenset({"type", "category", "status", "mode", "subtype", "kind"})


def uniform_prefix(values: Iterable[Any]) -> str | None:
    """The single identifier prefix a column's values share, if they share one."""
    samples = _sample(values)
    if len(samples) < 3:
        return None
    prefixes = {m.group(1).upper() for s in samples if (m := _ID_PREFIX.match(s))}
    if len(prefixes) != 1:
        return None
    prefix = prefixes.pop()
    if sum(1 for s in samples if s.upper().startswith(prefix)) / len(samples) < 0.9:
        return None
    return prefix


class SchemaLexicon:
    """Identifier vocabulary learned from one dataset's own confident columns.

    Datasets label their keys: ``ACCT_00240``, ``PERSON_00450``, ``TXN_005403``.
    Once a column whose header and values agree teaches us that ``ACCT_`` means
    ``ACCOUNT.id``, any later column full of ``ACCT_`` values is an account
    reference -- even if its header says something else entirely, which is
    exactly the case for an export whose header row has slipped.

    Learned per dataset and never shared between them: another dataset's
    ``ACC_`` may mean something else.
    """

    #: A prefix must be seen on this many values -- and account for at least
    #: 90% of the column it was learned from -- before it may outvote a header.
    STRONG_SUPPORT = 5

    def __init__(self) -> None:
        self._prefixes: dict[str, dict[str, int]] = {}

    def learn_column(self, canonical: str, values: Iterable[Any]) -> None:
        if not canonical or not canonical.endswith((".id", "_id")):
            return
        samples = _sample(values, 200)
        if len(samples) < 3:
            return
        prefixes = {m.group(1).upper() for s in samples if (m := _ID_PREFIX.match(s))}
        if len(prefixes) != 1:
            return
        prefix = prefixes.pop()
        matched = sum(1 for s in samples if s.upper().startswith(prefix))
        if matched / len(samples) < 0.9:
            return
        self._prefixes.setdefault(prefix, {})
        self._prefixes[prefix][canonical] = self._prefixes[prefix].get(canonical, 0) + matched

    def prefix_for(self, canonical: str) -> str | None:
        """The prefix this dataset uses for a field, if it uses one."""
        best: tuple[str | None, int] = (None, 0)
        for prefix, counts in self._prefixes.items():
            support = counts.get(canonical, 0)
            if support > best[1]:
                best = (prefix, support)
        return best[0] if best[1] >= self.STRONG_SUPPORT else None

    def knows(self, prefix: str) -> bool:
        return prefix.upper() in self._prefixes

    def guess(self, values: Iterable[Any]) -> tuple[str | None, float, int]:
        """canonical field, confidence, support -- from the values' prefix."""
        samples = _sample(values)
        if len(samples) < 3:
            return (None, 0.0, 0)
        prefixes = {m.group(1).upper() for s in samples if (m := _ID_PREFIX.match(s))}
        if len(prefixes) != 1:
            return (None, 0.0, 0)
        prefix = prefixes.pop()
        if sum(1 for s in samples if s.upper().startswith(prefix)) / len(samples) < 0.9:
            return (None, 0.0, 0)
        candidates = self._prefixes.get(prefix)
        if not candidates:
            return (None, 0.0, 0)
        canonical, support = max(candidates.items(), key=lambda kv: kv[1])
        return (canonical, 0.88, support)

    def as_dict(self) -> dict[str, str]:
        return {
            prefix: max(counts.items(), key=lambda kv: kv[1])[0]
            for prefix, counts in sorted(self._prefixes.items())
        }


# ---------------------------------------------------------------------------
# Table-level mapping
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ColumnMapping:
    column: str
    canonical: str | None
    confidence: float
    basis: str          # "alias" | "value_pattern" | "unmapped"


@dataclass(slots=True)
class TableMapping:
    """How one parsed table maps onto the canonical schema."""

    semantic_type: str                          # e.g. "PERSON_TABLE", "CDR", "UNKNOWN"
    primary_entity: str | None                  # canonical entity this table is about
    columns: list[ColumnMapping] = field(default_factory=list)
    confidence: float = 0.0
    needs_review: bool = False
    notes: list[str] = field(default_factory=list)
    #: Columns whose values contradicted their header. A table with any of
    #: these is worth mapping again once the whole dataset's key vocabulary is
    #: known -- see the two-phase normalization in ``pipeline``.
    contradictions: list[str] = field(default_factory=list)

    @property
    def mapped(self) -> dict[str, str]:
        """canonical field -> source column (first wins)."""
        out: dict[str, str] = {}
        for entry in self.columns:
            if entry.canonical and entry.canonical not in out:
                out[entry.canonical] = entry.column
        return out

    @property
    def unmapped(self) -> list[str]:
        return [c.column for c in self.columns if not c.canonical]

    def as_dict(self) -> dict[str, Any]:
        return {
            "semantic_type": self.semantic_type,
            "primary_entity": self.primary_entity,
            "confidence": round(self.confidence, 3),
            "needs_review": self.needs_review,
            "notes": list(self.notes),
            "contradictions": list(self.contradictions),
            "columns": [
                {
                    "column": c.column,
                    "canonical": c.canonical,
                    "confidence": round(c.confidence, 3),
                    "basis": c.basis,
                }
                for c in self.columns
            ],
        }


def _has(mapped: dict[str, str], *fields: str) -> bool:
    return all(f in mapped for f in fields)


def _any(mapped: dict[str, str], *fields: str) -> bool:
    return any(f in mapped for f in fields)


def classify_table(mapped: dict[str, str], columns: list[str]) -> tuple[str, str | None, list[str]]:
    """Decide what a table *is* from the fields that resolved.

    Ordered most-specific first: an event table (a CDR, a ledger) is recognised
    before the entity tables whose columns it also contains.
    """
    notes: list[str] = []
    lowered = {norm(c) for c in columns}

    if _has(mapped, "TRANSACTION.amount") and _any(
        mapped, "TRANSACTION.from_account", "TRANSACTION.to_account", "ACCOUNT.id", "ACCOUNT.number"
    ):
        return ("TRANSACTIONS", TRANSACTION, notes)
    if _any(mapped, "CALL.from_phone", "CALL.to_phone") or (
        _has(mapped, "CALL.duration")
        and _any(mapped, "CALL.from_person", "CALL.to_person", "PHONE.number")
    ) or (
        # A call log that lost its headers still has the shape of one: an
        # identified call, the two parties, and when it happened.
        _has(mapped, "CALL.from_person", "CALL.to_person")
        and _any(mapped, "CALL.id", "COMMON.observed_at")
    ):
        return ("CDR", CALL, notes)
    if _has(mapped, "PHONE.number") and "smsid" in lowered or "messagecategory" in lowered:
        return ("SMS", CALL, notes)
    if _any(mapped, "SIGHTING.id", "SIGHTING.driver") or (
        _has(mapped, "VEHICLE.id") and _any(mapped, "LOCATION.id", "LOCATION.name") and _any(mapped, "COMMON.observed_at")
    ):
        return ("VEHICLE_SIGHTINGS", VEHICLE, notes)
    if _any(mapped, "TRAVEL.id") or (_has(mapped, "TRAVEL.origin", "TRAVEL.destination")):
        return ("TRAVEL", PERSON, notes)
    if _has(mapped, "VEHICLE.owner_id") and _any(mapped, "COMMON.valid_from", "COMMON.valid_to"):
        return ("VEHICLE_OWNERSHIP", VEHICLE, notes)
    if _has(mapped, "PERSON.id", "ORGANIZATION.id"):
        return ("EMPLOYMENT", PERSON, notes)
    if _any(mapped, "COMMON.relationship") and _has(mapped, "COMMON.source_ref", "COMMON.target_ref"):
        return ("RELATIONSHIP_EDGES", None, notes)
    if _has(mapped, "CASE.id") and _has(mapped, "PERSON.id") and not _any(mapped, "CASE.type", "CASE.opened"):
        return ("CASE_ENTITIES", CASE, notes)
    if _has(mapped, "EVIDENCE.id"):
        return ("EVIDENCE_REGISTER", EVIDENCE, notes)
    if _has(mapped, "CASE.id") or _has(mapped, "CASE.number"):
        return ("CASE_TABLE", CASE, notes)
    if _has(mapped, "PERSON.alias") and _has(mapped, "PERSON.id"):
        return ("NAME_VARIANTS", PERSON, notes)
    if _has(mapped, "PROPERTY.id"):
        return ("PROPERTY_TABLE", PROPERTY, notes)
    if _has(mapped, "VEHICLE.registration") or _has(mapped, "VEHICLE.id"):
        return ("VEHICLE_TABLE", VEHICLE, notes)
    if _has(mapped, "ACCOUNT.number") or _has(mapped, "ACCOUNT.id"):
        return ("ACCOUNT_TABLE", ACCOUNT, notes)
    if _has(mapped, "PHONE.number") or _has(mapped, "PHONE.id"):
        return ("PHONE_TABLE", PHONE, notes)
    if _has(mapped, "EMAIL.address"):
        return ("EMAIL_TABLE", EMAIL, notes)
    if _has(mapped, "DEVICE.id"):
        return ("DEVICE_TABLE", DEVICE, notes)
    if _has(mapped, "OFFICER.id") or _has(mapped, "OFFICER.name"):
        return ("OFFICER_TABLE", OFFICER, notes)
    if _has(mapped, "ORGANIZATION.name") or _has(mapped, "ORGANIZATION.id"):
        return ("ORGANIZATION_TABLE", ORGANIZATION, notes)
    if _has(mapped, "ADDRESS.id") or (_has(mapped, "ADDRESS.line1") and _has(mapped, "ADDRESS.city")):
        return ("ADDRESS_TABLE", ADDRESS, notes)
    if _has(mapped, "LOCATION.id") or _has(mapped, "LOCATION.name"):
        return ("LOCATION_TABLE", LOCATION, notes)
    if _has(mapped, "PERSON.name") or _has(mapped, "PERSON.id"):
        return ("PERSON_TABLE", PERSON, notes)

    notes.append(
        "No canonical entity could be identified from these columns; the file is "
        "kept with its rows browsable but produces no entities."
    )
    return ("UNKNOWN", None, notes)


def map_table(
    columns: list[str],
    rows: list[dict[str, Any]],
    lexicon: "SchemaLexicon | None" = None,
) -> TableMapping:
    """Map one table's columns onto the canonical schema.

    Four tiers of evidence, strongest first: an exact header alias, header
    tokens, the dataset's own identifier vocabulary (``lexicon``), and the
    values' own shape. A header-derived mapping is kept only while the values
    do not contradict it -- see :func:`column_agreement`.
    """
    mappings: list[ColumnMapping] = []
    claimed: set[str] = set()
    notes: list[str] = []
    contradictions: list[str] = []

    def values_of(column: str) -> list[Any]:
        return [row.get(column) for row in rows[:200]]

    def from_values(column: str) -> ColumnMapping | None:
        """Lexicon first (dataset-specific), then generic value patterns."""
        if lexicon is not None:
            guess, score, support = lexicon.guess(values_of(column))
            if guess and guess not in claimed and support >= 3:
                claimed.add(guess)
                return ColumnMapping(column, guess, score, "dataset_lexicon")
        guess, score = detect_by_values(values_of(column))
        if guess and guess not in claimed and score >= 0.6:
            claimed.add(guess)
            return ColumnMapping(column, guess, score, "value_pattern")
        return None

    for column in columns:
        header = _ALIAS_INDEX.get(norm(column))
        basis = "alias"
        confidence = 0.97
        if header is None:
            near = match_by_tokens(column)
            if near:
                header, confidence, basis = near[0], near[1], "header_tokens"

        if header is not None:
            agreement = column_agreement(header, values_of(column))
            if agreement is not None and agreement < AGREEMENT_FLOOR:
                # The values say otherwise. Believe the values.
                notes.append(
                    f"Column '{column}' is headed as {header} but its values do "
                    f"not match ({agreement:.0%} agreement); mapped from the data instead"
                )
                contradictions.append(column)
                replacement = from_values(column)
                mappings.append(replacement or ColumnMapping(column, None, 0.0, "unmapped"))
                continue
            header_field = header.split(".", 1)[-1].lower()
            if lexicon is not None and (
                header_field == "id" or header_field in _CATEGORICAL_FIELDS
            ):
                # A strongly supported identifier vocabulary outranks a header
                # that claims a different *entity's* key, or a categorical
                # column that turns out to hold keys: a column of ACCT_* values
                # is an account reference however it is labelled. Role columns
                # (``from_person``, ``owner_id``, ``observed_driver``) are left
                # alone -- they already say which entity they point at, more
                # precisely than the prefix does.
                guess, score, support = lexicon.guess(values_of(column))
                if (
                    guess
                    and guess != header
                    and support >= SchemaLexicon.STRONG_SUPPORT
                    and guess not in claimed
                ):
                    notes.append(
                        f"Column '{column}' holds {guess} identifiers, not {header}; "
                        "mapped from the dataset's own key vocabulary"
                    )
                    contradictions.append(column)
                    claimed.add(guess)
                    mappings.append(ColumnMapping(column, guess, score, "dataset_lexicon"))
                    continue
                # Even when the foreign prefix is unrecognised, a dataset that
                # writes its accounts as ACCT_* is telling us that a column of
                # TXN_* values is not an account id.
                expected = lexicon.prefix_for(header)
                seen = uniform_prefix(values_of(column))
                if expected and seen and seen != expected:
                    notes.append(
                        f"Column '{column}' holds {seen}* values, but this dataset writes "
                        f"{header} as {expected}*; left unmapped rather than guessed"
                    )
                    contradictions.append(column)
                    mappings.append(
                        from_values(column) or ColumnMapping(column, None, 0.0, "unmapped")
                    )
                    continue
            if header not in claimed:
                claimed.add(header)
                mappings.append(ColumnMapping(column, header, confidence, basis))
                continue
            # A second column claiming the same canonical field (``date`` twice)
            # falls through to the values rather than overwriting the first.
            mappings.append(from_values(column) or ColumnMapping(column, None, 0.0, "unmapped"))
            continue

        mappings.append(from_values(column) or ColumnMapping(column, None, 0.0, "unmapped"))

    # A second column holding the same kind of key is the other end of a pair.
    for entry in mappings:
        if entry.canonical:
            continue
        guess = None
        if lexicon is not None:
            guess = lexicon.guess(values_of(entry.column))[0]
        if guess is None:
            guess = detect_by_values(values_of(entry.column))[0]
        pair = _PAIRED_FIELDS.get(guess or "")
        if not pair or guess not in claimed:
            continue
        first, second = pair
        if second in claimed:
            continue
        owner = next((m for m in mappings if m.canonical == guess), None)
        if owner is None:
            continue
        if first not in claimed:
            claimed.discard(guess)
            claimed.add(first)
            owner.canonical = first
            owner.basis = "paired_identifier"
        claimed.add(second)
        entry.canonical = second
        entry.confidence = 0.8
        entry.basis = "paired_identifier"
        notes.append(
            f"Columns '{owner.column}' and '{entry.column}' hold the same kind of key "
            f"and were read as {first} / {second}"
        )

    mapped = {m.canonical: m.column for m in mappings if m.canonical}
    semantic_type, primary, classify_notes = classify_table(mapped, columns)
    notes.extend(classify_notes)

    resolved = [m for m in mappings if m.canonical]
    coverage = len(resolved) / len(columns) if columns else 0.0
    strength = sum(m.confidence for m in resolved) / len(resolved) if resolved else 0.0
    confidence = round(0.6 * strength + 0.4 * coverage, 3)
    if semantic_type == "UNKNOWN":
        confidence = min(confidence, 0.45)

    return TableMapping(
        semantic_type=semantic_type,
        primary_entity=primary,
        columns=mappings,
        confidence=confidence,
        # A contradicted header always deserves an operator's eye, however
        # confident the mapping that replaced it.
        needs_review=confidence < REVIEW_THRESHOLD or bool(contradictions),
        notes=notes,
        contradictions=contradictions,
    )


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------

_NON_DIGITS = re.compile(r"\D+")
_WS = re.compile(r"\s+")


def normalize_phone(value: str) -> str:
    digits = _NON_DIGITS.sub("", str(value or ""))
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) > 10 and digits.startswith("0"):
        digits = digits.lstrip("0")
    return digits[-10:] if len(digits) >= 10 else digits


def normalize_plate(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def normalize_account(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def normalize_name(value: str) -> str:
    return _WS.sub(" ", str(value or "").strip()).title()


def normalize_amount(value: Any) -> float | None:
    """Parse an amount, tolerating ``27.87 lakh``, ``₹1,20,000`` and ``1.2 Cr``."""
    if value is None:
        return None
    text = str(value).strip().lower().replace(",", "").replace("₹", "").replace("rs.", "").replace("inr", "").strip()
    if not text:
        return None
    multiplier = 1.0
    for suffix, factor in (("crore", 1e7), ("cr", 1e7), ("lakh", 1e5), ("lac", 1e5), ("k", 1e3)):
        if text.endswith(suffix):
            multiplier = factor
            text = text[: -len(suffix)].strip()
            break
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def normalize_date(value: Any) -> str | None:
    """Return an ISO ``YYYY-MM-DD`` (or full timestamp) when one is recognisable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:19]
    for pattern, order in (
        (r"^(\d{2})[/-](\d{2})[/-](\d{4})$", ("d", "m", "y")),
        (r"^(\d{4})[/](\d{2})[/](\d{2})$", ("y", "m", "d")),
    ):
        match = re.match(pattern, text)
        if match:
            parts = dict(zip(order, match.groups()))
            return f"{parts['y']}-{parts['m']}-{parts['d']}"
    return None
