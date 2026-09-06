"""The dataset import pipeline.

One function -- :func:`run_import` -- takes whatever the user handed us and
carries it all the way to a queryable, browsable, graph-projected ACTIVE
dataset:

    UPLOADED
      -> VALIDATING            discover every file, record it in the manifest
      -> NORMALIZING           parse, map columns, build canonical records
      -> INGESTING             persist entities, relationships, cases, documents
      -> BUILDING_RELATIONSHIPS
      -> BUILDING_GRAPH        project into the graph store, tagged by dataset
      -> INDEXING              refresh derived indexes
      -> READY                 (and, if requested, ACTIVE)

Every stage reports progress through the supplied callback so Administration
can show what is happening; every stage that fails records *why* on the
dataset row instead of leaving it stuck in a spinner.

Nothing here knows the name of any particular dataset, folder, or column.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.datasets import discovery, graph_build, readers
from app.datasets import normalize as nz
from app.datasets import registry
from app.datasets import schema_map as sm
from app.db.base import new_uuid, utcnow
from app.db.models import (
    Case,
    CaseDocument,
    Dataset,
    DatasetEntity,
    DatasetFile,
    DatasetRelationship,
)
from app.domain.enums import CaseStatus, DocumentType, IngestionStatus, SourceConfidence
from app.logging import get_logger

log = get_logger("crimelink.datasets.pipeline")

ProgressFn = Callable[[str, int, str], Awaitable[None]]

#: Natural-identifier shapes that link a document back to canonical entities
#: (``PERSON_00042``, ``CASE_0007``, ``VEH_0011`` ...).  Deliberately generic:
#: it is a shape, not a list of prefixes from one particular corpus.
_ID_TOKEN = re.compile(r"\b([A-Z][A-Z0-9]{1,14}_\d{2,8})\b")
_PHONE_TOKEN = re.compile(r"\b(?:\+?91[-\s]?)?([6-9]\d{9})\b")
_PLATE_TOKEN = re.compile(r"\b([A-Z]{2}\s?\d{1,2}\s?[A-Z]{1,3}\s?\d{4})\b")

#: Cap on how much document text is scanned for mentions per file.
MENTION_SCAN_CHARS = 200_000
DEFAULT_JURISDICTION = "SYN-DEV"


@dataclass(slots=True)
class ImportOptions:
    name: str
    version: str = "1"
    source_kind: str = "folder"
    origin_note: str = ""
    activate: bool = True
    build_graph: bool = True
    ingest_documents: bool = True
    copy_inputs: bool = True
    jurisdiction_id: str = DEFAULT_JURISDICTION
    created_by: str | None = None


@dataclass(slots=True)
class ImportReport:
    dataset_id: str = ""
    status: str = "UPLOADED"
    files: dict[str, Any] = field(default_factory=dict)
    tables: list[dict[str, Any]] = field(default_factory=list)
    canonical: dict[str, Any] = field(default_factory=dict)
    cases_created: int = 0
    documents_created: int = 0
    mentions: int = 0
    graph: dict[str, Any] = field(default_factory=dict)
    needs_review: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "status": self.status,
            "files": self.files,
            "tables": self.tables,
            "canonical": self.canonical,
            "cases_created": self.cases_created,
            "documents_created": self.documents_created,
            "mentions": self.mentions,
            "graph": {k: v for k, v in self.graph.items() if k != "graph"},
            "needs_review": self.needs_review,
            "warnings": self.warnings[:100],
            "warning_count": len(self.warnings),
            "error": self.error,
            "duration_s": round(self.duration_s, 2),
        }


async def _noop(stage: str, pct: int, message: str) -> None:  # pragma: no cover
    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_import(
    session: AsyncSession,
    sources: Sequence[Path | str],
    options: ImportOptions,
    *,
    progress: ProgressFn | None = None,
) -> ImportReport:
    """Import a dataset end to end. Commits as it goes so progress survives."""
    report = ImportReport()
    started = time.monotonic()
    emit = progress or _noop

    dataset = await registry.create_dataset(
        session,
        name=options.name,
        version=options.version,
        source_kind=options.source_kind,
        origin_note=options.origin_note,
        created_by=options.created_by,
    )
    report.dataset_id = dataset.id
    await session.commit()

    try:
        # --- 1. VALIDATING -------------------------------------------------
        await registry.set_stage(session, dataset, "VALIDATING", detail="Discovering files")
        await session.commit()
        await emit("VALIDATING", 5, "Discovering files")

        workspace = registry.workspace_for(dataset.id)
        root = discovery.stage_inputs(sources, workspace, copy=options.copy_inputs)
        dataset.root_path = str(root)
        found = discovery.discover(root)
        report.files = found.summary()
        report.warnings.extend(found.warnings)
        if found.errors:
            raise RuntimeError("; ".join(found.errors))
        if not found.usable:
            raise RuntimeError(
                "No supported file was found in this upload. CrimeLink reads "
                "CSV, TSV, XLSX, JSON, JSONL, TXT, MD, PDF, DOCX and ZIP."
            )

        file_ids = await _persist_manifest(session, dataset.id, found.files)
        await session.commit()
        await emit("VALIDATING", 15, f"{len(found.usable)} readable files found")

        # --- 2. NORMALIZING ------------------------------------------------
        await registry.set_stage(session, dataset, "NORMALIZING", detail="Reading tables")
        await session.commit()

        normalizer = nz.Normalizer()
        lexicon = sm.SchemaLexicon()
        deferred: list[tuple[discovery.DiscoveredFile, set[str]]] = []
        tables = [f for f in found.usable if f.kind == "table"]
        for index, entry in enumerate(tables, start=1):
            await _normalize_file(
                session, dataset.id, entry, file_ids, normalizer, report,
                lexicon=lexicon, defer=deferred,
            )
            if index % 5 == 0 or index == len(tables):
                await session.commit()
                await emit(
                    "NORMALIZING",
                    15 + int(25 * index / max(len(tables), 1)),
                    f"Mapped {index}/{len(tables)} data files",
                )
        # Second look at the tables whose headers their own values contradicted,
        # now that the dataset's key vocabulary has been learned from the
        # tables that were self-consistent.
        for index, (entry, sheets) in enumerate(deferred, start=1):
            await _normalize_file(
                session, dataset.id, entry, file_ids, normalizer, report,
                lexicon=lexicon, only_sheets=sheets,
            )
            if index % 5 == 0 or index == len(deferred):
                await session.commit()
                await emit(
                    "NORMALIZING",
                    40,
                    f"Re-mapped {index}/{len(deferred)} files with mismatched headers",
                )
        if deferred:
            report.warnings.append(
                f"{len(deferred)} file(s) had headers contradicted by their own values "
                "and were mapped from the data: " + ", ".join(
                    sorted(e.relative_path for e, _ in deferred)[:10]
                )
            )
            log.info(
                "datasets.header_contradictions",
                dataset_id=dataset.id,
                files=len(deferred),
                lexicon=lexicon.as_dict(),
            )

        report.warnings.extend(normalizer.result.warnings)
        # Now that the whole id space is known, fold placeholder entities into
        # the records the dataset actually describes (a `counterparty` cell
        # holding PERSON_00379 is that person, not a new organisation).
        folded = normalizer.reconcile_identifiers()
        if folded:
            report.warnings.append(
                f"Reconciled {folded} placeholder records against identifiers "
                "defined elsewhere in the dataset"
            )
        report.canonical = normalizer.result.counts()
        await session.commit()

        # --- 3. INGESTING --------------------------------------------------
        await registry.set_stage(session, dataset, "INGESTING", detail="Persisting canonical records")
        await session.commit()
        await emit("INGESTING", 45, "Storing entities and relationships")

        await _persist_entities(session, dataset.id, normalizer.result.entities.values())
        await session.commit()
        await emit("INGESTING", 55, "Storing relationships")
        await _persist_relationships(session, dataset.id, normalizer.result.relationships.values())
        await session.commit()

        report.cases_created = await _materialise_cases(
            session, dataset, normalizer.result, options.jurisdiction_id
        )
        await session.commit()
        await emit("INGESTING", 62, f"{report.cases_created} cases created")

        if options.ingest_documents:
            created, mentions = await _ingest_documents(
                session, dataset, found, file_ids, normalizer.result, emit
            )
            report.documents_created = created
            report.mentions = mentions
            await session.commit()

        # --- 4/5. GRAPH ----------------------------------------------------
        if options.build_graph:
            await registry.set_stage(
                session, dataset, "BUILDING_GRAPH", detail="Projecting the graph"
            )
            await session.commit()
            report.graph = await graph_build.project_dataset(
                session, dataset, progress=_scaled(emit, 75, 95)
            )
            dataset.graph_built_at = utcnow()
            await session.commit()

        # --- 6. INDEXING ---------------------------------------------------
        await registry.set_stage(session, dataset, "INDEXING", detail="Refreshing indexes")
        await session.commit()
        await emit("INDEXING", 96, "Refreshing search indexes")
        dataset.search_indexed_at = utcnow()

        # --- 7. READY ------------------------------------------------------
        dataset.stats = await registry.dataset_stats(session, dataset.id)
        await registry.set_stage(session, dataset, "READY", detail="Dataset ready")
        if options.activate:
            await registry.activate(session, dataset)
        await session.commit()

        report.status = dataset.status
        report.duration_s = time.monotonic() - started
        await emit("READY", 100, "Dataset ready")
        log.info(
            "datasets.import_completed",
            dataset_id=dataset.id,
            entities=report.canonical.get("entities"),
            relationships=report.canonical.get("relationships"),
            duration_s=round(report.duration_s, 2),
        )
        return report

    except Exception as exc:  # noqa: BLE001 - the failure must reach the operator
        await session.rollback()
        report.error = f"{type(exc).__name__}: {exc}"
        report.status = "FAILED"
        report.duration_s = time.monotonic() - started
        # ``dataset`` is expired by the rollback above, so the id comes from the
        # report -- touching the ORM object here would raise a second,
        # misleading error on top of the real one.
        log.exception("datasets.import_failed", dataset_id=report.dataset_id)
        failed = await session.get(Dataset, report.dataset_id)
        if failed is not None:
            await registry.set_stage(
                session, failed, "FAILED", detail=report.error, error=report.error
            )
            await session.commit()
        await emit("FAILED", 100, report.error)
        return report


def _scaled(emit: ProgressFn, low: int, high: int) -> ProgressFn:
    """Rescale a sub-stage's 0-100 progress into an outer window."""

    async def inner(stage: str, pct: int, message: str) -> None:
        await emit(stage, low + int((high - low) * max(0, min(pct, 100)) / 100), message)

    return inner


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------


