"""Distinct, honest failure states for ``GET /api/v1/sources/preview``.

The reported defect was one message for every failure::

    GET /api/v1/sources/preview?path=evidence%2FCR-2007%2F..._01.pdf
    → 404 {"message": "Active dataset workspace is unavailable."}

shown while the file was listed as AVAILABLE two pixels away in the same UI.
"Workspace unavailable", "no such file", "registered but the bytes are gone"
and "object storage is down" were all the same sentence, so the investigator
could not tell a data problem from an outage from a wrong path.

Every failure now carries a machine-readable ``code`` that names the cause,
and an object-store outage is never reported as a missing file.
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.datasets import registry
from app.db.base import new_uuid
from app.db.models import Case, CaseDocument, DatasetFile
from app.db.session import async_session
from app.domain.enums import CaseStatus, DocumentType
from app.services import source_viewer

JURISDICTION = "RJ-JAIPUR"


def _pdf_bytes(title: str) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    doc = canvas.Canvas(buf, pagesize=letter)
    doc.setFont("Helvetica-Bold", 14)
    doc.drawString(72, 740, title)
    doc.showPage()
    doc.save()
    return buf.getvalue()


@pytest.fixture()
async def seeded(container, workspace, users):
    """One dataset with one PDF that really is in the object store."""
    async with async_session() as session:
        ds = await registry.create_dataset(
            session, name="Source error states", version="1", source_kind="builtin"
        )
        await registry.activate(session, ds)
        case = Case(
            id=new_uuid(),
            case_number="SRC-7001",
            title="Source error states",
            jurisdiction_id=JURISDICTION,
            dataset_id=ds.id,
            status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.flush()

        key = "evidence/SRC-7001/SRC-7001_FIR_01.pdf"
        payload = _pdf_bytes("FIR 1/2026 — SRC-7001")
        container.object_store.put(
            workspace.minio_bucket_documents, key, payload, content_type="application/pdf"
        )
        doc = CaseDocument(
            id="doc-src-7001-01",
            case_id=case.id,
            dataset_id=ds.id,
            document_type=DocumentType.FIR,
            filename="SRC-7001_FIR_01.pdf",
            storage_key=key,
            content_hash="sha7001",
            size_bytes=len(payload),
            mime_type="application/pdf",
            source_metadata={"relative_path": key},
        )
        session.add(doc)
        await session.flush()
        session.add(
            DatasetFile(
                id=new_uuid(),
                dataset_id=ds.id,
                relative_path=key,
                filename="SRC-7001_FIR_01.pdf",
                extension="pdf",
                media_type="application/pdf",
                file_kind="document",
                size_bytes=len(payload),
                sha256="c" * 64,
                status="INGESTED",
                doc_id=doc.id,
            )
        )
        await session.commit()
        context = {"dataset_id": ds.id, "case_id": case.id, "key": key, "doc_id": doc.id}

    yield context

    async with async_session() as session:
        await registry.purge_dataset_data(session, context["dataset_id"])
        await session.commit()


# --------------------------------------------------------------------------- #
# The happy path still reports a cause code, not just a status
# --------------------------------------------------------------------------- #

async def test_available_preview_reports_its_code(client, investigator_headers, seeded):
    resp = client.get(
        f"/api/v1/sources/preview?path={seeded['key']}", headers=investigator_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "AVAILABLE"
    assert body["code"] == "available"
    assert body["openable"] is True


# --------------------------------------------------------------------------- #
# A path the dataset does not own
# --------------------------------------------------------------------------- #

async def test_unknown_path_is_a_missing_file_not_a_broken_workspace(
    client, investigator_headers, seeded
):
    resp = client.get(
        "/api/v1/sources/preview?path=evidence/SRC-7001/NOT_THERE.pdf",
        headers=investigator_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == source_viewer.STATUS_NOT_FOUND
    assert body["code"] == source_viewer.CODE_SOURCE_FILE_NOT_FOUND
    assert body["openable"] is False
    # Names the path, and never blames the dataset workspace.
    assert "NOT_THERE.pdf" in body["reason"]
    assert "workspace is unavailable" not in resp.text.lower()


async def test_registered_document_with_no_bytes_reports_a_data_integrity_problem(
    client, investigator_headers, seeded, container, workspace
):
    """Registered in the database, absent from storage — not the same as absent
    from the database."""
    async with async_session() as session:
        case = await session.get(Case, seeded["case_id"])
        key = "evidence/SRC-7001/SRC-7001_MISSING_02.pdf"
        doc = CaseDocument(
            id="doc-src-7001-02",
            case_id=case.id,
            dataset_id=seeded["dataset_id"],
            document_type=DocumentType.CDR,
            filename="SRC-7001_MISSING_02.csv",
            storage_key=key,
            content_hash="sha7002",
            size_bytes=12,
            mime_type="text/csv",
            source_metadata={"relative_path": key},
        )
        session.add(doc)
        session.add(
            DatasetFile(
                id=new_uuid(),
                dataset_id=seeded["dataset_id"],
                relative_path=key,
                filename="SRC-7001_MISSING_02.csv",
                extension="csv",
                media_type="text/csv",
                file_kind="table",
                size_bytes=12,
                sha256="d" * 64,
                status="INGESTED",
                doc_id=doc.id,
            )
        )
        await session.commit()

    resp = client.get(f"/api/v1/sources/preview?path={key}", headers=investigator_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == source_viewer.STATUS_NOT_FOUND
    assert body["openable"] is False
    # The reason distinguishes "registered but not stored" from "unknown path".
    assert "registered to the active dataset" in body["reason"]


# --------------------------------------------------------------------------- #
# An object-store outage is not a missing file
# --------------------------------------------------------------------------- #

async def test_storage_outage_is_reported_as_an_outage(
    client, investigator_headers, seeded, monkeypatch
):
    """The distinction the old code lost: "we could not ask" ≠ "not there"."""
    from app.api.v1 import sources as sources_api

    def _down(*_args, **_kwargs):
        raise sources_api.StorageUnavailableError(
            "Object storage could not be queried: connection refused"
        )

    monkeypatch.setattr(sources_api, "_assert_some_storage", lambda: None)

    async def _resolve(**_kwargs):
        raise sources_api.StorageUnavailableError(
            "Object storage could not be queried for evidence/SRC-7001/SRC-7001_FIR_01.pdf"
        )

    monkeypatch.setattr(sources_api, "_resolve_source_path", _resolve)

    resp = client.get(
        f"/api/v1/sources/preview?path={seeded['key']}", headers=investigator_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == source_viewer.STATUS_STORAGE_UNAVAILABLE
    assert body["code"] == source_viewer.CODE_STORAGE_UNAVAILABLE
    assert body["status"] != source_viewer.STATUS_NOT_FOUND
    assert body["openable"] is False


async def test_no_active_dataset_gets_its_own_error_code(client, admin_headers, seeded):
    """Deactivating the dataset must not read as "this file is missing"."""
    async with async_session() as session:
        rows = (await session.execute(select(registry.Dataset))).scalars().all()
        for row in rows:
            row.is_active = False
        await session.commit()

    resp = client.get(
        f"/api/v1/sources/preview?path={seeded['key']}", headers=admin_headers
    )
    assert resp.status_code == 404, resp.text
    error = resp.json()["error"]
    assert error["code"] == source_viewer.CODE_ACTIVE_DATASET_UNAVAILABLE
    assert error["code"] != source_viewer.CODE_SOURCE_FILE_NOT_FOUND

    # Restore for the shared-session tests that follow.
    async with async_session() as session:
        dataset = await session.get(registry.Dataset, seeded["dataset_id"])
        await registry.activate(session, dataset)
        await session.commit()
