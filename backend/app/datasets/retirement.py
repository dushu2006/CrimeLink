"""Dataset retirement: removing the dataset a replacement leaves behind.

CrimeLink keeps **one active dataset at a time**.  The deployment runs on
limited-capacity storage, so "the previous import is no longer visible" is not
enough — its rows, its graph projection, its uploaded bytes and its staged
copies have to actually go, or every replacement permanently grows the
database and the object store.

What this module is, and is not
-------------------------------
It is the *retirement half* of the existing lifecycle.  Discovery, parsing,
normalization, canonical persistence, graph projection and activation all stay
where they are (``datasets/pipeline.py``, ``datasets/graph_build.py``,
``datasets/registry.py``); nothing here re-implements ingestion.  What is added
is the dependency-ordered removal of everything a retired dataset owns, and the
gate that decides whether an incoming dataset has earned the replacement.

Ordering — the property that makes replacement failure-safe
-----------------------------------------------------------
The old dataset is retired **after** the new one is verified usable, never
before::

    stage → discover → parse → normalize → persist → verify → build graph
          → verify usable → ACTIVATE new → RETIRE old → reclaim storage

Deleting first and importing second would leave a deployment with no usable
dataset whenever the new upload failed halfway, which is why
:func:`app.datasets.pipeline.run_import` only calls :func:`registry.activate`
once the incoming dataset is ``READY`` and :func:`usability_problems` finds
nothing wrong with it.  A failed import therefore costs nothing: the previous
dataset is still active, still whole, still queryable.

Ownership — what is deleted and what is never touched
-----------------------------------------------------
Deletion is scoped by ``dataset_id`` and by the cases/documents that belong to
it.  There is no ``TRUNCATE``, no ``DROP`` and no "delete from every table"
anywhere in this module:

*dataset-owned* (removed): ``dataset_files``, ``dataset_entities``,
``dataset_relationships``, ``dataset_pseudonyms``, ``cases``,
``case_documents``, ``source_references`` and the machine-derived analysis of
those cases (findings, patterns, resolution queue, stage runs, ingestion jobs,
document stage events, custody events) — plus the dataset's workspace directory
and the objects only it referenced.

*kept on purpose* (:data:`RETAINED_AFTER_RETIREMENT`): the record of human
activity against the corpus — investigation threads and jobs, import jobs,
notes, tasks, hypotheses, contradictions, claims, approvals and reports — and
the ``datasets`` registration row itself.  Replacing a haystack does not
un-happen an investigation, and the API refuses to resume a thread that belongs
to a retired dataset with an explanatory 422, which requires the thread row to
exist.  These rows are inert once their cases are gone and cost a few hundred
bytes.

*never touched* (:data:`PRESERVED_TABLES`): ``users`` and ``refresh_tokens``
(identity survives every replacement — deleting the accounts because a corpus
was swapped would be indefensible), ``audit_logs`` / ``audit_chain_head`` /
``audit_anchors`` (the append-only hash chain is user/system-level history and
rewriting it would break every hash link), ``jurisdiction_access_requests`` (a
person's authorization is not dataset data), ``model_registry`` and
``pattern_config`` (system configuration), ``quarantined_records`` (no
``dataset_id`` column, so its rows cannot be attributed to one import), and
every row whose ``dataset_id IS NULL`` — an investigator's hand-created case or
own upload belongs to them, not to an import.

Storage reclamation is deferred until the transaction commits
-------------------------------------------------------------
Deleting a row can be rolled back; deleting an object cannot.  The irreversible
work (object removal, workspace removal, re-storing the surviving dataset's
evidence bytes) is therefore queued on the session and executed by
:func:`finalize_pending` **after** the caller commits.  If the activation
transaction is rolled back, nothing was destroyed and the previous dataset is
still intact — including its files.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import (
    Case,
    CaseDocument,
    Dataset,
    DatasetEntity,
    DatasetFile,
    DatasetPseudonym,
    DatasetRelationship,
    DetectedPattern,
    DocumentStageEvent,
    EntityResolutionItem,
    EvidenceCustodyEvent,
    IngestionJob,
    InvestigationFinding,
    InvestigationStageRun,
    SourceReference,
)
from app.domain.provenance import content_hash
from app.logging import get_logger

log = get_logger("crimelink.datasets.retirement")

#: Attribute used to hang the deferred storage work off the session.  It lives
#: on the session instance, so a rollback (and the session that follows it)
#: simply drops the queue instead of executing it.
_PENDING_ATTR = "_crimelink_retirement_pending"

#: Attribute holding the object keys an import could not write because the
#: dataset being replaced still owns them (see :func:`note_object_conflict`).
_CONFLICT_ATTR = "_crimelink_retirement_conflicts"

#: Tables that are *never* removed by a dataset replacement, kept here as an
#: explicit list so the exclusion is a decision on the record rather than an
#: omission nobody noticed.
PRESERVED_TABLES: tuple[str, ...] = (
    # --- identity, authorization, audit and system configuration ----------

    "users",
    "refresh_tokens",
    "audit_logs",
    "audit_chain_head",
    "audit_anchors",
    "jurisdiction_access_requests",
    "model_registry",
    "pattern_config",
    # No ``dataset_id`` column exists on this table, so its rows cannot be
    # attributed to one import; retiring them would be a guess.
    "quarantined_records",
)

#: Dataset-scoped tables that a replacement deliberately keeps.
#:
#: These are the record of *human* activity against a corpus, not the corpus or
#: anything derived from it: which investigator opened which thread, what they
#: asked, what they concluded, what they approved.  Replacing the haystack does
#: not un-happen an investigation, and the API's cross-dataset guarantee depends
#: on the thread row still existing — resuming a thread that belongs to a
#: retired dataset is refused with an explanatory 422 rather than a bare 404
#: (``api/v1/investigate.py``).  Their cases are gone, so these rows are inert;
#: they cost a few hundred bytes and they are the audit trail.
RETAINED_AFTER_RETIREMENT: tuple[str, ...] = (
    "investigation_sessions",
    "investigation_jobs",
    "dataset_jobs",
    "investigator_notes",
    "investigation_tasks",
    "hypotheses",
    "contradictions",
    "claims",
    "approval_records",
    "investigation_reports",
)


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class RetiredDataset:
    """What one retired dataset cost, and what removing it reclaimed."""

    dataset_id: str
    name: str
    rows: dict[str, int] = field(default_factory=dict)
    graph_nodes_evicted: int = 0
    objects_deleted: int = 0
    bytes_reclaimed: int = 0
    workspace_removed: bool = False
    row_deleted: bool = False
    #: Object keys queued for deletion once the transaction commits.
    pending_object_keys: dict[str, list[str]] = field(default_factory=dict)
    pending_paths: list[str] = field(default_factory=list)

    @property
    def rows_removed(self) -> int:
        return sum(value for value in self.rows.values() if isinstance(value, int))

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "name": self.name,
            "rows": dict(self.rows),
            "rows_removed": self.rows_removed,
            "graph_nodes_evicted": self.graph_nodes_evicted,
            "objects_deleted": self.objects_deleted,
            "bytes_reclaimed": self.bytes_reclaimed,
            "workspace_removed": self.workspace_removed,
            "dataset_row_deleted": self.row_deleted,
        }


@dataclass(slots=True)
class RetirementReport:
    """The whole replacement: what was kept, what was retired, what was freed."""

    keep_dataset_id: str
    retired: list[RetiredDataset] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    objects_deleted: int = 0
    bytes_reclaimed: int = 0
    objects_restored: int = 0
    finalized: bool = False

    @property
    def rows_removed(self) -> int:
        return sum(item.rows_removed for item in self.retired)

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_dataset_id": self.keep_dataset_id,
            "retired_datasets": [item.as_dict() for item in self.retired],
            "retired_count": len(self.retired),
            "rows_removed": self.rows_removed,
            "graph_nodes_evicted": sum(item.graph_nodes_evicted for item in self.retired),
            "objects_deleted": self.objects_deleted,
            "objects_restored": self.objects_restored,
            "bytes_reclaimed": self.bytes_reclaimed,
            "workspace_dirs_removed": [
                path for item in self.retired for path in item.pending_paths
            ],
            "storage_finalized": self.finalized,
            "problems": list(self.problems),
            "preserved_tables": list(PRESERVED_TABLES),
        }


# --------------------------------------------------------------------------- #
# The gate: is the incoming dataset actually usable?
# --------------------------------------------------------------------------- #


async def usability_problems(
    session: AsyncSession,
    dataset: Dataset,
    *,
    expect_graph: bool = False,
    graph_stats: dict[str, Any] | None = None,
) -> list[str]:
    """Everything wrong with *dataset* that makes it unfit to become active.

    Counted from stored rows, never assumed: an import that discovered files
    but persisted nothing, or that was asked to project a graph and did not,
    is not a dataset the deployment can run on — and it must not cost the
    operator the dataset they had.  An empty list means "usable".
    """
    from sqlalchemy import func

    from app.datasets import registry, schema_map as sm

    problems: list[str] = []
    if dataset.error:
        problems.append(f"the import recorded an error: {dataset.error}")
    if dataset.status == "FAILED":
        problems.append("the import failed before it reached READY")

    stats = await registry.dataset_stats(session, dataset.id)
    if stats.get("files", 0) <= 0:
        problems.append("no file was registered in the dataset manifest")
    produced = (
        int(stats.get("entities", 0))
        + int(stats.get("relationships", 0))
        + int(stats.get("cases", 0))
        + int(stats.get("documents", 0))
    )
    if produced <= 0:
        problems.append(
            "no canonical record was persisted (no entity, relationship, case or document)"
        )

    if expect_graph:
        if dataset.graph_built_at is None:
            problems.append("the graph projection did not complete")
        elif graph_stats is not None:
            # Counted the way the projection itself decides: ``CASE`` entities
            # are real Case rows projected separately, and DOCUMENT/EVIDENCE
            # rows are provenance records that are never network nodes
            # (``graph_build.project_dataset``).  A corpus whose only canonical
            # rows are of those kinds legitimately projects no entity node —
            # refusing to activate it would refuse a valid dataset.
            eligible = 0
            for entity_type, count in (
                await session.execute(
                    select(DatasetEntity.entity_type, func.count(DatasetEntity.id))
                    .where(DatasetEntity.dataset_id == dataset.id)
                    .group_by(DatasetEntity.entity_type)
                )
            ).all():
                if str(entity_type or "").upper() == sm.CASE:
                    continue
                if sm.is_graph_eligible(entity_type):
                    eligible += int(count or 0)
            if eligible and not int(graph_stats.get("nodes_written", 0) or 0):
                problems.append(
                    f"the graph projection wrote no node for {eligible} canonical "
                    "entities; search and AI retrieval would have nothing to read"
                )

    return problems


# --------------------------------------------------------------------------- #
# Database removal — scoped by dataset_id, in dependency order
# --------------------------------------------------------------------------- #


async def _owned_ids(session: AsyncSession, dataset_id: str) -> tuple[list[str], list[str]]:
    """The case ids and document ids this dataset owns."""
    case_ids = list(
        (await session.execute(select(Case.id).where(Case.dataset_id == dataset_id))).scalars()
    )
    document_query = select(CaseDocument.id).where(CaseDocument.dataset_id == dataset_id)
    if case_ids:
        document_query = select(CaseDocument.id).where(
            or_(CaseDocument.dataset_id == dataset_id, CaseDocument.case_id.in_(case_ids))
        )
    document_ids = list((await session.execute(document_query)).scalars())
    return case_ids, document_ids


async def delete_dataset_rows(session: AsyncSession, dataset_id: str) -> dict[str, int]:
    """Delete every row a dataset owns, children first.

    One implementation, used by replacement and by a plain re-import purge.
    Foreign keys exist on only two of these relationships
    (``case_documents.case_id`` and ``source_references.doc_id``/
    ``evidence_custody_events.evidence_id``, all ``ON DELETE CASCADE``), and
    cascade behaviour differs between backends — SQLite needs
    ``PRAGMA foreign_keys=ON``, PostgreSQL does not — so every child table is
    deleted explicitly and in order instead of relying on the database to
    follow.  ``rowcount`` is reported per table so the operator can see exactly
    what a replacement reclaimed.
    """
    removed: dict[str, int] = {}
    case_ids, document_ids = await _owned_ids(session, dataset_id)

    async def drop(model: Any, label: str, *conditions: Any) -> None:
        statement = delete(model)
        for condition in conditions:
            statement = statement.where(condition)
        result = await session.execute(statement)
        removed[label] = removed.get(label, 0) + int(result.rowcount or 0)

    # --- 1. provenance and custody rows that point at the documents ---------
    if document_ids or case_ids:
        doc_clause = (
            SourceReference.doc_id.in_(document_ids)
            if document_ids
            else SourceReference.id.is_(None)
        )
        await drop(
            SourceReference,
            "source_references",
            or_(
                SourceReference.dataset_id == dataset_id,
                doc_clause,
                SourceReference.case_id.in_(case_ids) if case_ids else False,  # noqa: E712
            ),
        )
        if document_ids:
            await drop(
                EvidenceCustodyEvent,
                "evidence_custody_events",
                or_(
                    EvidenceCustodyEvent.evidence_id.in_(document_ids),
                    EvidenceCustodyEvent.case_id.in_(case_ids) if case_ids else False,  # noqa: E712
                ),
            )
            await drop(
                DocumentStageEvent,
                "document_stage_events",
                or_(
                    DocumentStageEvent.doc_id.in_(document_ids),
                    DocumentStageEvent.case_id.in_(case_ids) if case_ids else False,  # noqa: E712
                ),
            )
            await drop(
                IngestionJob,
                "ingestion_jobs",
                or_(
                    IngestionJob.doc_id.in_(document_ids),
                    IngestionJob.case_id.in_(case_ids) if case_ids else False,  # noqa: E712
                ),
            )
    else:
        await drop(SourceReference, "source_references", SourceReference.dataset_id == dataset_id)

    # --- 2. machine-derived analysis scoped to the dataset's cases ----------
    # What a retired corpus was analysed *into* goes with the corpus: patterns,
    # findings, resolution-queue items and stage runs are all recomputed from
    # the data by the next import, and leaving them behind would keep results
    # alive whose evidence no longer exists.  The investigator's own work
    # product is retained (``RETAINED_AFTER_RETIREMENT``).
    if case_ids:
        for model, label in (
            (EntityResolutionItem, "entity_resolution_items"),
            (DetectedPattern, "detected_patterns"),
            (InvestigationFinding, "investigation_findings"),
            (InvestigationStageRun, "investigation_stage_runs"),
        ):
            await drop(model, label, model.case_id.in_(case_ids))

    # --- 3. canonical pipeline layer ---------------------------------------
    await drop(DatasetRelationship, "relationships", DatasetRelationship.dataset_id == dataset_id)
    await drop(DatasetEntity, "entities", DatasetEntity.dataset_id == dataset_id)
    await drop(DatasetFile, "files", DatasetFile.dataset_id == dataset_id)
    await drop(DatasetPseudonym, "pseudonyms", DatasetPseudonym.dataset_id == dataset_id)

    # --- 4. documents, then the cases that own them ------------------------
    # Investigation threads, investigation/import jobs and the investigator's
    # notes are deliberately *not* removed here: see RETAINED_AFTER_RETIREMENT.
    if case_ids:
        await drop(
            CaseDocument,
            "documents",
            or_(
                CaseDocument.dataset_id == dataset_id,
                CaseDocument.case_id.in_(case_ids),
            ),
        )
    else:
        await drop(CaseDocument, "documents", CaseDocument.dataset_id == dataset_id)
    await drop(Case, "cases", Case.dataset_id == dataset_id)

    await session.flush()
    return {label: count for label, count in removed.items() if count}


async def _object_keys_of(
    session: AsyncSession, dataset_id: str, case_ids: Sequence[str]
) -> dict[str, set[str]]:
    """``{bucket: {key}}`` for the objects only this dataset's documents use.

    A key still referenced by a *retained* document is excluded: two corpora
    legitimately contain ``cases.csv``, and the object store is write-once and
    keyed by relative path, so the surviving dataset's bytes must not be
    deleted along with the retired one's.
    """
    settings = get_settings()
    buckets = {
        "documents": settings.minio_bucket_documents,
        "derived": settings.minio_bucket_derived,
    }
    query = select(
        CaseDocument.dataset_id,
        CaseDocument.case_id,
        CaseDocument.storage_key,
        CaseDocument.derived_key,
    )
    owned: dict[str, set[str]] = {name: set() for name in buckets}
    retained: dict[str, set[str]] = {name: set() for name in buckets}
    for row_dataset, row_case, storage_key, derived_key in (
        await session.execute(query)
    ).all():
        is_owned = row_dataset == dataset_id or (row_case in case_ids if row_case else False)
        target = owned if is_owned else retained
        if storage_key:
            target["documents"].add(storage_key)
        if derived_key:
            target["derived"].add(derived_key)

    return {
        buckets[name]: {key for key in owned[name] if key not in retained[name]}
        for name in buckets
        if owned[name] - retained[name]
    }


def _workspace_paths(dataset: Dataset) -> list[Path]:
    """Directories on disk that belong to this dataset alone.

    Only paths *inside* the datasets root qualify.  An imported-by-path dataset
    (``copy_inputs=False``) points ``root_path`` at the operator's own folder,
    which is never ours to delete.
    """
    from app.datasets import registry

    root = registry.datasets_root().resolve()
    candidates = [root / dataset.id]
    if dataset.root_path:
        candidates.append(Path(dataset.root_path))
    out: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover - a broken path is simply skipped
            continue
        if resolved == root:
            continue
        try:
            inside = resolved.is_relative_to(root)
        except AttributeError:  # pragma: no cover - Python 3.8
            inside = str(resolved).startswith(str(root))
        if inside and resolved not in out:
            out.append(resolved)
    return out


# --------------------------------------------------------------------------- #
# Retirement
# --------------------------------------------------------------------------- #


def _arm_rollback_guard(session: AsyncSession) -> None:
    """Forget the queued work if this transaction is rolled back.

    Deleting an object cannot be undone, so the queue must not outlive the
    transaction that decided to retire anything.  Without this, a caller that
    rolls back and then (correctly, in the success path) calls
    :func:`finalize_pending` would destroy the storage of a dataset that is
    still active.
    """
    from sqlalchemy import event

    target = getattr(session, "sync_session", session)
    if getattr(target, "_crimelink_rollback_guard_armed", False):
        return

    @event.listens_for(target, "after_rollback")
    def _drop_queued_retirement(_session: Any) -> None:
        setattr(session, _PENDING_ATTR, [])
        setattr(session, _CONFLICT_ATTR, {})
        log.info("datasets.retirement_queue_dropped", reason="transaction rolled back")

    setattr(target, "_crimelink_rollback_guard_armed", True)


def _queue(session: AsyncSession, work: dict[str, Any]) -> None:
    pending = getattr(session, _PENDING_ATTR, None)
    if pending is None:
        pending = []
        setattr(session, _PENDING_ATTR, pending)
        _arm_rollback_guard(session)
    pending.append(work)


def note_object_conflict(session: AsyncSession, bucket: str, key: str) -> None:
    """Record a key the incoming import could not write.

    The object store is write-once and keyed by dataset-relative path, so a
    corpus that contains ``evidence/brief.txt`` while the *still active* dataset
    holds different bytes under that key cannot store its own copy mid-import
    (destroying the active dataset's evidence is not an option).  The import
    reports it as a warning and records the key here; once the old dataset is
    retired the surviving bytes are written in their place by
    :func:`_restore_missing_objects`, hash-verified against the document row.
    """
    if not bucket or not key:
        return
    conflicts = getattr(session, _CONFLICT_ATTR, None)
    if conflicts is None:
        conflicts = {}
        setattr(session, _CONFLICT_ATTR, conflicts)
        _arm_rollback_guard(session)
    conflicts.setdefault(bucket, set()).add(key)


def _evict_graph(dataset_id: str) -> int:
    """Remove one dataset's projection.  Best-effort: the graph is rebuildable."""
    try:
        from app.container import get_container

        store = get_container().graph_store
        purge = getattr(store, "purge_dataset", None)
        if callable(purge):
            return int(purge(dataset_id) or 0)
    except Exception as exc:  # noqa: BLE001 - never fail a replacement on the graph
        log.warning("retirement.graph_purge_failed", dataset_id=dataset_id, error=str(exc))
    return 0


async def retire_dataset(
    session: AsyncSession,
    dataset: Dataset,
    *,
    delete_row: bool = False,
) -> RetiredDataset:
    """Remove everything *dataset* owns; queue the irreversible storage work.

    Rows and the graph projection go now (both are inside the caller's
    transaction or rebuildable); object and directory removal is queued for
    :func:`finalize_pending`, which the caller runs after committing.

    The ``datasets`` registration row is kept by default: it is the record that
    an import happened (with its job history, ``RETAINED_AFTER_RETIREMENT``),
    it is what makes the replacement visible in the dataset console, and it
    costs nothing once its data is gone.  ``delete_row=True`` is for an explicit
    "delete this dataset" action, not for replacement.
    """
    dataset_id = dataset.id
    name = dataset.name
    case_ids, _document_ids = await _owned_ids(session, dataset_id)
    pending_keys = await _object_keys_of(session, dataset_id, case_ids)
    paths = _workspace_paths(dataset)

    rows = await delete_dataset_rows(session, dataset_id)

    if delete_row:
        result = await session.execute(delete(Dataset).where(Dataset.id == dataset_id))
        rows["dataset_row"] = int(result.rowcount or 0)

    graph_nodes = _evict_graph(dataset_id)
    await session.flush()

    retired = RetiredDataset(
        dataset_id=dataset_id,
        name=name,
        rows=rows,
        graph_nodes_evicted=graph_nodes,
        pending_object_keys={bucket: sorted(keys) for bucket, keys in pending_keys.items()},
        pending_paths=[str(path) for path in paths],
        row_deleted=delete_row,
    )
    _queue(
        session,
        {
            "kind": "retire",
            "retired": retired,
            "keep_dataset_id": None,  # filled in by retire_replaced_datasets
        },
    )
    log.info(
        "datasets.retired",
        dataset_id=dataset_id,
        name=name,
        rows_removed=retired.rows_removed,
        graph_nodes_evicted=graph_nodes,
        objects_queued=sum(len(keys) for keys in pending_keys.values()),
        dataset_row_deleted=delete_row,
        **rows,
    )
    return retired


async def replaced_dataset_ids(session: AsyncSession, keep_dataset_id: str) -> list[str]:
    """The datasets this activation replaces: the ones that were ACTIVE before.

    Captured *before* :func:`app.datasets.registry.set_only_active` clears the
    flag, so the answer survives the deactivation.  A dataset that was never
    active is not replaced by anything — it is a candidate the operator may
    still activate, and deleting it is :func:`purge_dataset_data`'s explicit
    job, not a side effect of switching the haystack.
    """
    return list(
        (
            await session.execute(
                select(Dataset.id)
                .where(Dataset.is_active.is_(True), Dataset.id != keep_dataset_id)
                .order_by(Dataset.created_at)
            )
        ).scalars()
    )


async def retire_replaced_datasets(
    session: AsyncSession,
    keep_dataset_id: str,
    *,
    replaced_ids: list[str] | None = None,
    delete_dataset_row: bool = False,
) -> RetirementReport:
    """Retire the datasets the newly active one replaces.

    The single entry point used by :func:`app.datasets.registry.activate`, so
    an import, an explicit ``POST /datasets/{id}/activate`` and a repair script
    all reclaim storage the same way.  ``replaced_ids`` is how the caller names
    them (registry.activate resolves it before deactivating); when omitted the
    currently active datasets are the ones being replaced.

    A dataset that was *never* active is not a replacement casualty: it is a
    candidate the operator may still activate, and clearing it stays an explicit
    :func:`app.datasets.registry.purge_dataset_data` call.
    """
    report = RetirementReport(keep_dataset_id=keep_dataset_id)
    if replaced_ids is None:
        replaced_ids = await replaced_dataset_ids(session, keep_dataset_id)
    wanted = [str(dataset_id) for dataset_id in replaced_ids if dataset_id != keep_dataset_id]
    others = (
        list(
            (
                await session.execute(
                    select(Dataset)
                    .where(Dataset.id.in_(wanted))
                    .order_by(Dataset.created_at)
                )
            ).scalars()
        )
        if wanted
        else []
    )
    for dataset in others:
        report.retired.append(
            await retire_dataset(session, dataset, delete_row=delete_dataset_row)
        )
    for work in getattr(session, _PENDING_ATTR, []) or []:
        if work.get("kind") == "retire":
            work["keep_dataset_id"] = keep_dataset_id
    if report.retired:
        log.info(
            "datasets.replacement_completed",
            active_dataset_id=keep_dataset_id,
            retired=[item.dataset_id for item in report.retired],
            rows_removed=report.rows_removed,
        )
    return report


# --------------------------------------------------------------------------- #
# Deferred storage reclamation (runs after the transaction commits)
# --------------------------------------------------------------------------- #


async def finalize_pending(session: AsyncSession) -> dict[str, Any]:
    """Execute the queued storage work.  Call this *after* the commit.

    Idempotent and safe to call with nothing queued.  Every step is
    best-effort: a replacement that already committed must not be reported as
    failed because an object store refused a delete — but the shortfall is
    counted and logged, so reclaimed storage is a measured number rather than
    an assumption.
    """
    pending = list(getattr(session, _PENDING_ATTR, []) or [])
    if not pending:
        return {"finalized": False, "objects_deleted": 0, "bytes_reclaimed": 0}
    setattr(session, _PENDING_ATTR, [])

    summary = {
        "finalized": True,
        "objects_deleted": 0,
        "bytes_reclaimed": 0,
        "objects_restored": 0,
        "workspace_dirs_removed": [],
        "failures": [],
    }
    keep_ids = {work.get("keep_dataset_id") for work in pending if work.get("keep_dataset_id")}
    conflicts: dict[str, set[str]] = dict(getattr(session, _CONFLICT_ATTR, {}) or {})
    setattr(session, _CONFLICT_ATTR, {})

    for work in pending:
        if work.get("kind") != "retire":
            continue
        retired: RetiredDataset = work["retired"]

        # --- objects ---------------------------------------------------------
        store = None
        try:
            from app.container import get_container

            store = get_container().object_store
        except Exception as exc:  # noqa: BLE001 - no store, nothing to reclaim
            summary["failures"].append(f"object store unavailable: {exc}")
        remover = getattr(store, "delete", None) if store is not None else None
        if callable(remover):
            for bucket, keys in retired.pending_object_keys.items():
                for key in keys:
                    size = 0
                    try:
                        info = store.stat(bucket, key)
                        size = int(info.size or 0) if info is not None else 0
                    except Exception:  # noqa: BLE001 - size is accounting only
                        size = 0
                    try:
                        if remover(bucket, key):
                            retired.objects_deleted += 1
                            retired.bytes_reclaimed += size
                    except Exception as exc:  # noqa: BLE001 - report, never raise
                        summary["failures"].append(f"{bucket}/{key}: {exc}")
            summary["objects_deleted"] += retired.objects_deleted
            summary["bytes_reclaimed"] += retired.bytes_reclaimed
        elif retired.pending_object_keys:
            summary["failures"].append(
                "the object store backend cannot delete objects; the retired "
                "dataset's files were left in place"
            )

        # --- workspace directories ------------------------------------------
        for path in retired.pending_paths:
            directory = Path(path)
            try:
                if directory.is_dir():
                    shutil.rmtree(directory, ignore_errors=True)
                    retired.workspace_removed = not directory.exists()
                    if retired.workspace_removed:
                        summary["workspace_dirs_removed"].append(path)
            except Exception as exc:  # noqa: BLE001
                summary["failures"].append(f"{path}: {exc}")

    # --- restore the surviving dataset's evidence bytes ----------------------
    # A write-once object store keyed by relative path means two corpora that
    # both contain ``cases.csv`` collide: while the old dataset was still
    # active its bytes occupied the key, so the incoming import could not store
    # its own.  Now that the retired keys are free, re-store the surviving
    # dataset's bytes from its workspace — only when they hash to the value the
    # document row recorded, so nothing is stored that contradicts the record.
    for keep_id in sorted(item for item in keep_ids if item):
        try:
            restored = await _restore_missing_objects(session, keep_id, conflicts=conflicts)
        except Exception as exc:  # noqa: BLE001
            restored, reason = 0, f"{type(exc).__name__}: {exc}"
            summary["failures"].append(f"restore for {keep_id}: {reason}")
        summary["objects_restored"] += restored
    summary["object_conflicts"] = sum(len(keys) for keys in conflicts.values())

    log.info(
        "datasets.storage_reclaimed",
        objects_deleted=summary["objects_deleted"],
        bytes_reclaimed=summary["bytes_reclaimed"],
        objects_restored=summary["objects_restored"],
        workspace_dirs_removed=len(summary["workspace_dirs_removed"]),
        failures=summary["failures"][:10],
    )
    return summary


async def _restore_missing_objects(
    session: AsyncSession,
    dataset_id: str,
    *,
    conflicts: dict[str, set[str]] | None = None,
) -> int:
    """Make the surviving dataset's evidence bytes match its own records.

    Two situations, both created by the replacement itself:

    * the object is **absent** — the retired dataset's rows held the key, so the
      incoming import could not write its own copy;
    * the object **holds the retired dataset's bytes** — the key was a recorded
      conflict, so what is stored is not what this dataset's document row
      describes.

    Either way the bytes come from the surviving dataset's own workspace and are
    only written when they hash to the value the document row recorded, so a
    restore can never store content that contradicts the record.  A key another
    surviving row still claims is never rewritten.
    """
    from app.container import get_container

    dataset = await session.get(Dataset, dataset_id)
    if dataset is None or not dataset.root_path:
        return 0
    root = Path(dataset.root_path).resolve()
    if not root.is_dir():
        return 0

    container = get_container()
    store = container.object_store
    bucket = container.settings.minio_bucket_documents
    rows = (
        await session.execute(
            select(CaseDocument.storage_key, CaseDocument.content_hash, CaseDocument.source_metadata)
            .where(CaseDocument.dataset_id == dataset_id)
        )
    ).all()

    contested = {str(key) for key in (conflicts or {}).get(bucket, ()) or ()}
    foreign: set[str] = set()
    if contested:
        for other_key, other_dataset in (
            await session.execute(
                select(CaseDocument.storage_key, CaseDocument.dataset_id).where(
                    CaseDocument.storage_key.in_(sorted(contested))
                )
            )
        ).all():
            if other_dataset != dataset_id:
                foreign.add(other_key)

    restored = 0
    for storage_key, recorded_hash, metadata in rows:
        if not storage_key:
            continue
        present = store.exists(bucket, storage_key)
        if present and storage_key not in contested:
            continue  # untouched by this replacement
        if not present and storage_key in foreign:
            continue  # another surviving record owns the key now

        relative = ((metadata or {}).get("relative_path") or storage_key).replace("\\", "/")
        candidate = (root / relative.lstrip("/")).resolve()
        try:
            if not candidate.is_relative_to(root):
                continue
        except AttributeError:  # pragma: no cover - Python 3.8
            continue
        if not candidate.is_file():
            continue
        payload = candidate.read_bytes()
        if recorded_hash and content_hash(payload) != recorded_hash:
            # The file on disk is not the file the record describes: storing it
            # would make the hash check lie.  Leave the object as it is.
            log.warning(
                "retirement.restore_hash_mismatch",
                dataset_id=dataset_id,
                key=storage_key,
            )
            continue
        if present:
            if storage_key in foreign:
                continue
            try:
                if content_hash(store.get(bucket, storage_key)) == recorded_hash:
                    continue  # already this dataset's bytes
            except Exception as exc:  # noqa: BLE001 - unreadable counts as absent
                log.warning(
                    "retirement.restore_read_failed",
                    dataset_id=dataset_id,
                    key=storage_key,
                    error=str(exc),
                )
            if not store.delete(bucket, storage_key):
                summary_note = "the object store refused to release the retired key"
                log.warning(
                    "retirement.restore_blocked", dataset_id=dataset_id, key=storage_key,
                    detail=summary_note,
                )
                continue
        try:
            store.put(bucket, storage_key, payload)
            restored += 1
        except Exception as exc:  # noqa: BLE001 - best effort
            log.warning(
                "retirement.restore_failed",
                dataset_id=dataset_id,
                key=storage_key,
                error=str(exc),
            )
    if restored:
        log.info("retirement.objects_restored", dataset_id=dataset_id, count=restored)
    return restored


# --------------------------------------------------------------------------- #
# Upload staging
# --------------------------------------------------------------------------- #


def cleanup_upload_staging(sources: Iterable[Path | str]) -> list[str]:
    """Delete the temporary directories an HTTP upload was staged into.

    The import endpoint writes the browser's files to
    ``<data_dir>/uploads/<uuid>`` and the pipeline copies them into the
    dataset's own workspace, so the staging copy is dead weight the moment the
    import finishes — successful *or* failed.  On a limited-capacity deployment
    that duplicate is exactly the kind of thing that must not accumulate.

    Strictly scoped: only a path inside the staging root is removed.  An
    operator-supplied folder (``POST /datasets/import/path``) is never touched,
    and neither is a dataset workspace.
    """
    staging_root = (Path(get_settings().data_dir) / "uploads").resolve()
    removed: list[str] = []
    for source in sources or []:
        candidate = Path(source)
        try:
            resolved = candidate.resolve()
        except OSError:  # pragma: no cover
            continue
        try:
            inside = resolved.is_relative_to(staging_root) and resolved != staging_root
        except AttributeError:  # pragma: no cover - Python 3.8
            inside = False
        if not inside:
            continue
        shutil.rmtree(resolved, ignore_errors=True)
        if not resolved.exists():
            removed.append(str(resolved))
    if removed:
        log.info("datasets.upload_staging_cleaned", paths=removed)
    return removed