async def _persist_manifest(
    session: AsyncSession, dataset_id: str, files: Sequence[discovery.DiscoveredFile]
) -> dict[str, str]:
    """Write the file manifest. Returns relative_path -> dataset_file id."""
    ids: dict[str, str] = {}
    for entry in files:
        row = DatasetFile(
            id=new_uuid(),
            dataset_id=dataset_id,
            relative_path=entry.relative_path,
            filename=entry.filename,
            extension=entry.extension,
            media_type=entry.media_type,
            file_kind=entry.kind,
            size_bytes=entry.size_bytes,
            sha256=entry.sha256,
            container_path=entry.container_path,
            status=entry.status,
            reason=entry.reason,
        )
        session.add(row)
        ids[entry.relative_path] = row.id
    await session.flush()
    return ids


def _uses_unlearned_keys(
    mapping: sm.TableMapping,
    table: readers.Table,
    lexicon: sm.SchemaLexicon | None,
) -> bool:
    """Does this table reference identifiers the dataset has not explained yet?

    Import order is alphabetical, not pedagogical: a ledger can be read long
    before the account register that gives its keys meaning. A table keyed on
    an unrecognised prefix is therefore worth re-mapping at the end, when the
    dataset's whole key vocabulary is known.
    """
    if lexicon is None:
        return False
    for column in mapping.columns:
        if not column.canonical or not column.canonical.endswith((".id", "_id")):
            continue
        prefix = sm.uniform_prefix(row.get(column.column) for row in table.rows)
        if prefix and not lexicon.knows(prefix):
            return True
    return False


