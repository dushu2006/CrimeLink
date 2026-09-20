"""Evidence corroboration — how many independent records state the same thing.

CrimeLink already stores relationships and evidence.  This module answers the
question an investigator actually asks next: *is this fact supported by more
than one record, and are those records independent?*

    CLAIMS → GROUP BY ASSERTION → COUNT DISTINCT DOCUMENTS
           → COUNT DISTINCT EVIDENCE TYPES → CORROBORATION OBJECT

Two rules keep the answer honest:

* **One document is one source.**  A record that repeats the same statement
  three times is still a single source, because ``extract_claims_from_document``
  collapses repeats before counting.
* **Corroboration is documentary support, not truth and not guilt.**  The
  vocabulary in this module says "records", "sources" and "documentary
  support"; it never says a claim is proven, and it never converts a count into
  a legal conclusion.
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
    Claim,
    claim_identity,
    format_timestamp,
)

STATUS_SINGLE_SOURCE = "SINGLE_SOURCE"
STATUS_MULTI_SOURCE = "MULTI_SOURCE_SUPPORTED"
STATUS_MULTI_TYPE = "MULTI_TYPE_CORROBORATED"
STATUS_CONFLICTED = "CONFLICTED"
STATUS_UNRESOLVED = "UNRESOLVED"

#: Dimensions whose single-record assertions are too weak to be worth
#: reporting on their own.
_LOW_SIGNAL_DIMENSIONS = frozenset({DIMENSION_EVENT, DIMENSION_AMOUNT})


@dataclass
class Corroboration:
    """One assertion and every independent record that makes it."""

    claim_identity: str
    claim: str
    dimension: str
    subject_label: str
    object_label: str
    support_count: int
    sources: list[dict[str, Any]] = field(default_factory=list)
    independent_source_types: int = 0
    status: str = STATUS_UNRESOLVED
    first_seen: str | None = None
    last_seen: str | None = None
    contradicted: bool = False

    @property
    def document_ids(self) -> list[str]:
        return sorted({str(source.get("document_id")) for source in self.sources})

    @property
    def is_multi_source(self) -> bool:
        return self.status in (STATUS_MULTI_SOURCE, STATUS_MULTI_TYPE)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "dimension": self.dimension,
            "subject": self.subject_label,
            "value": self.object_label,
            "support_count": self.support_count,
            "independent_source_types": self.independent_source_types,
            "status": self.status,
            "sources": [
                {
                    "document_id": source.get("document_id"),
                    "evidence_type": source.get("evidence_type"),
                }
                for source in self.sources
            ],
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "contradicted": self.contradicted,
        }


def _claim_text(claim: Claim) -> str:
    """A short, declarative phrasing of the assertion (no pipeline internals)."""
    subject = claim.subject_label or "the case"
    if claim.dimension == DIMENSION_LOCATION:
        text = f"{subject} at {claim.object_label}"
    elif claim.dimension == DIMENSION_RELATIONSHIP:
        text = f"{subject} {claim.predicate.replace('_', ' ').lower()} {claim.object_label}"
    elif claim.dimension == DIMENSION_ROLE:
        text = f"{subject} is documented as {claim.object_label.lower()}"
    elif claim.dimension == DIMENSION_AMOUNT:
        text = f"{subject}: {claim.object_label}"
    elif claim.dimension == DIMENSION_STATUS:
        text = f"case status {claim.object_label.lower()}"
    elif claim.dimension == DIMENSION_EVENT:
        text = f"{subject} — {claim.object_label}"
    else:
        text = f"{subject} — {claim.object_label}"
    if claim.time:
        text = f"{text} ({format_timestamp(claim.time)})"
    if claim.polarity < 0:
        text = f"{text} — explicitly denied in this record"
    return text


def _status_for(
    support_count: int,
    independent_types: int,
    contradicted: bool,
) -> str:
    if contradicted:
        return STATUS_CONFLICTED
    if support_count >= 2 and independent_types >= 2:
        return STATUS_MULTI_TYPE
    if support_count >= 2:
        return STATUS_MULTI_SOURCE
    if support_count == 1:
        return STATUS_SINGLE_SOURCE
    return STATUS_UNRESOLVED


def corroborate_claims(
    claims: Sequence[Claim],
    *,
    contradicted_keys: Iterable[str] = (),
    min_support: int = 1,
) -> list[Corroboration]:
    """Group equal claims and count the distinct records that assert them."""
    contradicted = {str(key) for key in contradicted_keys or ()}
    groups: dict[str, list[Claim]] = {}
    for claim in claims or ():
        groups.setdefault(claim_identity(claim), []).append(claim)

    results: list[Corroboration] = []
    for identity, group in groups.items():
        # One document is one source, whatever it repeats internally.
        by_document: dict[str, Claim] = {}
        for claim in group:
            by_document.setdefault(claim.document_id, claim)
        sources = [
            {
                "document_id": doc_id,
                "evidence_type": claim.evidence_type,
                "time": claim.time,
                "quote": claim.text,
            }
            for doc_id, claim in sorted(by_document.items())
        ]
        support_count = len(sources)
        if support_count < max(1, min_support):
            continue
        independent_types = len({str(source["evidence_type"]) for source in sources})
        times = sorted(str(source["time"]) for source in sources if source.get("time"))
        first_claim = group[0]
        entry = Corroboration(
            claim_identity=identity,
            claim=_claim_text(first_claim),
            dimension=first_claim.dimension,
            subject_label=first_claim.subject_label,
            object_label=first_claim.object_label,
            support_count=support_count,
            sources=sources,
            independent_source_types=independent_types,
            status=_status_for(support_count, independent_types, identity in contradicted),
            first_seen=times[0] if times else None,
            last_seen=times[-1] if times else None,
            contradicted=identity in contradicted,
        )
        results.append(entry)

    results.sort(
        key=lambda item: (
            -item.support_count,
            -item.independent_source_types,
            item.dimension,
            item.claim,
        )
    )
    return results


def multi_source_corroborations(
    corroborations: Sequence[Corroboration],
    *,
    include_conflicted: bool = False,
) -> list[Corroboration]:
    """Only the assertions backed by more than one independent record."""
    out: list[Corroboration] = []
    for entry in corroborations or ():
        if entry.contradicted and not include_conflicted:
            continue
        if entry.support_count >= 2:
            out.append(entry)
    return out


def summarize_corroboration(corroborations: Sequence[Corroboration]) -> dict[str, Any]:
    """Machine-readable summary used by the evidence boundary."""
    multi = multi_source_corroborations(corroborations)
    multi_type = [c for c in multi if c.status == STATUS_MULTI_TYPE]
    return {
        "assertions_examined": len(corroborations or ()),
        "multi_source": len(multi),
        "multi_type": len(multi_type),
        "conflicted": sum(1 for c in corroborations or () if c.contradicted),
        "sources_examined": len(
            {source["document_id"] for c in corroborations or () for source in c.sources}
        ),
        "top": [c.as_dict() for c in multi[:8]],
    }
