"""Conversation layer — session chat continuity for the Case AI assistant.

The Case AI chat must behave like a normal conversational assistant: a
follow-up such as "What about Ravi?" is only meaningful *in the context of
the previous exchange*, and it is this module's job to turn such a fragment
into a standalone, retrievable question before the RAG pipeline sees it.

Everything here is deterministic and auditable — no model call is made to
"understand" the conversation.  Three outcomes are possible for an incoming
message:

1. ``standalone`` — the message is self-contained; use it as asked.
2. ``rewritten`` — the message is a follow-up and could be resolved against
   the recent conversation (entity slot substitution, pronoun antecedent,
   temporal anchor, document reference).  The rewritten question is used for
   planning/retrieval; the original wording is kept for display and audit.
3. ``clarification`` — the follow-up admits two equally plausible readings
   (e.g. substituting either end of an "X and Y" pair).  The gateway answers
   with a short clarifying question instead of guessing.

Scope discipline (§29): the conversation is *context*, never a key.  The
rewritten question still goes through case-scoped entity detection,
case-scoped retrieval and citation validation, so history can never smuggle
another case's data into the answer.  History text is treated as untrusted
data and sanitized before it is reused anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.ai.safety import sanitize_untrusted_evidence


# ---------------------------------------------------------------------------
# Conversation window
# ---------------------------------------------------------------------------

#: How much of the recent conversation is considered for coreference.  Long
#: enough for a real investigative dialogue, short enough that an abandoned
#: topic stops influencing new questions.
MAX_HISTORY_TURNS = 12
MAX_TURN_CHARS = 600

_VALID_ROLES = {"user", "assistant"}


@dataclass
class ConversationTurn:
    role: str
    content: str


def normalize_history(
    history: Iterable[dict[str, Any]] | None,
    *,
    max_turns: int = MAX_HISTORY_TURNS,
    max_chars: int = MAX_TURN_CHARS,
) -> list[ConversationTurn]:
    """Coerce the client-supplied history into a bounded, sanitized window.

    The client owns the conversation (in-memory session state); the backend
    receives the recent turns with each request.  We accept only well-formed
    user/assistant turns, drop everything else, cap the length of both the
    window and each turn, and mark the text as untrusted so it can never be
    interpreted as instructions anywhere downstream.
    """
    if not history:
        return []
    turns: list[ConversationTurn] = []
    for item in list(history)[-max_turns * 2 :]:  # pre-trim before validating
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in _VALID_ROLES:
            continue
        content = str(item.get("content") or item.get("text") or "").strip()
        if not content:
            continue
        if len(content) > max_chars:
            content = content[: max_chars - 1].rstrip() + "…"
        turns.append(
            ConversationTurn(role=role, content=sanitize_untrusted_evidence(content))
        )
    return turns[-max_turns:]


# ---------------------------------------------------------------------------
# Follow-up detection
# ---------------------------------------------------------------------------

#: "what about Ravi?", "and the vehicle?", "so how about the account?"
_WHAT_ABOUT_RE = re.compile(
    r"^\s*(?:and|then|so|now|ok(?:ay)?)?\s*,?\s*"
    r"(?:what|how)\s+about\s+(.+?)\s*\??\s*$",
    re.I,
)
#: "and Ravi?", "then the FIR?" — a bare topic fragment with a question mark.
_TOPIC_FRAGMENT_RE = re.compile(
    r"^\s*(?:and|then|so)\s+([a-zA-Z][\w .'-]{0,48}?)\s*\?\s*$",
    re.I,
)
#: Explicit sequels along the timeline.
_TEMPORAL_SEQUEL_RE = re.compile(
    r"\bwhat\s+happened\s+(after|before)\s+(?:that|this|it|then)\b|"
    r"\bwhat\s+(?:happened\s+)?(?:next|followed|then)\b|"
    r"\b(?:and\s+)?then\s+what\b|"
    r"\bwhat\s+came\s+(?:before|after)\s+(?:that|it)\b|^then\?$|^next\?$|^after\s+that\??$",
    re.I,
)
#: "Which document shows that?", "which file mentions it?", "that document?"
_DOCUMENT_ANAPHORA_RE = re.compile(
    r"\bwhich\s+(?:document|file|record|report|evidence)\s+(?:shows?|mentions?|proves?|"
    r"establishes?|contains?|supports?)\s+(?:that|this|it)\b|"
    r"^\s*(?:that|this)\s+(?:document|file|record)\s*\??\s*$|"
    r"\b(?:show|open)\s+(?:me\s+)?(?:that|this|the)\s+(?:document|file|record)\b",
    re.I,
)
#: Bare elliptical prompts that only mean anything against the last exchange.
_ELLIPTICAL_RE = re.compile(
    r"^\s*(?:and\s+)?(why|how|when|where|who|really|explain)\s*\??\s*$",
    re.I,
)
_PRONOUN_RE = re.compile(
    r"\b(he|him|his|she|her|hers|they|them|their|theirs|that\s+person|this\s+person|"
    r"that\s+guy|the\s+same\s+person)\b",
    re.I,
)

#: Question words that start a genuine standalone question even when the
#: message is short ("What happened on March 12?" is standalone; "What
#: happened after that?" is not — the sequel regex above catches it first).
_DATE_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}"                      # 2026-03-14
    r"|\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"            # 14/03/2026
    r"|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:\s*,?\s*\d{4})?"  # March 14
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s*,?\s*\d{4})?"  # 14 March 2026
    r")\b",
    re.I,
)

#: Anchor nouns that a temporal sequel can latch onto when no explicit date
#: appears in the last exchange.
_ANCHOR_PHRASES = (
    "the fir was filed", "the fir", "the incident", "the arrest", "the payment",
    "the transfer", "the meeting", "the call", "the event", "the raid",
    "the seizure", "the complaint",
)

_QUESTION_STOPWORDS = {
    "what", "who", "whom", "whose", "when", "where", "why", "how", "which",
    "is", "are", "was", "were", "did", "does", "do", "the", "a", "an", "of",
    "in", "on", "at", "to", "for", "about", "this", "that", "these", "those",
    "case", "tell", "me", "show", "give", "list", "and", "or", "his", "her",
    "their", "its", "between", "with", "from",
}

#: Attribute phrases that describe a property of some entity.  Used both to
#: detect that the previous question was "about an attribute" and to grow a
#: fresh follow-up frame when the old question has no replaceable entity slot.
_ATTRIBUTE_WORDS = (
    "phone number", "mobile number", "contact number", "phone", "mobile",
    "vehicle", "car", "bike", "account number", "bank account", "account",
    "address", "role", "age", "fir number", "case status",
)

#: Document/topic words that can act as a substitution slot even though they
#: are not entities ("What does the FIR say?" → "What does the CDR say?").
_TOPIC_WORDS = (
    "fir", "cdr", "call record", "bank statement", "witness statement",
    "charge sheet", "chargesheet", "case diary", "cctv", "forensic report",
    "surveillance log", "timeline", "financial evidence", "communication",
)


@dataclass
class Resolution:
    """The conversational meaning of one incoming user message."""

    standalone_question: str
    is_followup: bool = False
    kind: str = "standalone"  # standalone | what_about | topic_fragment | pronoun | temporal_anchor | document_reference | elliptical
    needs_clarification: bool = False
    clarification: str | None = None
    #: Short machine-readable note for diagnostics (never user-visible).
    note: str = ""


def _strip_qmark(text: str) -> str:
    return re.sub(r"[\s?.!]+$", "", (text or "").strip())


def _last_user_turn(turns: list[ConversationTurn]) -> str | None:
    for turn in reversed(turns):
        if turn.role == "user":
            return turn.content
    return None


def _last_assistant_turn(turns: list[ConversationTurn]) -> str | None:
    for turn in reversed(turns):
        if turn.role == "assistant":
            return turn.content
    return None


def _person_first_names(known_names: Iterable[str]) -> dict[str, str]:
    """first-name token -> full known name (only when unambiguous)."""
    out: dict[str, str] = {}
    for name in known_names:
        full = str(name or "").strip()
        if not full:
            continue
        first = full.split()[0].lower()
        if len(first) < 3:
            continue
        if first in out and out[first].lower() != full.lower():
            out[first] = ""  # ambiguous — never substitute on it
        else:
            out.setdefault(first, full)
    return {k: v for k, v in out.items() if v}


def _known_name_slots(text: str, known_names: Iterable[str]) -> list[str]:
    """Known case-entity names appearing in ``text``, longest first."""
    hay = text.lower()
    found: list[str] = []
    for name in sorted((str(n) for n in known_names if n), key=len, reverse=True):
        nlow = name.strip().lower()
        if len(nlow) < 3:
            continue
        if re.search(rf"\b{re.escape(nlow)}\b", hay):
            if not any(nlow in f.lower() or f.lower() in nlow for f in found):
                found.append(name)
    firsts = _person_first_names(known_names)
    for first, full in sorted(firsts.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(first)}\b", hay):
            if not any(full.lower() == f.lower() or full.lower() in f.lower() or f.lower() in full.lower()
                       for f in found):
                found.append(full)
    return found


def _capitalized_slots(text: str) -> list[str]:
    """Capitalized spans that look like proper nouns (fallback slot source)."""
    out: list[str] = []
    for match in re.finditer(r"\b([A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})*)\b", text):
        span = match.group(1)
        first_word = span.split()[0].lower()
        if first_word in _QUESTION_STOPWORDS:
            # A capital at position 0 is usually the question word itself.
            if match.start() <= 1:
                continue
        if span.lower() in _QUESTION_STOPWORDS:
            continue
        out.append(span)
    return out


def _topic_slot(text: str) -> str | None:
    """A document/topic word in the previous question that can be swapped."""
    low = text.lower()
    for topic in sorted(_TOPIC_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(topic)}\b", low):
            # Preserve the surface casing used in the original question.
            m = re.search(rf"\b{re.escape(topic)}\b", text, re.I)
            if m:
                return m.group(0)
            return topic
    return None


def _attribute_phrase(text: str) -> str | None:
    """The attribute the previous question asked about, if any."""
    low = text.lower()
    for attr in _ATTRIBUTE_WORDS:
        if re.search(rf"\b{re.escape(attr)}\b", low):
            return attr
    return None


#: Tokens that read as acronyms when substituted into a document slot.
_ACRONYM_TOKENS = frozenset(
    {"cdr", "fir", "cctv", "sms", "mms", "io", "ipc", "crpc", "upi", "atm", "gps"}
)


def _apply_preserving_case(original_slot: str, replacement: str) -> str:
    """Swap a slot for the follow-up topic, keeping the slot's casing style."""
    rep = replacement.strip()
    if not rep:
        return original_slot
    if original_slot.isupper() and len(original_slot) > 1:
        # Acronym slot: an acronym replacement stays uppercase; a natural
        # multi-word topic keeps its own casing inside the existing frame
        # ("What does the FIR say?" -> "What does the witness statement say?").
        if rep.isupper() or rep.lower() in _ACRONYM_TOKENS:
            return rep.upper()
        return rep
    if original_slot[:1].isupper():
        # Title-case a lowercase topic ("ravi" -> "Ravi"); keep acronyms ("CDR").
        if rep.isupper() or rep.islower():
            return " ".join(w.capitalize() for w in rep.split())
    return rep