async def _normalize_file(
    session: AsyncSession,
    dataset_id: str,
    entry: discovery.DiscoveredFile,
    file_ids: dict[str, str],
    normalizer: nz.Normalizer,
    report: ImportReport,
    *,
    lexicon: sm.SchemaLexicon | None = None,
    defer: list[tuple[discovery.DiscoveredFile, set[str]]] | None = None,
    only_sheets: set[str] | None = None,
) -> None:
    """Map and ingest one table file.

    When ``defer`` is supplied, a table whose values contradict its headers is
    mapped but *not* ingested: the dataset's key vocabulary is still being
    learned, and a second look once it is complete maps that table far better
    than a first look could. ``only_sheets`` drives that second pass.
    """
    file_id = file_ids.get(entry.relative_path)
    row = await session.get(DatasetFile, file_id) if file_id else None
    try:
        tables = readers.read_tables(entry.path, entry.extension)
    except readers.UnreadableSource as exc:
        report.warnings.append(f"{entry.relative_path}: {exc.reason}")
        if row is not None:
            row.status = exc.code if exc.code in {"UNSUPPORTED", "CORRUPT"} else "CORRUPT"
            row.reason = exc.reason
        return

    sheet_names: list[str] = []
    total_rows = 0
    mappings: list[dict[str, Any]] = []
    deferred_sheets: set[str] = set()
    for table in tables:
        if not table.columns:
            continue
        if only_sheets is not None and table.name not in only_sheets:
            continue
        mapping = sm.map_table(table.columns, table.rows, lexicon)
        if lexicon is not None and not mapping.contradictions:
            # Only a table whose headers and values agree may teach the
            # vocabulary; a contradicted table is not evidence of anything.
            for column in mapping.columns:
                if column.canonical and column.basis in {"alias", "header_tokens"}:
                    lexicon.learn_column(
                        column.canonical, (row.get(column.column) for row in table.rows)
                    )
        source = {
            "dataset_id": dataset_id,
            "file": entry.relative_path,
            "source_file": entry.filename,
            "source_type": entry.extension.lstrip("."),
            "sheet": table.name if len(tables) > 1 else None,
            "dataset_file_id": file_id,
            "extracted_at": utcnow().isoformat(),
        }
        if defer is not None and (
            mapping.contradictions or _uses_unlearned_keys(mapping, table, lexicon)
        ):
            # Either the values already disagree with the headers, or this table
            # keys onto identifiers the dataset has not explained yet. Both are
            # answered better by a second look once every table has been read.
            deferred_sheets.add(table.name)
            sheet_names.append(table.name)
            continue
        used = normalizer.ingest_table(table, mapping, source)
        sheet_names.append(table.name)
        total_rows += len(table.rows)
        payload = {
            "file": entry.relative_path,
            "sheet": table.name,
            "rows": len(table.rows),
            "rows_used": used,
            **mapping.as_dict(),
        }
        mappings.append(payload)
        report.tables.append(payload)
        if mapping.needs_review:
            report.needs_review.append(payload)
        if table.truncated:
            report.warnings.append(
                f"{entry.relative_path}: only the first {readers.MAX_ROWS} rows were read"
            )

    if deferred_sheets and defer is not None:
        defer.append((entry, deferred_sheets))
        return

    if row is not None:
        # The headline classification is the most informative sheet, not the
        # first one: a JSON file whose first sheet is two metadata keys is
        # still a call log if its second sheet holds 150 calls.
        primary = max(
            mappings,
            key=lambda m: (float(m.get("confidence") or 0.0), int(m.get("rows") or 0)),
            default={},
        ) if mappings else {}
        row.status = "NORMALIZED" if mappings else "SKIPPED"
        row.semantic_type = primary.get("semantic_type")
        row.classification_confidence = float(primary.get("confidence") or 0.0)
        row.column_mapping = {m["sheet"]: m["columns"] for m in mappings}
        # What a human would need to judge the mapping: how it was classified,
        # how sure we were, and every column whose values disagreed with its
        # header. Persisted per file so Administration can show it long after
        # the import job's log has scrolled away.
        row.mapping_notes = [
            {
                "sheet": m["sheet"],
                "semantic_type": m["semantic_type"],
                "confidence": m["confidence"],
                "needs_review": m["needs_review"],
                "contradictions": m.get("contradictions") or [],
                "notes": m.get("notes") or [],
            }
            for m in mappings
            if m.get("needs_review") or m.get("contradictions") or m.get("notes")
        ]
        row.unmapped_columns = sorted(
            {c["column"] for m in mappings for c in m["columns"] if not c["canonical"]}
        )
        row.sheet_names = sheet_names
        row.row_count = total_rows
        if not mappings:
            row.reason = "the file contained no readable table"


