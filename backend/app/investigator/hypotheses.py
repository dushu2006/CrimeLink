"""Hypotheses with mandatory contradiction search.

One association hypothesis per top entity pair (at most three pairs), each
carrying supporting evidence, contradicting evidence from seven always-run
contradiction rules, grounded innocent alternatives, and a strength rating.
Convergence then asks whether independent streams agree: MODERATE or better
on two or more independent records with no major contradiction.

The baseline alternative — "the evidence is insufficient" — ships with
every hypothesis, so the weakest honest reading is never missing.
"""

from __future__ import annotations

from .evidence import make_evidence
from .labels import (
    CORROBORATED_LEAD,
    HYPOTHESIS,
    LEAD,
    STRENGTH_LEVELS,
    score_strength,
)
from .schemas import (
    EvidenceItem,
    Hypothesis,
    ObservationBlock,
    RelationshipFinding,
    ResolvedEntity,
    SuspiciousPattern,
)

#: At most this many entity pairs get hypotheses; the rest stay findings.
MAX_PAIRS = 3

#: The weakest honest reading, attached to every hypothesis.
INSUFFICIENT_BASELINE = (
    "The evidence is insufficient to support any reading — more records are needed."
)

_GROUNDED_ALTERNATIVES: tuple[tuple[str, str], ...] = (
    ("CALLED", "Routine social or business contact with an ordinary purpose."),
    ("TRANSFER_TO", "Legitimate money movement (family support, loan, payment)."),
    ("COLOCATION", "Overlapping routines in shared spaces (home, work, commute)."),
    ("cross-case", "Distinct roles in each case, or incomplete case attribution."),
    ("social", "Online adjacency with no real-world association."),
)


def _pair_docs(items: list) -> set[str]:
    docs: set[str] = set()
    for item in items:
        for evidence in list(item.evidence if hasattr(item, "evidence") else []) + list(
            getattr(item, "supporting", []) or []
        ):
            for pointer in evidence.provenance or []:
                if pointer.doc_id:
                    docs.add(pointer.doc_id)
    return docs


def _rule_single_source(pair_docs: set[str]) -> list[EvidenceItem]:
    if len(pair_docs) == 1:
        return [
            make_evidence(
                "record",
                "Every supporting record comes from one document: a single origin.",
                label=HYPOTHESIS,
                stance="contradicts",
            )
        ]
    return []


def _rule_dismissed_conflict(
    pair_keys: set[str], dismissed: set[str], notes: dict[str, str]
) -> list[EvidenceItem]:
    hits = [
        note
        for sig, note in notes.items()
        if sig in dismissed and any(key in sig for key in pair_keys)
    ]
    if not hits:
        return []
    return [
        make_evidence(
            "record",
            f"A reviewer already set aside this combination: {hits[0]}",
            label=HYPOTHESIS,
            stance="contradicts",
        )
    ]


def _rule_benign_relationship(relationships: list[RelationshipFinding]) -> list[EvidenceItem]:
    for relationship in relationships:
        if relationship.kind == "coincidental" and "only" in relationship.title:
            return [
                make_evidence(
                    "relationship",
                    f"Known-benign framing exists: {relationship.title}.",
                    label=HYPOTHESIS,
                    stance="contradicts",
                )
            ]
    return []


def _rule_identity_open(entities: list[ResolvedEntity]) -> list[EvidenceItem]:
    out: list[EvidenceItem] = []
    for entity in entities:
        if not entity.resolved:
            out.append(
                make_evidence(
                    "record",
                    f"{entity.display_name} is unresolved: the hypothesis names a ghost.",
                    label=HYPOTHESIS,
                    stance="contradicts",
                )
            )
        elif entity.confidence < 0.85:
            out.append(
                make_evidence(
                    "record",
                    f"{entity.display_name} is provisionally matched (confidence {entity.confidence}).",
                    label=HYPOTHESIS,
                    stance="contradicts",
                )
            )
    return out


def _rule_low_confidence_only(relationships: list[RelationshipFinding]) -> list[EvidenceItem]:
    kinds = {relationship.kind for relationship in relationships}
    if kinds and kinds <= {"coincidental"}:
        return [
            make_evidence(
                "relationship",
                "Only low-confidence or benign-only links support this reading.",
                label=HYPOTHESIS,
                stance="contradicts",
            )
        ]
    return []


def _rule_no_shared_records(pair_docs: set[str], indirect_only: bool) -> list[EvidenceItem]:
    if indirect_only and not pair_docs:
        return [
            make_evidence(
                "record",
                "No single record names both; the link is purely inferential.",
                label=HYPOTHESIS,
                stance="contradicts",
            )
        ]
    return []


def _rule_thin_support(supporting: list[EvidenceItem]) -> list[EvidenceItem]:
    if not supporting:
        return [
            make_evidence(
                "record",
                "Nothing in the evidence supports this reading — it is speculation.",
                label=HYPOTHESIS,
                stance="contradicts",
            )
        ]
    return []


def _grounded_alternatives(
    relationships: list[RelationshipFinding], patterns: list[SuspiciousPattern]
) -> list[str]:
    text = " ".join(
        [relationship.title for relationship in relationships]
        + [pattern.kind for pattern in patterns]
    )
    alternatives = [
        alternative for needle, alternative in _GROUNDED_ALTERNATIVES if needle in text
    ]
    alternatives.append(INSUFFICIENT_BASELINE)
    return alternatives


def _strength_label(strength: str) -> str:
    if strength == "STRONG":
        return CORROBORATED_LEAD
    if strength == "MODERATE":
        return LEAD
    return HYPOTHESIS


