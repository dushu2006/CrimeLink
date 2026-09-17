"""Source and evidence navigation — MinIO-aware backend of the Source Viewer.

Two questions, one module:

* *where exactly did this piece of information come from?*  The reference
  endpoints answer it with bounded windows of the real dataset files, never
  whole files, under the same authorisation as ordinary resource access — a
  source reference is not a bypass, and every read is recorded as
  ``DOC_VIEW`` in the audit log.
* *what does the underlying file actually look like?*  ``/sources/files``
  lists the **active dataset's** manifest and ``/sources/preview`` renders
  each file with the matching renderer (PDF pages for PDFs, tables for
  CSV/XLSX, formatted JSON, extracted DOCX/PPTX content) and an explicit
  state (AVAILABLE / UNSUPPORTED / CORRUPTED / NOT_FOUND / EXTRACTION_FAILED
  / NO_EXTRACTED_TEXT).

MinIO-aware: In production, files live in MinIO (bucket documents) with
deterministic keys evidence/E-042/original.pdf. This module tries MinIO first
when backend is minio, then filesystem workspace. In production, MinIO is
mandatory — fails loudly if unavailable, no silent Local fallback.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.datasets import registry
from app.db.models import Case, CaseDocument, Dataset, DatasetFile, SourceReference
from app.db.session import get_db_session
from app.errors import NotFoundError, ServiceUnavailableError
from app.security.deps import (
    AuditRecorder,
    JurisdictionScope,
    Principal,
    get_audit_recorder,
    get_principal,
    get_scope,
    require_roles,
)
from app.services import cases as case_service
from app.services import source_viewer
from app.services.source_viewer import SourceAccessError, SourceNotFoundError

router = APIRouter(prefix="/sources", tags=["sources"])

_RANGE = re.compile(r"bytes=(\\d*)-(\\d*)")
_RAW_CHUNK = 256 * 1024
SOURCE_URL_TTL_SECONDS = 900


def _reference_row(ref: SourceReference) -> dict:
    return {
        "id": ref.id,
        "doc_id": ref.doc_id,
        "case_id": ref.case_id,
        "origin_file": ref.origin_file,
        "source_type": ref.source_type,
        "record_id": ref.record_id,
        "row_number": ref.row_number,
        "field_names": list(ref.field_names or []),
        "field_values": dict(ref.field_values or {}),
        "page_number": ref.page_number,
        "line_start": ref.line_start,
        "line_end": ref.line_end,
        "text_start": ref.text_start,
        "text_end": ref.text_end,
        "excerpt": ref.excerpt,
    }


async def _active_dataset(session: AsyncSession) -> Dataset | None:
    return await registry.active_dataset(session)


async def _dataset_root(
    session: AsyncSession, dataset_id: str | None = None
) -> Path | None:
    """The dataset's on-disk workspace, or ``None``.

    ``None`` is **not** an error and must never be turned into one by a caller.
    The object store is CrimeLink's storage architecture; a dataset whose files
    live only there (the seeded demo corpus, any MinIO-backed deployment) has
    no workspace directory at all, and every source still opens normally.
    """
    dataset: Dataset | None = None
    if dataset_id:
        dataset = await registry.get_dataset(session, dataset_id)
    if dataset is None:
        dataset = await _active_dataset(session)
    if dataset is None or not dataset.root_path:
        return None
    root = Path(dataset.root_path)
    return root if root.is_dir() else None


def _assert_some_storage() -> None:
    """Fail only when *neither* a workspace nor the object store can serve a file.

    This is the honest "we cannot reach storage" error.  It is deliberately
    distinct from "the file you asked for does not exist", which is a 404
    naming the file — conflating the two is what made a missing-file question
    answer "Active dataset workspace is unavailable."
    """
    try:
        store, _bucket, _is_minio, _is_prod = _get_object_store_for_sources()
    except SourceAccessError:
        raise
    except Exception as exc:
        raise ServiceUnavailableError(f"Object storage is unreachable: {exc}") from exc
    if store is None:
        raise ServiceUnavailableError(
            "Object storage is not configured, so source files cannot be opened."
        )


def _sign_source_path(relative_path: str, expires_s: int = SOURCE_URL_TTL_SECONDS) -> tuple[int, str]:
    settings = get_settings()
    expiry = int(time.time()) + int(expires_s)
    payload = f"sources:{relative_path}:{expiry}"
    signature = hmac.new(
        settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return expiry, signature


def _verify_source_signature(relative_path: str, exp: int, sig: str) -> bool:
    settings = get_settings()
    try:
        if int(exp) < int(time.time()):
            return False
    except (TypeError, ValueError):
        return False
    expected = hmac.new(
        settings.secret_key.encode("utf-8"),
        f"sources:{relative_path}:{int(exp)}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, sig or "")


def _raw_url(relative_path: str) -> str:
    expiry, signature = _sign_source_path(relative_path)
    return (
        f"/api/v1/sources/raw?path={quote(relative_path, safe='/')}"
        f"&exp={expiry}&sig={signature}"
    )


def _get_object_store_for_sources():
    """Get object store — MinIO mandatory in production, fail loudly.

    The **container's** store is preferred over a freshly constructed one: the
    container is built from the application's live settings, so the sources
    routes read from exactly the same storage root the ingest pipeline wrote
    to.  Constructing a second store from ``get_settings()`` is what let the
    routes look in one directory while the dataset lived in another.
    """
    settings = get_settings()
    is_prod = settings.profile == "production" or settings.environment == "production"
    backend = settings.effective_object_store_backend
    try:
        if backend == "minio":
            from app.adapters.objectstore.minio_store import MinioObjectStore
            store = MinioObjectStore(settings)
            return store, settings.minio_bucket_documents, True, is_prod
        from app.container import get_container
        try:
            container_store = get_container().object_store
            if container_store is not None:
                return container_store, settings.minio_bucket_documents, False, is_prod
        except Exception:
            pass
        if is_prod:
            raise RuntimeError("MinIO mandatory in production but backend is not minio — refusing Local fallback")
        from app.adapters.objectstore.local import LocalObjectStore
        store = LocalObjectStore(settings)
        return store, settings.minio_bucket_documents, False, is_prod
    except Exception as exc:
        if is_prod:
            from app.logging import get_logger
            get_logger("crimelink.sources").error("sources.minio_unavailable_in_prod", error=str(exc))
            raise SourceAccessError(f"Object storage unavailable in production: {exc}", status=source_viewer.STATUS_NOT_FOUND) from exc
        try:
            from app.adapters.objectstore.local import LocalObjectStore
            store = LocalObjectStore(settings)
            return store, settings.minio_bucket_documents, False, is_prod
        except Exception:
            return None, settings.minio_bucket_documents, False, is_prod


# ---------------------------------------------------------------------------
# Listing: the active dataset's manifest — MinIO-aware
# ---------------------------------------------------------------------------

@router.get("/files")
async def dataset_files(
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    dataset = await _active_dataset(session)
    if dataset is None:
        return {
            "root": None,
            "dataset_id": None,
            "dataset_name": None,
            "dataset_version": None,
            "dataset_status": None,
            "ok": False,
            "issues": ["No dataset is active."],
            "warnings": [],
            "counts": {"files": 0},
            "items": [],
        }

    root = await _dataset_root(session, dataset.id)
    rows = list(
        (
            await session.execute(
                select(DatasetFile)
                .where(DatasetFile.dataset_id == dataset.id)
                .order_by(DatasetFile.relative_path)
            )
        ).scalars()
    )

    # Check MinIO existence for each file
    store, bucket, is_minio, is_prod = _get_object_store_for_sources() if rows else (None, None, False, False)
    minio_existence: dict[str, bool] = {}
    if store is not None:
        for r in rows:
            try:
                meta = store.stat(bucket, r.relative_path)
                minio_existence[r.relative_path] = meta is not None
            except Exception:
                minio_existence[r.relative_path] = False

    counts = {
        row[0]: int(row[1])
        for row in (
            await session.execute(
                select(SourceReference.origin_file, func.count(SourceReference.id))
                .where(SourceReference.dataset_id == dataset.id)
                .group_by(SourceReference.origin_file)
            )
        ).all()
    }
    doc_ids = [row.doc_id for row in rows if row.doc_id]
    documents_by_id: dict[str, CaseDocument] = {}
    case_number_by_id: dict[str, str] = {}
    if doc_ids:
        documents = (
            await session.execute(select(CaseDocument).where(CaseDocument.id.in_(doc_ids)))
        ).scalars()
        documents_by_id = {d.id: d for d in documents}
        case_rows = (
            await session.execute(
                select(Case.id, Case.case_number).where(
                    Case.id.in_({d.case_id for d in documents_by_id.values()})
                )
            )
        ).all()
        case_number_by_id = {row[0]: row[1] for row in case_rows}

    items = []
    for row in rows:
        if _is_evaluation_path(row.relative_path):
            continue
        document = documents_by_id.get(row.doc_id) if row.doc_id else None
        exists_fs = bool(root and (root / row.relative_path).is_file())
        exists_minio = minio_existence.get(row.relative_path, False)
        status, openable = _file_availability(row, exists_fs, exists_minio)
        items.append(
            {
                "source_id": row.id,
                "dataset_id": dataset.id,
                "path": row.relative_path,
                "filename": row.filename,
                "extension": row.extension,
                "media_type": row.media_type,
                "file_kind": row.file_kind,
                "semantic_type": row.semantic_type,
                "size_bytes": row.size_bytes,
                "status": status,
                "reason": row.reason,
                "ingestion_status": (
                    document.ingestion_status.value if document is not None else None
                ),
                "extraction_status": row.status,
                "page_count": row.page_count,
                "row_count": row.row_count,
                "sheets": row.sheet_names,
                "sha256": row.sha256,
                "container_path": row.container_path,
                "doc_id": row.doc_id,
                "document_type": (
                    document.document_type.value if document is not None else None
                ),
                "case_id": document.case_id if document is not None else None,
                "case_number": case_number_by_id.get(document.case_id)
                if document is not None
                else None,
                "reference_count": counts.get(row.relative_path, 0),
                "openable": openable,
                "readable": openable,
                "download_url": _raw_url(row.relative_path),
                "storage": "minio" if exists_minio else ("filesystem" if exists_fs else "missing"),
            }
        )
    return {
        "root": dataset.root_path,
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "dataset_version": dataset.version,
        "dataset_status": dataset.status,
        "ok": dataset.status == "READY",
        "issues": [dataset.error] if dataset.error else [],
        "warnings": [],
        "counts": {"files": len(items), **(dataset.stats or {}).get("files_by_status", {})},
        "items": items,
    }


def _file_availability(row: DatasetFile, exists_fs: bool, exists_minio: bool = False) -> tuple[str, bool]:
    """(status, openable) — MinIO-aware: either FS or MinIO counts as available."""
    if not exists_fs and not exists_minio:
        return source_viewer.STATUS_NOT_FOUND, False
    manifest = (row.status or "").upper()
    if manifest == "UNSUPPORTED":
        return source_viewer.STATUS_UNSUPPORTED, False
    if manifest == "CORRUPT":
        return source_viewer.STATUS_CORRUPTED, True
    if manifest == "SKIPPED":
        return "SKIPPED", False
    return source_viewer.STATUS_AVAILABLE, True


async def _legacy_corpus_files(session: AsyncSession) -> dict:
    from app.adapters.sources import get_source_adapter

    try:
        adapter = get_source_adapter("synthetic_external")
        scan = adapter.scan()
    except Exception as exc:
        return {
            "root": None,
            "dataset_id": None,
            "dataset_name": None,
            "ok": False,
            "issues": [f"No dataset is active and the bundled corpus is unavailable ({exc})."],
            "warnings": [],
            "counts": {"files": 0},
            "items": [],
        }

    counts = {
        row[0]: int(row[1])
        for row in (
            await session.execute(
                select(SourceReference.origin_file, func.count(SourceReference.id))
                .group_by(SourceReference.origin_file)
            )
        ).all()
    }
    items = []
    for entry in scan.files:
        if entry.status in {"excluded", "ignored"} or _is_evaluation_path(entry.relative_path):
            continue
        items.append(
            {
                "path": entry.relative_path,
                "filename": Path(entry.relative_path).name,
                "status": entry.status,
                "section": entry.section,
                "size_bytes": entry.size_bytes,
                "document_type": entry.document_type.value if entry.document_type else None,
                "reason": entry.reason,
                "openable": entry.status in {"accepted", "reference"},
                "readable": entry.status in {"accepted", "reference"},
                "reference_count": counts.get(entry.relative_path, 0),
            }
        )
    summary = scan.summary()
    sc = dict(summary.get("counts") or {})
    sc.setdefault("files", len(items))
    return {
        "root": summary["root"],
        "dataset_id": None,
        "dataset_name": summary["dataset_name"],
        "ok": summary["ok"],
        "issues": summary["issues"],
        "warnings": summary["warnings"],
        "counts": sc,
        "items": items,
    }


def _is_evaluation_path(path_str: str) -> bool:
    from app.adapters.sources.synthetic_external import NEVER_INGEST_COMPONENTS

    normalized = path_str.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    dir_parts = {p.lower().replace("-", "").replace("_", "") for p in parts[:-1]}
    if dir_parts & NEVER_INGEST_COMPONENTS:
        return True
    if any(p.startswith(("_", ".")) for p in parts):
        return True
    return False


def _evaluation_guard(clean: str) -> None:
    if _is_evaluation_path(clean):
        raise NotFoundError(
            "This file is evaluation-only material and is not available as evidence."
        )


def _reject_unsafe_path(clean: str) -> None:
    """Reject traversal, absolute and drive-qualified paths up front.

    Runs *before* the evaluation guard so a traversal attempt is answered with
    "your path is illegal" (422) rather than being misreported as
    evaluation-only material.  Resolution itself is additionally sandboxed by
    ``Path.is_relative_to`` and by the object store's own key lookup, so this
    is defence in depth, not the only barrier — but it is the one that gives
    the caller an accurate reason.
    """
    normalized = (clean or "").replace("\\", "/").split("#", 1)[0]
    if not normalized.strip():
        raise SourceAccessError(
            "No source file was specified.", status=source_viewer.STATUS_NOT_FOUND
        )
    if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
        raise SourceAccessError("Source paths must be relative to the dataset root.")
    if any(part == ".." for part in Path(normalized).parts):
        raise SourceAccessError("Source paths must not traverse outside the dataset.")


async def _resolve_source_path(
    session: AsyncSession,
    dataset: Dataset,
    root: Path | None,
    path: str | None = None,
    doc_id: str | None = None,
    dataset_file_id: str | None = None,
) -> tuple[Path | None, str, DatasetFile | None, CaseDocument | None, bytes | None]:
    """Resolve source file — MinIO-aware: returns (fs_path_or_none, relative_path, dataset_file, case_doc, minio_bytes_or_none).

    Tries object storage first (MinIO in production, the local object store in
    dev), then the dataset workspace on disk.

    ``root`` may be ``None``.  That is a normal state, not an error: a dataset
    whose files live only in the object store — which is exactly how the
    seeded demo corpus and any MinIO-backed deployment are built — has no
    workspace directory at all.  Callers must not treat that as "workspace
    unavailable"; the object store is the storage architecture, and the
    workspace copy is only ever a convenience.
    """
    base = root.resolve() if root is not None else None

    def _fs(rel: str) -> Path | None:
        """Resolve ``rel`` inside the workspace, or None if there is no workspace.

        The ``is_relative_to`` check is the traversal guard: a symlink or a
        ``..`` that escapes the workspace resolves outside ``base`` and is
        rejected rather than served.
        """
        if base is None:
            return None
        cand = (base / rel).resolve()
        return cand if (cand.is_file() and cand.is_relative_to(base)) else None

    # Helper to try MinIO for a relative path
    def _try_minio(rel: str) -> bytes | None:
        try:
            store, bucket, is_minio, is_prod = _get_object_store_for_sources()
            if store is None:
                return None
            clean_rel = rel.replace("\\", "/").lstrip("/")
            meta = store.stat(bucket, clean_rel)
            if not meta:
                return None
            return store.get(bucket, clean_rel)
        except SourceAccessError:
            raise
        except Exception:
            return None

    # 1. Explicit dataset_file_id lookup
    if dataset_file_id:
        df = await session.get(DatasetFile, dataset_file_id)
        if df and df.dataset_id == dataset.id:
            # Try MinIO first
            minio_data = _try_minio(df.relative_path)
            if minio_data is not None:
                doc = await session.get(CaseDocument, df.doc_id) if df.doc_id else None
                return None, df.relative_path, df, doc, minio_data
            cand = _fs(df.relative_path.replace("\\", "/").lstrip("/"))
            if cand is not None:
                doc = await session.get(CaseDocument, df.doc_id) if df.doc_id else None
                return cand, df.relative_path, df, doc, None

    # 2. Explicit doc_id lookup
    if doc_id:
        doc = await session.get(CaseDocument, doc_id)
        if doc:
            df = (
                await session.execute(
                    select(DatasetFile).where(
                        DatasetFile.doc_id == doc.id,
                        DatasetFile.dataset_id == dataset.id,
                    )
                )
            ).scalars().first()
            rel = df.relative_path if df else ((doc.source_metadata or {}).get("relative_path") or doc.storage_key)
            if rel:
                rel_clean = rel.replace("\\", "/").lstrip("/")
                minio_data = _try_minio(rel_clean)
                if minio_data is not None:
                    return None, rel_clean, df, doc, minio_data
                cand = _fs(rel_clean)
                if cand is not None:
                    return cand, rel_clean, df, doc, None

    # 3. Path-based resolution
    cleaned = (path or "").strip().replace("\\", "/").split("#", 1)[0].lstrip("/")
    if not cleaned:
        raise SourceAccessError("No source file was specified.", status=source_viewer.STATUS_NOT_FOUND)
    if cleaned.startswith("/") or (len(cleaned) > 1 and cleaned[1] == ":"):
        raise SourceAccessError("Source paths must be relative to the dataset root.")
    if any(part == ".." for part in Path(cleaned).parts):
        raise SourceAccessError("Source paths must not traverse outside the dataset.")

    # The active dataset must *own* this path before any bytes are served.
    #
    # Without this check a file left in the object store by a
    # previously-active dataset stayed readable after the dataset was
    # switched: the store lookup is by key alone and knows nothing about
    # datasets.  Ownership is proven by a DatasetFile or CaseDocument row of
    # the active dataset — the same rows /sources/files lists, so the listing
    # and the reader can never disagree.
    df = (
        await session.execute(
            select(DatasetFile).where(
                DatasetFile.dataset_id == dataset.id,
                DatasetFile.relative_path == cleaned,
            )
        )
    ).scalars().first()
    doc = await session.get(CaseDocument, df.doc_id) if (df and df.doc_id) else None
    if df is None:
        doc = (
            await session.execute(
                select(CaseDocument).where(
                    CaseDocument.dataset_id == dataset.id,
                    CaseDocument.storage_key == cleaned,
                )
            )
        ).scalars().first()

    if df is not None or doc is not None:
        minio_data = _try_minio(cleaned)
        if minio_data is not None:
            return None, cleaned, df, doc, minio_data
        cand = _fs(cleaned)
        if cand is not None:
            return cand, cleaned, df, doc, None
        # Registered but not stored: a data-integrity problem, reported as
        # such rather than as "the file does not exist".
        raise SourceNotFoundError(
            f"{cleaned} is registered to the active dataset but its bytes are "
            "not in object storage or the dataset workspace."
        )

    # 3a. Direct check on disk in the dataset workspace (if it has one)
    cand = _fs(cleaned)
    if cand is not None:
        df = (
            await session.execute(
                select(DatasetFile).where(
                    DatasetFile.dataset_id == dataset.id,
                    DatasetFile.relative_path == cleaned,
                )
            )
        ).scalars().first()
        doc = None
        if df and df.doc_id:
            doc = await session.get(CaseDocument, df.doc_id)
        elif not df:
            doc = (
                await session.execute(
                    select(CaseDocument).where(
                        CaseDocument.storage_key == cleaned,
                        CaseDocument.dataset_id == dataset.id,
                    )
                )
            ).scalars().first()
        return cand, cleaned, df, doc, None

    # 3b. Check if cleaned matches a doc_id — try MinIO via its storage_key
    doc = await session.get(CaseDocument, cleaned)
    if doc:
        df = (
            await session.execute(
                select(DatasetFile).where(
                    DatasetFile.doc_id == doc.id,
                    DatasetFile.dataset_id == dataset.id,
                )
            )
        ).scalars().first()
        rel = df.relative_path if df else ((doc.source_metadata or {}).get("relative_path") or doc.storage_key)
        if rel:
            rel_clean = rel.replace("\\", "/").lstrip("/")
            minio_data = _try_minio(rel_clean)
            if minio_data is not None:
                return None, rel_clean, df, doc, minio_data
            cand = _fs(rel_clean)
            if cand is not None:
                return cand, rel_clean, df, doc, None

    # 3c. Check if cleaned matches a dataset_file_id
    df = await session.get(DatasetFile, cleaned)
    if df and df.dataset_id == dataset.id:
        minio_data = _try_minio(df.relative_path)
        if minio_data is not None:
            doc = await session.get(CaseDocument, df.doc_id) if df.doc_id else None
            return None, df.relative_path, df, doc, minio_data
        cand = _fs(df.relative_path.replace("\\", "/").lstrip("/"))
        if cand is not None:
            doc = await session.get(CaseDocument, df.doc_id) if df.doc_id else None
            return cand, df.relative_path, df, doc, None

    # 3d. Check if cleaned matches CaseDocument.storage_key
    doc = (
        await session.execute(
            select(CaseDocument).where(
                CaseDocument.storage_key == cleaned,
                CaseDocument.dataset_id == dataset.id,
            )
        )
    ).scalars().first()
    if doc:
        rel = (doc.source_metadata or {}).get("relative_path") or doc.storage_key
        rel_clean = rel.replace("\\", "/").lstrip("/")
        minio_data = _try_minio(rel_clean)
        if minio_data is not None:
            df = (
                await session.execute(
                    select(DatasetFile).where(
                        DatasetFile.doc_id == doc.id,
                        DatasetFile.dataset_id == dataset.id,
                    )
                )
            ).scalars().first()
            return None, rel_clean, df, doc, minio_data
        cand = _fs(rel_clean)
        if cand is not None:
            df = (
                await session.execute(
                    select(DatasetFile).where(
                        DatasetFile.doc_id == doc.id,
                        DatasetFile.dataset_id == dataset.id,
                    )
                )
            ).scalars().first()
            return cand, rel_clean, df, doc, None

    # 3e. Leading-segments-stripped fallback (a stored key that carries an
    #     extra prefix).  Still dataset-scoped: a suffix match only counts if
    #     the active dataset owns a row for it, and the metadata travels with
    #     the bytes so the caller can show provenance rather than a bare file.
    parts = Path(cleaned).parts
    for i in range(1, len(parts)):
        sub = str(Path(*parts[i:])).replace("\\", "/")
        sub_df = (
            await session.execute(
                select(DatasetFile).where(
                    DatasetFile.dataset_id == dataset.id,
                    DatasetFile.relative_path == sub,
                )
            )
        ).scalars().first()
        sub_doc = (
            await session.get(CaseDocument, sub_df.doc_id)
            if (sub_df and sub_df.doc_id)
            else None
        )
        if sub_df is None and sub_doc is None:
            sub_doc = (
                await session.execute(
                    select(CaseDocument).where(
                        CaseDocument.dataset_id == dataset.id,
                        CaseDocument.storage_key == sub,
                    )
                )
            ).scalars().first()
        if sub_df is None and sub_doc is None:
            continue
        minio_data = _try_minio(sub)
        if minio_data is not None:
            return None, sub, sub_df, sub_doc, minio_data
        cand = _fs(sub)
        if cand is not None:
            return cand, sub, sub_df, sub_doc, None

    raise SourceNotFoundError(f"Source file not found in the active dataset: {cleaned}")


@router.get("/preview")
async def preview_file(
    request: Request,
    path: str = Query(..., description="Dataset-relative path, file ID, or document ID"),
    doc_id: str | None = Query(None, description="Optional CaseDocument ID"),
    dataset_file_id: str | None = Query(None, description="Optional DatasetFile ID"),
    sheet: str | None = Query(None, description="Workbook sheet name (XLSX)"),
    row: int | None = Query(None, ge=1),
    line_start: int | None = Query(None, ge=1),
    line_end: int | None = Query(None, ge=1),
    context: int = Query(source_viewer.DEFAULT_CONTEXT, ge=0, le=50),
    limit: int | None = Query(None, ge=1, le=source_viewer.MAX_WINDOW),
    offset: int | None = Query(None, ge=2),
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    clean = path.split("#", 1)[0]
    _reject_unsafe_path(clean)
    _evaluation_guard(clean)
    dataset = await _active_dataset(session)
    if dataset is None:
        raise NotFoundError("No dataset is active.")
    # No workspace directory is fine: the object store is the storage layer.
    root = await _dataset_root(session, dataset.id)
    _assert_some_storage()
    dataset_id = dataset.id

    try:
        resolved_file, resolved_relative_path, dataset_file, case_doc, minio_bytes = await _resolve_source_path(
            session=session,
            dataset=dataset,
            root=root,
            path=clean,
            doc_id=doc_id,
            dataset_file_id=dataset_file_id,
        )
    except SourceNotFoundError as exc:
        return {
            "status": source_viewer.STATUS_NOT_FOUND,
            "reason": str(exc),
            "openable": False,
            "render_kind": "none",
            "file": {"path": clean},
            "window": None,
        }
    except SourceAccessError as exc:
        if exc.status != source_viewer.STATUS_NOT_FOUND:
            # Traversal, absolute path, empty path: a bad request, not a state.
            raise
        return {
            "status": exc.status,
            "reason": str(exc),
            "openable": False,
            "render_kind": "none",
            "file": {"path": clean},
            "window": None,
        }

    result = source_viewer.preview(
        resolved_relative_path,
        root=root,
        row=row,
        line_start=line_start,
        line_end=line_end,
        context=context,
        limit=limit,
        offset=offset,
        sheet=sheet,
        dataset_id=dataset_id,
        raw_url=_raw_url(resolved_relative_path),
        download_url=_raw_url(resolved_relative_path),
    )
    if "file" in result and isinstance(result["file"], dict):
        result["file"]["path"] = resolved_relative_path
        if dataset_file:
            result["file"]["dataset_file_id"] = dataset_file.id
            result["file"]["filename"] = dataset_file.filename
        if case_doc:
            result["file"]["doc_id"] = case_doc.id
            result["file"]["filename"] = case_doc.filename
        if minio_bytes is not None:
            result["file"]["storage"] = "minio"
            result["file"]["size_bytes"] = len(minio_bytes)
    if result["status"] in {source_viewer.STATUS_AVAILABLE, source_viewer.STATUS_NO_EXTRACTED_TEXT}:
        recorder.record("DOC_VIEW", target_resource=f"source:{resolved_relative_path}", details={"kind": result.get("render_kind")})
        await recorder.flush()
    return result


@router.get("/file")
async def read_file(
    request: Request,
    path: str = Query(..., description="Dataset-relative path, file ID, or document ID"),
    doc_id: str | None = Query(None, description="Optional CaseDocument ID"),
    dataset_file_id: str | None = Query(None, description="Optional DatasetFile ID"),
    row: int | None = Query(None, ge=1),
    line_start: int | None = Query(None, ge=1),
    line_end: int | None = Query(None, ge=1),
    context: int = Query(source_viewer.DEFAULT_CONTEXT, ge=0, le=50),
    limit: int | None = Query(None, ge=1, le=source_viewer.MAX_WINDOW),
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    clean = path.split("#", 1)[0]
    _reject_unsafe_path(clean)
    _evaluation_guard(clean)
    dataset = await _active_dataset(session)
    if dataset is None:
        raise NotFoundError("No dataset is active.")
    # No workspace directory is fine: the object store is the storage layer.
    root = await _dataset_root(session, dataset.id)
    _assert_some_storage()
    try:
        resolved_file, resolved_relative_path, dataset_file, case_doc, minio_bytes = await _resolve_source_path(
            session=session,
            dataset=dataset,
            root=root,
            path=clean,
            doc_id=doc_id,
            dataset_file_id=dataset_file_id,
        )
        window = source_viewer.read_window(
            resolved_relative_path,
            row=row,
            line_start=line_start,
            line_end=line_end,
            context=context,
            limit=limit,
            root=root,
        )
    except SourceNotFoundError as exc:
        # 404, naming the file that is genuinely not there.
        raise NotFoundError(str(exc)) from exc
    except SourceAccessError:
        # Traversal, absolute path, empty path: a bad request, not a missing file.
        raise
    recorder.record(
        "DOC_VIEW",
        target_resource=f"source:{resolved_relative_path}",
        details={"row": row, "line_start": line_start},
    )
    await recorder.flush()
    return window.to_dict()


# ---------------------------------------------------------------------------
# Raw bytes: MinIO-aware inline delivery
# ---------------------------------------------------------------------------

@router.get("/raw")
async def raw_file(
    request: Request,
    path: str = Query(..., description="Dataset-relative path, file ID, or document ID"),
    doc_id: str | None = Query(None, description="Optional CaseDocument ID"),
    dataset_file_id: str | None = Query(None, description="Optional DatasetFile ID"),
    exp: int | None = Query(None),
    sig: str | None = Query(None),
    download: bool = Query(False, description="Force attachment disposition"),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Stream a dataset file's original bytes — MinIO-aware.

    Tries MinIO first when backend is minio, then filesystem. In production,
    MinIO is mandatory.
    """
    clean = path.split("#", 1)[0]
    _reject_unsafe_path(clean)
    dataset = await _active_dataset(session)
    if dataset is None:
        raise NotFoundError("No dataset is active.")
    # No workspace directory is fine: the object store is the storage layer.
    root = await _dataset_root(session, dataset.id)
    _assert_some_storage()

    try:
        resolved_file, resolved_relative_path, dataset_file, case_doc, minio_bytes = await _resolve_source_path(
            session=session,
            dataset=dataset,
            root=root,
            path=clean,
            doc_id=doc_id,
            dataset_file_id=dataset_file_id,
        )
    except SourceNotFoundError as exc:
        # 404, naming the file that is genuinely not there.
        raise NotFoundError(str(exc)) from exc
    except SourceAccessError:
        # Traversal, absolute path, empty path: a bad request, not a missing file.
        raise

    authorised = False
    if exp is not None and sig is not None:
        authorised = _verify_source_signature(clean, int(exp), sig) or _verify_source_signature(resolved_relative_path, int(exp), sig)
    if not authorised:
        header = request.headers.get("authorization") or ""
        if header.startswith("Bearer "):
            from app.security.tokens import decode_access_token

            try:
                payload = decode_access_token(header[len("Bearer "):].strip())
                role = str(payload.get("role") or "").upper()
                authorised = role in {"INVESTIGATOR", "ADMIN"}
            except Exception:
                authorised = False
    if not authorised:
        raise NotFoundError("Link expired or invalid.")

    # Determine size, kind, bytes
    if minio_bytes is not None:
        size = len(minio_bytes)
        kind = source_viewer.detect_kind_from_bytes(minio_bytes, Path(resolved_relative_path).suffix)
        media_type = kind.media_type
        disposition = "attachment" if (download or kind.kind == "binary") else "inline"
        filename = quote(case_doc.filename if case_doc else (dataset_file.filename if dataset_file else Path(resolved_relative_path).name))
        headers = {
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(size),
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        }

        range_header = request.headers.get("range")
        if range_header and kind.kind == "pdf":
            match = _RANGE.match(range_header.strip())
            if match:
                start_s, end_s = match.groups()
                start = int(start_s) if start_s else max(0, size - (int(end_s) if end_s else 0))
                end = min(size - 1, int(end_s)) if end_s else size - 1
                if start <= end and start < size:
                    length = end - start + 1
                    chunk_data = minio_bytes[start:end+1]
                    return Response(
                        content=chunk_data,
                        status_code=206,
                        media_type=media_type,
                        headers={
                            **headers,
                            "Content-Range": f"bytes {start}-{end}/{size}",
                            "Content-Length": str(length),
                        },
                    )

        return Response(content=minio_bytes, media_type=media_type, headers=headers)

    # Filesystem path
    assert resolved_file is not None
    size = resolved_file.stat().st_size
    kind = source_viewer.detect_kind(resolved_file)
    media_type = kind.media_type
    disposition = "attachment" if (download or kind.kind == "binary") else "inline"
    filename = quote(case_doc.filename if case_doc else (dataset_file.filename if dataset_file else resolved_file.name))
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
    }

    range_header = request.headers.get("range")
    if range_header and kind.kind == "pdf":
        match = _RANGE.match(range_header.strip())
        if match:
            start_s, end_s = match.groups()
            start = int(start_s) if start_s else max(0, size - (int(end_s) if end_s else 0))
            end = min(size - 1, int(end_s)) if end_s else size - 1
            if start <= end and start < size:
                length = end - start + 1

                def stream_range(handle=resolved_file):
                    with handle.open("rb") as fh:
                        fh.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = fh.read(min(_RAW_CHUNK, remaining))
                            if not chunk:
                                break
                            remaining -= len(chunk)
                            yield chunk

                return StreamingResponse(
                    stream_range(),
                    status_code=206,
                    media_type=media_type,
                    headers={
                        **headers,
                        "Content-Range": f"bytes {start}-{end}/{size}",
                        "Content-Length": str(length),
                    },
                )

    def stream_all(handle=resolved_file):
        with handle.open("rb") as fh:
            while chunk := fh.read(_RAW_CHUNK):
                yield chunk

    return StreamingResponse(
        stream_all(), media_type=media_type, headers=headers
    )