def _surface_for_slot(question: str, slot: str) -> str | None:
    """The actual surface form a known entity takes inside the question.

    The known name may be "Rahul Kumar" while the question says "Rahul" —
    substitution must target the surface form, not the canonical one.
    """
    m = re.search(re.escape(slot), question, re.I)
    if m:
        return m.group(0)
    first = slot.split()[0]
    if len(first) >= 3:
        m = re.search(rf"\b{re.escape(first)}\b", question, re.I)
        if m:
            return m.group(0)
    return None


def _is_pair_frame(question: str, slots: list[str]) -> bool:
    """Whether the previous question framed a relationship between two slots."""
    if len(slots) < 2:
        return False
    low = question.lower()
    surf_a = (_surface_for_slot(question, slots[0]) or slots[0].split()[0]).lower()
    surf_b = (_surface_for_slot(question, slots[1]) or slots[1].split()[0]).lower()
    if surf_a not in low or surf_b not in low:
        return False
    both_between = "between" in low
    and_pair = bool(
        re.search(rf"\b{re.escape(surf_a)}\b\s+and\s+\b{re.escape(surf_b)}\b", low)
        or re.search(rf"\b{re.escape(surf_b)}\b\s+and\s+\b{re.escape(surf_a)}\b", low)
    )
    joined = bool(re.search(
        r"(?:connect|link|relat|associat|meet(?:ing)?\s+with|spoke|talk|call)", low
    ))
    return bool(both_between or and_pair or joined)


