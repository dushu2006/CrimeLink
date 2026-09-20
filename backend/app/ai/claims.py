"""Claim extraction and normalisation — the shared foundation of the
narrative contradiction and evidence corroboration capabilities.

A **claim** is one atomic assertion made by one record: "PERSON_01 was at
LOCATION_03 at 21:00 on 22 May 2025", "ACCOUNT_02 received ₹4,50,000",
"PERSON_01 called PHONE_01".  Claims are extracted deterministically from the
*sanitized, case-scoped* documents retrieval already authorised; nothing here
can reach outside the evidence the boundary will show the model.

Two properties matter downstream:

* **Provenance** — every claim carries the document id and evidence type it
  came from, so a contradiction or a corroboration can always point back to a
  stored record.  A claim that cannot cite a record is not a claim.
* **De-duplication inside a record** — repeating a sentence twice in one
  document produces one claim, because repetition is not corroboration.

The module is deliberately dependency-free (standard library only) so it can be
unit-tested without a database, graph store or provider.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------- #
# Dimensions
# --------------------------------------------------------------------------- #

DIMENSION_LOCATION = "LOCATION"
DIMENSION_TIME = "TIME"
DIMENSION_AMOUNT = "AMOUNT"
DIMENSION_ROLE = "ROLE"
DIMENSION_RELATIONSHIP = "RELATIONSHIP"
DIMENSION_STATUS = "STATUS"
DIMENSION_EVENT = "EVENT"

#: Dimensions a narrative contradiction can be raised in.  A contradiction is
#: only reported inside this set, which keeps the detector extensible without
#: letting it invent a dimension on the fly.
DIMENSIONS = frozenset(
    {
        DIMENSION_LOCATION,
        DIMENSION_TIME,
        DIMENSION_AMOUNT,
        DIMENSION_ROLE,
        DIMENSION_RELATIONSHIP,
        DIMENSION_STATUS,
        DIMENSION_EVENT,
    }
)

#: Roles that describe mutually exclusive positions in a case file.  A person
#: recorded as the *accused* in one record and as the *witness* in another is a
#: real conflict; "suspect" vs "accused" is escalation, not conflict, so those
#: live in the same group.
ROLE_GROUPS: dict[str, str] = {
    "ACCUSED": "ACCUSED",
    "PRIME_ACCUSED": "ACCUSED",
    "SUSPECT": "ACCUSED",
    "WITNESS": "WITNESS",
    "COMPLAINANT": "COMPLAINANT",
    "VICTIM": "VICTIM",
    "INFORMANT": "INFORMANT",
    "OFFICER": "OFFICER",
    "INVESTIGATING_OFFICER": "OFFICER",
}

#: Coarse classes used to decide whether two role claims really disagree.
ROLE_CLASSES: dict[str, str] = {
    "ACCUSED": "ADVERSE",
    "PRIME_ACCUSED": "ADVERSE",
    "SUSPECT": "ADVERSE",
    "WITNESS": "NEUTRAL",
    "COMPLAINANT": "NEUTRAL",
    "VICTIM": "NEUTRAL",
    "INFORMANT": "NEUTRAL",
    "OFFICER": "OFFICIAL",
    "INVESTIGATING_OFFICER": "OFFICIAL",
}

#: Case status families.  "INVESTIGATION CONCLUDED" and "case pending" cannot
#: both be current, so they are a conflict; "OPEN" and "UNDER_INVESTIGATION"
#: are the same family and are not.
STATUS_GROUPS: dict[str, str] = {
    "OPEN": "ACTIVE",
    "PENDING": "ACTIVE",
    "UNDER_INVESTIGATION": "ACTIVE",
    "IN_PROGRESS": "ACTIVE",
    "ACTIVE": "ACTIVE",
    "UNRESOLVED": "ACTIVE",
    "CLOSED": "CONCLUDED",
    "CONCLUDED": "CONCLUDED",
    "COMPLETED": "CONCLUDED",
    "DISPOSED": "CONCLUDED",
    "FINAL_REPORT_FILED": "CONCLUDED",
    "CHARGES_FILED": "CHARGED",
    "CHARGE_SHEET_FILED": "CHARGED",
    "CHARGESHEET_FILED": "CHARGED",
    "PROSECUTED": "CHARGED",
}

_ROLE_ALIASES: dict[str, str] = {
    "accused": "ACCUSED",
    "prime accused": "PRIME_ACCUSED",
    "main accused": "PRIME_ACCUSED",
    "primary accused": "PRIME_ACCUSED",
    "suspect": "SUSPECT",
    "alleged": "SUSPECT",
    "witness": "WITNESS",
    "eye witness": "WITNESS",
    "eyewitness": "WITNESS",
    "complainant": "COMPLAINANT",
    "informant": "INFORMANT",
    "victim": "VICTIM",
    "deceased": "VICTIM",
    "investigating officer": "INVESTIGATING_OFFICER",
    "investigation officer": "INVESTIGATING_OFFICER",
    "io": "INVESTIGATING_OFFICER",
    "officer": "OFFICER",
    "sub inspector": "OFFICER",
    "inspector": "OFFICER",
}

_STATUS_ALIASES: dict[str, str] = {
    "open": "OPEN",
    "pending": "PENDING",
    "under investigation": "UNDER_INVESTIGATION",
    "under enquiry": "UNDER_INVESTIGATION",
    "under inquiry": "UNDER_INVESTIGATION",
    "in progress": "IN_PROGRESS",
    "active": "ACTIVE",
    "unresolved": "UNRESOLVED",
    "closed": "CLOSED",
    "concluded": "CONCLUDED",
    "completed": "COMPLETED",
    "disposed": "DISPOSED",
    "final report filed": "FINAL_REPORT_FILED",
    "charges filed": "CHARGES_FILED",
    "charge sheet filed": "CHARGESHEET_FILED",
    "chargesheet filed": "CHARGESHEET_FILED",
    "prosecuted": "PROSECUTED",
}

#: Relationship predicates, keyed by the phrase that introduces them.
_RELATION_SYNONYMS: dict[str, str] = {
    "called": "CALLED",
    "phoned": "CALLED",
    "contacted": "CALLED",
    "in contact with": "CALLED",
    "transferred": "TRANSFERRED",
    "paid": "TRANSFERRED",
    "remitted": "TRANSFERRED",
    "received from": "TRANSFERRED",
    "uses": "USES",
    "used": "USES",
    "owns": "OWNS",
    "owned": "OWNS",
    "associated with": "ASSOCIATED_WITH",
    "associate of": "ASSOCIATED_WITH",
    "met": "MET",
    "met with": "MET",
    "travelled with": "TRAVELLED_WITH",
    "accompanied": "TRAVELLED_WITH",
}

_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|denied|denies|without any)\b[^.]{0,40}?"
    r"\b(?:contact|call|association|relationship|link|connection|transaction|transfer|meeting)\b",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_ISO_RE = re.compile(
    r"(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?:[T ](?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?)?"
)
_TEXT_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[A-Za-z]{3,9})\.?,?\s+(?P<year>\d{4})"
)
_TEXT_DATE_ALT_RE = re.compile(
    r"(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>\d{4})"
)
#: Year-less dates ("25 May", "May 25"), which is how a range is usually
#: written in a question: "between May 25 and June 1".
_MONTH_DAY_RE = re.compile(
    r"\b(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b"
)
_DAY_MONTH_RE = re.compile(
    r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?P<month>[A-Za-z]{3,9})\.?\b"
)

_CLOCK_RE = re.compile(
    r"\b(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*(?P<ampm>am|pm)?\b",
    re.IGNORECASE,
)
_DAY_N_RE = re.compile(r"\bDay\s*[-_]?\s*(?P<day>\d{1,3})\b", re.IGNORECASE)

_AMOUNT_RE = re.compile(
    r"(?:(?:Rs\.?|INR|₹)\s*(?P<amount1>[\d,]+(?:\.\d+)?)\s*(?P<scale1>crore|lakh|lac|thousand|k)?"
    r"|(?P<amount2>[\d,]+(?:\.\d+)?)\s*(?P<scale2>crore|lakh|lac|thousand)"
    r"\s*(?:rupees|inr|rs)?)",
    re.IGNORECASE,
)

_SCALE = {
    "crore": 10_000_000.0,
    "lakh": 100_000.0,
    "lac": 100_000.0,
    "thousand": 1_000.0,
    "k": 1_000.0,
}

#: Field names that carry a time value in the structured corpora.
_TIME_FIELDS = ("timestamp", "datetime", "date_time", "event_time", "time", "date")
_LOCATION_FIELDS = ("location", "place", "address", "city", "district", "police_station", "jurisdiction")
_AMOUNT_FIELDS = ("amount", "value", "txn_amount", "transaction_amount")
_STATUS_FIELDS = ("status", "case_status", "stage")
_ROLE_FIELDS = ("role", "designation", "capacity")
_ROLE_SUBJECT_FIELDS = ("person", "name", "subject", "accused", "witness", "officer")

#: CSV/pipe field pairs that assert a relationship between two records.
_ROW_LINKS: tuple[tuple[tuple[str, str], str], ...] = (
    (("caller_number", "callee_number"), "CALLED"),
    (("caller_msisdn", "callee_msisdn"), "CALLED"),
    (("a_party", "b_party"), "CALLED"),
    (("from_account", "to_account"), "TRANSFERRED"),
    (("sender_account", "receiver_account"), "TRANSFERRED"),
    (("debit_account", "credit_account"), "TRANSFERRED"),
    (("owner", "registration"), "OWNS"),
    (("owner", "vehicle_registration"), "OWNS"),
    (("person", "phone_number"), "USES"),
    (("subscriber", "msisdn"), "USES"),
)

_KV_RE = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_ ]{1,32})\s*(?:=|:)\s*(?P<value>[^,;|\n]{1,120})")

_MAX_UNITS_PER_DOCUMENT = 400
_MAX_CLAIMS_PER_DOCUMENT = 240
_MAX_QUOTE_CHARS = 260


def normalize_text(value: Any) -> str:
    """Lower-case, strip accents and collapse whitespace for comparison."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()


