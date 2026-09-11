"""Data gaps: missing evidence, reported honestly instead of filled in.

A gap names what is absent, which entities and cases it affects, and what
would help close it. Gaps never speculate about what the missing evidence
*would* say — that way lies invented testimony.
"""

from __future__ import annotations

from .labels import DATA_GAP
from .schemas import DataGap, Hypothesis, RelationshipFinding, ResolvedEntity

#: At most this many gaps per answer; the rest stay implied.
MAX_GAPS = 20

#: Source types whose absence is itself worth reporting, with guidance.
MISSING_SOURCE_HELP: dict[str, str] = {
    "FIR": "Obtain FIR records for the first-record account of the offence (who / what / where / when).",
    "CDR": "Obtain call-detail records to test contact hypotheses against actual traffic.",
    "FINANCIAL": "Obtain financial records (statements, transfers) to test money-movement readings.",
    "SOCIAL_MEDIA": "Obtain social-media captures where lawful; online adjacency alone proves nothing.",
    "CRIMINAL_HISTORY": "Pull stated criminal-history records rather than inferring antecedents.",
    "SURVEILLANCE": "Obtain surveillance records to test presence and movement claims.",
}


def build_gaps(
    entities: list[ResolvedEntity],
    doc_types: set[str],
    hypotheses: list[Hypothesis],
    relationships: list[RelationshipFinding],
    case_ids: list[str],
) -> list[DataGap]:
    """Collect every gap the current answer leaves open (capped at 20)."""
    gaps: list[DataGap] = []

    for entity in entities:
        if not entity.resolved:
            gaps.append(
                DataGap(
                    category="unresolved-entity",
                    description=f"{entity.display_name} matches no in-scope record.",
                    what_would_help=(
                        f"A hard identifier (phone, Aadhaar, PAN) or a confirming record "
                        f"naming {entity.display_name}."
                    ),
                    inference_label=DATA_GAP,
                    entities=[entity.display_name],
                    cases=list(case_ids),
                )
            )
        elif entity.confidence < 0.85:
            gaps.append(
                DataGap(
                    category="provisional-identity",
                    description=(
                        f"{entity.display_name} is only provisionally matched "
                        f"(confidence {entity.confidence})."
                    ),
                    what_would_help="A second identifying attribute to confirm the match.",
                    inference_label=DATA_GAP,
                    entities=[entity.display_name],
                    cases=list(case_ids),
                )
            )

    for source in sorted(MISSING_SOURCE_HELP):
        if source not in doc_types:
            gaps.append(
                DataGap(
                    category="missing-source",
                    description=f"No {source} records in the in-scope cases.",
                    what_would_help=MISSING_SOURCE_HELP[source],
                    inference_label=DATA_GAP,
                    entities=[],
                    cases=list(case_ids),
                )
            )

    for hypothesis in hypotheses:
        streams = hypothesis.strength_factors.independent_sources
        if streams <= 1:
            gaps.append(
                DataGap(
                    category="single-source",
                    description=f"{hypothesis.id} rests on {streams or 'no'} independent record(s).",
                    what_would_help=(
                        f"An independent record bearing on: {hypothesis.statement[:200]}"
                    ),
                    inference_label=DATA_GAP,
                    entities=list(hypothesis.entities),
                    cases=list(case_ids),
                )
            )

    resolved = [entity for entity in entities if entity.resolved]
    if len(resolved) >= 2 and not relationships:
        gaps.append(
            DataGap(
                category="missing-link",
                description="No records join the resolved entities to each other.",
                what_would_help="Contact, movement, or transaction records naming both sides.",
                inference_label=DATA_GAP,
                entities=[entity.display_name for entity in resolved],
                cases=list(case_ids),
            )
        )

    return gaps[:MAX_GAPS]