def _first_sentence(text: str, *, max_len: int = 220) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return ""
    m = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)
    first = m[0]
    if len(first) > max_len:
        first = first[: max_len - 1].rstrip() + "…"
    return first


def _temporal_anchor(turns: list[ConversationTurn]) -> tuple[str, str] | None:
    """(anchor_text, surface) for "what happened after that?" style sequels.

    Prefers an explicit date from the last user question, then the last
    assistant answer, then a well-known anchor noun ("the incident", "the
    FIR").  Returns (relation, anchor_phrase) or None.
    """
    candidates: list[str] = []
    user_turn = _last_user_turn(turns)
    assistant_turn = _last_assistant_turn(turns)
    for text in (user_turn, assistant_turn):
        if text:
            candidates.append(text)
    for text in candidates:
        m = _DATE_RE.search(text)
        if m:
            return m.group(1)
    for text in candidates:
        low = text.lower()
        for phrase in _ANCHOR_PHRASES:
            if phrase in low:
                return phrase
    return None


# ---------------------------------------------------------------------------
# The resolver
# ---------------------------------------------------------------------------


def resolve_followup(
    question: str,
    turns: list[ConversationTurn],
    *,
    known_names: Iterable[str] = (),
) -> Resolution:
    """Resolve ``question`` against the recent conversation.

    ``known_names`` are the display names of entities that exist in the
    *current* case (already case-scoped by the caller).  They make slot
    substitution precise; without them the resolver falls back to
    capitalization heuristics.
    """
    q_raw = (question or "").strip()
    if not q_raw:
        return Resolution(standalone_question=q_raw)
    if not turns:
        return Resolution(standalone_question=q_raw)

    q_low = q_raw.lower()
    last_user = _last_user_turn(turns)

    # --- 1. "What about X?" / "and X?" -------------------------------------
    topic: str | None = None
    kind = ""
    m = _WHAT_ABOUT_RE.match(q_raw)
    if m:
        topic = _strip_qmark(m.group(1))
        kind = "what_about"
    else:
        m2 = _TOPIC_FRAGMENT_RE.match(q_raw)
        if m2:
            frag = _strip_qmark(m2.group(1))
            # Guard: "then?" handled by temporal sequel; pronoun fragments by pronouns.
            if frag and not _PRONOUN_RE.search(frag) and len(frag.split()) <= 5:
                topic = frag
                kind = "topic_fragment"
    if topic:
        topic_core = re.sub(r"^(?:the|a|an)\s+", "", topic.strip(), flags=re.I).strip()
        if not topic_core:
            topic_core = topic.strip()
        # Pronoun-as-topic ("what about him?") is a pronoun resolution instead.
        if _PRONOUN_RE.fullmatch(topic_core.lower()):
            topic = None
        else:
            return _resolve_topic_followup(
                q_raw, topic_core, kind, turns, last_user, known_names
            )

    # --- 2. Temporal sequels ------------------------------------------------
    if _TEMPORAL_SEQUEL_RE.search(q_low):
        anchor = _temporal_anchor(turns)
        relation = "before" if re.search(r"\bbefore\b|\bcame\s+before\b", q_low) else "after"
        if anchor:
            standalone = f"What happened {relation} {anchor}?"
            return Resolution(
                standalone_question=standalone,
                is_followup=True,
                kind="temporal_anchor",
                note=f"anchor={anchor!r}",
            )
        # No anchor resolvable — still a follow-up (keeps it case-scoped),
        # and the temporal engine gets the question as-is.
        return Resolution(
            standalone_question=q_raw,
            is_followup=True,
            kind="temporal_anchor",
            note="no-anchor",
        )

    # --- 3. Document anaphora ----------------------------------------------
    if _DOCUMENT_ANAPHORA_RE.search(q_low):
        subject = None
        if last_user:
            slots = _known_name_slots(last_user, known_names) or _capitalized_slots(last_user)
            if slots:
                subject = slots[0]
        if subject:
            standalone = f"Which case documents mention {subject}?"
        else:
            topic_anchor = _topic_slot(last_user or "") or _attribute_phrase(last_user or "")
            if topic_anchor:
                standalone = f"Which case documents mention {topic_anchor}?"
            else:
                first = _first_sentence(_last_assistant_turn(turns) or "")
                standalone = (
                    f"Which case documents support the statement: {first}"
                    if first
                    else q_raw
                )
        return Resolution(
            standalone_question=standalone,
            is_followup=True,
            kind="document_reference",
        )

    # --- 4. Pronoun coreference --------------------------------------------
    if _PRONOUN_RE.search(q_raw):
        antecedent = _pronoun_antecedent(turns, known_names)
        if antecedent:
            standalone = _substitute_pronouns(q_raw, antecedent)
            return Resolution(
                standalone_question=standalone,
                is_followup=True,
                kind="pronoun",
                note=f"antecedent={antecedent!r}",
            )
        # Ambiguous or unknown antecedent: keep the question but mark as a
        # follow-up so downstream history coreference (entity-key level)
        # still applies and the GK gate never fires.
        return Resolution(
            standalone_question=q_raw,
            is_followup=True,
            kind="pronoun",
            note="unresolved-antecedent",
        )

    # --- 5. Bare elliptical prompts ("why?", "how?", "then?") ---------------
    m3 = _ELLIPTICAL_RE.match(q_raw)
    if m3:
        anchor_text = last_user or ""
        referent = _first_sentence(_last_assistant_turn(turns) or "")
        word = m3.group(1).capitalize()
        if referent:
            standalone = f"{word} — explain the following in more detail: {referent}"
        elif anchor_text:
            standalone = f"{word}: {anchor_text}"
        else:
            standalone = q_raw
        return Resolution(
            standalone_question=standalone,
            is_followup=True,
            kind="elliptical",
        )

    return Resolution(standalone_question=q_raw)