def _squash(value: Any) -> str:
    """Normalise a *value* (not prose) for identity comparison."""
    text = normalize_text(value)
    text = re.sub(r"[^a-z0-9+ ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _digits(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


# --------------------------------------------------------------------------- #
# Entity vocabulary
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VocabEntry:
    """One case entity the claim extractor can recognise in prose/rows."""

    key: str
    kind: str
    label: str
    aliases: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "kind": self.kind, "label": self.label,
                "aliases": list(self.aliases)}


_KIND_BY_LABEL = {
    "PERSON": "PERSON",
    "PHONE": "PHONE",
    "PHONENUMBER": "PHONE",
    "BANKACCOUNT": "ACCOUNT",
    "ACCOUNT": "ACCOUNT",
    "VEHICLE": "VEHICLE",
    "LOCATION": "LOCATION",
    "ADDRESS": "LOCATION",
    "ORGANIZATION": "ORGANIZATION",
    "COMPANY": "ORGANIZATION",
}

#: Which node properties can name an entity, per kind.
_NAME_PROPERTIES = (
    "name", "full_name", "display_name", "label", "title",
    "number", "phone", "msisdn", "phone_number",
    "account_number", "account_no",
    "plate", "registration", "registration_number",
    "address", "city", "district",
    "company_name", "org_name",
)


class EntityVocabulary:
    """The entities of one case, indexed for fast alias lookup.

    Built from the same case-scoped nodes the boundary shows the model, so a
    claim can never reference an entity that is not part of the case.
    """

    def __init__(self, entries: Iterable[VocabEntry] = ()) -> None:
        self.entries: list[VocabEntry] = list(entries)
        self._by_key: dict[str, VocabEntry] = {e.key: e for e in self.entries}
        self._alias_index: list[tuple[str, str, VocabEntry]] = []
        for entry in self.entries:
            seen: set[str] = set()
            candidates = (entry.label, *entry.aliases)
            for alias in candidates:
                norm = _squash(alias)
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                self._alias_index.append((norm, entry.kind, entry))
        # Longest alias first so "Prakash Jain" wins over "Prakash".
        self._alias_index.sort(key=lambda item: (-len(item[0]), item[0]))

    # ---------------------------------------------------------------- factory
    @classmethod
    def from_nodes(cls, nodes: Sequence[dict[str, Any]] | None) -> "EntityVocabulary":
        entries: list[VocabEntry] = []
        for node in nodes or ():
            props = node.get("properties") or node
            raw_label = str(node.get("label") or props.get("entity_type") or "")
            kind = _KIND_BY_LABEL.get(raw_label.upper())
            if not kind:
                continue
            key = str(node.get("provenance_key") or node.get("id") or props.get("id") or "")
            if not key:
                continue
            values: list[str] = []
            for prop in _NAME_PROPERTIES:
                value = props.get(prop)
                if value in (None, "", []):
                    continue
                values.append(str(value))
            if kind == "PERSON":
                full = props.get("name") or props.get("full_name")
                if full:
                    parts = str(full).split()
                    if len(parts) > 1 and len(parts[-1]) > 3:
                        values.append(parts[-1])
            if not values:
                continue
            label = values[0]
            entries.append(
                VocabEntry(key=key, kind=kind, label=label,
                           aliases=tuple(dict.fromkeys(values[1:])))
            )
        return cls(entries)

    # ------------------------------------------------------------------ query
    def find(self, text: str, *, kind: str | None = None) -> VocabEntry | None:
        """Return the entity mentioned in ``text`` (longest alias wins)."""
        norm = _squash(text)
        if not norm:
            return None
        for alias, alias_kind, entry in self._alias_index:
            if kind and alias_kind != kind:
                continue
            if alias in norm:
                return entry
        return None

    def all_of_kind(self, kind: str) -> list[VocabEntry]:
        return [e for e in self.entries if e.kind == kind]

    def by_key(self, key: str) -> VocabEntry | None:
        return self._by_key.get(key)

    def __len__(self) -> int:  # pragma: no cover - convenience
        return len(self.entries)


# --------------------------------------------------------------------------- #
# Time parsing
# --------------------------------------------------------------------------- #