async def _persist_entities(
    session: AsyncSession, dataset_id: str, entities: Any
) -> int:
    rows = [
        {
            "id": new_uuid(),
            "dataset_id": dataset_id,
            "canonical_id": entity.canonical_id,
            "entity_type": entity.entity_type,
            "name": (entity.name or "")[:500],
            "normalized_value": (entity.normalized_value or "")[:500],
            "attributes": entity.attributes,
            "provenance": entity.provenance,
        }
        for entity in entities
    ]
    for chunk in _chunks(rows, 2000):
        await session.execute(DatasetEntity.__table__.insert(), chunk)
    return len(rows)


async def _persist_relationships(
    session: AsyncSession, dataset_id: str, relationships: Any
) -> int:
    rows = [
        {
            "id": new_uuid(),
            "dataset_id": dataset_id,
            "source_canonical_id": rel.source_canonical_id,
            "target_canonical_id": rel.target_canonical_id,
            "rel_type": rel.rel_type,
            "confidence": float(rel.confidence),
            "edge_key": rel.edge_key,
            "valid_from": rel.valid_from,
            "valid_to": rel.valid_to,
            "observed_at": rel.observed_at,
            "attributes": rel.attributes,
            "provenance": rel.provenance,
            "case_ids": rel.case_ids,
        }
        for rel in relationships
    ]
    for chunk in _chunks(rows, 2000):
        await session.execute(DatasetRelationship.__table__.insert(), chunk)
    return len(rows)


