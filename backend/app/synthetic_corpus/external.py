"""External synthetic corpus ingestion — explicit operator action.

Reads the external synthetic corpus configured by
``CRIMELINK_SYNTHETIC_DATA_ROOT`` (mode ``CRIMELINK_SYNTHETIC_DATA_MODE=external``)
and feeds every supported file through the *standard* CrimeLink ingestion
path — the exact same ``upload_document`` service an investigator upload
uses, followed by the six-stage pipeline (parse → deterministic extraction →
NLP → entity resolution → graph injection → pattern detection).  Nothing is
written to PostgreSQL/Neo4j directly and nothing bypasses the pipeline.

Safety and isolation guarantees:

* Ingestion is always explicit; application startup never imports the corpus.
* Only ``operational/`` and ``documents/`` are read. ``ground_truth/``
  (evaluation answers) and ``metadata/`` are never operational input.
* Idempotent by construction: cases are matched by deterministic case number
  (``SYN-EXT/<group>``) and documents by ``UNIQUE(case_id, content_hash)`` —
  re-running the command reports records as duplicates instead of copying
  them, and the graph's provenance keys make re-processing converge.
* Every imported record carries ``source_environment=synthetic`` and
  ``source_confidence=SYNTHETIC`` — never presented as real police data.
* Refuses to run against a production environment without an explicit
  ``--yes-i-am-sure``.

CLI::

    python -m app.synthetic_corpus.external                # ingest configured root
    python -m app.synthetic_corpus.external --dry-run      # validate/classify only
    python -m app.synthetic_corpus.external --root ../CrimeLink_Synthetic_Corpus_v1
    python -m app.cli ingest-synthetic                     # mode-aware umbrella CLI
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.logging import configure_logging, get_logger

log = get_logger("crimelink.synthetic.external")

JURISDICTION_ID = "SYN-DEV"
CASE_TITLE_PREFIX = "[SYNTHETIC]"


@dataclass(slots=True)
class IngestedFileResult:
    """Outcome of a single accepted corpus file."""

    relative_path: str
    case_number: str
    document_type: str
    status: str             # "uploaded" | "duplicate" | "rejected" | "failed"
    document_id: str | None = None
    detail: str | None = None


@dataclass(slots=True)
class ExternalIngestReport:
    """Operator-facing summary of one external-corpus ingestion run."""

    root: str
    mode: str = "external"
    files: list[IngestedFileResult] = field(default_factory=list)
    unsupported: list[dict[str, str]] = field(default_factory=list)
    excluded: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cases: dict[str, str] = field(default_factory=dict)   # case_number -> case id
    records_processed: int = 0
    #: Rows read from the corpus that could not be attached to any case.  Kept
    #: as data, not just a count, so the operator sees which rows were skipped.
    quarantined_rows: list[dict[str, Any]] = field(default_factory=list)
    quarantined_new: int = 0
    import_run_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex[:16])
    elapsed_seconds: float = 0.0
    pipeline: dict[str, Any] = field(default_factory=dict)  # filled when CLI waits

    # ------------------------------------------------------------ counters
    @property
    def uploaded(self) -> int:
        return sum(1 for f in self.files if f.status == "uploaded")

    @property
    def duplicates(self) -> int:
        return sum(1 for f in self.files if f.status == "duplicate")

    @property
    def rejected(self) -> int:
        return sum(1 for f in self.files if f.status == "rejected")

    @property
    def failed(self) -> int:
        return sum(1 for f in self.files if f.status == "failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "root": self.root,
            "records_discovered": (
                len(self.files) + len(self.unsupported) + len(self.excluded)
            ),
            "records_accepted": len(self.files),
            "records_ingested": self.uploaded,
            "records_processed": self.records_processed,
            "records_skipped_duplicates": self.duplicates,
            "records_rejected": self.rejected + len(self.unsupported),
            "records_failed": self.failed,
            "excluded_evaluation_files": len(self.excluded),
            "cases": self.cases,
            "warnings": self.warnings,
            "unsupported": self.unsupported,
            "excluded": self.excluded,
            "files": [
                {
                    "relative_path": f.relative_path,
                    "case_number": f.case_number,
                    "document_type": f.document_type,
                    "status": f.status,
                    "document_id": f.document_id,
                    "detail": f.detail,
                }
                for f in self.files
            ],
            "pipeline": self.pipeline,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


async def _ensure_external_cases(
    case_specs: dict[str, dict[str, str]], user_id: str
) -> dict[str, str]:
    """Find-or-create the synthetic cases; returns case_number -> case id.

    Mirrors ``app.synthetic_corpus.generate._ensure_cases``: matching by
    deterministic case number makes re-ingestion converge instead of
    duplicating cases.  Titles carry the ``[SYNTHETIC]`` marker.
    Existing cases are left unchanged (idempotent).
    """
    from sqlalchemy import select

    from app.db.base import new_uuid
    from app.db.models import Case as DBCase
    from app.db.session import async_session
    from app.domain.enums import CaseStatus

    mapping: dict[str, str] = {}
    async with async_session() as session:
        for case_number in sorted(case_specs):
            spec = case_specs[case_number]
            existing = (
                await session.execute(
                    select(DBCase).where(DBCase.case_number == case_number)
                )
            ).scalar_one_or_none()
            if existing is not None:
                mapping[case_number] = existing.id
                continue
            status_raw = (spec.get("status") or "OPEN").strip().upper()
            try:
                status = CaseStatus(status_raw)
            except ValueError:
                status = CaseStatus.OPEN
            title = spec.get("title") or (
                f"{CASE_TITLE_PREFIX} External synthetic corpus — "
                f"{spec.get('case_key') or case_number}"
            )
            if not title.startswith(CASE_TITLE_PREFIX):
                title = f"{CASE_TITLE_PREFIX} {title}"
            db_case = DBCase(
                id=new_uuid(),
                case_number=case_number,
                title=title,
                jurisdiction_id=JURISDICTION_ID,
                status=status,
                created_by=user_id,
            )
            session.add(db_case)
            await session.flush()
            mapping[case_number] = db_case.id
    return mapping


def _dataset_version(root: Path) -> str | None:
    """Read the corpus's own declared version, rather than inventing one.

    Quarantined rows outlive the run that produced them, so recording which
    dataset they came from is what lets a later import tell "still unresolved"
    apart from "belongs to an older corpus".
    """
    import json

    config = Path(root) / "metadata" / "generation_config.json"
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = payload.get("dataset_version")
    return str(version) if version is not None else None


async def ingest_external_corpus(
    *,
    root: str | Path | None = None,
    safety_confirmed: bool = False,
    settings: Settings | None = None,
    container: Any | None = None,
) -> ExternalIngestReport:
    """Validate and ingest the external synthetic corpus via ``upload_document``.

    Raises :class:`ExternalCorpusError` (a ``RuntimeError``) when the corpus
    cannot be used at all — missing root, missing ``operational/`` or
    ``documents/`` directory — so the failure is loud and names the path.
    """
    from app.adapters.sources import get_source_adapter
    from app.adapters.sources.synthetic_external import ExternalCorpusError
    from app.container import get_container
    from app.db.models import Case as DBCase
    from app.db.models import User
    from app.db.session import async_session, init_db
    from app.domain.enums import DocumentType
    from app.errors import ConflictError, ValidationFailedError
    from app.security.deps import Principal
    from app.services import documents as doc_service
    from app.synthetic_corpus.generate import _ensure_system_user

    settings = settings or get_settings()
    if settings.environment == "production" and not safety_confirmed:
        raise RuntimeError(
            "Refusing to ingest the synthetic corpus against a production environment. "
            "Pass --yes-i-am-sure (safety_confirmed=True) if you really want to proceed."
        )

    started = time.time()
    adapter = get_source_adapter(
        "synthetic_external", root=str(root) if root is not None else None
    )
    resolved = adapter.resolve_root()
    scan = adapter.scan()
    if scan.issues:
        raise ExternalCorpusError(
            "External synthetic corpus is not ingestible: " + "; ".join(scan.issues)
        )

    report = ExternalIngestReport(root=str(resolved))
    for entry in scan.files:
        if entry.status in ("unsupported", "unreadable"):
            report.unsupported.append(
                {
                    "relative_path": entry.relative_path,
                    "status": entry.status,
                    "reason": entry.reason or "",
                }
            )
        elif entry.status == "excluded":
            report.excluded.append(
                {"relative_path": entry.relative_path, "reason": entry.reason or ""}
            )

    accepted = [r for r in adapter.records_from_scan(scan)]
    report.warnings.extend(scan.warnings)
    report.quarantined_rows = list(scan.quarantined)
    report.records_processed = sum(int(r.metadata.get("row_count") or 1) for r in accepted)
    if not accepted:
        report.elapsed_seconds = time.time() - started
        log.warning("synthetic.external.nothing_to_ingest", root=str(resolved))
        return report

    await init_db()                      # idempotent; never drops anything
    container = container or get_container()
    user_id = await _ensure_system_user()

    # Rows the adapter could read but could not attach to a case are recorded
    # before any document is uploaded, so a run that is interrupted still
    # leaves an auditable account of what was skipped and why.
    if scan.quarantined:
        from app.services.quarantine import persist_quarantined_records

        async with async_session() as q_session:
            report.quarantined_new = await persist_quarantined_records(
                q_session,
                scan.quarantined,
                dataset_version=_dataset_version(resolved),
                import_run_id=report.import_run_id,
            )
            await q_session.commit()

    case_specs: dict[str, dict[str, str]] = {}
    for record in accepted:
        spec = case_specs.setdefault(record.case_number, {})
        if record.metadata.get("case_title"):
            spec["title"] = str(record.metadata["case_title"])
        if record.metadata.get("case_status"):
            spec["status"] = str(record.metadata["case_status"])
        spec.setdefault(
            "case_key",
            str(record.metadata.get("case_key") or record.case_number),
        )
    case_map = await _ensure_external_cases(case_specs, user_id)
    report.cases = dict(sorted(case_map.items()))

    async with async_session() as session:
        system_user = await session.get(User, user_id)
        if system_user is None:  # pragma: no cover - defensive
            raise RuntimeError("Synthetic system user not found")
        principal = Principal(system_user)
        for record in accepted:
            result = IngestedFileResult(
                relative_path=record.metadata["relative_path"],
                case_number=record.case_number,
                document_type=(
                    record.document_type.value
                    if isinstance(record.document_type, DocumentType)
                    else str(record.document_type)
                ),
                status="failed",
            )
            report.files.append(result)
            case_obj = await session.get(DBCase, case_map[record.case_number])
            if case_obj is None:  # pragma: no cover - defensive
                result.detail = "Synthetic case row missing."
                continue
            payload = (
                record.content.encode("utf-8")
                if isinstance(record.content, str)
                else record.content
            )
            try:
                document, _job = await doc_service.upload_document(
                    session,
                    container=container,
                    case=case_obj,
                    principal=principal,
                    filename=record.filename,
                    payload=payload,
                    document_type=record.document_type,
                    source_confidence=record.source_confidence,
                    mime_type=record.content_type,
                    language_hint=record.language,
                    source_metadata={
                        key: record.metadata[key]
                        for key in ("document_origin", "line_origins", "relative_path", "verbatim")
                        if record.metadata.get(key) is not None
                    },
                )
            except ConflictError as exc:
                await session.rollback()
                result.status = "duplicate"
                result.detail = str(exc)
                log.debug(
                    "synthetic.external.duplicate", file=result.relative_path
                )
            except ValidationFailedError as exc:
                await session.rollback()
                result.status = "rejected"
                result.detail = str(exc)
                log.warning(
                    "synthetic.external.rejected",
                    file=result.relative_path,
                    reason=str(exc),
                )
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop a run
                await session.rollback()
                result.status = "failed"
                result.detail = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "synthetic.external.failed",
                    file=result.relative_path,
                    error=str(exc),
                )
            else:
                result.status = "uploaded"
                result.document_id = document.id

    report.elapsed_seconds = time.time() - started
    log.info(
        "synthetic.external.ingested",
        root=str(resolved),
        uploaded=report.uploaded,
        duplicates=report.duplicates,
        rejected=report.rejected,
        failed=report.failed,
        unsupported=len(report.unsupported),
        elapsed=round(report.elapsed_seconds, 2),
    )
    return report


async def await_pipeline_quiet(
    container: Any, report: ExternalIngestReport, *, timeout_seconds: float
) -> dict[str, Any]:
    """Best-effort wait for the in-process broker, then summarise outcomes.

    Only meaningful for the embedded/inline broker (or the recording broker in
    tests after a drain); a Celery deployment reports asynchronously through
    the jobs API instead.
    """
    from sqlalchemy import func, select

    from app.db.models import CaseDocument, IngestionJob
    from app.db.session import async_session

    pending_fn = getattr(container.broker, "health", None)
    deadline = time.time() + timeout_seconds
    pending = None
    if callable(pending_fn):
        while time.time() < deadline:
            pending = container.broker.health().get("pending_jobs")
            if not pending:
                break
            await asyncio.sleep(0.5)
    summary: dict[str, Any] = {"pending_jobs": pending}
    doc_ids = [f.document_id for f in report.files if f.document_id]
    if not doc_ids:
        return summary
    async with async_session() as session:
        rows = (
            await session.execute(
                select(CaseDocument.ingestion_status, func.count(CaseDocument.id)).where(
                    CaseDocument.id.in_(doc_ids)
                ).group_by(CaseDocument.ingestion_status)
            )
        ).all()
        summary["documents_by_status"] = {
            (status.value if hasattr(status, "value") else str(status)): int(count)
            for status, count in rows
        }
        quarantined = (
            await session.execute(
                select(CaseDocument.filename, CaseDocument.failure_reason).where(
                    CaseDocument.id.in_(doc_ids), CaseDocument.quarantined.is_(True)
                )
            )
        ).all()
        summary["quarantined"] = [
            {"filename": name, "reason": reason} for name, reason in quarantined
        ]
        jobs = (
            await session.execute(
                select(IngestionJob.status, func.count(IngestionJob.id)).where(
                    IngestionJob.doc_id.in_(doc_ids)
                ).group_by(IngestionJob.status)
            )
        ).all()
        summary["jobs_by_status"] = {
            (status.value if hasattr(status, "value") else str(status)): int(count)
            for status, count in jobs
        }
    try:
        summary["graph"] = container.graph_store.stats()
    except Exception as exc:  # noqa: BLE001 - stats are best-effort
        log.debug("synthetic.external.graph_stats_failed", error=str(exc))
    return summary


async def corpus_status(container: Any | None = None) -> dict[str, Any]:
    """Live dataset / ingestion status for the administration UI.

    Counts come from the application database and graph — never hardcoded.
    """
    from sqlalchemy import func, select

    from app.adapters.sources import get_source_adapter
    from app.container import get_container
    from app.db.models import Case, CaseDocument, DetectedPattern, EntityResolutionItem
    from app.db.session import async_session
    from app.domain.enums import IngestionStatus, PatternStatus, ResolutionStatus

    settings = get_settings()
    container = container or get_container()
    adapter = get_source_adapter("synthetic_external")
    root = adapter.resolve_root()
    scan = adapter.scan()

    pending_jobs = None
    try:
        pending_jobs = container.broker.health().get("pending_jobs")
    except Exception:  # noqa: BLE001
        pending_jobs = None

    async with async_session() as session:
        case_count = int(
            (
                await session.execute(
                    select(func.count(Case.id)).where(Case.jurisdiction_id == JURISDICTION_ID)
                )
            ).scalar()
            or 0
        )
        doc_rows = (
            await session.execute(
                select(CaseDocument.ingestion_status, func.count(CaseDocument.id))
                .join(Case, Case.id == CaseDocument.case_id)
                .where(Case.jurisdiction_id == JURISDICTION_ID)
                .group_by(CaseDocument.ingestion_status)
            )
        ).all()
        documents_by_status = {
            (status.value if hasattr(status, "value") else str(status)): int(count)
            for status, count in doc_rows
        }
        documents_total = sum(documents_by_status.values())
        pending_matches = int(
            (
                await session.execute(
                    select(func.count(EntityResolutionItem.id)).where(
                        EntityResolutionItem.status == ResolutionStatus.PENDING
                    )
                )
            ).scalar()
            or 0
        )
        new_patterns = int(
            (
                await session.execute(
                    select(func.count(DetectedPattern.id)).where(
                        DetectedPattern.status == PatternStatus.NEW
                    )
                )
            ).scalar()
            or 0
        )

    busy_docs = sum(
        documents_by_status.get(name, 0)
        for name in (
            IngestionStatus.PENDING.value,
            IngestionStatus.PROCESSING.value,
        )
    )
    graph: dict[str, Any] = {}
    try:
        graph = container.graph_store.stats()
    except Exception as exc:  # noqa: BLE001
        graph = {"error": str(exc)}

    stage_hint = "Idle"
    if pending_jobs or busy_docs:
        if documents_by_status.get(IngestionStatus.PROCESSING.value):
            stage_hint = "Importing documents / resolving entities / updating graph"
        else:
            stage_hint = "Importing structured data"

    return {
        "mode": settings.synthetic_data_mode,
        "root": str(root),
        "root_exists": root.is_dir(),
        "dataset_name": root.name,
        "jurisdiction_id": JURISDICTION_ID,
        "scan": scan.summary(),
        "cases": case_count,
        "documents_total": documents_total,
        "documents_by_status": documents_by_status,
        "pending_jobs": pending_jobs,
        "pending_matches": pending_matches,
        "new_patterns": new_patterns,
        "graph": graph,
        "busy": bool(pending_jobs) or busy_docs > 0,
        "stage_hint": stage_hint,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_scan(root: Path, scan) -> None:
    print(f"\n=== External synthetic corpus — dry run ===\nroot: {root}\n")
    if scan.issues:
        print("FATAL problems:")
        for issue in scan.issues:
            print(f"  ! {issue}")
    for entry in scan.files:
        dtype = entry.document_type.value if entry.document_type else "-"
        reason = f"  ({entry.reason})" if entry.reason else ""
        print(f"  [{entry.status:>11}] {entry.relative_path} -> {dtype}{reason}")
    for warning in scan.warnings:
        print(f"  warning: {warning}")
    summary = scan.summary()
    print(f"\n  counts: {summary['counts']}\n")


def _print_report(report: ExternalIngestReport) -> None:
    data = report.to_dict()
    print("\n=== External synthetic corpus ingestion ===")
    print(f"  root                      {data['root']}")
    print(f"  records_discovered        {data['records_discovered']}")
    print(f"  records_accepted          {data['records_accepted']}")
    print(f"  records_ingested          {data['records_ingested']}")
    print(f"  records_skipped_dupes     {data['records_skipped_duplicates']}")
    print(f"  records_rejected          {data['records_rejected']}")
    print(f"  records_failed            {data['records_failed']}")
    print(f"  excluded_evaluation_files {data['excluded_evaluation_files']}")
    if data["cases"]:
        print("  cases:")
        for number in data["cases"]:
            print(f"    {number}")
    if data["pipeline"]:
        print("  pipeline:")
        print("    " + json.dumps(data["pipeline"], default=str))
    for warning in data["warnings"]:
        print(f"  warning: {warning}")
    for item in data["unsupported"]:
        print(f"  unsupported: {item['relative_path']} — {item['reason']}")
    for item in data["excluded"]:
        print(f"  excluded: {item['relative_path']} — {item['reason']}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest the external synthetic corpus through the standard pipeline"
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Corpus root (default: CRIMELINK_SYNTHETIC_DATA_ROOT, resolved "
        "relative to the CrimeLink repository when relative)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Discover/validate/classify only; write nothing to the database",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=600.0,
        metavar="SECONDS",
        help="Wait for embedded-broker jobs to finish and report pipeline "
        "outcomes (default: 600; 0 disables)",
    )
    parser.add_argument(
        "--yes-i-am-sure",
        action="store_true",
        help="Safety override for non-dev environments",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)

    from app.adapters.sources import get_source_adapter
    from app.adapters.sources.synthetic_external import ExternalCorpusError

    adapter = get_source_adapter(
        "synthetic_external", root=args.root
    )
    resolved = adapter.resolve_root()
    scan = adapter.scan()

    if args.dry_run:
        _print_scan(resolved, scan)
        return 2 if scan.issues else 0

    async def _run() -> ExternalIngestReport:
        report = await ingest_external_corpus(
            root=args.root, safety_confirmed=args.yes_i_am_sure
        )
        if args.wait and report.uploaded:
            from app.container import get_container

            report.pipeline = await await_pipeline_quiet(
                get_container(), report, timeout_seconds=args.wait
            )
        return report

    try:
        report = asyncio.run(_run())
    except (ExternalCorpusError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
