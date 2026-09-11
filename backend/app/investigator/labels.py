"""Inference labels and evidence-strength scoring.

Six labels, one vocabulary, used by every investigator surface:

* ``FACT`` — directly supported by provided evidence.
* ``CORROBORATED_LEAD`` — supported by two or more independent sources.
* ``LEAD`` — supported by a single source; worth following.
* ``HYPOTHESIS`` — a testable reading that outruns its evidence.
* ``COINCIDENCE`` — explainable without a causal link; decoys land here.
* ``DATA_GAP`` — the evidence needed to decide is missing.

Strength (STRONG / MODERATE / WEAK / INSUFFICIENT) is scored from counts
and downgrades only — never by a model — and every rating carries its
auditable :class:`StrengthFactors`.
"""

from __future__ import annotations

from .schemas import StrengthFactors

FACT = "FACT"
CORROBORATED_LEAD = "CORROBORATED_LEAD"
LEAD = "LEAD"
HYPOTHESIS = "HYPOTHESIS"
COINCIDENCE = "COINCIDENCE"
DATA_GAP = "DATA_GAP"

INFERENCE_LABELS: tuple[str, ...] = (
    FACT,
    CORROBORATED_LEAD,
    LEAD,
    HYPOTHESIS,
    COINCIDENCE,
    DATA_GAP,
)

#: Older gateway vocabulary mapped onto the six labels so mixed surfaces
#: (engine findings, AI answers) render consistently in the workspace.
LEGACY_LABEL_MAP: dict[str, str] = {
    "FACT": FACT,
    "CORROBORATED": CORROBORATED_LEAD,
    "CORROBORATED_LEAD": CORROBORATED_LEAD,
    "LEAD": LEAD,
    "INFERENCE": HYPOTHESIS,
    "HYPOTHESIS": HYPOTHESIS,
    "COINCIDENCE": COINCIDENCE,
    "UNVERIFIED": LEAD,
    "DATA_GAP": DATA_GAP,
}

STRENGTH_LEVELS: tuple[str, ...] = ("INSUFFICIENT", "WEAK", "MODERATE", "STRONG")


def normalize_label(value: str | None) -> str:
    """Fold any legacy/unknown label onto the six-label vocabulary."""
    if not value:
        return LEAD
    return LEGACY_LABEL_MAP.get(str(value).strip().upper(), LEAD)


def score_strength(
    *,
    independent_sources: int,
    corroborating_records: int = 0,
    contradiction_level: str = "none",
    entity_certainty: str = "resolved",
    direct: bool = True,
    temporal_relevance: str = "unknown",
    consistency: str = "unknown",
    notes: list[str] | None = None,
) -> tuple[str, StrengthFactors]:
    """Score evidence strength from counts, then apply downgrades.

    Base rating comes from independent-source count alone (3+ STRONG,
    2 MODERATE, 1 WEAK, 0 INSUFFICIENT). A major contradiction steps the
    rating down one level; an unresolved entity caps it at WEAK; indirect
    evidence caps it at MODERATE. The returned factors record every input
    so the rating can be audited later.
    """
    count = max(0, int(independent_sources))
    if count >= 3:
        strength = "STRONG"
    elif count == 2:
        strength = "MODERATE"
    elif count == 1:
        strength = "WEAK"
    else:
        strength = "INSUFFICIENT"

    factor_notes = list(notes or [])
    level = STRENGTH_LEVELS.index(strength)
    if contradiction_level == "major" and level > 0:
        level -= 1
        factor_notes.append("Downgraded one level: major contradiction present.")
    strength = STRENGTH_LEVELS[level]
    if entity_certainty != "resolved" and STRENGTH_LEVELS.index(strength) > STRENGTH_LEVELS.index(
        "WEAK"
    ):
        strength = "WEAK"
        factor_notes.append("Capped at WEAK: entity identity is not resolved.")
    if not direct and STRENGTH_LEVELS.index(strength) > STRENGTH_LEVELS.index("MODERATE"):
        strength = "MODERATE"
        factor_notes.append("Capped at MODERATE: evidence is indirect.")
    return strength, StrengthFactors(
        independent_sources=count,
        corroborating_records=max(0, int(corroborating_records)),
        temporal_relevance=temporal_relevance,
        directness="direct" if direct else "indirect",
        consistency=consistency,
        contradiction_level=contradiction_level,
        entity_certainty=entity_certainty,
        notes=factor_notes,
    )
