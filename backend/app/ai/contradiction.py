"""Narrative contradiction detection — surfaces conflicts, never decides truth.

Two case records that place the same person in two different places at the same
time have a *documentary* problem, and an investigator needs to see it.  This
module turns that into an explicit object:

    {"type": "LOCATION_CONFLICT", "entity": "...", "time": "...",
     "claim_a": {...}, "claim_b": {...}, "sources": [...],
     "status": "UNRESOLVED"}

The pipeline is deterministic-first:

    RECORDS → CLAIMS → NORMALISATION → CONFLICT DETECTION → CONTRADICTION →
    SOURCE VALIDATION → ANSWER

A model may *propose* candidate narrative claims, but every candidate is
validated against the stored record it quotes before it can take part in
detection (:func:`app.ai.claims.validate_candidate_claims`), so the assistant
cannot invent a conflict.

The system deliberately stops at "potentially conflicting accounts".  It never
says which record is correct, because deciding that is an investigator's job
and would need an independently authoritative record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from app.ai.claims import (
    DIMENSION_AMOUNT,
    DIMENSION_EVENT,
    DIMENSION_LOCATION,
    DIMENSION_RELATIONSHIP,
    DIMENSION_ROLE,
    DIMENSION_STATUS,
    STATUS_GROUPS,
    Claim,
    claim_identity,
    format_timestamp,
    time_bucket,
)

CONTRADICTION_LOCATION = "LOCATION_CONFLICT"
CONTRADICTION_TIMELINE = "TIMELINE_CONFLICT"
CONTRADICTION_IDENTITY_ROLE = "IDENTITY_ROLE_CONFLICT"
CONTRADICTION_AMOUNT = "AMOUNT_CONFLICT"
CONTRADICTION_EVENT = "EVENT_DESCRIPTION_CONFLICT"
CONTRADICTION_RELATIONSHIP = "RELATIONSHIP_CONFLICT"
CONTRADICTION_STATUS = "STATUS_CONFLICT"

STATUS_UNRESOLVED = "UNRESOLVED"

#: Human-facing label for each conflict type.
CONTRADICTION_LABELS = {
    CONTRADICTION_LOCATION: "location conflict",
    CONTRADICTION_TIMELINE: "timeline conflict",
    CONTRADICTION_IDENTITY_ROLE: "identity / role conflict",
    CONTRADICTION_AMOUNT: "amount conflict",
    CONTRADICTION_EVENT: "event-description conflict",
    CONTRADICTION_RELATIONSHIP: "relationship conflict",
    CONTRADICTION_STATUS: "status conflict",
}

#: Role classes that cannot both describe the same person in the same case file.
_EXCLUSIVE_ROLE_CLASSES = {"ADVERSE", "NEUTRAL", "OFFICIAL"}

#: Status values that cannot both be current for the same case at the same time
#: (compared through :data:`app.ai.claims.STATUS_GROUPS`, so "pending" and
#: "under investigation" — the same family — are not a conflict).
_INCOMPATIBLE_STATUS = {
    frozenset({"ACTIVE", "CONCLUDED"}),
    frozenset({"ACTIVE", "CHARGED"}),
    frozenset({"CONCLUDED", "CHARGED"}),
}


@dataclass
class Contradiction:
    """Two claims that speak about the same thing and disagree."""

    contradiction_id: str
    type: str
    dimension: str
    entity_key: str
    entity_label: str
    time: str | None
    claim_a: Claim
    claim_b: Claim
    sources: list[str] = field(default_factory=list)
    status: str = STATUS_UNRESOLVED
    detail: str = ""

    @property
    def label(self) -> str:
        return CONTRADICTION_LABELS.get(self.type, "conflict")

    def as_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "type": self.type,
            "label": self.label,
            "dimension": self.dimension,
            "entity": self.entity_label,
            "entity_key": self.entity_key,
            "time": self.time,
            "time_display": format_timestamp(self.time),
            "claim_a": {
                "text": self.claim_a.text,
                "document_id": self.claim_a.document_id,
                "evidence_type": self.claim_a.evidence_type,
            },
            "claim_b": {
                "text": self.claim_b.text,
                "document_id": self.claim_b.document_id,
                "evidence_type": self.claim_b.evidence_type,
            },
            "sources": list(self.sources),
            "status": self.status,
            "detail": self.detail,
        }


def _group_key(claim: Claim, *, minutes: int) -> tuple[str, str, str]:
    """What two claims must agree on before their values can conflict."""
    if claim.dimension in (DIMENSION_LOCATION, DIMENSION_EVENT):
        return (claim.subject_key, claim.dimension, time_bucket(claim.time, minutes=minutes))
    if claim.dimension == DIMENSION_STATUS:
        # Statuses are only compared when both carry a time: "pending" early in
        # an investigation and "charges filed" later are not a conflict.
        return (claim.subject_key, claim.dimension, time_bucket(claim.time, minutes=1440))
    return (claim.subject_key, claim.dimension, "")


def _classify_value(claim: Claim) -> str:
    if claim.dimension == DIMENSION_ROLE:
        return str(claim.category or claim.value)
    return claim.object_key


def _display_value(claim: Claim) -> str:
    """The value as an investigator would read it, not the comparison key."""
    if claim.dimension == DIMENSION_ROLE:
        return str(claim.object_label or claim.value)
    return str(claim.object_label or claim.object_key)


def _conflict_type(left: Claim, right: Claim) -> str | None:
    dimension = left.dimension
    if dimension == DIMENSION_LOCATION:
        return CONTRADICTION_LOCATION
    if dimension == DIMENSION_EVENT:
        return CONTRADICTION_EVENT
    if dimension == DIMENSION_AMOUNT:
        return CONTRADICTION_AMOUNT
    if dimension == DIMENSION_ROLE:
        return CONTRADICTION_IDENTITY_ROLE
    if dimension == DIMENSION_STATUS:
        return CONTRADICTION_STATUS
    if dimension == DIMENSION_RELATIONSHIP:
        return CONTRADICTION_RELATIONSHIP
    return None


def _is_conflict(left: Claim, right: Claim) -> bool:
    if left.dimension != right.dimension:
        return False
    if left.document_id == right.document_id:
        # One record disagreeing with itself is a data-quality problem, not a
        # cross-record contradiction; the file-level checks cover that.
        return False
    if _classify_value(left) == _classify_value(right):
        return False

    if left.dimension == DIMENSION_ROLE:
        # Only mutually exclusive role classes conflict. "Associate" and "lead"
        # are compatible with any role, and "suspect" and "accused" are points
        # on one scale rather than a disagreement.
        if not left.category or not right.category:
            return False
        if left.category == right.category:
            return False
        return {left.category, right.category} <= _EXCLUSIVE_ROLE_CLASSES

    if left.dimension == DIMENSION_STATUS:
        if not (left.time and right.time):
            return False
        left_family = STATUS_GROUPS.get(str(left.object_key).upper())
        right_family = STATUS_GROUPS.get(str(right.object_key).upper())
        if not left_family or not right_family:
            return False
        return frozenset({left_family, right_family}) in _INCOMPATIBLE_STATUS

    if left.dimension == DIMENSION_RELATIONSHIP:
        same_target = left.object_key.split(":")[-1] == right.object_key.split(":")[-1]
        return same_target and left.polarity != right.polarity

    if left.dimension == DIMENSION_AMOUNT:
        # Amounts only conflict when the records point at the same subject and
        # the same moment; a ledger simply has many amounts.
        if not (left.time and right.time):
            return False
        return time_bucket(left.time, minutes=60) == time_bucket(right.time, minutes=60)

    return True


def detect_contradictions(
    claims: Sequence[Claim],
    *,
    minutes: int = 60,
    limit: int = 25,
) -> list[Contradiction]:
    """Return the conflicts implied by a set of claims.

    Claims are grouped by (subject, dimension, time bucket) and every pair
    inside a group that asserts different values becomes a contradiction with
    ``status="UNRESOLVED"``.  No judgement is made about which claim is right.
    """
    groups: dict[tuple[str, str, str], list[Claim]] = {}
    for claim in claims or ():
        if claim.dimension not in (
            DIMENSION_LOCATION,
            DIMENSION_EVENT,
            DIMENSION_AMOUNT,
            DIMENSION_ROLE,
            DIMENSION_STATUS,
            DIMENSION_RELATIONSHIP,
        ):
            continue
        groups.setdefault(_group_key(claim, minutes=minutes), []).append(claim)

    contradictions: list[Contradiction] = []
    for key, group in groups.items():
        if len(group) < 2:
            continue
        # Claims from one document are one account; dedupe them first so the
        # same record cannot be paired with itself.
        unique: list[Claim] = []
        seen_identity: set[tuple[str, str]] = set()
        for claim in group:
            signature = (claim.document_id, _classify_value(claim))
            if signature in seen_identity:
                continue
            seen_identity.add(signature)
            unique.append(claim)
        for index, left in enumerate(unique):
            for right in unique[index + 1:]:
                if not _is_conflict(left, right):
                    continue
                sources = sorted({left.document_id, right.document_id})
                contradictions.append(
                    Contradiction(
                        contradiction_id=f"c{len(contradictions) + 1}",
                        type=_conflict_type(left, right) or "CONFLICT",
                        dimension=left.dimension,
                        entity_key=left.subject_key,
                        entity_label=left.subject_label,
                        time=left.time or right.time,
                        claim_a=left,
                        claim_b=right,
                        sources=sources,
                        detail=(
                            f"{left.subject_label}: one record states "
                            f"'{_display_value(left)}' while another states "
                            f"'{_display_value(right)}'."
                        ),
                    )
                )
                if len(contradictions) >= limit:
                    return contradictions
    return contradictions


def contradicted_identities(contradictions: Iterable[Contradiction]) -> set[str]:
    """Claim identities that are known to be contested.

    Corroboration must not label a contested statement "corroborated", so the
    corroboration pass consults this set.
    """
    keys: set[str] = set()
    for contradiction in contradictions or ():
        keys.add(claim_identity(contradiction.claim_a))
        keys.add(claim_identity(contradiction.claim_b))
    return keys


def summarize_contradictions(contradictions: Sequence[Contradiction]) -> dict[str, Any]:
    """Machine-readable summary used by the evidence boundary."""
    by_type: dict[str, int] = {}
    for contradiction in contradictions or ():
        by_type[contradiction.type] = by_type.get(contradiction.type, 0) + 1
    return {
        "total": len(contradictions or ()),
        "by_type": by_type,
        "unresolved": sum(1 for c in contradictions or () if c.status == STATUS_UNRESOLVED),
        "documents_involved": len({ref for c in contradictions or () for ref in c.sources}),
    }