def _resolve_topic_followup(
    original: str,
    topic: str,
    kind: str,
    turns: list[ConversationTurn],
    last_user: str | None,
    known_names: Iterable[str],
) -> Resolution:
    """Substitute ``topic`` into the frame established by the last exchange."""
    if not last_user:
        return Resolution(
            standalone_question=f"Tell me about {topic} in this case.",
            is_followup=True,
            kind=kind,
            note="no-prior-frame",
        )

    slots = _known_name_slots(last_user, known_names)
    slot_source = "known"
    if not slots:
        slots = _capitalized_slots(last_user)
        slot_source = "capitalized"

    # The previous question named two entities ("How is A connected to B?")
    # — substituting the new topic for either is a coin flip.  Ask instead.
    if len(slots) >= 2 and _is_pair_frame(last_user, slots):
        surface_a = _surface_for_slot(last_user, slots[0])
        surface_b = _surface_for_slot(last_user, slots[1])
        if surface_a and surface_b:
            first_frame = last_user.replace(
                surface_b, _apply_preserving_case(surface_b, topic), 1
            )
            second_frame = last_user.replace(
                surface_a, _apply_preserving_case(surface_a, topic), 1
            )
            clarify = (
                f"Do you mean: \"{_strip_qmark(first_frame)}?\" "
                f"or \"{_strip_qmark(second_frame)}?\""
            )
            return Resolution(
                standalone_question=original,
                is_followup=True,
                kind=kind,
                needs_clarification=True,
                clarification=clarify,
                note="ambiguous-pair-substitution",
            )

    if slots:
        slot = slots[0]
        if slot.lower().split()[0] == topic.lower().split()[0]:
            # "What about Rahul?" right after a Rahul question: re-ask the
            # same frame (idempotent) rather than producing nonsense.
            return Resolution(
                standalone_question=last_user,
                is_followup=True,
                kind=kind,
                note="same-slot",
            )
        surface = _surface_for_slot(last_user, slot) or slot
        replacement = _apply_preserving_case(surface, topic)
        standalone = re.sub(
            rf"(?<![A-Za-z0-9]){re.escape(surface)}(?![A-Za-z0-9])",
            replacement, last_user, count=1, flags=re.I,
        )
        return Resolution(
            standalone_question=standalone,
            is_followup=True,
            kind=kind,
            note=f"slot={slot!r}<-{topic!r} ({slot_source})",
        )

    # No entity slot — try document/topic substitution ("the FIR" -> "the CDR").
    old_topic = _topic_slot(last_user)
    if old_topic:
        replacement = _apply_preserving_case(old_topic, topic)
        standalone = re.sub(
            rf"\b{re.escape(old_topic)}\b", replacement, last_user, count=1, flags=re.I
        )
        return Resolution(
            standalone_question=standalone,
            is_followup=True,
            kind=kind,
            note=f"topic-slot={old_topic!r}<-{topic!r}",
        )

    # Attribute frame without a resolvable name slot: reapply the attribute.
    attr = _attribute_phrase(last_user)
    if attr:
        standalone = f"What is the {attr} of {topic}?"
        return Resolution(
            standalone_question=standalone,
            is_followup=True,
            kind=kind,
            note=f"attribute-frame={attr!r}",
        )

    # Nothing to reuse: the safest reading is a scoped entity overview.
    return Resolution(
        standalone_question=f"Tell me about {topic} in this case.",
        is_followup=True,
        kind=kind,
        note="topic-overview",
    )