def build_hypotheses(
    pairs: list[tuple[ResolvedEntity, ResolvedEntity]],
    relationships: list[RelationshipFinding],
    patterns: list[SuspiciousPattern],
    *,
    dismissed_signatures: set[str] | None = None,
    dismissed_notes: dict[str, str] | None = None,
) -> list[Hypothesis]:
    """Build one association hypothesis per pair (at most three)."""
    dismissed_signatures = dismissed_signatures or set()
    dismissed_notes = dismissed_notes or {}
    hypotheses: list[Hypothesis] = []

    for index, (first, second) in enumerate(pairs[:MAX_PAIRS]):
        names = {first.display_name, second.display_name}
        pair_keys = {first.canonical_id, second.canonical_id}
        pair_relationships = [
            relationship
            for relationship in relationships
            if names & set(relationship.entities)
        ]
        pair_patterns = [
            pattern
            for pattern in patterns
            if not pattern.excluded and pair_keys & set(pattern.entity_keys)
        ]
        supporting: list[EvidenceItem] = []
        for relationship in pair_relationships:
            supporting.extend(relationship.evidence)
        for pattern in pair_patterns:
            supporting.extend(pattern.evidence)
        docs = _pair_docs(pair_relationships) | _pair_docs(pair_patterns)

        # Mandatory contradiction search: all seven rules, every hypothesis.
        contradicting: list[EvidenceItem] = []
        contradicting.extend(_rule_single_source(docs))
        contradicting.extend(
            _rule_dismissed_conflict(pair_keys, dismissed_signatures, dismissed_notes)
        )
        contradicting.extend(_rule_benign_relationship(pair_relationships))
        contradicting.extend(_rule_identity_open([first, second]))
        contradicting.extend(_rule_low_confidence_only(pair_relationships))
        indirect_only = pair_relationships and all(
            relationship.kind == "indirect" for relationship in pair_relationships
        )
        contradicting.extend(_rule_no_shared_records(docs, bool(indirect_only)))
        contradicting.extend(_rule_thin_support(supporting))
        checks_run, checks_hit = 7, sum(
            1
            for group in (
                _rule_single_source(docs),
                _rule_dismissed_conflict(pair_keys, dismissed_signatures, dismissed_notes),
                _rule_benign_relationship(pair_relationships),
                _rule_identity_open([first, second]),
                _rule_low_confidence_only(pair_relationships),
                _rule_no_shared_records(docs, bool(indirect_only)),
                _rule_thin_support(supporting),
            )
            if group
        )

        major = any("reviewer already set aside" in item.summary for item in contradicting)
        contradiction_level = "major" if (major or len(contradicting) >= 3) else (
            "minor" if contradicting else "none"
        )
        entity_certainty = (
            "resolved"
            if all(entity.resolved and entity.confidence >= 0.85 for entity in (first, second))
            else "provisional"
        )
        direct = any(
            relationship.kind in {"direct", "repeated"} for relationship in pair_relationships
        )
        strength, factors = score_strength(
            independent_sources=len(docs),
            corroborating_records=len(supporting),
            contradiction_level=contradiction_level,
            entity_certainty=entity_certainty,
            direct=direct or len(docs) > 0,
            notes=[f"Contradiction search: {checks_run} checks run, {checks_hit} triggered."],
        )
        statement = (
            f"{first.display_name} and {second.display_name} are associated "
            f"({len(supporting)} supporting item(s), {len(contradicting)} contradicting)."
        )
        hypotheses.append(
            Hypothesis(
                id=f"H{index + 1}-association",
                statement=statement,
                entities=[first.display_name, second.display_name],
                supporting=supporting[:20],
                contradicting=contradicting[:20],
                innocent_alternatives=_grounded_alternatives(pair_relationships, pair_patterns),
                inference_label=_strength_label(strength),
                strength=strength,
                strength_factors=factors,
                analysis=ObservationBlock(
                    observation=(
                        f"{len(supporting)} supporting item(s) across {len(docs)} document(s); "
                        f"{len(contradicting)} contradicting item(s)."
                    ),
                    interpretation=(
                        "Association fits the supporting side; each contradicting item "
                        "is a reason the fit may be wrong."
                    ),
                    assessment=(
                        f"Contradiction search ran {checks_run} checks, {checks_hit} triggered. "
                        f"Strength {strength}; treat as {strength.lower()} until the "
                        "contradicting side is cleared."
                    ),
                ),
            )
        )
    return hypotheses


def build_convergence(hypotheses: list[Hypothesis]) -> dict:
    """Do independent streams agree? MODERATE+ on ≥2 records, no major hit."""
    if not hypotheses:
        return {
            "converges": False,
            "reading": None,
            "top_hypothesis": None,
            "top_strength": "INSUFFICIENT",
            "streams": [],
            "stream_count": 0,
            "note": "No hypotheses were formed: nothing to converge.",
        }
    rank = {level: index for index, level in enumerate(STRENGTH_LEVELS)}
    top = max(hypotheses, key=lambda item: rank.get(item.strength, 0))
    streams = sorted(_pair_docs([top]))
    major = top.strength_factors.contradiction_level == "major"
    readable = top.strength in ("MODERATE", "STRONG")
    converges = readable and len(streams) >= 2 and not major
    if converges:
        note = (
            f"Evidence converges on {top.id}: {top.strength} across "
            f"{len(streams)} independent records."
        )
    elif major:
        note = f"Evidence does not converge: {top.id} carries a major contradiction."
    elif len(streams) < 2:
        note = "Evidence does not converge: top reading rests on a single record."
    else:
        note = f"Evidence does not converge: top reading is only {top.strength}."
    return {
        "converges": converges,
        "reading": top.statement if converges else None,
        "top_hypothesis": top.id,
        "top_strength": top.strength,
        "streams": streams,
        "stream_count": len(streams),
        "note": note,
    }