def _chunks(items: list[Any], size: int) -> Any:
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def _materialise_cases(
    session: AsyncSession,
    dataset: Dataset,
    result: nz.NormalizationResult,
    jurisdiction_id: str,
) -> int:
    """Turn canonical CASE entities into real ``cases`` rows.

    A dataset with no case table still gets one container case, because
    documents and evidence have to hang off something the rest of the
    application already understands.
    """
    case_entities = [e for e in result.entities.values() if e.entity_type == sm.CASE]
    created = 0
    # Scoped to this dataset: case numbers are unique per dataset, not
    # globally (see the uq_cases_dataset_case_number constraint). Two imports
    # of the same corpus legitimately carry the same numbers, and forcing them
    # apart would rename the very identifiers investigators search by.
    existing_numbers = set(
        (
            await session.execute(
                select(Case.case_number).where(Case.dataset_id == dataset.id)
            )
        ).scalars()
    )

    def unique_number(preferred: str) -> str:
        """A case number no other case *in this dataset* already holds.

        Source data does occasionally repeat a case number within one export;
        when it does, the duplicate is suffixed rather than dropped. The
        untouched natural key is always kept on ``dataset_case_key``, which is
        what relationships and provenance resolve against.
        """
        candidate = preferred
        suffix = 2
        while candidate in existing_numbers:
            candidate = f"{preferred} ({suffix})"
            suffix += 1
        existing_numbers.add(candidate)
        return candidate

    for entity in sorted(case_entities, key=lambda e: e.canonical_id):
        natural_key = entity.canonical_id.split(":", 1)[-1]
        attributes = entity.attributes or {}
        number = str(attributes.get("case_number") or natural_key).strip() or natural_key
        title = entity.name or number
        session.add(
            Case(
                id=new_uuid(),
                case_number=unique_number(number),
                title=title,
                jurisdiction_id=jurisdiction_id,
                dataset_id=dataset.id,
                dataset_case_key=natural_key,
                status=_case_status(attributes.get("raw_status")),
            )
        )
        created += 1

    # Every dataset also gets a container case. Records that no case claims --
    # a phone directory, a bank's customer list, an address register -- are
    # still part of the dataset, and every case-scoped read (search, the case
    # graph, the timeline) works through case membership. Without somewhere to
    # put them they exist in the store and are reachable from nowhere, which
    # is indistinguishable from not having imported them at all.
    session.add(
        Case(
            id=new_uuid(),
            case_number=unique_number(
                f"{dataset.name[:40]} (all records)" if not created
                else f"{dataset.name[:40]} (unassigned records)"
            ),
            title=(
                f"{dataset.name} (all records)" if not created
                else f"{dataset.name} — records not linked to a case"
            ),
            jurisdiction_id=jurisdiction_id,
            dataset_id=dataset.id,
            dataset_case_key="ALL",
            status=CaseStatus.OPEN,
        )
    )
    created += 1
    await session.flush()
    return created