def _pronoun_antecedent(
    turns: list[ConversationTurn], known_names: Iterable[str]
) -> str | None:
    """The entity the pronouns point at: last case entity mentioned."""
    for turn in reversed(turns):
        slots = _known_name_slots(turn.content, known_names)
        if slots:
            return slots[0]
    # Fall back to capitalized spans in the most recent user question.
    last_user = _last_user_turn(turns)
    if last_user:
        caps = _capitalized_slots(last_user)
        if caps:
            return caps[0]
    return None


def _substitute_pronouns(question: str, name: str) -> str:
    """Replace gender/number pronouns with the resolved entity name."""
    possessive = f"{name}'s"
    out = question
    out = re.sub(r"\bthat\s+person\b|\bthis\s+person\b|\bthat\s+guy\b", name, out, flags=re.I)
    out = re.sub(r"\bthe\s+same\s+person\b", name, out, flags=re.I)
    out = re.sub(r"\b(his|her|hers|their|theirs)\b", possessive, out, flags=re.I)
    out = re.sub(r"\b(he|she|they)\b", name, out, flags=re.I)
    out = re.sub(r"\b(him|her|them)\b", name, out, flags=re.I)
    return out


# ---------------------------------------------------------------------------
# General-knowledge gate
# ---------------------------------------------------------------------------

