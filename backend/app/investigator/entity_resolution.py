"""Question mentions become canonical graph ids — or honest DATA_GAPs.

Resolution order is fixed and conservative:

1. **Hard identifiers** (phone, Aadhaar, PAN, account, plate): exact match
   on node properties, confidence 1.0.
2. **Merge-aware canonicalization**: a node absorbed by ``MERGED_INTO``
   resolves to its canonical target, transparently.
3. **Names**: exact name first, then aliases, then token overlap — ranked
   the way the gateway ranks candidates (exact beats alias beats partial).
4. **Open alias proposals** (``POTENTIAL_ALIAS``) are surfaced with an
   ambiguity note and capped at 0.6: a proposal is never silently merged.
5. **No match**: ``resolved=False`` with an ambiguity note; the
   orchestrator turns these into DATA_GAPs. Nothing is ever invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.domain.models import CaseGraphSnapshot

from .schemas import ResolvedEntity

#: Confidence ceiling for open alias proposals: suggestive, not decisive.
ALIAS_PROPOSAL_CAP = 0.6

#: Node property keys treated as hard identifiers (exact match only).
IDENTIFIER_KEYS: tuple[str, ...] = (
    "aadhaar",
    "aadhar",
    "pan",
    "phone",
    "phone_number",
    "number",
    "msisdn",
    "account",
    "account_number",
    "plate",
    "vehicle_number",
    "email",
    "device_id",
    "imei",
)

_NAME_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "between",
        "any",
        "what",
        "who",
        "whom",
        "whose",
        "which",
        "is",
        "are",
        "was",
        "were",
        "there",
        "their",
        "about",
        "connection",
        "connections",
        "link",
        "links",
        "linked",
        "related",
        "relation",
        "relationship",
        "case",
        "show",
        "tell",
        "find",
        "please",
    }
)


#: Words that *open* a question and would otherwise be glued onto the first
#: name ("Are Harish Varma and … connected?" → "Are Harish Varma"). A leading
#: token is only dropped at a sentence boundary, so a name that legitimately
#: starts with one of these words is left alone mid-sentence, and a one-word
#: remainder is still subject to the usual stopword filter.
_LEADING_QUESTION_WORDS = frozenset(
    {
        "are", "is", "was", "were", "am", "be", "been",
        "do", "does", "did", "has", "have", "had",
        "can", "could", "should", "would", "will", "shall", "may", "might", "must",
        "who", "whom", "whose", "what", "when", "where", "why", "how", "which",
        "tell", "show", "find", "list", "give", "check", "explain",
    }
)

#: Characters (other than the start of the text) that end a sentence, so the
#: next capitalized word opens a new one.
_SENTENCE_END = ".?!:;\n•-—"


@dataclass
class PendingAlias:
    """An open identity proposal the resolver must flag, never apply."""

    source_key: str
    target_key: str
    note: str = ""
    extra: dict = field(default_factory=dict)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().casefold())


def _opens_sentence(question: str, start: int) -> bool:
    """True when ``start`` begins the text or follows sentence punctuation."""
    index = start - 1
    while index >= 0 and question[index].isspace():
        index -= 1
    return index < 0 or question[index] in _SENTENCE_END


def _strip_leading_question_word(question: str, run: str, start: int) -> str:
    """Drop a sentence-opening auxiliary/interrogative from a capitalized run.

    "Are Harish Varma" is a question and a name, not a three-word person. The
    ordinary lowercase form ("Is there a connection between Harish Varma and
    …") never produced the artifact because the name did not sit directly after
    the opening word; this keeps the two shapes consistent.
    """
    tokens = run.split()
    if len(tokens) < 2 or not _opens_sentence(question, start):
        return run
    while len(tokens) > 1 and tokens[0].casefold() in _LEADING_QUESTION_WORDS:
        tokens = tokens[1:]
    return " ".join(tokens)


def extract_mentions(question: str, *, limit: int = 6) -> list[str]:
    """Pull candidate entity mentions out of a question, deterministically.

    Quoted spans win first (the investigator named them exactly); then
    capitalized multiword runs. Single generic words never qualify.
    """
    mentions: list[str] = []
    seen: set[str] = set()

    def _keep(raw: str) -> None:
        cleaned = _fold_possessive(raw.strip(" \t\"'“”‘’,.;:()"))
        if len(cleaned) < 2 or len(mentions) >= limit:
            return
        key = _normalize(cleaned)
        if not key or key in seen or key in _NAME_STOPWORDS:
            return
        seen.add(key)
        mentions.append(cleaned)

    for match in re.finditer(r"([\"“”'‘’])([^\"“”'‘’]{2,60})\1", question):
        opener = match.group(1)
        preceding = question[match.start() - 1] if match.start() else ""
        if opener in "'’" and (preceding.isalnum() or preceding in "_’'"):
            # An apostrophe inside a word ("Iyer's phone") is punctuation, not a
            # quote: treating it as one invents a mention out of the possessive.
            continue
        _keep(match.group(2))
    for match in re.finditer(r"\b([A-Z][\w'.-]*(?:\s+[A-Z][\w'.-]*){1,3})", question):
        _keep(_strip_leading_question_word(question, match.group(1), match.start()))
    return mentions


def _node_names(node: Any) -> list[str]:
    props = node.properties or {}
    names = [node.name] if getattr(node, "name", None) else []
    for key in ("name", "full_name", "display_name"):
        value = props.get(key)
        if value and value not in names:
            names.append(str(value))
    return [name for name in names if name]


def _node_aliases(node: Any) -> list[str]:
    props = node.properties or {}
    aliases: list[str] = []
    for key in ("aliases", "alias", "aka", "nickname", "known_as"):
        value = props.get(key)
        if isinstance(value, (list, tuple)):
            aliases.extend(str(item) for item in value if item)
        elif value:
            aliases.append(str(value))
    return aliases


def follow_merges(start_key: str, edges: list) -> tuple[str, list[str]]:
    """Follow ``MERGED_INTO`` edges transitively to the canonical node."""
    followed = [start_key]
    current = start_key
    targets = {
        edge.source_key: edge.target_key
        for edge in edges
        if getattr(edge, "rel_type", "") == "MERGED_INTO"
    }
    while current in targets and targets[current] not in followed:
        current = targets[current]
        followed.append(current)
    return current, followed


def _identifier_hit(mention: str, node: Any) -> str | None:
    wanted = _normalize(mention)
    props = node.properties or {}
    for key in IDENTIFIER_KEYS:
        value = props.get(key)
        if value is None:
            continue
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for candidate in candidates:
            if _normalize(str(candidate)) == wanted:
                return key
    return None


def _fold_possessive(text: str) -> str:
    """``Sana Iyer's`` is a name in a possessive, not a name of its own."""
    return re.sub(r"(?<=\w)['’]s\b", "", text)


def _name_score(mention: str, node: Any) -> tuple[float, str]:
    wanted = _normalize(_fold_possessive(mention))
    for name in _node_names(node):
        if _normalize(_fold_possessive(name)) == wanted:
            return 1.0, "name"
    for alias in _node_aliases(node):
        if _normalize(_fold_possessive(alias)) == wanted:
            return 0.9, "alias"
    wanted_tokens = {token for token in re.split(r"\W+", wanted) if token}
    for name in _node_names(node):
        name_tokens = {
            token for token in re.split(r"\W+", _normalize(_fold_possessive(name))) if token
        }
        if wanted_tokens and wanted_tokens <= name_tokens:
            return 0.75, "name-partial"
    return 0.0, "none"


def resolve_mention(
    mention: str,
    snapshot: CaseGraphSnapshot,
    *,
    pending_aliases: list[PendingAlias] | None = None,
) -> ResolvedEntity:
    """Resolve one mention against the in-scope snapshot."""
    pending_aliases = pending_aliases or []
    nodes = snapshot.nodes or {}

    best_key: str | None = None
    best_score = 0.0
    best_how = "none"
    for key, node in nodes.items():
        hit = _identifier_hit(mention, node)
        if hit:
            best_key, best_score, best_how = key, 1.0, hit
            break
        score, how = _name_score(mention, node)
        if score > best_score:
            best_key, best_score, best_how = key, score, how

    if best_key is None or best_score <= 0.0:
        return ResolvedEntity(
            canonical_id=f"unresolved:{_normalize(mention)}",
            label="UNKNOWN",
            display_name=mention,
            confidence=0.0,
            matched_by="none",
            resolved=False,
            ambiguity_note=f"No in-scope record matches {mention!r}; treated as a data gap.",
        )

    canonical_key, followed = follow_merges(best_key, list(snapshot.edges or []))
    node = nodes.get(canonical_key, nodes[best_key])
    display = node.name or mention
    aliases = _node_aliases(node)

    proposal_note: str | None = None
    for proposal in pending_aliases:
        if proposal.source_key in (best_key, canonical_key) or proposal.target_key in (
            best_key,
            canonical_key,
        ):
            other = (
                proposal.target_key
                if proposal.source_key in (best_key, canonical_key)
                else proposal.source_key
            )
            other_node = nodes.get(other)
            other_name = other_node.name if other_node is not None else other
            proposal_note = (
                f"Open identity proposal: {display!r} may be the same as "
                f"{other_name!r} ({proposal.note or 'awaiting review'}). "
                "Not merged: confidence capped."
            )
            best_score = min(best_score, ALIAS_PROPOSAL_CAP)
            break

    notes = [proposal_note] if proposal_note else []
    if len(followed) > 1:
        notes.append(f"Followed merge chain: {' → '.join(followed)}.")
    criminal_status = (node.properties or {}).get("criminal_status")

    return ResolvedEntity(
        canonical_id=canonical_key,
        label=getattr(node, "label", "UNKNOWN"),
        display_name=display,
        aliases=aliases,
        confidence=round(best_score, 3),
        matched_by=best_how,
        entity_keys=[canonical_key],
        criminal_status=str(criminal_status) if criminal_status else None,
        resolved=True,
        ambiguity_note=" ".join(notes) if notes else None,
    )


def resolve_mentions(
    mentions: list[str],
    snapshot: CaseGraphSnapshot,
    *,
    pending_aliases: list[PendingAlias] | None = None,
) -> list[ResolvedEntity]:
    """Resolve every mention; order and duplicates preserved for honesty."""
    return [
        resolve_mention(mention, snapshot, pending_aliases=pending_aliases)
        for mention in mentions
    ]