def _case_status(raw: Any) -> CaseStatus:
    text = str(raw or "").strip().lower()
    if text in {"closed", "disposed", "chargesheeted", "charge_sheeted", "archived"}:
        return CaseStatus.CLOSED
    if text in {"under_review", "under review", "review", "pending_review"}:
        return CaseStatus.UNDER_REVIEW
    return CaseStatus.OPEN


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


async def _ingest_documents(
    session: AsyncSession,
    dataset: Dataset,
    found: discovery.DiscoveryResult,
    file_ids: dict[str, str],
    result: nz.NormalizationResult,
    emit: ProgressFn,
) -> tuple[int, int]:
    """Register document files as case documents and link their mentions.

    Documents are attached to whichever case their text actually names.  When
    a document names no known case it goes to the dataset's default case, so
    it stays reachable from the Evidence page instead of vanishing.
    """
    documents = [f for f in found.usable if f.kind in {"document", "text"}]
    if not documents:
        return (0, 0)

    cases = (
        await session.execute(select(Case).where(Case.dataset_id == dataset.id))
    ).scalars().all()
    if not cases:
        return (0, 0)
    case_by_key = {c.dataset_case_key: c for c in cases if c.dataset_case_key}
    default_case = cases[0]

    # Lookup tables for mention resolution, built once.
    by_natural: dict[str, str] = {}
    by_phone: dict[str, str] = {}
    by_plate: dict[str, str] = {}
    for entity in result.entities.values():
        natural = entity.canonical_id.split(":", 1)[-1]
        by_natural.setdefault(natural.upper(), entity.canonical_id)
        if entity.entity_type == sm.PHONE and entity.normalized_value:
            by_phone.setdefault(entity.normalized_value, entity.canonical_id)
        elif entity.entity_type == sm.VEHICLE and entity.normalized_value:
            by_plate.setdefault(entity.normalized_value, entity.canonical_id)

    created = 0
    mention_count = 0
    seen_hashes: set[tuple[str, str]] = set()
    mention_rows: list[dict[str, Any]] = []

    for index, entry in enumerate(documents, start=1):
        try:
            parsed = readers.read_text(entry.path, entry.extension)
            text = parsed.text
            pages = parsed.page_count
            status = IngestionStatus.COMPLETE
            failure = None
            if parsed.empty:
                status = IngestionStatus.COMPLETE
                failure = "no extractable text (the file may be a scan)"
        except readers.UnreadableSource as exc:
            text, pages = "", 0
            status = IngestionStatus.FAILED
            failure = exc.reason
        except Exception as exc:  # noqa: BLE001
            text, pages = "", 0
            status = IngestionStatus.FAILED
            failure = f"{type(exc).__name__}: {exc}"

        mentions = _mentions(text, by_natural, by_phone, by_plate)
        case = _pick_case(mentions, case_by_key) or default_case

        content_hash = entry.sha256 or hashlib.sha256(
            entry.relative_path.encode("utf-8")
        ).hexdigest()
        if (case.id, content_hash) in seen_hashes:
            continue
        seen_hashes.add((case.id, content_hash))

        document = CaseDocument(
            id=new_uuid(),
            case_id=case.id,
            dataset_id=dataset.id,
            document_type=_document_type(entry, text),
            filename=entry.filename,
            storage_key=entry.relative_path,
            content_hash=content_hash,
            size_bytes=entry.size_bytes,
            mime_type=entry.media_type,
            ingestion_status=status,
            ingestion_stage=6 if status == IngestionStatus.COMPLETE else 0,
            failure_reason=failure,
            source_confidence=SourceConfidence.SYNTHETIC,
            source_metadata={
                "dataset_id": dataset.id,
                "relative_path": entry.relative_path,
                "source_file": entry.filename,
                "source_type": entry.extension.lstrip("."),
                "page_count": pages,
                "text_extracted": bool(text.strip()),
                "extracted_chars": len(text),
                "extraction_timestamp": utcnow().isoformat(),
                "mentions": sorted(mentions)[:200],
            },
        )
        session.add(document)
        created += 1

        file_id = file_ids.get(entry.relative_path)
        if file_id:
            row = await session.get(DatasetFile, file_id)
            if row is not None:
                row.status = "INGESTED" if status == IngestionStatus.COMPLETE else "CORRUPT"
                row.doc_id = document.id
                row.page_count = pages
                row.reason = failure

        provenance = {
            "dataset_id": dataset.id,
            "file": entry.relative_path,
            "source_file": entry.filename,
            "source_type": entry.extension.lstrip("."),
            "doc_id": document.id,
            "page_count": pages,
            "extracted_at": utcnow().isoformat(),
        }
        for canonical_id in mentions:
            if canonical_id.startswith(f"{sm.CASE}:"):
                continue
            mention_rows.append(
                {
                    "id": new_uuid(),
                    "dataset_id": dataset.id,
                    "source_canonical_id": canonical_id,
                    "target_canonical_id": f"{sm.DOCUMENT}:{document.id}",
                    "rel_type": "MENTIONED_IN",
                    "confidence": 0.9,
                    "edge_key": hashlib.sha256(
                        f"MENTIONED_IN|{canonical_id}|{document.id}".encode()
                    ).hexdigest()[:40],
                    "valid_from": None,
                    "valid_to": None,
                    "observed_at": None,
                    "attributes": {"filename": entry.filename},
                    "provenance": provenance,
                    "case_ids": [case.id],
                }
            )
            mention_count += 1

        # The document itself is a canonical entity, so it can be a graph node.
        result.add_entity(
            nz.CanonicalEntity(
                canonical_id=f"{sm.DOCUMENT}:{document.id}",
                entity_type=sm.DOCUMENT,
                name=entry.filename,
                normalized_value=entry.relative_path,
                attributes={"page_count": pages, "media_type": entry.media_type},
                provenance=provenance,
            )
        )

        if index % 100 == 0:
            # Commit rather than flush: a flush holds SQLite's single writer
            # lock for the entire remaining loop, which starves the job
            # reporter writing progress from its own connection. Committing
            # releases the lock, and the work so far is real work worth
            # keeping if the run is interrupted.
            await session.commit()
            await emit(
                "INGESTING",
                62 + int(12 * index / max(len(documents), 1)),
                f"Indexed {index}/{len(documents)} documents",
            )

    # Persist the document entities and their mention edges.
    document_entities = [
        e for e in result.entities.values() if e.entity_type == sm.DOCUMENT
    ]
    await _persist_entities(session, dataset.id, document_entities)
    for chunk in _chunks(mention_rows, 2000):
        await session.execute(DatasetRelationship.__table__.insert(), chunk)
    await session.flush()
    log.info(
        "datasets.documents_ingested",
        dataset_id=dataset.id,
        documents=created,
        mentions=mention_count,
    )
    return (created, mention_count)


