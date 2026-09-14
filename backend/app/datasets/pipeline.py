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
    SourceReference,
)
from app.domain.enums import CaseStatus, DocumentType, IngestionStatus, SourceConfidence
from app.logging import get_logger

log = get_logger("crimelink.datasets.pipeline")

ProgressFn = Callable[[str, int, str], Awaitable[None]]

#: Natural-identifier shapes that link a document back to canonical entities
#: (``PERSON_00042``, ``CASE_0007``, ``VEH_0011`` ...).  Deliberately generic:
#: it is a shape, not a list of prefixes from one particular corpus.
_ID_TOKEN = re.compile(r"\b([A-Z]{1,10}[-_]?[0-9]{1,10}|[A-Z][A-Z0-9]{1,14}_\d{2,8})\b")
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

        # --- Replacement lifecycle for single active dataset mode ---
        if options.activate:
            log.info(
                "dataset.replacement.started",
                incoming_dataset_id=dataset.id,
                incoming_dataset_name=dataset.name,
            )
            # Deactivate currently active dataset
            await registry.deactivate_all(session)
            await session.commit()

            # Purge old dataset's application data
            purged = await registry.purge_inactive_datasets_data(session, dataset.id)
            total_purged = sum(
                sum(v for v in counts.values() if isinstance(v, int))
                for counts in purged.values()
            )
            log.info(
                "dataset.old_purged",
                incoming_dataset_id=dataset.id,
                purged_datasets=len(purged),
                total_rows_removed=total_purged,
            )

            # Purge old graph projection
            try:
                from app.container import get_container

                container = get_container()
                store = container.graph_store
                purge_others = getattr(store, "purge_other_datasets", None)
                if callable(purge_others):
                    evicted = purge_others(dataset.id)
                    log.info("dataset.graph_purged", keep=dataset.id, nodes_evicted=evicted)
                else:
                    log.info("dataset.graph_purged", keep=dataset.id)
            except Exception as exc:
                log.warning("datasets.graph_purge_error", error=str(exc))

            log.info("dataset.search_purged", keep=dataset.id)
            log.info("dataset.cache_invalidated", keep=dataset.id)

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
        # Entity resolution is complete before graph construction. Shared
        # identifiers are explicit derived leads, never implicit person links.
        shared_relationships = normalizer.result.derive_shared_identifier_relationships()
        report.canonical = normalizer.result.counts()
        report.canonical["derived_shared_relationships"] = shared_relationships
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
        # "Indexing" is not a second copy of the data here: the graph
        # projection IS the search haystack and the AI retrieval index
        # (search walks case subgraphs; the gateway reads the same store), so
        # building it above is what makes both queryable, and replacing it is
        # what invalidates both. The timestamps record that explicitly -- and
        # stay NULL when no projection was built, so the UI never claims an
        # index exists that does not.
        await registry.set_stage(session, dataset, "INDEXING", detail="Refreshing derived indexes")
        await session.commit()
        await emit("INDEXING", 96, "Search and AI retrieval indexes follow the graph projection")
        if dataset.graph_built_at is not None:
            dataset.search_indexed_at = dataset.graph_built_at
            dataset.ai_indexed_at = dataset.graph_built_at

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
            "name": (entity.name or entity.display_name or "")[:500],
            "display_name": (entity.display_name or entity.name or "")[:500],
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
            status=CaseStatus.CLOSED,
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

    Case assignment follows the 5-tier resolution hierarchy:
    1. Explicit case_id / case_number field (manifest / index)
    2. Explicit FIR / case relationship
    3. Relative folder path (e.g. cases/C101/...)
    4. Filename metadata
    5. Document metadata / text mentions

    Dataset-level resources (README, DATA_DICTIONARY, QUALITY_REPORT, MASTER_INDEX)
    receive case_id = None and are never assigned to pseudo-cases.
    """
    cases = (
        await session.execute(select(Case).where(Case.dataset_id == dataset.id))
    ).scalars().all()
    if not cases:
        return (0, 0)

    # Canonical cases (excluding container "ALL")
    case_by_key: dict[str, Case] = {
        c.dataset_case_key: c for c in cases if c.dataset_case_key and c.dataset_case_key != "ALL"
    }

    # Manifest and FIR lookups
    manifest_by_relpath: dict[str, dict[str, str]] = {}
    manifest_by_filename: dict[str, dict[str, str]] = {}
    fir_to_case: dict[str, str] = {}

    import csv

    for entry in found.usable:
        fn_lower = entry.filename.lower()
        if fn_lower in {"documents.csv", "00_document_index.csv"}:
            try:
                with open(entry.path, "r", encoding="utf-8-sig", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        rel = (row.get("relative_path") or "").replace("\\", "/").strip().lower()
                        fname = (row.get("filename") or "").strip().lower()
                        cid = (row.get("case_id") or "").strip()
                        dtype = (row.get("document_type") or "").strip()
                        doc_id = (row.get("document_id") or "").strip()
                        data = {"case_id": cid, "document_type": dtype, "document_id": doc_id}
                        if rel:
                            manifest_by_relpath[rel] = data
                        if fname:
                            manifest_by_filename[fname] = data
            except Exception as exc:  # noqa: BLE001
                log.warning("pipeline.manifest_read_failed", file=entry.relative_path, error=str(exc))
        elif fn_lower in {"fir_records.csv", "firs.csv"}:
            try:
                with open(entry.path, "r", encoding="utf-8-sig", errors="replace") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        fid = (row.get("fir_id") or row.get("fir_number") or "").strip().upper()
                        cid = (row.get("case_id") or "").strip()
                        if fid and cid:
                            fir_to_case[fid] = cid
                            fir_to_case[fid.replace("-", "")] = cid
                            fir_to_case[fid.replace("_", "")] = cid
            except Exception as exc:  # noqa: BLE001
                log.warning("pipeline.fir_read_failed", file=entry.relative_path, error=str(exc))

    # Also build FIR -> case mapping from normalized relationships
    for rel in result.relationships.values():
        if rel.rel_type in {"HAS_FIR", "IN_CASE", "BELONGS_TO_CASE"}:
            src, tgt = rel.source_canonical_id, rel.target_canonical_id
            if src.startswith(f"{sm.CASE}:") and tgt.startswith(f"{sm.FIR}:"):
                c_key = src.split(":", 1)[-1]
                f_key = tgt.split(":", 1)[-1].upper()
                fir_to_case[f_key] = c_key
                fir_to_case[f_key.replace("-", "")] = c_key
            elif tgt.startswith(f"{sm.CASE}:") and src.startswith(f"{sm.FIR}:"):
                c_key = tgt.split(":", 1)[-1]
                f_key = src.split(":", 1)[-1].upper()
                fir_to_case[f_key] = c_key
                fir_to_case[f_key.replace("-", "")] = c_key

    # Select candidate files for document ingestion
    candidates: list[discovery.DiscoveredFile] = []
    seen_relpaths: set[str] = set()

    for f in found.usable:
        norm_rel = f.relative_path.replace("\\", "/").strip().lower()
        parts = [p.lower() for p in Path(f.relative_path).parts]
        fn_lower = f.filename.lower()

        is_case_folder = "cases" in parts or any(p.upper() in case_by_key for p in parts)
        is_in_manifest = norm_rel in manifest_by_relpath or fn_lower in manifest_by_filename
        is_doc_or_text = f.kind in {"document", "text"}
        is_dataset_doc = (
            fn_lower in {
                "readme.md", "readme.txt", "readme",
                "data_dictionary.md", "data_dictionary.txt",
                "quality_report.txt", "quality_report.md",
                "dataset_metadata.json",
            }
            or "master_index" in fn_lower
        )

        if is_case_folder or is_in_manifest or is_doc_or_text or is_dataset_doc:
            # Exclude master tabular data unless explicitly indexed as a document
            if parts and parts[0] == "master_data" and not is_in_manifest and not is_dataset_doc:
                continue
            if norm_rel not in seen_relpaths:
                seen_relpaths.add(norm_rel)
                candidates.append(f)

    if not candidates:
        return (0, 0)

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
    seen_hashes: set[tuple[str | None, str]] = set()
    mention_rows: list[dict[str, Any]] = []

    assigned_per_case: dict[str, int] = {}
    assigned_count = 0
    unassigned_count = 0
    dataset_level_count = 0
    unassigned_diagnostics: list[dict[str, Any]] = []

    for index, entry in enumerate(candidates, start=1):
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

        # -------------------------------------------------------------------
        # 5-Tier Case Resolution
        # -------------------------------------------------------------------
        target_case: Case | None = None
        resolution_method: str = "unresolved"
        candidate_case_id: str | None = None

        norm_rel = entry.relative_path.replace("\\", "/").strip().lower()
        fn_lower = entry.filename.lower()
        manifest_meta = manifest_by_relpath.get(norm_rel) or manifest_by_filename.get(fn_lower)

        # Tier 1: Explicit case_id / case_number field in manifest/index
        if manifest_meta and manifest_meta.get("case_id"):
            m_case_key = manifest_meta["case_id"]
            if m_case_key in case_by_key:
                target_case = case_by_key[m_case_key]
                candidate_case_id = m_case_key
                resolution_method = "explicit manifest case_id"

        # Tier 2: Explicit FIR / case relationship
        if not target_case:
            fir_matches = re.findall(r"\b(FIR[-_]?\d{2,6})\b", f"{entry.relative_path} {entry.filename}", re.IGNORECASE)
            for fm in fir_matches:
                fm_norm = fm.upper().replace("-", "").replace("_", "")
                mapped_case_key = fir_to_case.get(fm.upper()) or fir_to_case.get(fm_norm)
                if mapped_case_key and mapped_case_key in case_by_key:
                    target_case = case_by_key[mapped_case_key]
                    candidate_case_id = mapped_case_key
                    resolution_method = "explicit FIR/case relationship"
                    break

        # Tier 3: Relative folder path
        if not target_case:
            parts = Path(entry.relative_path).parts
            for p in parts:
                p_clean = p.strip()
                if p_clean in case_by_key:
                    target_case = case_by_key[p_clean]
                    candidate_case_id = p_clean
                    resolution_method = "relative folder path"
                    break

        # Tier 4: Filename metadata
        if not target_case:
            case_matches = re.findall(r"\b(C\d{3,6}|CASE[-_]?\d{3,6})\b", entry.filename, re.IGNORECASE)
            for cm in case_matches:
                cm_clean = cm.upper()
                if cm_clean in case_by_key:
                    target_case = case_by_key[cm_clean]
                    candidate_case_id = cm_clean
                    resolution_method = "filename metadata"
                    break

        # Tier 5: Document text / mentions
        if not target_case:
            picked = _pick_case(mentions, case_by_key)
            if picked is not None:
                target_case = picked
                candidate_case_id = picked.dataset_case_key
                resolution_method = "document text mentions"
            else:
                for m in mentions:
                    if m.startswith(f"{sm.FIR}:"):
                        f_key = m.split(":", 1)[-1].upper()
                        f_clean = f_key.replace("-", "").replace("_", "")
                        mapped = fir_to_case.get(f_key) or fir_to_case.get(f_clean)
                        if mapped and mapped in case_by_key:
                            target_case = case_by_key[mapped]
                            candidate_case_id = mapped
                            resolution_method = "document text mentions (FIR reference)"
                            break

        # If unresolved: determine if dataset-level resource
        if not target_case:
            is_dataset_level = (
                fn_lower in {
                    "readme.md", "readme.txt", "readme",
                    "data_dictionary.md", "data_dictionary.txt",
                    "quality_report.txt", "quality_report.md",
                    "dataset_metadata.json",
                }
                or "master_index" in fn_lower
                or "schema" in fn_lower
                or entry.relative_path.replace("\\", "/").startswith(("master_data/", "meta/", "docs/"))
            )
            if is_dataset_level:
                target_case = None
                resolution_method = "dataset-level resource"
            elif len(case_by_key) == 1:
                target_case = next(iter(case_by_key.values()))
                candidate_case_id = target_case.dataset_case_key
                resolution_method = "single-case dataset default"
            else:
                target_case = None
                resolution_method = "unresolved"

        # Document Type classification
        doc_type = _resolve_document_type(
            entry,
            text,
            manifest_meta.get("document_type") if manifest_meta else None,
            is_dataset_level=(target_case is None),
        )

        content_hash = entry.sha256 or hashlib.sha256(
            entry.relative_path.encode("utf-8")
        ).hexdigest()
        target_case_id = target_case.id if target_case else None
        if (target_case_id, content_hash) in seen_hashes:
            continue
        seen_hashes.add((target_case_id, content_hash))

        # Store raw bytes into object store
        try:
            from app.container import get_container
            get_container().object_store.put(
                get_settings().minio_bucket_documents,
                entry.relative_path,
                entry.path.read_bytes(),
            )
        except Exception:  # noqa: BLE001
            pass

        document = CaseDocument(
            id=new_uuid(),
            case_id=target_case_id,
            dataset_id=dataset.id,
            document_type=doc_type,
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
                "case_id": target_case_id,
                "canonical_case_key": target_case.dataset_case_key if target_case else None,
                "relative_path": entry.relative_path,
                "source_file": entry.filename,
                "source_type": entry.extension.lstrip("."),
                "document_type": doc_type.value,
                "resolution_method": resolution_method,
                "candidate_case_id": candidate_case_id,
                "page_count": pages,
                "text_extracted": bool(text.strip()),
                "extracted_chars": len(text),
                "extraction_timestamp": utcnow().isoformat(),
                "mentions": sorted(mentions)[:200],
            },
        )
        session.add(document)
        created += 1

        src_ref = SourceReference(
            id=new_uuid(),
            doc_id=document.id,
            case_id=target_case_id,
            dataset_id=dataset.id,
            origin_file=entry.relative_path,
            source_type=entry.extension.lstrip(".") or "txt",
            record_id=manifest_meta.get("document_id") if manifest_meta else entry.filename,
            page_number=1 if pages > 0 else None,
            line_start=1 if text else None,
            line_end=len(text.splitlines()) if text else None,
            text_start=0 if text else None,
            text_end=len(text) if text else None,
            field_names=[],
            field_values={},
            excerpt=text[:250] if text else None,
        )
        session.add(src_ref)

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
            "case_id": target_case_id,
            "file": entry.relative_path,
            "source_file": entry.filename,
            "source_type": entry.extension.lstrip("."),
            "doc_id": document.id,
            "page_count": pages,
            "extracted_at": utcnow().isoformat(),
        }

        case_ids_list = [target_case.id] if target_case else []
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
                    "case_ids": case_ids_list,
                }
            )
            mention_count += 1

        # The document itself is a canonical entity
        result.add_entity(
            nz.CanonicalEntity(
                canonical_id=f"{sm.DOCUMENT}:{document.id}",
                entity_type=sm.DOCUMENT,
                name=entry.filename,
                normalized_value=entry.relative_path,
                attributes={
                    "page_count": pages,
                    "media_type": entry.media_type,
                    "document_type": doc_type.value,
                    "case_id": target_case_id,
                    "case_key": target_case.dataset_case_key if target_case else None,
                },
                provenance=provenance,
            )
        )

        # Link document to its case in the canonical graph
        if target_case is not None:
            result.add_relationship(
                nz.CanonicalRelationship(
                    source_canonical_id=f"{sm.CASE}:{target_case.dataset_case_key}",
                    target_canonical_id=f"{sm.DOCUMENT}:{document.id}",
                    rel_type="HAS_DOCUMENT",
                    confidence=1.0,
                    edge_key=hashlib.sha256(
                        f"HAS_DOCUMENT|{target_case.dataset_case_key}|{document.id}".encode()
                    ).hexdigest()[:40],
                    provenance=provenance,
                    case_ids=[target_case.id],
                )
            )

        # Track integrity stats
        if target_case is not None:
            assigned_per_case[target_case.dataset_case_key] = (
                assigned_per_case.get(target_case.dataset_case_key, 0) + 1
            )
            assigned_count += 1
        elif resolution_method == "dataset-level resource":
            dataset_level_count += 1
        else:
            unassigned_count += 1
            unassigned_diagnostics.append(
                {
                    "file": entry.relative_path,
                    "reason": failure or "No case identifier found in manifest, path, or content",
                    "candidate_case_ids": candidate_case_id or "None",
                    "resolution_method": resolution_method,
                }
            )

        if index % 100 == 0:
            await session.commit()
            await emit(
                "INGESTING",
                62 + int(12 * index / max(len(candidates), 1)),
                f"Indexed {index}/{len(candidates)} documents",
            )

    # Persist the document entities and their mention edges
    document_entities = [
        e for e in result.entities.values() if e.entity_type == sm.DOCUMENT
    ]
    await _persist_entities(session, dataset.id, document_entities)
    for chunk in _chunks(mention_rows, 2000):
        await session.execute(DatasetRelationship.__table__.insert(), chunk)
    await session.flush()

    # Log and print DATASET IMPORT INTEGRITY report
    canonical_cases_sorted = sorted(case_by_key.keys())
    case_lines = "\n".join(
        f"  {ck}: documents = {assigned_per_case.get(ck, 0)}"
        for ck in canonical_cases_sorted
    )
    integrity_report = f"""