def parse_time(
    text: str,
    *,
    base_time: str | None = None,
    default_year: int | None = None,
) -> tuple[str | None, str | None]:
    """Extract the first timestamp from ``text``.

    Returns ``(iso_timestamp, precision)`` where precision is ``"minute"``,
    ``"day"`` or ``None``.  A bare clock ("at 21:00") borrows the date from
    ``base_time`` — the document's own date header — so a diary written as
    "22 May 2025 ... at 21:00" resolves to a real instant rather than to
    1970-01-01.
    """
    raw = str(text or "")
    if not raw.strip():
        return None, None

    base = _parse_iso(base_time) if base_time else None

    match = _ISO_RE.search(raw)
    if match:
        precision = "minute" if match.group("hour") else "day"
        return _build_iso(match), precision

    date_match = _TEXT_DATE_RE.search(raw) or _TEXT_DATE_ALT_RE.search(raw)
    if date_match:
        year = int(date_match.group("year"))
        month = _month_number(date_match.group("month"))
        day = int(date_match.group("day"))
        if month:
            clock = _CLOCK_RE.search(raw[date_match.end():]) or _CLOCK_RE.search(raw[: date_match.start()])
            if clock:
                hour, minute, second = _clock_parts(clock)
                return _iso(year, month, day, hour, minute, second), "minute"
            return _iso(year, month, day, 0, 0, 0), "day"

    month_day = None
    for pattern in (_MONTH_DAY_RE, _DAY_MONTH_RE):
        candidate = pattern.search(raw)
        if candidate and _month_number(candidate.group("month")):
            month_day = candidate
            break
    if month_day:
        year = base.year if base is not None else datetime.now(timezone.utc).year
        clock = _CLOCK_RE.search(raw[month_day.end():]) or _CLOCK_RE.search(raw[: month_day.start()])
        if clock:
            hour, minute, second = _clock_parts(clock)
            return _iso(year, _month_number(month_day.group("month")), int(month_day.group("day")),
                        hour, minute, second), "minute"
        return _iso(year, _month_number(month_day.group("month")), int(month_day.group("day")),
                    0, 0, 0), "day"

    clock = _CLOCK_RE.search(raw)
    if clock and base is not None:
        hour, minute, second = _clock_parts(clock)
        return (
            _iso(base.year, base.month, base.day, hour, minute, second),
            "minute",
        )

    day_n = _DAY_N_RE.search(raw)
    if day_n and base is not None:
        offset = int(day_n.group("day")) - 1
        if 0 <= offset <= 3660:
            return _iso_from_datetime(base + timedelta(days=offset), 0, 0, 0), "day"

    return None, None


def _month_number(name: str) -> int | None:
    return _MONTHS.get(normalize_text(name).rstrip("."))


def _clock_parts(match: re.Match[str]) -> tuple[int, int, int]:
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second") or 0)
    ampm = (match.group("ampm") or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return min(hour, 23), min(minute, 59), min(second, 59)


def _build_iso(match: re.Match[str]) -> str:
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    second = int(match.group("second") or 0)
    return _iso(int(match.group("year")), int(match.group("month")), int(match.group("day")),
                hour, minute, second)


def _iso(year: int, month: int, day: int, hour: int, minute: int, second: int) -> str:
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}+00:00"


def _iso_from_datetime(value: datetime, hour: int, minute: int, second: int) -> str:
    return _iso(value.year, value.month, value.day, hour, minute, second)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def time_bucket(value: str | None, minutes: int = 60) -> str | None:
    """Floor a timestamp into a comparison bucket (default: the hour)."""
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    if minutes <= 0:
        return parsed.date().isoformat()
    seconds = max(60, int(minutes) * 60)
    floored = int(parsed.timestamp()) // seconds * seconds
    return datetime.fromtimestamp(floored, tz=timezone.utc).isoformat()


def format_timestamp(value: str | None) -> str:
    """A human-readable rendering of an ISO timestamp for answer prose."""
    parsed = _parse_iso(value)
    if parsed is None:
        return ""
    if parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0:
        return parsed.strftime("%d %b %Y")
    return parsed.strftime("%d %b %Y, %H:%M UTC")


def parse_timestamp(
    text: str,
    *,
    base_time: str | None = None,
    base: str | None = None,
) -> str | None:
    """Backwards-compatible helper: the ISO timestamp in ``text``, or None."""
    value, _precision = parse_time(text, base_time=base_time or base)
    return value


def hours_between(a: str | None, b: str | None) -> float | None:
    left, right = _parse_iso(a), _parse_iso(b)
    if left is None or right is None:
        return None
    return abs((left - right).total_seconds()) / 3600.0


def sort_key(value: str | None) -> tuple[int, str]:
    parsed = _parse_iso(value)
    if parsed is None:
        return (1, "")
    return (0, parsed.isoformat())


# --------------------------------------------------------------------------- #
# Claim model
# --------------------------------------------------------------------------- #


@dataclass
class Claim:
    """One assertion, attributed to exactly one stored record."""

    claim_id: str
    dimension: str
    subject_key: str
    subject_label: str
    predicate: str
    object_key: str
    object_label: str
    value: str
    time: str | None = None
    time_precision: str | None = None
    document_id: str = ""
    evidence_type: str = ""
    quote: str = ""
    unit_index: int = 0
    polarity: int = 1
    confidence: float = 1.0
    origin: str = "record"
    #: The record text the claim was read from.  Kept as a field because the
    #: corroboration/contradiction tests build claims by hand; ``quote`` and
    #: ``text`` are two names for the same audited sentence.
    text: str = ""

    def __post_init__(self) -> None:
        if not self.quote and self.text:
            self.quote = self.text
        elif not self.text and self.quote:
            self.text = self.quote

    @property
    def key(self) -> str:
        """Stable identity used to group identical assertions."""
        return claim_identity(self)

    @property
    def category(self) -> str:
        """Comparison class for role claims (see :data:`ROLE_GROUPS`).

        "Accused" and "suspect" share the ADVERSE class and therefore never
        conflict; accused and witness do not.
        """
        if self.dimension != DIMENSION_ROLE:
            return ""
        return ROLE_CLASSES.get(self.object_key, "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "dimension": self.dimension,
            "subject": self.subject_label,
            "subject_key": self.subject_key,
            "predicate": self.predicate,
            "object": self.object_label,
            "value": self.value,
            "time": self.time,
            "document_id": self.document_id,
            "evidence_type": self.evidence_type,
            "quote": self.quote,
            "polarity": self.polarity,
            "origin": self.origin,
        }


def claim_identity(claim: Claim, *, bucket_minutes: int = 60) -> str:
    """Identity of the assertion: subject + dimension + normalised value.

    Time is *not* part of the identity for corroboration (the same fact stated
    on two dates is still the same fact), but it is part of the identity for
    contradictions, which group claims by ``claim_identity`` plus a time
    bucket.
    """
    parts = [claim.subject_key or "case", claim.dimension, _squash(claim.object_key or claim.value)]
    if claim.polarity < 0:
        parts.append("negated")
    return "|".join(parts)


def claim_ids(claims: Sequence[Claim]) -> set[str]:
    return {claim.claim_id for claim in claims}


# --------------------------------------------------------------------------- #
# Unit splitting
# --------------------------------------------------------------------------- #

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+")


def split_units(text: str) -> list[str]:
    """Split a document into claim-sized units (lines, then sentences)."""
    units: list[str] = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if len(line) <= 220 or _KV_RE.search(line):
            units.append(line)
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip()
            if sentence:
                units.append(sentence)
    return units


