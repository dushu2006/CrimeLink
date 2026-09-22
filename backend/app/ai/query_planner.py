"""Query Planner — understands the investigator's question.

The planner classifies intent, extracts named entities (persons, phones,
accounts, vehicles, locations), detects temporal constraints, requested
evidence types, and the preferred response style. It is deterministic and
does not require an LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Intents — must drive both retrieval strategy and response style.
# ---------------------------------------------------------------------------

INTENT_CASE_OVERVIEW = "CASE_OVERVIEW"
INTENT_PEOPLE = "PEOPLE"
INTENT_EVIDENCE_INVENTORY = "EVIDENCE_INVENTORY"
INTENT_RELATIONSHIP = "RELATIONSHIP"
INTENT_TIMELINE = "TIMELINE"
INTENT_CONTRADICTION = "CONTRADICTION"
INTENT_CORROBORATION = "CORROBORATION"
INTENT_FINANCIAL = "FINANCIAL"
INTENT_COMMUNICATION = "COMMUNICATION"
INTENT_LOCATION = "LOCATION"
INTENT_SUMMARY = "SUMMARY"
INTENT_ENTITY_LOOKUP = "ENTITY_LOOKUP"
INTENT_GENERAL = "GENERAL"


# The depth/detail the user is asking for.
DETAIL_BRIEF = "brief"
DETAIL_STANDARD = "standard"
DETAIL_DETAILED = "detailed"


# How the answer should be presented to the investigator.
STYLE_NATURAL = "natural"          # conversational prose — the default
STYLE_LIST = "list"                # bulleted list (e.g. file inventory)
STYLE_TABLE = "table"              # structured rows (e.g. transaction table)
STYLE_TIMELINE = "timeline"        # chronological
STYLE_COMPARISON = "comparison"    # contradictions / pro-con


@dataclass
class QueryPlan:
    """Structured understanding of an investigator question."""

    original_question: str
    intent: str = INTENT_GENERAL
    detail: str = DETAIL_STANDARD
    response_style: str = STYLE_NATURAL
    entities: list[str] = field(default_factory=list)
    entity_labels: list[dict[str, str]] = field(default_factory=list)
    # name-like spans found in the question — used for exact entity matching
    person_names: list[str] = field(default_factory=list)
    keywords: set[str] = field(default_factory=set)
    exact_terms: list[str] = field(default_factory=list)
    temporal_constraint: dict[str, Any] | None = None
    evidence_types: list[str] = field(default_factory=list)
    entity_types: list[str] = field(default_factory=list)
    relationship_types: list[str] = field(default_factory=list)
    need_citations: bool = True
    need_timeline: bool = False
    need_graph_paths: bool = False
    need_documents: bool = True
    # Filled in later against known case entities
    resolved_entity_keys: list[str] = field(default_factory=list)
    #: Temporal shape of the question: BEFORE / AFTER / BETWEEN / AROUND /
    #: NEAREST / CHRONOLOGICAL / "" (empty).  Filled by
    #: :mod:`app.ai.temporal` when the question is anchored on an event.
    temporal_relation: str = ""
    temporal_anchor: str = ""
    #: For INTENT_ENTITY_LOOKUP: which attribute is being asked for
    #: (phone / vehicle / account / address / role / identity / fir_number /
    #: case_status / incident_date / "").  Empty means "the question names an
    #: entity but no specific attribute".
    requested_attribute: str = ""
    #: For INTENT_ENTITY_LOOKUP: which attribute is being asked for
    #: (phone / vehicle / account / address / role / identity / fir_number /
    #: case_status / incident_date / "").  Empty means "the question names an
    #: entity but no specific attribute".
    requested_attribute: str = ""
    confidence: float = 0.5
    # legacy compatibility fields
    answer_mode: str = "CASE_SUMMARY"

    @property
    def legacy_intent(self) -> str:
        """Backward-compatible retrieval intent string for older ranking code."""
        return {
            INTENT_RELATIONSHIP: "connection",
            INTENT_TIMELINE: "timeline",
            INTENT_EVIDENCE_INVENTORY: "evidence",
            INTENT_SUMMARY: "summary",
            INTENT_FINANCIAL: "financial",
            INTENT_COMMUNICATION: "communication",
            INTENT_LOCATION: "location",
            INTENT_PEOPLE: "connection",
            INTENT_CASE_OVERVIEW: "general",
            INTENT_CONTRADICTION: "evidence",
            INTENT_CORROBORATION: "evidence",
        }.get(self.intent, "general")


# ---------------------------------------------------------------------------
# Intent detection patterns — kept small and conservative.
# ---------------------------------------------------------------------------

_INTENT_RULES: list[tuple[str, re.Pattern[str], int]] = [
    # Each rule is (intent, pattern, base_score).  Higher base_score wins ties.
    # Direct entity-attribute lookups ("What is Rahul's phone number?",
    # "What is the FIR number?", "Where does Ravi live?").  These must never
    # degrade into a summary: the answer is one recorded value.  The base
    # score sits above FINANCIAL/COMMUNICATION/LOCATION because their
    # vocabularies (bank, call, where) appear inside attribute questions too.
    (INTENT_ENTITY_LOOKUP, re.compile(
        r"\b(phone\s+(?:number|no\.?)|mobile\s+(?:number|no\.?)|contact\s+(?:number|details?|info(?:rmation)?)|"
        r"vehicle\s+(?:number|registration|plate)|registration\s+(?:number|no\.?)|"
        r"account\s+(?:number|details?)|bank\s+account\s+(?:number|details?)|"
        r"fir\s+(?:number|no\.?)|case\s+(?:status|number)|"
        r"date\s+of\s+(?:the\s+)?incident|incident\s+(?:date|time)|"
        r"where\s+does\s+\w+\s+(?:live|stay|reside)|address\s+of|"
        r"(?:what|which)\s+(?:vehicle|car|bike|phone\s+number|mobile\s+number|account\s+number|address)|"
        r"whose\s+(?:phone|mobile|vehicle|car|account)|"
        r"(?:phone|mobile|address|bank\s+account)\s+of\s+[A-Z][a-zA-Z]{2,}"
        r"|[A-Z][a-zA-Z]{2,}'s\s+(?:phone|mobile|vehicle|car|bike|account|address|bank))\b", re.I), 65),
    # Contradictions — very specific phrasing
    (INTENT_CONTRADICTION, re.compile(
        r"\b(contradict(?:ions?|ory)?|conflict(?:ing|s)?|inconsisten|discrepan|don't\s+match|doesn't\s+match|"
        r"clash|disagree|differences?\s+in\s+(?:the\s+)?(?:statements|accounts|evidence))\b", re.I), 40),
    # Timeline / before / after / what happened
    (INTENT_TIMELINE, re.compile(
        r"\b(timeline|chronolog|sequence|order\s+of\s+events|what\s+happened\s+(?:before|after|during)|"
        r"before\s+(?:the\s+)?incident|after\s+(?:the\s+)?incident|lead(?:ing)?\s+up\s+to|"
        r"when\s+did|events?\s+(?:leading|prior))\b", re.I), 30),
    # Corroboration — asks which facts more than one record supports
    (INTENT_CORROBORATION, re.compile(
        r"\b(corroborat(?:e|ed|ion|ing)?|supported\s+by\s+(?:multiple|several|more\s+than\s+one)|"
        r"multiple\s+(?:evidence\s+)?(?:sources?|records?|documents?)|independent\s+(?:sources?|records?|evidence)|"
        r"more\s+than\s+one\s+(?:source|record|document)|which\s+facts?\s+are\s+supported|"
        r"cross-?check(?:ed)?|backed\s+by|confirm(?:ed)?\s+by\s+(?:another|a\s+second))\b", re.I), 45),
    # Connections / relationships between entities — beats generic "evidence"
    (INTENT_RELATIONSHIP, re.compile(
        r"\b(connect(?:s|ed|ion|ions)?|link(?:s|ed|age)?|relat(?:e|ed|ions?|ionship)s?|associat(?:e|ed|ion)|"
        r"between|path|route|contact(?:s|ed)?\s+with|relationship\s+between)\b", re.I), 35),
    # People / individuals
    (INTENT_PEOPLE, re.compile(
        r"\b(person|people|individuals?|suspects?|accused|witness(?:es)?|involved\s+parties|"
        r"who\s+(?:is|are|was|were)|tell\s+me\s+about\s+(?:the\s+)?people|who\s+(?:all\s+)?(?:is|are)\s+involved)\b", re.I), 25),
    # File / evidence inventory (only when asking to list files/records)
    (INTENT_EVIDENCE_INVENTORY, re.compile(
        r"\b(files?|documents?|inventory|list\s+(?:all\s+)?(?:files|documents|evidence|records)|"
        r"what\s+(?:files|documents|records)\s*(?:do\s+we\s+have|are\s+(?:there|available))?|"
        r"available\s+(?:files|documents|evidence|records))\b", re.I), 20),
    # Financial
    (INTENT_FINANCIAL, re.compile(
        r"\b(financial\s+(?:evidence|records?|statements?)|money|transfers?|transactions?|bank\s+(?:records|statements?|accounts?)|"
        r"payment|amount|funds?|ledger|balance|withdraw|deposit)\b", re.I), 20),
    # Communication
    (INTENT_COMMUNICATION, re.compile(
        r"\b(calls?|calling?|phone\s+(?:records?|logs?)|mobile|sms|text|contact(?:ed|s)?\s+(?:records?|logs?)|"
        r"communications?|communicated|cdr|call\s+detail|spoke\s+(?:to|with))\b", re.I), 18),
    # Location
    (INTENT_LOCATION, re.compile(
        r"\b(where|location|cctv|address|place|warehouse|spot|scene|tower\s+location|"
        r"movement|travel|visited|went\s+to)\b", re.I), 15),
    # Summary
    (INTENT_SUMMARY, re.compile(
        r"\b(summar[iy]z?e|brief|in\s+short|nutshell|tldr|recap)\b", re.I), 25),
    # Case overview / details
    (INTENT_CASE_OVERVIEW, re.compile(
        r"\b(details\s+of\s+(?:this|the)\s+case|case\s+details|what\s+(?:is|are)\s+this\s+case|"
        r"tell\s+me\s+about\s+(?:this|the)\s+case|case\s+overview|about\s+(?:this|the)\s+case|"
        r"what\s+happened\s+in\s+this\s+case|what\s+is\s+this\s+(?:case|about)|overview|describe)\b", re.I), 10),
]


# Evidence type keywords — maps natural phrases to canonical evidence type codes.
_EVIDENCE_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("FIR", ["fir", "first information"]),
    ("CHARGESHEET", ["chargesheet", "charge sheet"]),
    ("CCTV", ["cctv", "camera", "footage", "video"]),
    ("CALL_RECORD", ["call record", "cdr", "call detail", "call log", "phone record"]),
    ("BANK_STATEMENT", ["bank statement", "account statement", "transaction record"]),
    ("WITNESS_STATEMENT", ["witness statement", "statement of witness", "witness account", "161 statement"]),
    ("FINANCIAL", ["financial record", "financial evidence", "ledger"]),
    ("FIELD_REPORT", ["field report", "spot report", "seizure memo"]),
    ("FORENSIC_REPORT", ["forensic report", "forensic"]),
    ("PANAMA", ["panama"]),
]


def _between_spans_are_dates(question: str) -> bool:
    """True when "between X and Y" names two dates rather than two people."""
    from app.ai.claims import parse_timestamp

    match = re.search(r"\bbetween\s+(?P<start>[^,.;]{3,40}?)\s+and\s+(?P<end>[^,.;]{3,40})", question or "", re.I)
    if not match:
        return False
    return bool(
        parse_timestamp(match.group("start")) and parse_timestamp(match.group("end"))
    )


def _temporal_intent_override(question: str, intent: str) -> str | None:
    """A chronology question is a chronology question without the word "timeline"."""
    relation, anchor = _extract_temporal_shape(question)
    if not relation:
        return None
    if relation == "BETWEEN":
        if intent in (INTENT_RELATIONSHIP, INTENT_GENERAL, INTENT_CASE_OVERVIEW) and _between_spans_are_dates(question):
            return INTENT_TIMELINE
        return None
    if anchor and intent in (INTENT_GENERAL, INTENT_CASE_OVERVIEW):
        return INTENT_TIMELINE
    return None


def _classify_intent(question: str) -> tuple[str, float]:
    q = question or ""
    scores: dict[str, float] = {}
    for intent, pattern, base in _INTENT_RULES:
        matches = pattern.findall(q)
        if matches:
            scores[intent] = scores.get(intent, 0.0) + base + len(matches) * 2
    if not scores:
        # Fallback: "what happened" without further qualifiers → OVERVIEW
        override = _temporal_intent_override(q, INTENT_GENERAL)
        if override:
            return override, 0.6
        if re.search(r"\bwhat\s+happened\b", q, re.I):
            return INTENT_CASE_OVERVIEW, 0.5
        return INTENT_GENERAL, 0.3
    intent = max(scores.items(), key=lambda x: x[1])[0]
    confidence = min(1.0, 0.4 + scores[intent] / 50.0)

    # A question anchored on an event ("... around the incident", "... closest
    # to the meeting", "between May 25 and June 1") is answered from the
    # chronology, not from a case summary.
    override = _temporal_intent_override(q, intent)
    if override:
        return override, max(confidence, 0.6)
    return intent, confidence


#: Possessive single names ("What is Ravi's phone number?") and "of <Name>"
#: forms ("the phone number of Ravi") — unambiguous single-name references
#: the two-word heuristic cannot catch.
_POSSESSIVE_NAME_RE = re.compile(r"\b([A-Z][a-z]{2,})'s\b")
_OF_NAME_RE = re.compile(r"\b(?:of|about|for)\s+([A-Z][a-z]{3,})(?![a-z'])")

_NAME_STOPWORDS = {
    "what", "who", "when", "where", "why", "how", "which", "this", "that",
    "case", "incident", "fir", "phone", "number", "vehicle", "account",
    "address", "role", "status", "date", "time", "file", "record",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "capital", "president", "minister", "population", "currency",
}


def _extract_person_names(question: str, known_names: Iterable[str] = ()) -> list[str]:
    """Extract likely person names.

    First matches known names (if provided), then applies a conservative
    two-capitalized-words heuristic for western/south-asian romanized names,
    then possessive / "of <Name>" single-name forms.
    """
    found: list[str] = []
    q = question or ""
    # Known names first — longest-match first to capture full names.
    sorted_known = sorted((n.strip() for n in known_names if n and n.strip()),
                          key=lambda s: -len(s))
    lowered = q.lower()
    for name in sorted_known:
        if name.lower() in lowered and name not in found:
            found.append(name)
    if found:
        return found
    # Heuristic: two or more capitalized words that are not at sentence start
    # only (e.g. "Anjali Hussain", "Dinesh Malhotra").
    # Look for sequences of Capitalized Words after common phrasings.
    name_match = re.findall(
        r"(?<![A-Za-z])([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})(?![A-Za-z])",
        q,
    )
    skip_words = {
        "Case", "Case Intelligence", "The Case", "What Are", "Tell Me", "Show Me",
        "Who Are", "Indian", "Case Details", "How Many", "Evidence Drawer",
        "Direct Answer", "Why This", "Suggested Follow",
    }
    for candidate in name_match:
        if candidate in skip_words:
            continue
        # Filter out sentence-initial "What files" / "The people" etc.
        words = candidate.split()
        if len(words) < 2:
            continue
        # Require at least two non-stopword words
        if all(w.lower() in {"the", "a", "an", "what", "who", "this", "that", "these", "those"} for w in words):
            continue
        found.append(candidate)
    if found:
        return found[:5]
    # Single-name references: "What is Ravi's phone number?", "the phone
    # number of Rahul" — unambiguous possessive/of forms.
    singles: list[str] = []
    for pattern in (_POSSESSIVE_NAME_RE, _OF_NAME_RE):
        for match in pattern.finditer(q):
            name = match.group(1)
            if name.lower() in _NAME_STOPWORDS:
                continue
            if name not in singles:
                singles.append(name)
    return singles[:5]


def _extract_exact_terms(question: str) -> list[str]:
    if not question:
        return []
    candidates = re.findall(
        r"(?<![A-Za-z0-9])[A-Za-z]{2,}[-_/][A-Za-z0-9][A-Za-z0-9_-]*|"
        r"(?<![A-Za-z0-9])\+?\d{10,13}(?![A-Za-z0-9])",
        question,
    )
    stop = {"what", "when", "where", "which", "show", "tell", "case"}
    return sorted({c for c in candidates if c.casefold() not in stop}, key=str.casefold)


def _extract_keywords(question: str) -> set[str]:
    if not question:
        return set()
    tokens = re.split(r"[^a-z0-9]+", question.lower())
    stop = {
        "what", "who", "when", "where", "why", "how", "which", "this", "that",
        "these", "those", "the", "and", "or", "but", "with", "from", "about",
        "into", "case", "tell", "show", "list", "give", "find", "are", "is",
        "was", "were", "been", "have", "has", "had", "does", "did", "can",
        "could", "would", "should", "will", "me", "you", "for", "are", "does",
        "available", "involved", "details",
    }
    return {t for t in tokens if len(t) >= 3 and t not in stop}


def _extract_temporal(question: str) -> dict[str, Any] | None:
    if not question:
        return None
    # before / after the incident
    before = bool(re.search(r"\bbefore\b", question, re.I))
    after = bool(re.search(r"\bafter\b", question, re.I))
    during = bool(re.search(r"\bduring\b", question, re.I))
    # Month / date hints
    date_match = re.search(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?\b",
        question, re.I,
    )
    if before or after or during or date_match:
        return {
            "before": before,
            "after": after,
            "during": during,
            "date_text": date_match.group(0) if date_match else None,
            "raw": question,
        }
    return None


#: Temporal shapes the planner recognises, in priority order.
_TEMPORAL_SHAPES: list[tuple[str, re.Pattern[str]]] = [
    ("BETWEEN", re.compile(r"\bbetween\b[^,.;]{0,40}?\band\b", re.I)),
    ("NEAREST", re.compile(r"\b(?:closest|nearest|most\s+recent|latest)\b", re.I)),
    ("AFTER", re.compile(r"\b(?:after|following|since|subsequent\s+to|later\s+than)\b", re.I)),
    ("BEFORE", re.compile(r"\b(?:before|prior\s+to|earlier\s+than|leading\s+up\s+to|ahead\s+of)\b", re.I)),
    ("AROUND", re.compile(r"\b(?:around|near|close\s+to|during)\b", re.I)),
]

_TEMPORAL_ANCHOR_RE = re.compile(
    r"\b(?:before|after|prior\s+to|following|since|around|near|leading\s+up\s+to|closest\s+to)\b\s+"
    r"(?:the\s+|this\s+|that\s+)?(?P<anchor>[a-z][a-z\s]{2,40})",
    re.I,
)


def _extract_temporal_shape(question: str) -> tuple[str, str]:
    """What kind of chronology is being asked for, and anchored on what."""
    text = str(question or "")
    for relation, pattern in _TEMPORAL_SHAPES:
        if pattern.search(text):
            anchor = ""
            match = _TEMPORAL_ANCHOR_RE.search(text)
            if match:
                anchor = match.group("anchor").strip().rstrip("?.,;:")
                anchor = re.split(r"\s+(?:of|for|about|in)\s+", anchor)[0].strip()
            return relation, anchor
    return "", ""


def _extract_evidence_types(question: str) -> list[str]:
    found: list[str] = []
    q = question.lower()
    for canonical, keywords in _EVIDENCE_TYPE_KEYWORDS:
        for kw in keywords:
            if kw in q and canonical not in found:
                found.append(canonical)
                break
    return found


def _classify_response_style(intent: str, question: str) -> str:
    q = (question or "").lower()
    if intent in (INTENT_EVIDENCE_INVENTORY, INTENT_FINANCIAL):
        if re.search(r"\b(list|inventory|table|all\s+files|all\s+documents)\b", q):
            return STYLE_LIST
    if intent == INTENT_TIMELINE:
        return STYLE_TIMELINE
    if intent == INTENT_CONTRADICTION:
        return STYLE_COMPARISON
    if intent == INTENT_CORROBORATION:
        return STYLE_NATURAL
    if intent == INTENT_RELATIONSHIP:
        return STYLE_NATURAL
    return STYLE_NATURAL


def _classify_detail(question: str) -> str:
    q = (question or "").lower()
    if re.search(r"\b(brief|short|quick|summary|in\s+short|tldr)\b", q):
        return DETAIL_BRIEF
    if re.search(r"\b(detailed|thorough|deep|in\s+detail|comprehensive|full|everything)\b", q):
        return DETAIL_DETAILED
    return DETAIL_STANDARD


#: Attribute vocabularies for INTENT_ENTITY_LOOKUP.  The value is the
#: canonical attribute name consumed by the composer.
_ATTRIBUTE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("phone", re.compile(
        r"\b(phone\s+(?:number|no\.?)|mobile|contact\s+(?:number|details?|info(?:rmation)?)|"
        r"phone|cell(?:phone)?)\b", re.I)),
    ("vehicle", re.compile(
        r"\b(vehicle|car|bike|motorcycle|scooter|registration\s+(?:number|no\.?)|plate\s+number)\b", re.I)),
    ("account", re.compile(
        r"\b(account\s+(?:number|details?)|bank\s+account|account)\b", re.I)),
    ("address", re.compile(
        r"\b(address|residence|where\s+does\s+\w+\s+(?:live|stay|reside)|lives?\s+(?:at|in))\b", re.I)),
    ("fir_number", re.compile(r"\bfir\s+(?:number|no\.?)\b", re.I)),
    ("case_status", re.compile(
        r"\bcase\s+status\b|\bstatus\s+of\s+(?:this|the)\s+case\b", re.I)),
    ("case_number", re.compile(r"\bcase\s+number\b", re.I)),
    ("incident_date", re.compile(
        r"\b(date\s+of\s+(?:the\s+)?incident|incident\s+(?:date|time)|"
        r"when\s+did\s+(?:the\s+)?(?:incident|crime|event|it)\s+(?:happen|occur|take\s+place))\b", re.I)),
]

_IDENTITY_RE = re.compile(
    r"^\s*who\s+(?:is|was|are|were)\s+(.+?)\s*\??\s*$", re.I,
)
_ROLE_RE = re.compile(r"\b(role|designation|position)\s+of\b|\bwhat\s+role\b", re.I)

#: "Who is the investigating officer?" asks for a role *holder*, not for a
#: named person's identity — those stay on the people-intent path.
_GENERIC_IDENTITY_SUBJECTS = frozenset({
    "the investigating officer", "investigating officer", "the io", "io",
    "the complainant", "complainant", "the accused", "accused",
    "the suspect", "the suspects", "suspects", "the victim", "victim",
    "the witness", "witnesses", "the criminals", "criminals", "the people",
    "people", "the persons", "persons", "the individuals",
})
#: Individual words that mark an identity question as asking about a role
#: rather than a named person ("Who are the persons in this case?").
_GENERIC_IDENTITY_WORDS = frozenset({
    "complainant", "accused", "suspect", "suspects", "victim", "victims",
    "witness", "witnesses", "criminal", "criminals", "officer", "people",
    "person", "persons", "individuals", "involved", "case",
})


def _detect_requested_attribute(question: str) -> str:
    """Which recorded attribute the question asks for ("" when none)."""
    q = question or ""
    for name, pattern in _ATTRIBUTE_PATTERNS:
        if pattern.search(q):
            return name
    if _ROLE_RE.search(q):
        return "role"
    return ""


def plan_query(
    question: str,
    *,
    known_person_names: Iterable[str] = (),
) -> QueryPlan:
    """Deterministically parse an investigator question into a QueryPlan."""
    intent, intent_conf = _classify_intent(question)
    person_names = _extract_person_names(question, known_person_names)
    exact_terms = _extract_exact_terms(question)
    keywords = _extract_keywords(question)
    temporal = _extract_temporal(question)
    evidence_types = _extract_evidence_types(question)
    temporal_relation, temporal_anchor = _extract_temporal_shape(question)
    if not temporal_relation and intent == INTENT_TIMELINE:
        # A chronology question without an explicit anchor is still chronological.
        temporal_relation = "CHRONOLOGICAL"

    # "Who is Rahul Kumar?" — a single named person's identity/role — is an
    # attribute lookup, not a roster question.  "Who are the people
    # involved?" stays INTENT_PEOPLE.  Single-word names ("Who is Rahul?")
    # are caught by the identity shape even when the two-word heuristic
    # could not extract a name.
    identity_match = _IDENTITY_RE.match(question or "")
    if intent in (INTENT_PEOPLE, INTENT_GENERAL) and identity_match:
        subject = identity_match.group(1).strip()
        subject_low = subject.lower()
        generic_role_holder = (
            subject_low in _GENERIC_IDENTITY_SUBJECTS
            or any(
                re.search(rf"\b{re.escape(word)}\b", subject_low)
                for word in _GENERIC_IDENTITY_WORDS
            )
        )
        if len(subject) >= 2 and not generic_role_holder:
            intent = INTENT_ENTITY_LOOKUP
            if not person_names and re.search(r"[A-Za-z]", subject):
                person_names = [subject]

    requested_attribute = (
        _detect_requested_attribute(question) if intent == INTENT_ENTITY_LOOKUP else ""
    )
    if intent == INTENT_ENTITY_LOOKUP and not requested_attribute and identity_match:
        requested_attribute = "identity"

    entity_types: list[str] = []
    relationship_types: list[str] = []
    q_lower = (question or "").lower()
    if re.search(r"\b(person|people|individual|who)\b", q_lower):
        entity_types.append("PERSON")
    if re.search(r"\b(phone|mobile|call|contact)\b", q_lower):
        entity_types.append("PHONE")
        relationship_types.append("CALL")
    if re.search(r"\b(vehicle|car|plate|bike)\b", q_lower):
        entity_types.append("VEHICLE")
    if re.search(r"\b(account|bank|financial|transaction|money)\b", q_lower):
        entity_types.append("BANKACCOUNT")
        relationship_types.append("TRANSFER")

    response_style = _classify_response_style(intent, question)
    detail = _classify_detail(question)

    need_timeline = (
        intent == INTENT_TIMELINE
        or temporal is not None
        or intent in (INTENT_CASE_OVERVIEW, INTENT_SUMMARY)
    )
    if intent == INTENT_CONTRADICTION:
        # A "contradictions" question is answered by comparing records, never
        # by summarising the case.
        need_documents = True
    need_graph_paths = intent in (INTENT_RELATIONSHIP, INTENT_PEOPLE)
    need_documents = True
    need_citations = True

    # answer_mode maps to the legacy "answer_mode" expected elsewhere
    answer_mode_map = {
        INTENT_CASE_OVERVIEW: "CASE_OVERVIEW",
        INTENT_PEOPLE: "PERSON_ANALYSIS",
        INTENT_EVIDENCE_INVENTORY: "EVIDENCE_INVENTORY",
        INTENT_RELATIONSHIP: "RELATIONSHIP_ANALYSIS",
        INTENT_TIMELINE: "TIMELINE_ANALYSIS",
        INTENT_CONTRADICTION: "CONTRADICTION_ANALYSIS",
        INTENT_CORROBORATION: "CORROBORATION_ANALYSIS",
        INTENT_FINANCIAL: "FINANCIAL_ANALYSIS",
        INTENT_COMMUNICATION: "COMMUNICATION_ANALYSIS",
        INTENT_LOCATION: "LOCATION_ANALYSIS",
        INTENT_SUMMARY: "CASE_SUMMARY",
        INTENT_ENTITY_LOOKUP: "ENTITY_LOOKUP",
        INTENT_GENERAL: "GENERAL",
    }
    answer_mode = answer_mode_map.get(intent, "GENERAL")

    # Person-name spans become entity candidates
    entity_labels = [{"label": "PERSON", "name": n} for n in person_names]

    confidence = 0.5 + intent_conf * 0.2
    if person_names:
        confidence += 0.15
    if evidence_types:
        confidence += 0.1
    if temporal:
        confidence += 0.1
    confidence = min(1.0, confidence)

    return QueryPlan(
        original_question=question,
        intent=intent,
        detail=detail,
        response_style=response_style,
        person_names=person_names,
        entity_labels=entity_labels,
        keywords=keywords,
        exact_terms=exact_terms,
        temporal_constraint=temporal,
        evidence_types=evidence_types,
        entity_types=entity_types,
        relationship_types=relationship_types,
        need_citations=need_citations,
        need_timeline=need_timeline,
        need_graph_paths=need_graph_paths,
        need_documents=need_documents,
        temporal_relation=temporal_relation,
        temporal_anchor=temporal_anchor,
        requested_attribute=requested_attribute,
        confidence=confidence,
        answer_mode=answer_mode,
    )


def resolve_plan_entities(
    plan: QueryPlan,
    nodes: Iterable[dict[str, Any]],
) -> QueryPlan:
    """Resolve person names in the plan to provenance keys among known nodes.

    This is deterministic string matching against case-scoped entity names.
    Names that cannot be matched are left untouched (the retriever will still
    attempt keyword search but will not invent entities).
    """
    # Build name → list of (key, label) index
    name_index: dict[str, list[tuple[str, str]]] = {}
    for n in nodes:
        props = n.get("properties") or n or {}
        label = str(n.get("label") or props.get("label") or "").upper()
        pk = str(n.get("provenance_key") or n.get("id") or "")
        candidates = [
            props.get("name"), props.get("full_name"),
            n.get("name"), n.get("full_name"),
        ]
        for cand in candidates:
            if not cand:
                continue
            norm = str(cand).strip().lower()
            if not norm:
                continue
            name_index.setdefault(norm, []).append((pk, label))

    matched_keys: list[str] = []
    matched_labels: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    for name in list(plan.person_names) + list(plan.entities):
        if not name:
            continue
        nlower = name.strip().lower()
        # exact match first
        matches = name_index.get(nlower, [])
        if not matches:
            # try substring match (e.g. "Anjali" matches "Anjali Hussain")
            for candidate_name, entries in name_index.items():
                if nlower in candidate_name or candidate_name in nlower:
                    matches.extend(entries)
                    if len(matches) >= 3:
                        break
        for key, label in matches:
            if key and key not in seen_keys:
                seen_keys.add(key)
                matched_keys.append(key)
                matched_labels.append({"label": label, "name": name, "key": key})

    plan.resolved_entity_keys = matched_keys
    # Preserve existing labels and add resolved ones
    existing_names = {e.get("name") for e in plan.entity_labels}
    for entry in matched_labels:
        if entry.get("name") not in existing_names:
            plan.entity_labels.append(entry)

    # Update legacy entities field for ranking code compatibility
    plan.entities = matched_keys
    return plan