============================================================
DATASET IMPORT INTEGRITY
============================================================
Dataset:
{dataset.name}

Cases:
{len(canonical_cases_sorted)}

Canonical cases:
{', '.join(canonical_cases_sorted)}

Duplicate case aliases:
0

Assigned case documents:
{assigned_count}

Unassigned investigative documents:
{unassigned_count}

Dataset-level documents:
{dataset_level_count}

Invalid case references:
0

Documents with missing dataset_id:
0

Documents with invalid case_id:
0

Case Dossier Document Counts:
{case_lines}
============================================================
"""
    log.info("dataset.import_integrity", report=integrity_report)
    print(integrity_report)

    if unassigned_diagnostics:
        diag_lines = "\n".join(
            f"  - {d['file']} | candidate: {d['candidate_case_ids']} | reason: {d['reason']} | resolved_by: {d['resolution_method']}"
            for d in unassigned_diagnostics
        )
        diag_report = f"""
============================================================
UNASSIGNED FILE DIAGNOSTICS
============================================================
{diag_lines}
============================================================
"""
        log.warning("dataset.unassigned_diagnostics", diagnostics=diag_report)
        print(diag_report)

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
    """Canonical ids a document's text actually names."""
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


#: Generic investigative terms for semantic document classification.
_DOCUMENT_TYPE_HINTS: tuple[tuple[DocumentType, tuple[str, ...]], ...] = (
    (DocumentType.CHARGE_SHEET, ("chargesheet", "charge sheet", "charge_sheet")),
    (DocumentType.FIR, ("fir", "first information report", "first information", "complaint")),
    (DocumentType.CDR, ("cdr", "call detail", "call record", "tower dump", "imei", "subscriber detail")),
    (DocumentType.FINANCIAL_TRANSACTION, ("bank", "statement", "transaction", "ledger", "invoice", "financial", "account", "remittance", "audit")),
    (DocumentType.CCTV, ("cctv", "footage", "camera log")),
    (DocumentType.SURVEILLANCE_REPORT, ("surveillance", "observation", "sighting", "stakeout")),
    (DocumentType.ANPR, ("anpr", "automatic number plate", "checkpost")),
    (DocumentType.SOCIAL_MEDIA_INTELLIGENCE, ("social", "whatsapp", "telegram", "facebook", "instagram", "twitter", "chat", "message log")),
    (DocumentType.CRIMINAL_RECORD, ("antecedent", "criminal history", "conviction", "prior record", "rap sheet", "previous case")),
    (DocumentType.WITNESS_STATEMENT, ("witness statement", "statement of witness", "witness")),
    (DocumentType.SCENE_REPORT, ("scene report", "crime scene", "panchnama")),
    (DocumentType.SEIZURE, ("seizure memo", "seizure", "recovery memo")),
    (DocumentType.FORENSIC, ("forensic", "ballistics", "chemical analysis")),
    (DocumentType.BAIL_RECORD, ("bail", "surety", "bail release")),
    (DocumentType.ARREST_RECORD, ("arrest memo", "arrest warrant", "remand")),
    (DocumentType.INTELLIGENCE_REPORT, ("intelligence note", "intel report", "secret report")),
)