def _mentions(
    text: str,
    by_natural: dict[str, str],
    by_phone: dict[str, str],
    by_plate: dict[str, str],
) -> set[str]:
    """Canonical ids a document's text actually names.

    Purely evidence-driven: an id has to appear in the text for the link to
    exist.  No inference, no "documents in this folder belong to that case".
    """
    if not text:
        return set()
    window = text[:MENTION_SCAN_CHARS]
    hits: set[str] = set()
    for token in _ID_TOKEN.findall(window):
        canonical = by_natural.get(token.upper())
        if canonical:
            hits.add(canonical)
    for token in _PHONE_TOKEN.findall(window):
        canonical = by_phone.get(sm.normalize_phone(token))
        if canonical:
            hits.add(canonical)
    for token in _PLATE_TOKEN.findall(window.upper()):
        canonical = by_plate.get(sm.normalize_plate(token))
        if canonical:
            hits.add(canonical)
    return hits


def _pick_case(mentions: set[str], case_by_key: dict[str, Case]) -> Case | None:
    for canonical_id in sorted(mentions):
        if canonical_id.startswith(f"{sm.CASE}:"):
            case = case_by_key.get(canonical_id.split(":", 1)[-1])
            if case is not None:
                return case
    return None


#: ``DocumentType`` is a *semantic* vocabulary (what the document is about),
#: not a file-format one, so a document is classified from words that appear in
#: its name or its opening text.  Every rule is a generic investigative term --
#: none of them names a folder or a particular corpus.
_DOCUMENT_TYPE_HINTS: tuple[tuple[DocumentType, tuple[str, ...]], ...] = (
    (DocumentType.FIR, ("fir", "first information", "chargesheet", "charge sheet", "complaint", "arrest", "seizure", "panchnama", "remand")),
    (DocumentType.CDR, ("cdr", "call detail", "call record", "tower dump", "imei", "subscriber detail")),
    (DocumentType.FINANCIAL, ("bank", "statement", "transaction", "ledger", "invoice", "financial", "account", "remittance", "audit")),
    (DocumentType.SURVEILLANCE, ("surveillance", "observation", "sighting", "anpr", "cctv", "watch", "tail", "stakeout")),
    (DocumentType.SOCIAL_MEDIA, ("social", "whatsapp", "telegram", "facebook", "instagram", "twitter", "chat", "message log")),
    (DocumentType.CRIMINAL_HISTORY, ("antecedent", "criminal history", "conviction", "prior record", "rap sheet", "previous case")),
)


