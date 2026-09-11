"""Next-move recommendations: lawful, identity-first, deep-linked.

The ordering rule is fixed: establish *who* before chasing *what*.
Identity steps come first, then corroboration of the top hypothesis, then
gap-closing record work, then review hygiene. The sort is stable on rank
alone so insertion order (identity-first) always wins.

Nothing here is coercive: no arrest, detention, interrogation, search, or
seizure. Steps ask for records, checks, and reviews — the investigator and
the magistrate decide the rest.
"""

from __future__ import annotations

from .schemas import (
    DataGap,
    Hypothesis,
    NextStep,
    ResolvedEntity,
    SuspiciousPattern,
)

#: At most this many recommendations per answer.
MAX_STEPS = 10

_RANK = {"high": 0, "medium": 1, "low": 2}


def build_next_steps(
    entities: list[ResolvedEntity],
    gaps: list[DataGap],
    hypotheses: list[Hypothesis],
    patterns: list[SuspiciousPattern],
    case_ids: list[str],
) -> list[NextStep]:
    """Recommend follow-ups, identity first (stable rank-only sort)."""
    steps: list[NextStep] = []

    for entity in entities:
        if not entity.resolved:
            steps.append(
                NextStep(
                    action=f"Establish the identity of {entity.display_name}.",
                    rationale=(
                        "Nothing can be concluded about an unmatched mention; "
                        "identity work precedes all analysis."
                    ),
                    priority="high",
                    links={"entities": [entity.display_name], "case_ids": list(case_ids)},
                )
            )
        elif entity.confidence < 0.85:
            steps.append(
                NextStep(
                    action=f"Confirm the provisional match for {entity.display_name}.",
                    rationale=f"Currently matched ({entity.matched_by}) at confidence {entity.confidence}.",
                    priority="high",
                    links={"entities": [entity.display_name], "case_ids": list(case_ids)},
                )
            )

    for pattern in patterns:
        if pattern.kind == "ER_SIGNAL" and not pattern.excluded:
            steps.append(
                NextStep(
                    action=f"Resolve the open identity proposal: {pattern.title}.",
                    rationale="A pending merge/unmerge decision blocks firm conclusions.",
                    priority="high",
                    links={"entities": list(pattern.entities), "case_ids": list(case_ids)},
                )
            )

    live = [hypothesis for hypothesis in hypotheses]
    if live:
        top = max(live, key=lambda item: {"INSUFFICIENT": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}.get(item.strength, 0))
        steps.append(
            NextStep(
                action=f"Corroborate or rule out {top.id}: {top.statement[:160]}",
                rationale=f"Top reading stands at {top.strength}; test it against a fresh record.",
                priority="high" if top.strength in ("MODERATE", "STRONG") else "medium",
                links={"entities": list(top.entities), "case_ids": list(case_ids)},
            )
        )

    for gap in gaps:
        if gap.category == "missing-source":
            steps.append(
                NextStep(
                    action=f"Obtain {gap.description.split(' records')[0].replace('No ', '')} records.",
                    rationale=gap.what_would_help,
                    priority="medium",
                    links={"case_ids": list(gap.cases or case_ids)},
                )
            )
        elif gap.category == "single-source":
            steps.append(
                NextStep(
                    action="Find an independent record for the single-source reading.",
                    rationale=gap.what_would_help,
                    priority="medium",
                    links={"entities": list(gap.entities), "case_ids": list(gap.cases or case_ids)},
                )
            )

    cross_case = [pattern for pattern in patterns if pattern.kind.startswith("CROSS_CASE") and not pattern.excluded]
    if cross_case:
        steps.append(
            NextStep(
                action="Compare the linked case files side by side.",
                rationale="Cross-case links need file-level comparison before they mean anything.",
                priority="medium",
                links={"case_ids": sorted({case for pattern in cross_case for case in pattern.cases})},
            )
        )

    if not steps:
        steps.append(
            NextStep(
                action="Ask a narrower question about one named entity.",
                rationale="The current question produced no foothold; narrow the scope.",
                priority="low",
                links={"case_ids": list(case_ids)},
            )
        )

    steps.sort(key=lambda step: _RANK.get(step.priority, 1))
    return steps[:MAX_STEPS]