# ---------------------------------------------------------------------------
# References: exact provenance positions
# ---------------------------------------------------------------------------

@router.get("/reference/{reference_id}")
async def get_reference(
    reference_id: str,
    context: int = Query(source_viewer.DEFAULT_CONTEXT, ge=0, le=50),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    ref = (
        await session.execute(
            select(SourceReference).where(SourceReference.id == reference_id)
        )
    ).scalar_one_or_none()
    if ref is None:
        raise NotFoundError("Source reference not found.")

    case = await case_service.require_case(session, scope, ref.case_id)
    document = await session.get(CaseDocument, ref.doc_id)

    root = await _dataset_root(session, ref.dataset_id)
    window_dict: dict | None = None
    status = source_viewer.STATUS_AVAILABLE
    reason: str | None = None
    try:
        window_dict = source_viewer.read_window(
            ref.origin_file,
            row=ref.row_number,
            line_start=ref.line_start,
            line_end=ref.line_end,
            context=context,
            root=root,
        ).to_dict()
    except SourceNotFoundError as exc:
        status, reason = source_viewer.STATUS_NOT_FOUND, str(exc)
    except SourceAccessError as exc:
        status, reason = exc.status, str(exc)
    recorder.record(
        "DOC_VIEW",
        target_resource=f"source:{ref.origin_file}",
        case_id=ref.case_id,
        details={
            "reference_id": ref.id,
            "row": ref.row_number,
            "record_id": ref.record_id,
        },
    )
    await recorder.flush()
    return {
        "reference": _reference_row(ref),
        "window": window_dict,
        "status": status,
        "reason": reason,
        "preview_url": f"/api/v1/sources/preview?path={quote(ref.origin_file, safe='/')}",
        "raw_url": _raw_url(ref.origin_file.split("#", 1)[0]),
        "case": {"id": case.id, "case_number": case.case_number},
        "document": (
            {
                "id": document.id,
                "filename": document.filename,
                "document_type": document.document_type.value,
            }
            if document is not None
            else None
        ),
    }


@router.get("/documents/{doc_id}/references")
async def document_references(
    doc_id: str,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    document = await session.get(CaseDocument, doc_id)
    if document is None:
        raise NotFoundError("Document not found.")
    await case_service.require_case(session, scope, document.case_id)

    total = (
        await session.execute(
            select(func.count(SourceReference.id)).where(SourceReference.doc_id == doc_id)
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(SourceReference)
            .where(SourceReference.doc_id == doc_id)
            .order_by(SourceReference.row_number, SourceReference.text_start)
            .limit(limit)
            .offset(offset)
        )
    ).scalars()
    return {
        "document_id": doc_id,
        "case_id": document.case_id,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "items": [_reference_row(r) for r in rows],
    }


@router.get("/lookup")
async def lookup_reference(
    origin_file: str = Query(..., description="Dataset-relative path"),
    record_id: str | None = Query(None),
    row: int | None = Query(None, ge=1),
    scope: JurisdictionScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    query = select(SourceReference).where(SourceReference.origin_file == origin_file)
    if record_id:
        query = query.where(SourceReference.record_id == record_id)
    if row is not None:
        query = query.where(SourceReference.row_number == row)
    rows = list((await session.execute(query.limit(50))).scalars())

    allowed = []
    for ref in rows:
        try:
            await case_service.require_case(session, scope, ref.case_id)
        except Exception:
            continue
        allowed.append(_reference_row(ref))
    return {"origin_file": origin_file, "items": allowed, "count": len(allowed)}