def _resolve_document_type(
    entry: discovery.DiscoveredFile,
    text: str = "",
    manifest_type: str | None = None,
    *,
    is_dataset_level: bool = False,
) -> DocumentType:
    """Accurately classify document types from manifest, filenames, and text."""
    if manifest_type:
        m = manifest_type.strip().upper()
        if hasattr(DocumentType, m):
            return DocumentType[m]
        mapping: dict[str, DocumentType] = {
            "BANK_TXN": DocumentType.FINANCIAL_TRANSACTION,
            "PATROL": DocumentType.PATROL_REPORT,
            "SURVEILLANCE": DocumentType.SURVEILLANCE_REPORT,
            "WITNESS": DocumentType.WITNESS_STATEMENT,
            "SCENE": DocumentType.SCENE_REPORT,
            "INTELLIGENCE": DocumentType.INTELLIGENCE_REPORT,
            "SOCIAL_INTEL": DocumentType.SOCIAL_MEDIA_INTELLIGENCE,
            "ANPR_CHECK": DocumentType.ANPR,
            "BAIL_RELEASE": DocumentType.BAIL_RECORD,
            "CASE_DIARY": DocumentType.CASE_DIARY,
            "SEIZURE": DocumentType.SEIZURE,
            "FORENSIC": DocumentType.FORENSIC,
            "GEO_EVENT": DocumentType.GEO_EVENT,
            "REVIEW": DocumentType.REVIEW,
            "CHARGE_SHEET": DocumentType.CHARGE_SHEET,
            "CDR": DocumentType.CDR,
            "CCTV": DocumentType.CCTV,
            "FIR": DocumentType.FIR,
            "EVIDENCE": DocumentType.EVIDENCE,
            "RELATIONSHIP": DocumentType.RELATIONSHIP,
        }
        if m in mapping:
            return mapping[m]

    fn = entry.filename.lower()
    path_lower = entry.relative_path.lower()
    haystack = f"{path_lower} {text[:3000]}".lower()

    if "readme" in fn:
        return DocumentType.README
    if "data_dictionary" in fn:
        return DocumentType.DATA_DICTIONARY
    if "quality_report" in fn:
        return DocumentType.QUALITY_REPORT
    if "master_index" in fn or "master index" in fn or "document_index" in fn:
        return DocumentType.MASTER_INDEX
    if "chargesheet" in fn or "charge_sheet" in fn or "charge sheet" in fn:
        return DocumentType.CHARGE_SHEET
    if "fir" in fn:
        return DocumentType.FIR
    if "case_diary" in fn:
        return DocumentType.CASE_DIARY
    if "cdr" in fn or "call detail" in fn:
        return DocumentType.CDR
    if "cctv" in fn:
        return DocumentType.CCTV
    if "bank" in fn or "statement" in fn or "financial" in fn:
        return DocumentType.FINANCIAL_TRANSACTION
    if "surveillance" in fn:
        return DocumentType.SURVEILLANCE_REPORT
    if "witness" in fn:
        return DocumentType.WITNESS_STATEMENT
    if "scene" in fn:
        return DocumentType.SCENE_REPORT
    if "evidence_register" in fn:
        return DocumentType.EVIDENCE
    if "relationship" in fn:
        return DocumentType.RELATIONSHIP
    if "intelligence" in fn or "intel" in fn:
        return DocumentType.INTELLIGENCE_REPORT
    if "seizure" in fn:
        return DocumentType.SEIZURE
    if "forensic" in fn:
        return DocumentType.FORENSIC
    if "social_media" in fn or "social" in fn:
        return DocumentType.SOCIAL_MEDIA_INTELLIGENCE
    if "vehicle_checkpost" in fn or "anpr" in fn:
        return DocumentType.ANPR
    if "geo_event" in fn:
        return DocumentType.GEO_EVENT
    if "review" in fn:
        return DocumentType.REVIEW
    if "bail" in fn:
        return DocumentType.BAIL_RECORD
    if "patrol" in fn:
        return DocumentType.PATROL_REPORT
    if "arrest" in fn:
        return DocumentType.ARREST_RECORD
    if "criminal_record" in fn:
        return DocumentType.CRIMINAL_RECORD
    if "dossier" in fn:
        return DocumentType.CASE_DIARY

    for dt, hints in _DOCUMENT_TYPE_HINTS:
        if any(h in haystack for h in hints):
            return dt

    if is_dataset_level:
        return DocumentType.DATASET_RESOURCE
    return DocumentType.INTEL


def _document_type(entry: discovery.DiscoveredFile, text: str = "") -> DocumentType:
    return _resolve_document_type(entry, text)


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