#: Vocabulary that anchors a question to the investigation — its presence
#: always forces the case pipeline even when the shape looks general.
_CASE_VOCAB_RE = re.compile(
    r"\b(fir|first information report|accused|suspect(?:s)?|witness(?:es)?|victim(?:s)?|"
    r"complainant|chargesheet|charge sheet|arrest|remand|bail|"
    r"this case|the case|our case|in the case|case file|case records?|"
    r"evidence|cdr|call records?|call detail|bank statements?|transactions?|"
    r"investigating officer|io\b|forensic|seizure|raid|"
    r"the incident|the crime|the accused|the suspect|the victim|"
    r"interrogation|statement under|section \d|ipc\b|crpc\b|bnss?\b|bsa\b)\b",
    re.I,
)

#: Phrases whose answer can only come from world knowledge, never from a
#: case file.  Kept specific: a false positive here would answer a case
#: question from general knowledge, which is the worse failure.
_GENERAL_KNOWLEDGE_RE = re.compile(
    r"\b("
    r"capital of|president of|prime minister of|chief minister of|governor of|"
    r"population of|currency of|national (?:animal|bird|flower|anthem|song|game|fruit)|"
    r"how many (?:continents|planets|oceans|countries|states|union territories|days|weeks)|"
    r"largest (?:country|state|city|planet|ocean|desert)|smallest (?:country|state|planet)|"
    r"longest (?:river|bridge|highway)|highest (?:mountain|peak|waterfall)|deepest ocean|"
    r"boiling point|freezing point|speed of light|speed of sound|"
    r"square root of|cube root of|factorial of|"
    r"meaning of|definition of|full form of|abbreviation of|stands for\b(?!.*case)|"
    r"synonym (?:of|for)|antonym (?:of|for)|translate|"
    r"who (?:wrote|invented|discovered|composed|painted|founded)|author of|"
    r"in which year did|when did world war|"
    r"first (?:president|prime minister|person|man) (?:of|on|in)|"
    r"father of (?:the nation|the constitution|)|"
    r"chemical (?:symbol|formula) (?:of|for)|atomic number of"
    r")",
    re.I,
)

