"""Data gaps: missing evidence, reported honestly instead of filled in.

A gap names what is absent, which entities and cases it affects, and what
would help close it. Gaps never speculate about what the missing evidence
*would* say — that way lies invented testimony.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .labels import DATA_GAP
from .schemas import DataGap, Hypothesis, RelationshipFinding, ResolvedEntity

#: At most this many gaps per answer; the rest stay implied.
MAX_GAPS = 20

#: How gaps are ranked when the cap forces a choice. Generic "this source family
#: is absent" notices are honest but never urgent; a gap about the entities or
#: the hypothesis actually under discussion is what the investigator must see,
#: so it must not be pushed out by nine families the scope happens not to hold.
GAP_PRIORITY: dict[str, int] = {
    "unresolved-entity": 0,
    "provisional-identity": 0,
    "missing-documents": 1,
    "provisional-link": 1,
    "single-source": 2,
    "missing-link": 2,
    "incomplete-records": 3,
    "missing-source": 4,
}

#: Source types whose absence is itself worth reporting, with guidance. One entry
#: per source family an investigation is expected to be able to reason over; a
#: family is only ever reported missing when the scope really holds no trace of
#: it (see :func:`present_sources`).
MISSING_SOURCE_HELP: dict[str, str] = {
    "FIR": "Obtain FIR records for the first-record account of the offence (who / what / where / when).",
    "CDR": "Obtain call-detail records to test contact hypotheses against actual traffic.",
    "FINANCIAL": "Obtain financial records (statements, transfers) to test money-movement readings.",
    "VEHICLE": "Obtain vehicle-registry records: ownership and usage are different claims.",
    "CCTV": "Obtain CCTV coverage for the relevant windows; footage is the record, not the recollection.",
    "SURVEILLANCE": "Obtain surveillance records to test presence and movement claims.",
    "SOCIAL_MEDIA": "Obtain social-media captures where lawful; online adjacency alone proves nothing.",
    "INTELLIGENCE": "Obtain intel/human-source notes and grade their reliability before relying on them.",
    "CRIMINAL_HISTORY": "Pull stated criminal-history records rather than inferring antecedents.",
}


#: Edge types that *are* the source, whatever the documents are called.
#: A corpus can hold call traffic as structured edges with no document typed
#: CDR; announcing "no CDR records" then would be a false statement about the
#: data, and this module exists to state absences truthfully.
SOURCE_EDGE_SIGNALS: dict[str, tuple[str, ...]] = {
    "CDR": ("CALLED", "CONTACTED", "SMS", "MESSAGED", "COMMUNICATED"),
    "FINANCIAL": ("TRANSFER_TO", "OWNS_ACCOUNT", "CONTROLS_ACCOUNT", "PAID"),
    "VEHICLE": ("OWNS_VEHICLE", "USES_VEHICLE", "DROVE", "REGISTERED_TO", "TRAVELLED_IN"),
    "CCTV": ("CAPTURED_ON", "RECORDED_BY", "SEEN_ON_CAMERA"),
    "SURVEILLANCE": ("SEEN_AT", "SIGHTED_AT", "TRAVELLED_TO", "OBSERVED_AT"),
    "SOCIAL_MEDIA": (
        "POSTED",
        "TAGGED",
        "FOLLOWS",
        "ASSOCIATED_ONLINE",
        "INTERACTED_WITH",
        "LINKED_ON_SOCIAL",
        "SOCIAL_LINK",
    ),
}

#: File classifications that are themselves a source family (the corpus can hold
#: CCTV manifests, vehicle registries and intel notes as *documents*).
FILE_TYPE_SIGNALS: dict[str, tuple[str, ...]] = {
    "CCTV": ("CCTV", "CCTV_FOOTAGE", "CAMERA_LOG"),
    "VEHICLE": ("VEHICLE", "VEHICLE_REGISTRY", "VEHICLE_TABLE"),
    "INTELLIGENCE": ("INTEL", "INTELLIGENCE", "HUMINT", "TECHINT"),
    "FIR": ("FIR",),
    "CDR": ("CDR", "CALL_RECORDS", "CALL_DETAIL"),
    "FINANCIAL": ("FINANCIAL", "BANK", "TRANSACTION"),
}

#: Node attributes that only an authoritative record can carry.
SOURCE_NODE_SIGNALS: dict[str, tuple[str, ...]] = {
    "CRIMINAL_HISTORY": ("criminal_status", "antecedents", "conviction", "charge_sheet"),
    "CCTV": ("camera_id", "cctv_id", "footage_ref"),
    "VEHICLE": ("registration_number", "vehicle_id"),
}

#: Import states that mean "the file was catalogued but could not be used".
FAILED_FILE_STATUSES: tuple[str, ...] = ("UNSUPPORTED", "CORRUPT")

#: Reasons that mean a file was left out *on purpose* -- evaluation material and
#: the like. Those are a boundary, not a hole: the analysis is not silently
#: missing them, so they are reported as a statement rather than a gap.
POLICY_EXCLUSION_MARKERS: tuple[str, ...] = (
    "never ingested",
    "ground truth",
    "ground-truth",
    "ground_truth",
    "evaluation material",
    "excluded by policy",
)


@dataclass
class ImportReport:
    """What the dataset manifest says about files that are not fully usable.

    A scope can have every source family present and still be incomplete: files
    that could not be read, or records that exist without a source document to
    open. Saying so is part of reporting the data honestly, and it is different
    from "this source family is absent" -- hence a separate gap, worded so each
    case is distinguishable:

    * ``failed`` -- catalogued but unreadable/unsupported: missing evidence.
    * ``without_document`` -- produced records, but no document behind them, so
      those records can only be provenanced to the dataset.
    * ``excluded_on_policy`` -- deliberately left out (evaluation material).
      Reported so nobody mistakes it for a gap, never counted as one.
    """

    files_total: int = 0
    failed: int = 0
    without_document: int = 0
    excluded_on_policy: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def incomplete(self) -> bool:
        return self.failed > 0 or self.without_document > 0


def present_sources(doc_types: set[str], snapshot: Any) -> set[str]:
    """Which of the tracked sources actually have records in this scope.

    Document types alone under-report: the corpus feeds call records, sightings
    and transfers straight into the graph, so the *data* is read, not just the
    file label. A scope holding 16,442 ``CALLED`` edges has call records whatever
    its files are named, and announcing "no CDR records" there would be a false
    statement about the data. Anything not present here is a genuine gap.
    """
    labels = {str(item).upper() for item in doc_types}
    present = {str(item) for item in doc_types}
    for source, file_types in FILE_TYPE_SIGNALS.items():
        if any(
            label == file_type or label.startswith(f"{file_type}_") or label.endswith(f"_{file_type}")
            for label in labels
            for file_type in file_types
        ):
            present.add(source)
    rel_types = {str(edge.rel_type).upper() for edge in (getattr(snapshot, "edges", None) or [])}
    for source, edge_types in SOURCE_EDGE_SIGNALS.items():
        if rel_types.intersection(edge_types):
            present.add(source)
    for node in (getattr(snapshot, "nodes", None) or {}).values():
        properties = getattr(node, "properties", None) or {}
        for source, attributes in SOURCE_NODE_SIGNALS.items():
            if any(attribute in properties for attribute in attributes):
                present.add(source)
    return present


def build_gaps(
    entities: list[ResolvedEntity],
    doc_types: set[str],
    hypotheses: list[Hypothesis],
    relationships: list[RelationshipFinding],
    case_ids: list[str],
    *,
    import_report: ImportReport | None = None,
    documents_in_scope: int = 0,
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

    if documents_in_scope == 0:
        gaps.append(
            DataGap(
                category="missing-documents",
                description=(
                    "No source document is attached to the in-scope cases, so nothing "
                    "in this answer can be opened against a record."
                ),
                what_would_help=(
                    "Import the case file (FIR, statements, exhibits) for these cases, "
                    "or widen the scope to a case that has documents."
                ),
                inference_label=DATA_GAP,
                entities=[entity.display_name for entity in entities if entity.resolved],
                cases=list(case_ids),
            )
        )

    if import_report is not None and import_report.incomplete:
        parts: list[str] = []
        if import_report.failed:
            parts.append(f"{import_report.failed} file(s) could not be read")
        if import_report.without_document:
            parts.append(
                f"{import_report.without_document} produced records without a source document"
            )
        example = f" Examples: {', '.join(import_report.examples[:3])}." if import_report.examples else ""
        excluded = (
            f" A further {import_report.excluded_on_policy} file(s) were excluded by policy "
            "and are not counted here."
            if import_report.excluded_on_policy
            else ""
        )
        gaps.append(
            DataGap(
                category="incomplete-records",
                description=(
                    f"Out of {import_report.files_total} catalogued file(s), "
                    f"{' and '.join(parts)}.{example}{excluded}"
                ),
                what_would_help=(
                    "Re-export the unreadable files in a supported format, or supply the "
                    "missing pages, so the analysis is not silently working from a subset; "
                    "records without a document need the file they came from."
                ),
                inference_label=DATA_GAP,
                entities=[],
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

    gaps.sort(key=lambda gap: GAP_PRIORITY.get(gap.category, 5))
    return gaps[:MAX_GAPS]
