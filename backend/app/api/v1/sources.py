"""Source and evidence navigation — the backend of the Source Viewer.

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
  / NO_EXTRACTED_TEXT).  Before this existed the Sources page listed the
  bundled evaluation corpus rather than the uploaded dataset, and PDFs were
  decoded as UTF-8 text -- the "%PDF-1.3" garbage on screen.

Binary is streamed, never transcribed: ``/sources/raw`` serves the original
bytes with a sniffed content type (and Range support, so native PDF viewers
work), signed with the same HMAC scheme as the object store so a browser
``<embed>`` can load it without an Authorization header.  Paths resolve
*inside the active dataset's workspace only*: files of a replaced dataset are
not openable, which is exactly what "one active dataset" means at the file
level.
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
from app.errors import NotFoundError
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

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")
_RAW_CHUNK = 256 * 1024
#: Signed source links are shorter-lived than object-store links: they open a
#: file for immediate viewing, not for a workflow that spans a shift.
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


# ---------------------------------------------------------------------------
# Active-dataset resolution (the isolation boundary for every route here)
# ---------------------------------------------------------------------------


async def _active_dataset(session: AsyncSession) -> Dataset | None:
    return await registry.active_dataset(session)


async def _dataset_root(
    session: AsyncSession, dataset_id: str | None = None
) -> Path | None:
    """Workspace of the given dataset (or the active one), if it exists."""
    dataset: Dataset | None = None
    if dataset_id:
        dataset = await registry.get_dataset(session, dataset_id)
    if dataset is None:
        dataset = await _active_dataset(session)
    if dataset is None or not dataset.root_path:
        return None
    root = Path(dataset.root_path)
    return root if root.is_dir() else None


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


# ---------------------------------------------------------------------------
# Listing: the active dataset's manifest
# ---------------------------------------------------------------------------


@router.get("/files")
async def dataset_files(
    session: AsyncSession = Depends(get_db_session),
    principal: Principal = Depends(get_principal),
) -> dict:
    """Every file of the active dataset, with explicit availability per file.

    The listing is the persistence layer's truth, not a folder scan: the
    ``dataset_files`` manifest written during ingestion carries the sniffed
    media type, the extraction status, the reason for any skip, and the
    document id the file became -- which is what ties a source to the
    evidence and case records the rest of the application shows.
    """
    dataset = await _active_dataset(session)
    if dataset is None:
        return await _legacy_corpus_files(session)

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
        exists = bool(root and (root / row.relative_path).is_file())
        status, openable = _file_availability(row, exists)
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


def _file_availability(row: DatasetFile, exists: bool) -> tuple[str, bool]:
    """(status, openable) for one manifest row.

    The status vocabulary here is deliberately the viewer's vocabulary: what
    the list shows is what opening the file will report, so the page never
    says "AVAILABLE" next to a dialog that then explains it is not.
    """
    if not exists:
        return source_viewer.STATUS_NOT_FOUND, False
    manifest = (row.status or "").upper()
    if manifest == "UNSUPPORTED":
        return source_viewer.STATUS_UNSUPPORTED, False
    if manifest == "CORRUPT":
        return source_viewer.STATUS_CORRUPTED, True  # opening explains why
    if manifest == "SKIPPED":
        return "SKIPPED", False
    return source_viewer.STATUS_AVAILABLE, True


async def _legacy_corpus_files(session: AsyncSession) -> dict:
    """Pre-dataset flow: the bundled external corpus, for evaluation builds.

    Kept for the ``synthetic_data_mode=external`` deployment where files are
    browsed straight from the corpus directory rather than through an import.
    """
    from app.adapters.sources import get_source_adapter

    try:
        adapter = get_source_adapter("synthetic_external")
        scan = adapter.scan()
    except Exception as exc:  # noqa: BLE001 - a missing corpus is an empty listing
        return {
            "root": None,
            "dataset_name": None,
            "ok": False,
            "issues": [f"No dataset is active and the bundled corpus is unavailable ({exc})."],
            "warnings": [],
            "counts": {},
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
                # Only operational material is openable; ground truth is not.
                "readable": entry.status in {"accepted", "reference"},
                "reference_count": counts.get(entry.relative_path, 0),
            }
        )
    summary = scan.summary()
    return {
        "root": summary["root"],
        "dataset_name": summary["dataset_name"],
        "ok": summary["ok"],
        "issues": summary["issues"],
        "warnings": summary["warnings"],
        "counts": summary["counts"],
        "items": items,
    }


# ---------------------------------------------------------------------------
# Preview: render any file of the active dataset
# ---------------------------------------------------------------------------


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


@router.get("/preview")
async def preview_file(
    request: Request,
    path: str = Query(..., description="Dataset-relative path"),
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
    """Render one dataset file with the matching viewer and an explicit state.

    PDF → parsed pages plus a signed ``raw_url`` the browser renders natively;
    CSV/XLSX → tables; JSON → formatted; DOCX/PPTX → extracted structure;
    anything else → ``UNSUPPORTED`` with the reason, never a wall of
    replacement characters.
    """
    clean = path.split("#", 1)[0]
    _evaluation_guard(clean)
    dataset = await _active_dataset(session)
    if dataset is None:
        from app.adapters.sources import get_source_adapter

        adapter = get_source_adapter("synthetic_external")
        root = adapter.resolve_root()
        dataset_id = "synthetic-corpus"
    else:
        root = await _dataset_root(session, dataset.id)
        dataset_id = dataset.id

    result = source_viewer.preview(
        path,
        root=root,
        row=row,
        line_start=line_start,
        line_end=line_end,
        context=context,
        limit=limit,
        offset=offset,
        sheet=sheet,
        dataset_id=dataset_id,
        raw_url=_raw_url(clean),
        download_url=_raw_url(clean),
    )
    if result["status"] in {source_viewer.STATUS_AVAILABLE, source_viewer.STATUS_NO_EXTRACTED_TEXT}:
        recorder.record("DOC_VIEW", target_resource=f"source:{clean}", details={"kind": result.get("render_kind")})
        await recorder.flush()
    return result


@router.get("/file")
async def read_file(
    request: Request,
    path: str = Query(..., description="Dataset-relative path"),
    row: int | None = Query(None, ge=1),
    line_start: int | None = Query(None, ge=1),
    line_end: int | None = Query(None, ge=1),
    context: int = Query(source_viewer.DEFAULT_CONTEXT, ge=0, le=50),
    limit: int | None = Query(None, ge=1, le=source_viewer.MAX_WINDOW),
    principal: Principal = Depends(require_roles("INVESTIGATOR", "ADMIN")),
    session: AsyncSession = Depends(get_db_session),
    recorder: AuditRecorder = Depends(get_audit_recorder),
) -> dict:
    """Open a dataset file directly at a position, for dataset exploration.

    Kept as the *window-only* contract (a bare :class:`SourceWindow`-shaped
    body) because citation viewers already speak it; the Sources page uses
    ``/preview``, which is the same read plus the explicit status.
    """
    clean = path.split("#", 1)[0]
    _evaluation_guard(clean)
    dataset = await _active_dataset(session)
    if dataset is None:
        from app.adapters.sources import get_source_adapter

        adapter = get_source_adapter("synthetic_external")
        root = adapter.resolve_root()
    else:
        root = await _dataset_root(session, dataset.id)
    try:
        window = source_viewer.read_window(
            path,
            row=row,
            line_start=line_start,
            line_end=line_end,
            context=context,
            limit=limit,
            root=root,
        )
    except SourceNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    except SourceAccessError as exc:
        # Explicit rather than opaque: the caller learns WHICH state the file
        # is in instead of a bare 400 with a stack-flavoured message.
        return {
            "status": exc.status,
            "reason": str(exc),
            "window": None,
            "file": clean,
        }
    recorder.record(
        "DOC_VIEW",
        target_resource=f"source:{clean}",
        details={"row": row, "line_start": line_start},
    )
    await recorder.flush()
    return window.to_dict()


# ---------------------------------------------------------------------------
# Raw bytes: inline delivery for native viewers (PDF) and downloads
# ---------------------------------------------------------------------------


@router.get("/raw")
async def raw_file(
    request: Request,
    path: str = Query(..., description="Dataset-relative path"),
    exp: int | None = Query(None),
    sig: str | None = Query(None),
    download: bool = Query(False, description="Force attachment disposition"),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Stream a dataset file's original bytes with a sniffed content type.

    The bytes are never re-encoded and never sent through a text renderer:
    a PDF arrives as ``application/pdf`` and the browser (or PDF.js) does
    what a PDF viewer does with a PDF.

    Auth is deliberately dual-path: a signed link (no header possible inside
    an ``<embed>``) or a normal session bearer token. Anything else is
    refused with the same 404 an unknown path gets, so the endpoint cannot be
    used to probe which evidence files exist.
    """
    clean = path.split("#", 1)[0]
    authorised = False
    if exp is not None and sig is not None:
        authorised = _verify_source_signature(clean, int(exp), sig)
    if not authorised:
        header = request.headers.get("authorization") or ""
        if header.startswith("Bearer "):
            from app.security.tokens import decode_access_token

            try:
                payload = decode_access_token(header[len("Bearer "):].strip())
                role = str(payload.get("role") or "").upper()
                authorised = role in {"INVESTIGATOR", "ADMIN"}
            except Exception:  # noqa: BLE001 - an invalid token is simply unauthorised
                authorised = False
    if not authorised:
        raise NotFoundError("Link expired or invalid.")

    dataset = await _active_dataset(session)
    if dataset is None:
        from app.adapters.sources import get_source_adapter

        adapter = get_source_adapter("synthetic_external")
        root = adapter.resolve_root()
    else:
        root = await _dataset_root(session, dataset.id)
    try:
        resolved = source_viewer.resolve_in_dataset(clean, root=root)
    except SourceNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    except SourceAccessError as exc:
        raise NotFoundError(str(exc)) from exc

    size = resolved.stat().st_size
    kind = source_viewer.detect_kind(resolved)
    media_type = kind.media_type
    disposition = "attachment" if (download or kind.kind == "binary") else "inline"
    filename = quote(resolved.name)
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

                def stream_range(handle=resolved):
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

    def stream_all(handle=resolved):
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
    """Open a stored source reference at its exact position."""
    ref = (
        await session.execute(
            select(SourceReference).where(SourceReference.id == reference_id)
        )
    ).scalar_one_or_none()
    if ref is None:
        raise NotFoundError("Source reference not found.")

    # Authorisation flows through the owning case, exactly as for the document.
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
    """Every source reference recorded for one ingested document."""
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
    """Find stored references for an origin row — used to jump from a file to a case."""
    query = select(SourceReference).where(SourceReference.origin_file == origin_file)
    if record_id:
        query = query.where(SourceReference.record_id == record_id)
    if row is not None:
        query = query.where(SourceReference.row_number == row)
    rows = list((await session.execute(query.limit(50))).scalars())

    allowed = []
    for ref in rows:
        # Silently drop references the caller may not see, rather than leaking
        # their existence through a 403.  (require_case also rejects cases
        # from replaced datasets, which is the isolation rule speaking.)
        try:
            await case_service.require_case(session, scope, ref.case_id)
        except Exception:  # noqa: BLE001
            continue
        allowed.append(_reference_row(ref))
    return {"origin_file": origin_file, "items": allowed, "count": len(allowed)}
