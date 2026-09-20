"""Temporal reasoning — chronology the investigator can actually ask about.

Case records carry dates in a dozen shapes: ISO timestamps in a CDR, ``Day 07``
in an investigation diary, ``22 May 2025`` inside a PDF.  Before anything can be
ordered, that has to be normalised into one event shape:

    {"event_id", "timestamp", "start_time", "end_time", "entity_ids",
     "event_type", "description", "sources"}

On top of that this module answers the temporal *questions* an investigator
asks — before / after an anchor event, between two dates, around an event,
closest to an event — by selecting and ordering real events, never by
summarising the case.  If nothing is anchored, it says so instead of inventing
a chronology.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from app.ai.claims import (
    Claim,
    DIMENSION_AMOUNT,
    DIMENSION_EVENT,
    DIMENSION_LOCATION,
    DIMENSION_RELATIONSHIP,
    DIMENSION_TIME,
    format_timestamp,
    parse_timestamp,
)

#: Claim dimension → event type for the chronology.
_DIMENSION_EVENT_TYPES = {
    DIMENSION_EVENT: None,          # the predicate itself
    DIMENSION_LOCATION: "LOCATION",
    DIMENSION_TIME: "RECORD",
    DIMENSION_AMOUNT: "PAYMENT",
    DIMENSION_RELATIONSHIP: None,   # the predicate itself (CALLED, TRANSFERRED …)
}

RELATION_BEFORE = "BEFORE"
RELATION_AFTER = "AFTER"
RELATION_BETWEEN = "BETWEEN"
RELATION_AROUND = "AROUND"
RELATION_NEAREST = "NEAREST"
RELATION_CHRONOLOGICAL = "CHRONOLOGICAL"
RELATION_NONE = "NONE"

#: Default half-width of an "around/before/after" investigation window.
DEFAULT_WINDOW = timedelta(days=3)
AROUND_WINDOW = timedelta(hours=6)
NEAREST_COUNT = 5

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "a": 1, "an": 1, "couple": 2,
}

_UNIT_DAYS = {
    "hour": 1 / 24, "hours": 1 / 24,
    "day": 1, "days": 1, "week": 7, "weeks": 7, "fortnight": 14,
    "month": 30, "months": 30, "year": 365, "years": 365,
}

#: Anchor words → the event/evidence kind they refer to.
_ANCHOR_KINDS: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (re.compile(r"\b(?:incident|offence|offense|crime|occurrence)\b", re.I),
     ("INCIDENT", "FIR", "SCENE")),
    (re.compile(r"\b(?:payment|transaction|transfer|deposit|withdrawal|bribe)\b", re.I),
     ("TRANSFER", "FINANCIAL", "PAYMENT")),
    (re.compile(r"\b(?:communication|call|phone\s+contact|conversation)\b", re.I),
     ("CALL", "COMMUNICATION", "CALLED")),
    (re.compile(r"\b(?:meeting|rendezvous|visit)\b", re.I), ("MET", "MEETING", "VISIT")),
    (re.compile(r"\b(?:arrest|custody)\b", re.I), ("ARREST", "ARRESTED")),
    (re.compile(r"\b(?:filing|charge\s*sheet|chargesheet)\b", re.I), ("CHARGES_FILED", "CHARGESHEET")),
    (re.compile(r"\b(?:statement|witness)\b", re.I), ("WITNESS", "RECORDED", "STATEMENT")),
]

_RELATIVE_RE = re.compile(
    r"\b(?P<count>\d+|one|two|three|four|five|six|seven|eight|nine|ten|a|an)\s+"
    r"(?P<unit>hours?|days?|weeks?|fortnights?|months?|years?)\s+"
    r"(?P<direction>before|after|earlier\s+than|later\s+than|prior\s+to|following)\b",
    re.I,
)

_BEFORE_RE = re.compile(r"\b(?:before|prior\s+to|earlier\s+than|leading\s+up\s+to|preceding)\b", re.I)
_AFTER_RE = re.compile(r"\b(?:after|following|later\s+than|subsequent\s+to|since)\b", re.I)
_BETWEEN_RE = re.compile(r"\bbetween\b\s+(?P<start>[^,.;]{4,40}?)\s+and\s+(?P<end>[^,.;]{4,40})", re.I)
_AROUND_RE = re.compile(r"\b(?:around|near|close\s+to|about|approximately)\b", re.I)
_NEAREST_RE = re.compile(r"\b(?:closest|nearest|nearest\s+to|most\s+recent|latest|last)\b", re.I)
_FIRST_RE = re.compile(r"\b(?:first|earliest|initial|began|start\s+of)\b", re.I)

_ENTITY_ANCHOR_RE = re.compile(r"\b(?:about|regarding|involving|for|of|by)\b\s*([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)")


# --------------------------------------------------------------------------- #
# Event model
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class TemporalEvent:
    """One thing that happened, with the record that says so."""

    event_id: str
    timestamp: str | None
    start_time: str | None
    end_time: str | None
    entity_keys: tuple[str, ...]
    entity_labels: tuple[str, ...]
    event_type: str
    description: str
    sources: tuple[str, ...]
    location: str | None = None
    origin: str = "record"

    @property
    def display_time(self) -> str:
        return format_timestamp(self.timestamp or self.start_time)

    def as_dict(self, *, document_titles: dict[str, str] | None = None) -> dict[str, Any]:
        titles = document_titles or {}
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "time_display": self.display_time,
            "entity_ids": list(self.entity_keys),
            "entities": list(self.entity_labels),
            "event_type": self.event_type,
            "description": self.description,
            "location": self.location,
            "sources": [
                {"document_id": ref, "filename": titles.get(ref, ref)} for ref in self.sources
            ],
            "origin": self.origin,
        }


def events_from_claims(claims: Sequence[Claim], *, limit: int = 400) -> list[TemporalEvent]:
    """Convert dated claims into ordered events.

    An event is a dated statement about something that happened: a location
    observation, a recorded action, or a located record with no named person.
    Undated claims cannot take part in a chronology and are skipped.
    """
    events: list[TemporalEvent] = []
    seen: set[tuple[str, str, str]] = set()
    for claim in claims or ():
        if claim.dimension not in _DIMENSION_EVENT_TYPES:
            continue
        if not claim.time:
            continue
        signature = (claim.time, claim.document_id, claim.object_key)
        if signature in seen:
            continue
        seen.add(signature)
        event_type = _DIMENSION_EVENT_TYPES[claim.dimension] or claim.predicate
        entity_keys = () if claim.subject_key == "case" else (claim.subject_key,)
        entity_labels = () if claim.subject_key == "case" else (claim.subject_label,)
        description = claim.text
        if claim.dimension in (DIMENSION_LOCATION, DIMENSION_EVENT) and claim.origin == "row":
            # A structured row is read as its own sentence, not as a dump of
            # every column it happens to carry.
            description = claim.value
        if claim.dimension == DIMENSION_AMOUNT:
            # One ledger row is one event: the money movement, described as an
            # investigator reads it, not the whole CSV row twice.
            description = f"{claim.subject_label} — {claim.value}"
        elif claim.dimension == DIMENSION_RELATIONSHIP:
            description = (
                f"{claim.subject_label} {claim.predicate.replace('_', ' ').lower()} "
                f"{claim.object_label}"
            )
        events.append(
            TemporalEvent(
                event_id=claim.claim_id,
                timestamp=claim.time,
                start_time=claim.time,
                end_time=None,
                entity_keys=tuple(entity_keys),
                entity_labels=tuple(entity_labels),
                event_type=str(event_type).upper(),
                description=_clean_description(
                    description,
                    str(event_type).upper(),
                    claim.object_label if claim.dimension == DIMENSION_LOCATION else None,
                ),
                sources=(claim.document_id,),
                location=claim.object_label if claim.dimension == DIMENSION_LOCATION else None,
                origin="record",
            )
        )
        if len(events) >= limit:
            break
    return events


#: Which claim dimension describes an occurrence best.  A ledger row yields
#: both an amount claim and a transfer claim about the same movement; the
#: chronology shows it once, using the richer description.
_EVENT_TYPE_PREFERENCE = {
    "PAYMENT": 0,
    "TRANSFERRED": 1,
    "CALLED": 1,
    "USES": 2,
    "OWNS": 2,
    "LOCATED_AT": 3,
    "SIGHTED_AT": 3,
    "RECORDED_EVENT": 4,
}


def _dedupe_events(events: Sequence[TemporalEvent]) -> list[TemporalEvent]:
    """One thing that happened = one event, however many claims describe it."""
    best: dict[tuple[str | None, tuple[str, ...], tuple[str, ...]], TemporalEvent] = {}
    order: list[tuple[str | None, tuple[str, ...], tuple[str, ...]]] = []
    for event in events or ():
        if not str(event.description or "").strip():
            continue
        key = (event.timestamp, tuple(event.sources), tuple(event.entity_keys))
        current = best.get(key)
        if current is None:
            best[key] = event
            order.append(key)
            continue
        if _EVENT_TYPE_PREFERENCE.get(event.event_type, 9) < _EVENT_TYPE_PREFERENCE.get(
            current.event_type, 9
        ):
            best[key] = event
    return [best[key] for key in order]


def events_from_graph(
    timeline_entries: Iterable[dict[str, Any]],
    *,
    key_to_label: dict[str, str] | None = None,
    limit: int = 400,
) -> list[TemporalEvent]:
    """Convert the graph timeline (already built per case) into events."""
    key_to_label = key_to_label or {}
    events: list[TemporalEvent] = []
    for index, entry in enumerate(timeline_entries or ()):
        timestamp = parse_timestamp(entry.get("timestamp"))
        if not timestamp:
            continue
        source_key = str(entry.get("source_key") or entry.get("source") or "")
        target_key = str(entry.get("target_key") or entry.get("target") or "")
        rel_type = str(entry.get("rel_type") or entry.get("label") or "RELATED").upper()
        props = entry.get("properties") if isinstance(entry.get("properties"), dict) else {}
        sources = tuple(
            str(ref) for ref in (props.get("source_doc_ids") or ([props["source_doc_id"]] if props.get("source_doc_id") else []))
        )
        source_label = key_to_label.get(source_key, key_to_label.get(source_key.split(":")[-1], source_key))
        target_label = key_to_label.get(target_key, key_to_label.get(target_key.split(":")[-1], target_key))
        events.append(
            TemporalEvent(
                event_id=f"graph:{index}:{rel_type}",
                timestamp=timestamp,
                start_time=timestamp,
                end_time=None,
                entity_keys=tuple(k for k in (source_key, target_key) if k),
                entity_labels=tuple(l for l in (source_label, target_label) if l),
                event_type=rel_type,
                description=_clean_description(
                    f"{source_label} — {rel_type.replace('_', ' ').lower()} — {target_label}",
                    rel_type,
                ),
                sources=sources,
                origin="graph",
            )
        )
        if len(events) >= limit:
            break
    return events


def merge_events(*event_lists: Iterable[TemporalEvent], limit: int = 600) -> list[TemporalEvent]:
    """Merge event sources, dropping duplicates, ordered chronologically."""
    merged: dict[tuple[str, str, str], TemporalEvent] = {}
    for events in event_lists:
        for event in events or ():
            signature = (str(event.timestamp), event.event_type, event.description)
            merged.setdefault(signature, event)
    ordered = sorted(merged.values(), key=lambda e: (e.timestamp or "", e.event_id))
    return ordered[:limit]


def overlapping_events(
    start: str | None,
    end: str | None,
    events: Sequence[TemporalEvent],
) -> list[TemporalEvent]:
    """Events whose interval intersects ``[start, end]``."""
    if not start:
        return []
    start_dt = _as_datetime(start)
    end_dt = _as_datetime(end) if end else start_dt
    if start_dt is None:
        return []
    if end_dt is None or end_dt < start_dt:
        end_dt = start_dt
    out: list[TemporalEvent] = []
    for event in events or ():
        event_start = _as_datetime(event.start_time or event.timestamp)
        event_end = _as_datetime(event.end_time) or event_start
        if event_start is None:
            continue
        if event_end is None:
            event_end = event_start
        if event_start <= end_dt and event_end >= start_dt:
            out.append(event)
    return out


# --------------------------------------------------------------------------- #
# Query understanding
# --------------------------------------------------------------------------- #

@dataclass
class TemporalQuery:
    """What an investigator's temporal question is asking for."""

    relation: str = RELATION_NONE
    anchor_text: str = ""
    anchor_kinds: tuple[str, ...] = ()
    start: str | None = None
    end: str | None = None
    offset: timedelta | None = None
    direction: str = ""
    wants_communications: bool = False
    raw: str = ""