def _document_type(entry: discovery.DiscoveredFile, text: str = "") -> DocumentType:
    """Classify a document by what it says, falling back to INTEL.

    ``INTEL`` is the honest default: "we have this document, we have not
    determined its category", which is better than mislabelling it.
    """
    haystack = f"{entry.relative_path} {text[:2000]}".lower()
    for document_type, hints in _DOCUMENT_TYPE_HINTS:
        if any(hint in haystack for hint in hints):
            return document_type
    return DocumentType.INTEL


# ---------------------------------------------------------------------------
# Rebuild helpers
# ---------------------------------------------------------------------------


async def rebuild_graph(
    session: AsyncSession,
    dataset: Dataset,
    *,
    progress: ProgressFn | None = None,
) -> dict[str, Any]:
    """Re-project an already-imported dataset's graph from canonical storage.

    This is the "Rebuild Graph" button: it never re-reads the source files, so
    it is fast and cannot introduce a difference between what the database
    says and what the graph shows.
    """
    emit = progress or _noop
    await registry.set_stage(session, dataset, "BUILDING_GRAPH", detail="Rebuilding graph")
    await session.commit()
    try:
        stats = await graph_build.project_dataset(
            session, dataset, progress=_scaled(emit, 0, 95)
        )
        dataset.graph_built_at = utcnow()
        dataset.stats = await registry.dataset_stats(session, dataset.id)
        await registry.set_stage(session, dataset, "READY", detail="Graph rebuilt")
        await session.commit()
        await emit("READY", 100, "Graph rebuilt")
        return stats
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        message = f"{type(exc).__name__}: {exc}"
        refreshed = await session.get(Dataset, dataset.id)
        if refreshed is not None:
            await registry.set_stage(session, refreshed, "FAILED", detail=message, error=message)
            await session.commit()
        raise
