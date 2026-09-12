"""Provenanced evidence construction.

The rule this module enforces: no claim without a pointer. Every helper
here produces :class:`EvidenceItem` / :class:`ProvenanceItem` records that
name the document, source row, graph edge, metric, or audit row behind the
claim, so the console can open each one and the reviewer can check it.
"""

from __future__ import annotations

from typing import Iterable

from app.domain.enums import SourceConfidence

from .labels import COINCIDENCE, CORROBORATED_LEAD, FACT, HYPOTHESIS, LEAD
from .schemas import EvidenceItem, ProvenanceItem

#: Document confidence never upgrades a claim beyond what the document says.
#: A verified single source is still one source; corroboration is counted
#: separately by the strength scorer.
CONFIDENCE_LABELS: dict[SourceConfidence, str] = {
    SourceConfidence.VERIFIED: CORROBORATED_LEAD,
    SourceConfidence.UNVERIFIED: LEAD,
    SourceConfidence.ANONYMOUS_TIP: HYPOTHESIS,
    SourceConfidence.SYNTHETIC: COINCIDENCE,
}


def confidence_label(confidence: SourceConfidence | str | None) -> str:
    """Map document confidence onto the inference-label vocabulary."""
    if isinstance(confidence, SourceConfidence):
        return CONFIDENCE_LABELS.get(confidence, LEAD)
    if isinstance(confidence, str):
        try:
            return CONFIDENCE_LABELS.get(SourceConfidence(confidence), LEAD)
        except ValueError:
            return LEAD
    return LEAD


def doc_pointer(
    *,
    doc_id: str,
    label: str,
    origin_file: str | None = None,
    row_number: int | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    content_hash: str | None = None,
    detail: str | None = None,
) -> ProvenanceItem:
    """Pointer to an ingested document (optionally a position inside it)."""
    return ProvenanceItem(
        kind="document",
        ref=doc_id,
        label=label,
        detail=detail,
        doc_id=doc_id,
        origin_file=origin_file,
        row_number=row_number,
        line_start=line_start,
        line_end=line_end,
        content_hash=content_hash,
    )


def dataset_pointer(
    *, dataset_id: str, label: str, detail: str | None = None
) -> ProvenanceItem:
    """Pointer to the dataset itself, for rows with no source document.

    The importer stamps operational-table rows with ``source_doc_id =
    dataset:<id>``. Dressing that up as a document made the console offer a
    link that goes nowhere, so the fallback is named for what it is.
    """
    return ProvenanceItem(
        kind="dataset", ref=f"dataset:{dataset_id}", label=label, detail=detail
    )


def source_pointer(
    *,
    doc_id: str,
    label: str,
    origin_file: str | None = None,
    row_number: int | None = None,
    content_hash: str | None = None,
    detail: str | None = None,
) -> ProvenanceItem:
    """Pointer for whatever a record's ``source_doc_id`` actually names."""
    if doc_id.startswith("dataset:"):
        return dataset_pointer(
            dataset_id=doc_id.split(":", 1)[1], label=label, detail=detail
        )
    return doc_pointer(
        doc_id=doc_id,
        label=label,
        origin_file=origin_file,
        row_number=row_number,
        content_hash=content_hash,
        detail=detail,
    )


def source_row_pointer(
    *,
    origin_file: str,
    row_number: int,
    label: str,
    doc_id: str | None = None,
    detail: str | None = None,
) -> ProvenanceItem:
    """Pointer to one row of a source file (``cdr.csv · row 18342``)."""
    return ProvenanceItem(
        kind="source_row",
        ref=f"{origin_file}#row-{row_number}",
        label=label,
        detail=detail,
        doc_id=doc_id,
        origin_file=origin_file,
        row_number=row_number,
    )


def edge_pointer(*, edge_key: str, label: str, detail: str | None = None) -> ProvenanceItem:
    """Pointer to a knowledge-graph edge behind a relationship claim."""
    return ProvenanceItem(kind="graph_edge", ref=edge_key, label=label, detail=detail)


def metric_pointer(*, name: str, label: str, detail: str | None = None) -> ProvenanceItem:
    """Pointer to a computed analytic (``centrality:betweenness``)."""
    return ProvenanceItem(kind="metric", ref=name, label=label, detail=detail)


def audit_pointer(*, audit_id: int, label: str) -> ProvenanceItem:
    """Pointer to a tamper-evident audit-log row."""
    return ProvenanceItem(kind="audit", ref=f"audit:{audit_id}", label=label)


def note_pointer(*, label: str, detail: str | None = None) -> ProvenanceItem:
    """Pointer to investigator-supplied context (never evidence by itself)."""
    return ProvenanceItem(kind="note", ref=f"note:{label}", label=label, detail=detail)


def make_evidence(
    kind: str,
    summary: str,
    *,
    label: str = FACT,
    stance: str = "supports",
    provenance: list[ProvenanceItem] | None = None,
) -> EvidenceItem:
    """Build one evidence item; stance defaults to supporting context."""
    return EvidenceItem(
        kind=kind,  # type: ignore[arg-type]
        summary=summary,
        inference_label=label,
        stance=stance,  # type: ignore[arg-type]
        provenance=list(provenance or []),
    )


#: Most pointers a single finding may carry; the rest stay in the evidence items.
FINDING_PROVENANCE_CAP = 20


def roll_up_provenance(
    items: Iterable[EvidenceItem], *, cap: int = FINDING_PROVENANCE_CAP
) -> list[ProvenanceItem]:
    """Flatten the pointers behind a finding's own evidence, deduped and capped.

    Findings carry both the evidence items and a flat pointer list so a console
    (or an auditor reading the JSON) can open the sources without walking the
    nested structure. The roll-up is derived, never invented: it is exactly the
    union of the evidence items' pointers, in the order they were attached.
    """
    seen: set[tuple[str, str]] = set()
    pointers: list[ProvenanceItem] = []
    for item in items or []:
        for pointer in getattr(item, "provenance", []) or []:
            signature = (str(pointer.kind), str(pointer.ref))
            if signature in seen:
                continue
            seen.add(signature)
            pointers.append(pointer)
            if len(pointers) >= cap:
                return pointers
    return pointers