@dataclass
class TemporalSelection:
    """The answer to a temporal question: anchor, window, ordered events."""

    relation: str
    anchor_label: str
    anchor_time: str | None
    window_start: str | None
    window_end: str | None
    events: list[TemporalEvent] = field(default_factory=list)
    matched: int = 0
    total_events: int = 0
    note: str = ""
    anchor_kind: str = ""

    @property
    def has_events(self) -> bool:
        return bool(self.events)

    def as_dict(self, *, document_titles: dict[str, str] | None = None) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "anchor": self.anchor_label,
            "anchor_time": self.anchor_time,
            "anchor_time_display": format_timestamp(self.anchor_time),
            "window_start": self.window_start,
            "window_end": self.window_end,
            "matched_events": self.matched,
            "total_events": self.total_events,
            "note": self.note,
            "events": [event.as_dict(document_titles=document_titles) for event in self.events[:40]],
        }


#: Characters that are separators rather than content.
_SEPARATOR_RE = re.compile(r"^[\s\-—–|:,.;]+$")


def _clean_description(value: str | None, event_type: str, location: str | None = None) -> str:
    """A description an investigator can read, never a bare separator.

    JSON/CSV-derived units can leave a fragment such as "— event —" behind;
    that is noise, so it degrades to a plain statement of what the record is.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    core = re.sub(r"[^A-Za-z0-9 ]", " ", text).strip().lower()
    if _SEPARATOR_RE.match(text) or len(text) < 8 or core in {"", "event", "record", "entry", "row"}:
        label = str(event_type or "record").replace("_", " ").strip().title()
        return f"{label} recorded" + (f" at {location}" if location else "")
    return text


def _as_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_temporal_query(question: str, *, now: datetime | None = None) -> TemporalQuery:
    """Classify the temporal shape of a question (before / after / between …)."""
    text = str(question or "")
    query = TemporalQuery(raw=text)
    now = now or datetime.now(timezone.utc)

    between = _BETWEEN_RE.search(text)
    if between:
        start = parse_timestamp(between.group("start"), base=now)
        end = parse_timestamp(between.group("end"), base=now)
        if start and end:
            query.relation = RELATION_BETWEEN
            query.start, query.end = start, end
            query.anchor_text = f"{format_timestamp(start)} to {format_timestamp(end)}"
            return query

    relative = _RELATIVE_RE.search(text)
    if relative:
        count_raw = relative.group("count").lower()
        count = _NUMBER_WORDS.get(count_raw)
        if count is None:
            try:
                count = int(count_raw)
            except ValueError:
                count = 1
        days = _UNIT_DAYS.get(relative.group("unit").lower(), 1)
        query.offset = timedelta(days=count * days)
        query.direction = relative.group("direction").lower()

    if _NEAREST_RE.search(text) and not _BEFORE_RE.search(text) and not _AFTER_RE.search(text):
        query.relation = RELATION_NEAREST
    elif query.offset and query.direction in ("before", "earlier than", "prior to"):
        query.relation = RELATION_BEFORE
    elif query.offset and query.direction in ("after", "later than", "following"):
        query.relation = RELATION_AFTER
    elif _BEFORE_RE.search(text):
        query.relation = RELATION_BEFORE
    elif _AFTER_RE.search(text):
        query.relation = RELATION_AFTER
    elif _AROUND_RE.search(text):
        query.relation = RELATION_AROUND
    else:
        query.relation = RELATION_CHRONOLOGICAL

    if re.search(r"\b(?:communications?|calls?|contact|phone|sms)\b", text, re.I):
        query.wants_communications = True

    for pattern, kinds in _ANCHOR_KINDS:
        match = pattern.search(text)
        if match:
            query.anchor_text = match.group(0).strip()
            query.anchor_kinds = kinds
            break
    if not query.anchor_text:
        entity = _ENTITY_ANCHOR_RE.search(text)
        if entity:
            query.anchor_text = entity.group(1).strip()
    return query


def resolve_anchor(
    query: TemporalQuery,
    events: Sequence[TemporalEvent],
    *,
    case_incident_time: str | None = None,
    fallback_events: Sequence[TemporalEvent] = (),
) -> tuple[str, str | None, str]:
    """Resolve the event a question is anchored on.

    Returns ``(label, timestamp, kind)``.  The case's own incident datetime wins
    for incident questions because it is authoritative metadata rather than an
    inference from a record.
    """
    anchor_kind = ",".join(query.anchor_kinds)
    if query.anchor_kinds and any(
        kind in ("INCIDENT", "FIR", "SCENE") for kind in query.anchor_kinds
    ) and case_incident_time:
        return ("the reported incident", case_incident_time, "INCIDENT")

    if query.anchor_kinds:
        candidates = [
            event for event in events
            if any(kind in event.event_type.upper() or kind in (event.description or "").upper()
                   for kind in query.anchor_kinds)
        ]
        if candidates:
            chosen = candidates[0]
            return (query.anchor_text or "the anchor event", chosen.timestamp, chosen.event_type)

    anchor_text = query.anchor_text.lower()
    if anchor_text:
        candidates = [
            event for event in events
            if anchor_text in (event.description or "").lower()
            or any(anchor_text in label.lower() for label in event.entity_labels)
        ]
        if candidates:
            chosen = candidates[0]
            return (query.anchor_text, chosen.timestamp, chosen.event_type)

    if case_incident_time:
        return ("the reported incident", case_incident_time, "INCIDENT")

    ordered = [event for event in (fallback_events or events) if event.timestamp]
    if ordered:
        return ("the earliest recorded event", ordered[0].timestamp, ordered[0].event_type)
    return ("", None, "")


def _communication_event(event: TemporalEvent) -> bool:
    haystack = f"{event.event_type} {event.description}".lower()
    return any(
        token in haystack
        for token in ("call", "phone", "contact", "sms", "communication", "message", "cdr")
    )


def select_temporal_events(
    question: str,
    events: Sequence[TemporalEvent],
    *,
    case_incident_time: str | None = None,
    now: datetime | str | None = None,
    limit: int = 12,
) -> TemporalSelection:
    """Answer *which* events a temporal question is about, in order.

    ``now`` (also accepted as an ISO string) is the reference point for a
    year-less expression such as "between May 25 and June 1".  Callers pass the
    case's own incident time, because "May 25" in a 2025 case means 2025 — not
    whatever year the server happens to be running in.
    """
    if isinstance(now, str):
        now = _as_datetime(now)
    query = parse_temporal_query(question, now=now)
    ordered = [event for event in events or () if event.timestamp]
    ordered.sort(key=lambda event: (event.timestamp or "", event.event_id))

    anchor_label, anchor_time, anchor_kind = resolve_anchor(
        query,
        ordered,
        case_incident_time=case_incident_time,
    )
    anchor_dt = _as_datetime(anchor_time)

    selection = TemporalSelection(
        relation=query.relation,
        anchor_label=anchor_label,
        anchor_time=anchor_time,
        window_start=None,
        window_end=None,
        total_events=len(ordered),
        anchor_kind=anchor_kind,
    )
    # Notes accumulate instead of overwriting: "the window was widened" and
    # "filtered to communications" are both true and both worth saying.
    notes: list[str] = []

    if query.relation == RELATION_BETWEEN and query.start and query.end:
        start_dt, end_dt = _as_datetime(query.start), _as_datetime(query.end)
        selection.window_start, selection.window_end = query.start, query.end
        selection.events = [
            event for event in ordered
            if start_dt and end_dt and start_dt <= (_as_datetime(event.timestamp) or start_dt) <= end_dt
        ]
    elif query.relation == RELATION_NEAREST:
        selection.window_start, selection.window_end = None, None
        if anchor_dt is not None:
            ranked = sorted(
                ordered,
                key=lambda event: abs(
                    ((_as_datetime(event.timestamp) or anchor_dt) - anchor_dt).total_seconds()
                ),
            )
            ranked = [event for event in ranked if event.timestamp != anchor_time]
            selection.events = ranked
        else:
            selection.events = ordered
    elif query.relation == RELATION_BEFORE:
        window = anchor_dt - (query.offset or DEFAULT_WINDOW) if anchor_dt else None
        selection.window_end = anchor_time
        selection.window_start = window.isoformat() if window else None
        selection.events = [
            event for event in ordered
            if anchor_dt and (_as_datetime(event.timestamp) or anchor_dt) < anchor_dt
            and (window is None or (_as_datetime(event.timestamp) or window) >= window)
        ]
        if not selection.events and anchor_dt:
            selection.events = [
                event for event in ordered if (_as_datetime(event.timestamp) or anchor_dt) < anchor_dt
            ][-limit:]
            if selection.events:
                notes.append(
                    "no records fall inside the default window; the nearest earlier records are shown"
                )
    elif query.relation == RELATION_AFTER:
        window = anchor_dt + (query.offset or DEFAULT_WINDOW) if anchor_dt else None
        selection.window_start = anchor_time
        selection.window_end = window.isoformat() if window else None
        selection.events = [
            event for event in ordered
            if anchor_dt and (_as_datetime(event.timestamp) or anchor_dt) > anchor_dt
            and (window is None or (_as_datetime(event.timestamp) or window) <= window)
        ]
        if not selection.events and anchor_dt:
            selection.events = [
                event for event in ordered if (_as_datetime(event.timestamp) or anchor_dt) > anchor_dt
            ][:limit]
            if selection.events:
                notes.append(
                    "no records fall inside the default window; the nearest later records are shown"
                )
    elif query.relation == RELATION_AROUND:
        selection.window_start = (anchor_dt - AROUND_WINDOW).isoformat() if anchor_dt else None
        selection.window_end = (anchor_dt + AROUND_WINDOW).isoformat() if anchor_dt else None
        selection.events = (
            overlapping_events(selection.window_start, selection.window_end, ordered)
            if anchor_dt else []
        )
        if not selection.events:
            selection.events = [event for event in ordered if event.timestamp == anchor_time]
        if not selection.events and anchor_dt:
            # Nothing inside the tight window is not "nothing happened": show
            # the nearest dated records and say that the window was widened.
            ranked = sorted(
                (event for event in ordered if event.timestamp != anchor_time),
                key=lambda event: abs(
                    ((_as_datetime(event.timestamp) or anchor_dt) - anchor_dt).total_seconds()
                ),
            )
            selection.events = ranked[:NEAREST_COUNT]
            if selection.events:
                notes.append(
                    "no records fall inside the default window; the nearest records are shown"
                )
    else:
        selection.events = ordered

    if query.wants_communications:
        communications = [event for event in selection.events if _communication_event(event)]
        if communications:
            selection.events = communications
            notes.append("filtered to communication records")
        elif selection.events:
            # Say what is missing instead of silently answering with a
            # non-communication chronology.
            notes.append(
                "no communication record falls in this window; the dated records that do are shown"
            )

    selection.matched = len(selection.events)
    selection.events = selection.events[:limit]
    if not selection.events:
        notes.append(
            f"no dated events {'in that window' if anchor_time else 'were retrieved'} "
            f"among {selection.total_events} dated records"
        )
    selection.note = "; ".join(dict.fromkeys(notes))
    return selection


def describe_selection(selection: TemporalSelection | None) -> str:
    """One sentence describing what the temporal selection covers."""
    if selection is None:
        return ""
    if selection.relation == RELATION_BEFORE and selection.anchor_time:
        return f"before {selection.anchor_label} ({format_timestamp(selection.anchor_time)})"
    if selection.relation == RELATION_AFTER and selection.anchor_time:
        return f"after {selection.anchor_label} ({format_timestamp(selection.anchor_time)})"
    if selection.relation == RELATION_BETWEEN and selection.window_start and selection.window_end:
        return (
            f"between {format_timestamp(selection.window_start)} and "
            f"{format_timestamp(selection.window_end)}"
        )
    if selection.relation == RELATION_NEAREST:
        return f"closest to {selection.anchor_label} ({format_timestamp(selection.anchor_time)})"
    if selection.relation == RELATION_AROUND and selection.anchor_time:
        return f"around {selection.anchor_label} ({format_timestamp(selection.anchor_time)})"
    return "chronologically"