#: A pure arithmetic question ("what is 14 * 27?", "calculate 18% of 450").
_MATH_RE = re.compile(
    r"^\s*(?:what\s+is|calculate|compute|solve|evaluate)?\s*"
    r"[\d(]\s*[\d\s.()+\-*/x×^%]*[-+*/x×^]\s*[\d\s.()+%]+\s*[?=]?\s*$",
    re.I,
)
_PERCENT_RE = re.compile(
    r"^\s*(?:what\s+is|calculate|compute)?\s*\d+(?:\.\d+)?\s*%\s*of\s*[\d.,]+\s*\??\s*$",
    re.I,
)


def is_general_knowledge_question(
    question: str,
    *,
    is_followup: bool,
    matched_case_names: Iterable[str] = (),
) -> bool:
    """Whether the question is answerable from world knowledge, not the case.

    Conservative by design — every doubt goes to the case pipeline:

    * a follow-up is never general knowledge (its meaning lives in the
      conversation, which is about the case);
    * a question that names a case entity is never general knowledge;
    * a question using investigation vocabulary is never general knowledge;
    * otherwise, an explicit world-knowledge shape or a pure arithmetic
      expression marks the question as general.
    """
    q = (question or "").strip()
    if not q or is_followup:
        return False
    if any(str(n).strip() for n in matched_case_names):
        return False
    if _CASE_VOCAB_RE.search(q):
        return False
    if _GENERAL_KNOWLEDGE_RE.search(q):
        return True
    if _MATH_RE.match(q) or _PERCENT_RE.match(q):
        return True
    return False


# ---------------------------------------------------------------------------
# Prompt context
# ---------------------------------------------------------------------------


def conversation_for_prompt(
    turns: list[ConversationTurn], *, max_turns: int = 6
) -> list[dict[str, str]]:
    """Recent turns, shaped for inclusion in the reasoning prompt.

    The turns are already bounded and sanitized by :func:`normalize_history`;
    they are placed in the prompt as *data* so the model can interpret "that"
    and "then" — they never become instructions, and they never widen
    retrieval beyond the case boundary.
    """
    window = turns[-max_turns:]
    return [{"role": t.role, "content": t.content} for t in window]