def _csv_rows(content: str) -> list[dict[str, str]] | None:
    """Parse a tabular evidence file into field → value rows.

    CDR, ledger and ANPR exports are the backbone of corroboration ("the CDR
    and the case diary both put this phone with this person"), and a bare CSV
    row carries no field names of its own — without the header it is just
    numbers.  Returns ``None`` when the content is not tabular.
    """
    lines = [line for line in str(content or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    try:
        table = list(csv.reader(lines))
    except csv.Error:
        return None
    header = [cell.strip() for cell in table[0]]
    if len(header) < 2:
        return None
    normalized = [_squash(cell).replace(" ", "_") for cell in header]
    known = sum(1 for name in normalized if name in _KNOWN_FIELDS)
    if known < 2 and known < len(header) // 3:
        return None
    rows: list[dict[str, str]] = []
    for row in table[1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        entry: dict[str, str] = {}
        for name, cell in zip(normalized, row):
            if name and str(cell).strip():
                entry[name] = str(cell).strip()
        if entry:
            rows.append(entry)
    return rows or None


def _row_unit(row: dict[str, str]) -> str:
    return " | ".join(f"{key}: {value}" for key, value in row.items())


def document_units(content: str) -> list[str]:
    """The claim units of one document, tabular or prose."""
    rows = _csv_rows(content)
    if rows is not None:
        return [_row_unit(row) for row in rows]
    return split_units(content)


def _quote(unit: str) -> str:
    quote = re.sub(r"\s+", " ", unit).strip()
    return quote[:_MAX_QUOTE_CHARS]


def _make_claim(
    *,
    document: dict[str, Any],
    dimension: str,
    subject: VocabEntry | None,
    predicate: str,
    object_key: str,
    object_label: str,
    value: str,
    unit: str,
    unit_index: int,
    time: str | None = None,
    time_precision: str | None = None,
    polarity: int = 1,
    confidence: float = 1.0,
    origin: str = "prose",
) -> Claim:
    doc_id = str(document.get("doc_id") or document.get("id") or "")
    evidence_type = str(document.get("document_type") or document.get("evidence_type") or "DOCUMENT").upper()
    subject_key = subject.key if subject else "case"
    subject_label = subject.label if subject else "the case"
    digest = f"{doc_id}|{dimension}|{subject_key}|{predicate}|{_squash(object_key)}|{time or ''}|{unit_index}"
    return Claim(
        claim_id=digest,
        dimension=dimension,
        subject_key=subject_key,
        subject_label=subject_label,
        predicate=predicate,
        object_key=object_key,
        object_label=object_label or object_key,
        value=value,
        time=time,
        time_precision=time_precision,
        document_id=doc_id,
        evidence_type=evidence_type,
        quote=_quote(unit),
        unit_index=unit_index,
        polarity=polarity,
        confidence=confidence,
        origin=origin,
    )


# --------------------------------------------------------------------------- #
# Structured (row/field) extraction
# --------------------------------------------------------------------------- #


#: Field names the structured extractor understands.  A key like
#: "ravi_kumar_amount" is indexed under both its full name and its trailing
#: known field name, so "Ravi Kumar amount: 250000" is read as an amount.
_KNOWN_FIELDS = frozenset(
    _TIME_FIELDS + _LOCATION_FIELDS + _AMOUNT_FIELDS + _STATUS_FIELDS + _ROLE_FIELDS
    + tuple(name for pair, _ in _ROW_LINKS for name in pair)
    + ("narrative", "description", "purpose", "reference", "detected_person",
       "registration", "vehicle_registration", "plate", "owner", "person", "name",
       "subject", "accused", "witness", "officer", "call_type", "direction")
)


def _structured_fields(unit: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _KV_RE.finditer(unit):
        key = _squash(match.group("key")).replace(" ", "_")
        if not key:
            continue
        fields.setdefault(key, match.group("value").strip())
        tokens = [token for token in key.split("_") if token]
        for length in (2, 1):
            tail = "_".join(tokens[-length:])
            if tail in _KNOWN_FIELDS:
                fields.setdefault(tail, match.group("value").strip())
                break
    return fields


def _pick(fields: dict[str, str], names: Sequence[str]) -> str | None:
    for name in names:
        value = fields.get(name)
        if value:
            return value
    return None


#: A bare number in a field that is *known* to hold money ("amount: 250000").
_BARE_AMOUNT_RE = re.compile(
    r"^\s*(?P<amount>[\d,]+(?:\.\d+)?)\s*(?P<scale>crore|lakh|lac|thousand|k)?\b", re.IGNORECASE
)


def _normalized_amount(raw: str | None, *, labelled: bool = False) -> tuple[str, str] | None:
    if not raw:
        return None
    match = _AMOUNT_RE.search(raw)
    if match:
        amount = match.group("amount1") or match.group("amount2")
        scale = (match.group("scale1") or match.group("scale2") or "").lower()
    elif labelled:
        # The field name already says this is money, so an unadorned figure is
        # still an amount ("amount: 250000").
        bare = _BARE_AMOUNT_RE.match(raw)
        if not bare:
            return None
        amount = bare.group("amount")
        scale = (bare.group("scale") or "").lower()
    else:
        return None
    if not amount:
        return None
    try:
        value = float(amount.replace(",", ""))
    except ValueError:
        return None
    if scale:
        value *= _SCALE.get(scale, 1.0)
    if value <= 0:
        return None
    formatted = f"{value:,.0f}"
    return formatted, f"₹{formatted}"


def _structured_claims(
    unit: str,
    unit_index: int,
    document: dict[str, Any],
    vocabulary: EntityVocabulary,
    *,
    base_time: str | None,
) -> list[Claim]:
    fields = _structured_fields(unit)
    if not fields:
        return []
    claims: list[Claim] = []

    time_value, precision = None, None
    raw_time = _pick(fields, _TIME_FIELDS)
    if raw_time:
        time_value, precision = parse_time(raw_time, base_time=base_time)
    if time_value is None and base_time:
        time_value, precision = parse_time(unit, base_time=base_time)

    # ---- relationship links between two structured records (CDR, ledger …)
    for (left_field, right_field), predicate in _ROW_LINKS:
        left_raw = fields.get(left_field)
        right_raw = fields.get(right_field)
        if not left_raw or not right_raw:
            continue
        left = (
            vocabulary.find(left_raw)
            or _entity_from_value(left_raw, vocabulary)
            or _value_entity(left_raw)
        )
        right = (
            vocabulary.find(right_raw)
            or _entity_from_value(right_raw, vocabulary)
            or _value_entity(right_raw)
        )
        if left is None and right is None:
            continue
        claims.append(
            _make_claim(
                document=document,
                dimension=DIMENSION_RELATIONSHIP,
                subject=left,
                predicate=predicate,
                object_key=(right.key if right else _squash(right_raw)),
                object_label=(right.label if right else right_raw),
                value=f"{left.label if left else left_raw} {predicate.replace('_', ' ').lower()} "
                      f"{right.label if right else right_raw}",
                unit=unit,
                unit_index=unit_index,
                time=time_value,
                time_precision=precision,
                origin="row",
            )
        )

    # ---- location rows (ANPR sighting, scene, beat)
    location_raw = _pick(fields, _LOCATION_FIELDS)
    if location_raw:
        place = vocabulary.find(location_raw, kind="LOCATION") or _entity_from_value(location_raw, vocabulary)
        subject = None
        for field in ("detected_person", "person", "name", "subject", "accused", "witness", "officer", "owner"):
            if fields.get(field):
                subject = (
                    vocabulary.find(fields[field])
                    or _entity_from_value(fields[field], vocabulary)
                    or _value_entity(fields[field])
                )
                if subject:
                    break
        if place or subject:
            claims.append(
                _make_claim(
                    document=document,
                    dimension=DIMENSION_LOCATION if place else DIMENSION_EVENT,
                    subject=subject,
                    predicate="LOCATED_AT",
                    object_key=(place.key if place else _squash(location_raw)),
                    object_label=(place.label if place else location_raw),
                    value=f"{subject.label if subject else 'A record'} at {place.label if place else location_raw}",
                    unit=unit,
                    unit_index=unit_index,
                    time=time_value,
                    time_precision=precision,
                    origin="row",
                )
            )
        registration = _pick(fields, ("registration", "vehicle_registration", "plate"))
        if registration and (location_raw or time_value):
            vehicle = (
                vocabulary.find(registration, kind="VEHICLE")
                or _entity_from_value(registration, vocabulary)
                or _value_entity(registration)
            )
            claims.append(
                _make_claim(
                    document=document,
                    dimension=DIMENSION_EVENT,
                    subject=vehicle,
                    predicate="SIGHTED_AT",
                    object_key=(_squash(location_raw) if location_raw else (time_value or "")),
                    object_label=location_raw or (time_value or "the recorded location"),
                    value=f"Vehicle {vehicle.label if vehicle else registration} recorded at "
                          f"{location_raw or 'the recorded location'}",
                    unit=unit,
                    unit_index=unit_index,
                    time=time_value,
                    time_precision=precision,
                    origin="row",
                )
            )

    # ---- amounts
    amount_raw = _pick(fields, _AMOUNT_FIELDS)
    normalized = _normalized_amount(amount_raw, labelled=True)
    if normalized:
        amount_text, amount_label = normalized
        subject = None
        for field in _ROLE_SUBJECT_FIELDS + ("from_account", "to_account", "subscriber", "owner", "detected_person"):
            if fields.get(field):
                subject = (
                    vocabulary.find(fields[field])
                    or _entity_from_value(fields[field], vocabulary)
                    or _value_entity(fields[field])
                )
                if subject:
                    break
        narrative = _pick(fields, ("narrative", "description", "purpose", "reference")) or ""
        claims.append(
            _make_claim(
                document=document,
                dimension=DIMENSION_AMOUNT,
                subject=subject,
                predicate="AMOUNT",
                object_key=amount_text,
                object_label=amount_label,
                value=(
                    f"payment of {amount_label}"
                    + (f" — {narrative.strip()}" if narrative.strip() else "")
                ),
                unit=unit,
                unit_index=unit_index,
                time=time_value,
                time_precision=precision,
                origin="row",
            )
        )

    # ---- status and role fields
    status_raw = _pick(fields, _STATUS_FIELDS)
    if status_raw:
        normalized_status = _normalize_status(status_raw)
        if normalized_status:
            claims.append(
                _make_claim(
                    document=document,
                    dimension=DIMENSION_STATUS,
                    subject=None,
                    predicate="STATUS",
                    object_key=normalized_status,
                    object_label=normalized_status.replace("_", " ").title(),
                    value=f"the case is recorded as {normalized_status.replace('_', ' ').lower()}",
                    unit=unit,
                    unit_index=unit_index,
                    time=time_value,
                    time_precision=precision,
                    origin="row",
                )
            )
    role_raw = _pick(fields, _ROLE_FIELDS)
    if role_raw:
        normalized_role = _normalize_role(role_raw)
        subject = None
        for field in _ROLE_SUBJECT_FIELDS:
            if fields.get(field):
                subject = vocabulary.find(fields[field]) or _entity_from_value(fields[field], vocabulary)
                if subject:
                    break
        if normalized_role and subject:
            claims.append(
                _make_claim(
                    document=document,
                    dimension=DIMENSION_ROLE,
                    subject=subject,
                    predicate="ROLE",
                    object_key=normalized_role,
                    object_label=normalized_role.replace("_", " ").title(),
                    value=f"{subject.label} is recorded as {normalized_role.replace('_', ' ').lower()}",
                    unit=unit,
                    unit_index=unit_index,
                    time=time_value,
                    time_precision=precision,
                    origin="row",
                )
            )
    return claims


def _value_entity(raw: str) -> VocabEntry | None:
    """A claim subject/object read straight from a record's own value.

    A CDR row names two numbers.  If the case graph happens to hold those
    numbers as entities the claim links to them; if it does not, the value is
    still a fact the record asserts, so it becomes a value entity rather than
    the row being dropped — otherwise a whole evidence type would contribute
    nothing to corroboration or chronology just because an entity was not
    resolved.
    """
    text = str(raw or "").strip().strip('"')
    if not text:
        return None
    digits = _digits(text)
    if len(digits) >= 6:
        return VocabEntry(key=f"value:{digits}", kind="NUMBER", label=text)
    squashed = _squash(text)
    if not squashed or squashed in {"na", "none", "-", "unknown", "null"}:
        return None
    return VocabEntry(key=f"value:{squashed}", kind="TEXT", label=text)


def _entity_from_value(raw: str, vocabulary: EntityVocabulary) -> VocabEntry | None:
    """Match a raw phone/account/plate value against known case entities."""
    text = str(raw or "").strip()
    if not text:
        return None
    direct = vocabulary.find(text)
    if direct:
        return direct
    digits = _digits(text)
    if len(digits) >= 6:
        for entry in vocabulary.entries:
            if entry.kind in {"PHONE", "ACCOUNT"}:
                if digits and digits in _digits(entry.label):
                    return entry
                for alias in entry.aliases:
                    if digits and digits in _digits(alias):
                        return entry
    squashed = _squash(text)
    if squashed:
        for entry in vocabulary.entries:
            if entry.kind == "VEHICLE" and squashed.replace(" ", "") == _squash(entry.label).replace(" ", ""):
                return entry
    return None


def _normalize_role(raw: str) -> str | None:
    text = normalize_text(raw)
    for phrase, role in sorted(_ROLE_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if phrase == "io":
            continue
        if phrase in text:
            return role
    return None


def _normalize_status(raw: str) -> str | None:
    text = normalize_text(raw)
    for phrase, status in sorted(_STATUS_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if phrase in text:
            return status
    return None


# --------------------------------------------------------------------------- #
# Prose extraction
# --------------------------------------------------------------------------- #

_LOCATION_VERB_RE = re.compile(
    r"\b(?:was|were|is|are|seen|spotted|present|located|found|situated|resides|staying|reached|visited|arrived)\b",
    re.IGNORECASE,
)
#: A place mention: a capitalised word run after a locative preposition.  The
#: run stops at the next lower-case word, which is what keeps "at 21:00 on 22
#: May 2025" out of the capture.
_PLACE_AT_RE = re.compile(
    r"\b(?:at|near|outside|inside|in|from|to)\s+"
    r"(?P<place>[A-Z][A-Za-z0-9'’.-]*(?:\s+[A-Z][A-Za-z0-9'’.-]*)*)"
)
_ROLE_CLAUSE_RE = re.compile(
    r"(?P<subject>[^,.;]{2,60}?)\s+(?:is|was|were|are|has been|had been)\s+"
    r"(?:the\s+|a\s+|an\s+)?(?P<role>accused|prime accused|main accused|suspect|witness|eye ?witness|"
    r"complainant|informant|victim|investigating officer|investigation officer)\b",
    re.IGNORECASE,
)
_ROLE_TOWARD_RE = re.compile(
    r"\b(?P<role>accused|suspect|witness|complainant|informant|victim|investigating officer)\b"
    r"\s*(?:is|was|named as|:)?\s*(?P<subject>[^,.;]{2,60})",
    re.IGNORECASE,
)
_STATUS_PROSE_RE = re.compile(
    r"\b(?:case|investigation|matter|enquiry|inquiry)\s+(?:status\s+)?"
    r"(?:is|was|remains|has been|were)?\s*(?P<status>open|pending|under investigation|under enquiry|"
    r"under inquiry|in progress|active|unresolved|closed|concluded|completed|disposed|"
    r"final report filed|charges filed|charge[- ]?sheet filed|prosecuted)\b",
    re.IGNORECASE,
)
_RELATIONSHIP_RE = re.compile(
    r"\b(?P<subject>[A-Z][^,.;]{1,50}?)\s+"
    r"(?P<verb>called|phoned|contacted|transferred(?:\s+\w+)?|paid|remitted|received from|uses|used|owns|owned|"
    r"associated with|met with|met|travelled with|accompanied)\s+"
    r"(?P<object>[A-Z0-9][^,.;]{1,50})",
    re.IGNORECASE,
)
#: Actions worth recording as an event-description claim.  Without this gate
#: every dated sentence would become an "event", and an event claim that merely
#: restates a location claim would raise a second, duplicate conflict.
_EVENT_ACTION_RE = re.compile(
    r"\b(?:handed\s+over|handed|delivered|collected|received|paid|bribed|threatened|assaulted|"
    r"assault|attacked|signed|executed|boarded|left|departed|arrived|met|visited|inspected|"
    r"seized|recovered|arrested|detained|interviewed|recorded|submitted|filed|withdrew|"
    r"deposited|issued|obtained|transferred|escaped|fled)\b",
    re.IGNORECASE,
)


def _prose_claims(
    unit: str,
    unit_index: int,
    document: dict[str, Any],
    vocabulary: EntityVocabulary,
    *,
    time_value: str | None,
    precision: str | None,
) -> list[Claim]:
    claims: list[Claim] = []
    mentions = _mentions(unit, vocabulary)

    # ---- roles ---------------------------------------------------------
    for match in _ROLE_CLAUSE_RE.finditer(unit):
        subject = vocabulary.find(match.group("subject")) or _nearest_mention(mentions, match.start("subject"), match.end("subject"))
        role = _normalize_role(match.group("role"))
        if subject and role:
            claims.append(
                _make_claim(
                    document=document, dimension=DIMENSION_ROLE, subject=subject, predicate="ROLE",
                    object_key=role, object_label=role.replace("_", " ").title(),
                    value=f"{subject.label} is recorded as {role.replace('_', ' ').lower()}",
                    unit=unit, unit_index=unit_index, time=time_value, time_precision=precision,
                )
            )
    for match in _ROLE_TOWARD_RE.finditer(unit):
        subject = vocabulary.find(match.group("subject")) or _nearest_mention(mentions, match.start("subject"), match.end("subject"))
        role = _normalize_role(match.group("role"))
        if subject and role:
            claim = _make_claim(
                document=document, dimension=DIMENSION_ROLE, subject=subject, predicate="ROLE",
                object_key=role, object_label=role.replace("_", " ").title(),
                value=f"{subject.label} is recorded as {role.replace('_', ' ').lower()}",
                unit=unit, unit_index=unit_index, time=time_value, time_precision=precision,
            )
            if claim.claim_id not in {c.claim_id for c in claims}:
                claims.append(claim)

    # ---- status --------------------------------------------------------
    for match in _STATUS_PROSE_RE.finditer(unit):
        status = _normalize_status(match.group("status"))
        if status:
            claims.append(
                _make_claim(
                    document=document, dimension=DIMENSION_STATUS, subject=None, predicate="STATUS",
                    object_key=status, object_label=status.replace("_", " ").title(),
                    value=f"the case is recorded as {status.replace('_', ' ').lower()}",
                    unit=unit, unit_index=unit_index, time=time_value, time_precision=precision,
                )
            )

    # ---- relationships -------------------------------------------------
    polarity = -1 if _NEGATION_RE.search(unit) else 1
    for match in _RELATIONSHIP_RE.finditer(unit):
        subject = vocabulary.find(match.group("subject")) or _nearest_mention(mentions, match.start("subject"), match.end("subject"))
        obj = vocabulary.find(match.group("object")) or _nearest_mention(mentions, match.start("object"), match.end("object"))
        predicate = _RELATION_SYNONYMS.get(normalize_text(match.group("verb")))
        if predicate is None:
            for phrase, name in sorted(_RELATION_SYNONYMS.items(), key=lambda kv: -len(kv[0])):
                if phrase in normalize_text(match.group("verb")):
                    predicate = name
                    break
        if not predicate:
            continue
        if subject is None and obj is None:
            continue
        claims.append(
            _make_claim(
                document=document, dimension=DIMENSION_RELATIONSHIP,
                subject=subject,
                predicate=predicate,
                object_key=(obj.key if obj else _squash(match.group("object"))),
                object_label=(obj.label if obj else match.group("object").strip()),
                value=(
                    f"{subject.label if subject else match.group('subject').strip()} "
                    f"{'did not ' if polarity < 0 else ''}{predicate.replace('_', ' ').lower()} "
                    f"{obj.label if obj else match.group('object').strip()}"
                ),
                unit=unit, unit_index=unit_index, time=time_value, time_precision=precision,
                polarity=polarity,
            )
        )

    # ---- location ------------------------------------------------------
    people = [item for item in mentions if item[0].kind == "PERSON"]
    place_value = None
    if _LOCATION_VERB_RE.search(unit):
        for match in _PLACE_AT_RE.finditer(unit):
            candidate = match.group("place").strip()
            if normalize_text(candidate).startswith(
                ("least", "around", "approximately", "about", "the time", "the same time")
            ):
                continue
            place = vocabulary.find(candidate, kind="LOCATION") or vocabulary.find(candidate)
            if place and place.kind == "LOCATION":
                place_value = place
                break
            if place:
                continue
            # A place the graph does not hold can still be a location a record
            # asserts — a two-word proper noun after a locative preposition.
            named = _named_place(candidate)
            if named:
                place_value = VocabEntry(key=f"place:{_squash(named)}", kind="LOCATION", label=named)
                break
    if place_value is not None:
        subject = _nearest_person_before(people, unit.find(place_value.label)) or (
            people[0][0] if people else None
        )
        claims.append(
            _make_claim(
                document=document, dimension=DIMENSION_LOCATION, subject=subject, predicate="LOCATED_AT",
                object_key=place_value.key, object_label=place_value.label,
                value=f"{subject.label if subject else 'A record'} was at {place_value.label}",
                unit=unit, unit_index=unit_index, time=time_value, time_precision=precision,
            )
        )

    # ---- amounts -------------------------------------------------------
    money = _AMOUNT_RE.search(unit)
    if money:
        normalized = _normalized_amount(money.group(0))
        if normalized:
            amount_text, amount_label = normalized
            subject = _nearest_person_before(people, money.start()) or (people[0][0] if people else None)
            claims.append(
                _make_claim(
                    document=document, dimension=DIMENSION_AMOUNT, subject=subject, predicate="AMOUNT",
                    object_key=amount_text, object_label=amount_label,
                    value=f"{amount_label} recorded in this record",
                    unit=unit, unit_index=unit_index, time=time_value, time_precision=precision,
                )
            )

    # ---- event description --------------------------------------------
    if (
        time_value
        and people
        and _EVENT_ACTION_RE.search(unit)
        and not any(c.dimension in (DIMENSION_LOCATION, DIMENSION_RELATIONSHIP) for c in claims)
    ):
        subject = people[0][0]
        claims.append(
            _make_claim(
                document=document, dimension=DIMENSION_EVENT, subject=subject,
                predicate="RECORDED_EVENT",
                object_key=_squash(unit)[:120],
                object_label=unit[:120],
                value=unit[:200],
                unit=unit, unit_index=unit_index, time=time_value, time_precision=precision,
            )
        )
    return claims


#: Words that begin sentences or labels rather than place names.
_PLACE_STOPWORDS = frozenset(
    {
        "the", "a", "an", "this", "that", "these", "those", "he", "she", "they", "it",
        "we", "i", "you", "as", "at", "on", "in", "for", "with", "and", "but", "case",
        "accused", "witness", "complainant", "informant", "statement", "investigation",
        "officer", "court", "report", "record", "evidence", "time", "date", "present",
        "least", "approximately", "about", "around", "same", "next", "last",
    }
)


def _named_place(candidate: str) -> str | None:
    """A two-or-more-word proper noun that reads as a place name."""
    text = re.sub(r"\s+", " ", str(candidate or "")).strip(" .,;:")
    tokens = [token for token in text.split(" ") if token]
    while tokens and normalize_text(tokens[0]) in {"the", "a", "an"}:
        tokens.pop(0)
    if len(tokens) < 2:
        return None
    for token in tokens:
        word = token.strip(".,;:()")
        if not word or not word[0].isupper():
            return None
        if any(ch.isdigit() for ch in word):
            return None
        if normalize_text(word) in _PLACE_STOPWORDS:
            return None
    return " ".join(tokens)


def _mentions(text: str, vocabulary: EntityVocabulary) -> list[tuple[VocabEntry, str, int, int]]:
    """Return ``(entry, alias, start, end)`` for every entity mentioned."""
    found: list[tuple[VocabEntry, str, int, int]] = []
    haystack = normalize_text(text)
    for entry in vocabulary.entries:
        aliases = [entry.label, *entry.aliases]
        best: tuple[int, int] | None = None
        for alias in aliases:
            needle = normalize_text(alias)
            if len(needle) < 3:
                continue
            start = haystack.find(needle)
            if start >= 0 and (best is None or start < best[0]):
                best = (start, start + len(needle))
        if best:
            found.append((entry, text[best[0]:best[1]], best[0], best[1]))
    found.sort(key=lambda item: item[2])
    return found


def _nearest_person_before(
    people: Sequence[tuple[VocabEntry, str, int, int]], position: int
) -> VocabEntry | None:
    """The person mentioned closest before ``position`` (the acting subject)."""
    best: tuple[int, VocabEntry] | None = None
    for entry, _alias, start, end in people:
        if end <= position:
            distance = position - end          # before the value
        else:
            distance = (start - position) + 1000  # after it, so second choice
        if best is None or distance < best[0]:
            best = (distance, entry)
    return best[1] if best else None


def _nearest_mention(
    mentions: Sequence[tuple[VocabEntry, str, int, int]], start: int, end: int
) -> VocabEntry | None:
    """The mentioned entity closest to a span — used to attribute a role.

    Labelling every person in a sentence with the sentence's role is wrong
    ("the IO recorded that the witness saw the accused"); the entity nearest
    the role keyword is the one the record is describing.
    """
    best: tuple[int, VocabEntry] | None = None
    for entry, _alias, m_start, m_end in mentions:
        if m_end <= start:
            distance = start - m_end
        elif m_start >= end:
            distance = m_start - end
        else:
            distance = 0
        if best is None or distance < best[0]:
            best = (distance, entry)
    return best[1] if best else None


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #


def extract_claims_from_unit(
    unit: str,
    *,
    unit_index: int,
    document: dict[str, Any],
    vocabulary: EntityVocabulary,
    base_time: str | None = None,
) -> list[Claim]:
    time_value, precision = parse_time(unit, base_time=base_time)
    structured = _structured_claims(unit, unit_index, document, vocabulary, base_time=base_time)
    prose = _prose_claims(
        unit, unit_index, document, vocabulary,
        time_value=time_value, precision=precision,
    )
    # Structured extraction is authoritative for its own row; prose extraction
    # must not re-assert the same field with a different normalisation.
    seen = {claim.key + "|" + str(claim.time) for claim in structured}
    merged = list(structured)
    for claim in prose:
        signature = claim.key + "|" + str(claim.time)
        if signature in seen:
            continue
        seen.add(signature)
        merged.append(claim)
    return merged


def document_base_time(document: dict[str, Any]) -> str | None:
    """A document-level date (usually the record header) used to resolve clocks."""
    for key in ("document_date", "date", "created_at", "registered_at", "timestamp"):
        value = document.get(key)
        if value:
            parsed, _ = parse_time(str(value))
            if parsed:
                return parsed
    content = str(document.get("content") or "")
    parsed, precision = parse_time(content[:400])
    if parsed and precision == "day":
        return parsed
    return parsed


def extract_claims_from_document(
    document: dict[str, Any],
    *,
    vocabulary: EntityVocabulary | None = None,
    max_units: int = _MAX_UNITS_PER_DOCUMENT,
    max_claims: int = _MAX_CLAIMS_PER_DOCUMENT,
) -> list[Claim]:
    """Every claim one document makes (de-duplicated inside the record)."""
    vocab = vocabulary or EntityVocabulary.from_nodes(
        document.get("nodes") or document.get("entities") or ()
    )
    document = dict(document)
    content = str(document.get("content") or document.get("text") or "")
    base_time = document_base_time(document)
    units = document_units(content)[:max_units]
    claims: list[Claim] = []
    seen: set[str] = set()
    running_time = base_time
    for index, unit in enumerate(units):
        unit_claims = extract_claims_from_unit(
            unit, unit_index=index, document=document, vocabulary=vocab, base_time=running_time,
        )
        if not unit_claims:
            continue
        found_time, precision = parse_time(unit, base_time=running_time)
        if found_time and precision == "day":
            running_time = found_time
        for claim in unit_claims:
            if claim.key + "|" + str(claim.time) in seen:
                continue
            seen.add(claim.key + "|" + str(claim.time))
            claims.append(claim)
            if len(claims) >= max_claims:
                return claims
    return claims


def extract_claims_from_documents(
    documents: Sequence[dict[str, Any]],
    *,
    vocabulary: EntityVocabulary | None = None,
    max_documents: int | None = None,
) -> list[Claim]:
    """Claims across a case-scoped document set (each doc de-duplicated)."""
    selected = list(documents)
    if max_documents is not None:
        selected = selected[: max(1, max_documents)]
    claims: list[Claim] = []
    for document in selected:
        claims.extend(extract_claims_from_document(document, vocabulary=vocabulary))
    return claims


# --------------------------------------------------------------------------- #
# Graph edges as claims (so a relationship can be corroborated)
# --------------------------------------------------------------------------- #

#: Relationship type prefixes → (predicate shown to the investigator, dimension).
_EDGE_PREDICATES: tuple[tuple[str, str, str], ...] = (
    ("CALL", "CALLED", DIMENSION_RELATIONSHIP),
    ("TRANSFER", "TRANSFERRED", DIMENSION_RELATIONSHIP),
    ("PAYMENT", "TRANSFERRED", DIMENSION_RELATIONSHIP),
    ("TX", "TRANSFERRED", DIMENSION_RELATIONSHIP),
    ("USES_PHONE", "USES", DIMENSION_RELATIONSHIP),
    ("USES", "USES", DIMENSION_RELATIONSHIP),
    ("OWNS_VEHICLE", "OWNS", DIMENSION_RELATIONSHIP),
    ("OWNS_ACCOUNT", "OWNS", DIMENSION_RELATIONSHIP),
    ("OWNS", "OWNS", DIMENSION_RELATIONSHIP),
    ("RELATIVE_OF", "RELATIVE_OF", DIMENSION_RELATIONSHIP),
    ("ASSOCIATE_OF", "ASSOCIATED_WITH", DIMENSION_RELATIONSHIP),
    ("ASSOCIATED", "ASSOCIATED_WITH", DIMENSION_RELATIONSHIP),
    ("MEMBER_OF", "MEMBER_OF", DIMENSION_RELATIONSHIP),
    ("EMPLOYED_BY", "EMPLOYED_BY", DIMENSION_RELATIONSHIP),
    ("MET", "MET", DIMENSION_RELATIONSHIP),
    ("LOCATED_AT", "LOCATED_AT", DIMENSION_LOCATION),
    ("PRESENT_AT", "LOCATED_AT", DIMENSION_LOCATION),
    ("SIGHTED", "LOCATED_AT", DIMENSION_LOCATION),
    ("PARTICIPATED_IN", "PARTICIPATED_IN", DIMENSION_EVENT),
    ("INVOLVED_IN", "PARTICIPATED_IN", DIMENSION_EVENT),
)


def _edge_predicate(rel_type: str) -> tuple[str, str] | None:
    upper = str(rel_type or "").upper()
    for prefix, predicate, dimension in _EDGE_PREDICATES:
        if upper.startswith(prefix) or prefix in upper:
            return predicate, dimension
    return None


def claims_from_edges(
    edges: Sequence[dict[str, Any]],
    *,
    key_to_label: dict[str, str] | None = None,
    evidence_types: dict[str, str] | None = None,
    max_claims: int = 400,
) -> list[Claim]:
    """Turn case-scoped graph relationships into claims.

    A relationship the graph stores once but *documents twice* (a CDR row and a
    case-diary entry) becomes two claims, which is exactly what corroboration
    needs to see.  Edges without document provenance keep their own record id so
    they can still be counted — as a graph record, never as a document.
    """
    labels = key_to_label or {}
    types = evidence_types or {}
    claims: list[Claim] = []
    for edge in edges or ():
        rel_type = str(edge.get("rel_type") or edge.get("type") or "")
        mapped = _edge_predicate(rel_type)
        if not mapped:
            continue
        predicate, dimension = mapped
        source_key = str(edge.get("source_key") or edge.get("source") or "")
        target_key = str(edge.get("target_key") or edge.get("target") or "")
        props = edge.get("properties") or {}
        source_label = labels.get(source_key) or source_key or str(props.get("source_label") or "the record")
        target_label = labels.get(target_key) or target_key or str(props.get("target_label") or "the record")
        if not source_key and not target_key:
            continue
        raw_docs = edge.get("source_doc_ids") or ([edge.get("source_doc_id")] if edge.get("source_doc_id") else [])
        doc_ids = [str(doc) for doc in raw_docs if doc]
        if not doc_ids:
            doc_ids = [f"graph:{rel_type}:{source_key}->{target_key}"]
        timestamp = edge.get("timestamp") or props.get("timestamp") or props.get("start_time")
        timestamp, precision = parse_time(str(timestamp)) if timestamp else (None, None)
        value = f"{source_label} {predicate.replace('_', ' ').lower()} {target_label}"
        for doc_id in doc_ids:
            document = {
                "doc_id": doc_id,
                "document_type": types.get(doc_id, "GRAPH" if doc_id.startswith("graph:") else "DOCUMENT"),
            }
            claims.append(
                _make_claim(
                    document=document,
                    dimension=dimension,
                    subject=VocabEntry(key=source_key or "case", kind="PERSON", label=source_label),
                    predicate=predicate,
                    object_key=target_key or _squash(target_label),
                    object_label=target_label,
                    value=value,
                    unit=value,
                    unit_index=0,
                    time=timestamp,
                    time_precision=precision,
                    origin="graph",
                )
            )
            if len(claims) >= max_claims:
                return claims
    return claims


# --------------------------------------------------------------------------- #
# Model-proposed candidate claims (validated against stored records)
# --------------------------------------------------------------------------- #


@dataclass
class CandidateValidation:
    """Result of validating model-proposed claims against the case records."""

    accepted: list[Claim] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.accepted) + len(self.rejected)

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "reasons": {entry["reason"]: True for entry in self.rejected},
        }


#: A quoted span has to be long enough to be a real quotation rather than a
#: label, and short enough to be a sentence from a document.
_MIN_QUOTE_CHARS = 24
_MAX_QUOTE_CHARS_ALLOWED = 600


def validate_candidate_claims(
    candidates: Sequence[dict[str, Any]] | None,
    documents: Sequence[dict[str, Any]] | None,
    *,
    vocabulary: EntityVocabulary | None = None,
) -> CandidateValidation:
    """Keep only model-proposed claims that quote a record the case really has.

    A model may read a narrative record and propose "the witness says the
    accused was at the depot".  This function accepts that proposal **only** if
    the document exists in the case scope and the quoted sentence appears in
    it, so a conflict can never be raised against text nobody wrote.
    """
    result = CandidateValidation()
    by_document = {
        str(document.get("doc_id") or document.get("id")): document
        for document in documents or ()
        if document.get("doc_id") or document.get("id")
    }
    for index, candidate in enumerate(candidates or ()):
        if not isinstance(candidate, dict):
            result.rejected.append({"index": index, "reason": "not_an_object"})
            continue
        document_id = str(candidate.get("document_id") or candidate.get("doc_id") or "")
        dimension = str(candidate.get("dimension") or "").upper()
        quote = re.sub(r"\s+", " ", str(candidate.get("quote") or candidate.get("span") or "")).strip()
        value = str(candidate.get("value") or candidate.get("object") or "").strip()
        subject = str(candidate.get("subject") or "").strip()

        if dimension not in DIMENSIONS:
            result.rejected.append({"index": index, "reason": "unknown_dimension"})
            continue
        if not document_id or document_id not in by_document:
            result.rejected.append({"index": index, "reason": "document_not_in_case_scope"})
            continue
        if len(quote) < _MIN_QUOTE_CHARS or len(quote) > _MAX_QUOTE_CHARS_ALLOWED:
            result.rejected.append({"index": index, "reason": "quote_not_a_quotation"})
            continue
        if not value:
            result.rejected.append({"index": index, "reason": "missing_value"})
            continue
        document = by_document[document_id]
        content = normalize_text(document.get("content") or document.get("text") or "")
        if normalize_text(quote) not in content:
            result.rejected.append({"index": index, "reason": "quote_not_found_in_source_record"})
            continue
        entry = None
        if vocabulary is not None and subject:
            entry = vocabulary.find(subject)
        time_value, precision = parse_time(quote, base_time=document_base_time(document))
        claim = _make_claim(
            document=document,
            dimension=dimension,
            subject=entry,
            predicate=str(candidate.get("predicate") or dimension),
            object_key=value,
            object_label=str(candidate.get("object_label") or value),
            value=str(candidate.get("claim") or f"{subject or 'the record'}: {value}"),
            unit=quote,
            unit_index=int(candidate.get("unit_index") or 0),
            time=time_value,
            time_precision=precision,
            confidence=0.8,
            origin="model",
        )
        result.accepted.append(claim)
    return result
