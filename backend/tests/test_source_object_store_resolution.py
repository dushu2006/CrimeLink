"""Source resolution when the dataset has **no workspace directory**.

This is the shape of the seeded demo corpus and of any MinIO-backed
deployment: ``datasets.root_path`` names a directory that was never created,
and every file lives in the object store.  The reported defect was exactly
this combination —

    GET /api/v1/sources/preview?path=evidence/CR-2007/..._INTELLIGENCE_REPORT_01.pdf
    → 404 {"message": "Active dataset workspace is unavailable."}

— while ``GET /api/v1/sources/files`` listed the very same file as
``AVAILABLE / openable: true``.  The listing consulted the object store; the
preview/raw/file routes bailed out on the missing workspace directory before
they ever looked.  A missing *directory* was being reported as a missing
*file*, with a message that blamed the dataset instead of naming the path.

Covers acceptance points A, B, C, D, E, G, H, R and S.
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.datasets import registry
from app.db.base import new_uuid
from app.db.models import Case, CaseDocument, DatasetFile, SourceReference
from app.db.session import async_session
from app.domain.enums import CaseStatus, DocumentType

JURISDICTION = "RJ-JAIPUR"


def _pdf_bytes(title: str, lines: list[str]) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    doc = canvas.Canvas(buf, pagesize=letter)
    doc.setFont("Helvetica-Bold", 14)
    doc.drawString(72, 740, title)
    doc.setFont("Helvetica", 10)
    y = 710
    for line in lines:
        doc.drawString(72, y, line)
        y -= 16
    doc.showPage()
    doc.save()
    return buf.getvalue()


@pytest.fixture()
async def object_store_only_dataset(container, workspace, users):
    """A dataset whose files exist ONLY in the object store.

    ``root_path`` deliberately points at a directory that is never created —
    the exact condition that produced the reported 404.
    """
    async with async_session() as session:
        ds = await registry.create_dataset(
            session, name="Object-store-only sources", version="1", source_kind="builtin"
        )
        await registry.activate(session, ds)
        # The trap: a workspace path that does not exist on disk.
        ds.root_path = str(workspace.data_dir / "datasets" / "never-created")
        case = Case(
            id=new_uuid(), case_number="SRC-9001", title="Object store sources",
            jurisdiction_id=JURISDICTION, dataset_id=ds.id, status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.flush()

        pdf_key = "evidence/SRC-9001/SRC-9001_FIR_01.pdf"
        csv_key = "evidence/SRC-9001/SRC-9001_CDR_02.csv"
        pdf_bytes = _pdf_bytes(
            "FIR 12/2026 — SRC-9001",
            ["Complainant: Rakesh Mehta", "Accused: Vikram Verma", "Object-store residency test."],
        )
        csv_bytes = (
            "call_id,caller_number,callee_number,timestamp,duration_s\n"
            "CDR9001-000,+919000000001,+919000000002,2026-01-04T09:12:00+00:00,142\n"
            "CDR9001-001,+919000000001,+919000000003,2026-01-04T10:40:00+00:00,88\n"
        ).encode("utf-8")

        bucket = workspace.minio_bucket_documents
        store = container.object_store
        store.put(bucket, pdf_key, pdf_bytes, content_type="application/pdf")
        store.put(bucket, csv_key, csv_bytes, content_type="text/csv")

        doc_pdf = CaseDocument(
            id="doc-src-9001-01", case_id=case.id, dataset_id=ds.id,
            document_type=DocumentType.FIR, filename="SRC-9001_FIR_01.pdf",
            storage_key=pdf_key, content_hash="sha256_src9001_pdf",
            size_bytes=len(pdf_bytes), mime_type="application/pdf",
            source_metadata={"evidence_id": "E-9001", "dataset": ds.id},
        )
        doc_csv = CaseDocument(
            id="doc-src-9001-02", case_id=case.id, dataset_id=ds.id,
            document_type=DocumentType.CDR, filename="SRC-9001_CDR_02.csv",
            storage_key=csv_key, content_hash="sha256_src9001_csv",
            size_bytes=len(csv_bytes), mime_type="text/csv",
            source_metadata={"evidence_id": "E-9002", "dataset": ds.id},
        )
        session.add_all([doc_pdf, doc_csv])
        await session.flush()
        session.add_all([
            DatasetFile(
                id=new_uuid(), dataset_id=ds.id, relative_path=pdf_key,
                filename="SRC-9001_FIR_01.pdf", extension="pdf",
                media_type="application/pdf", file_kind="document",
                size_bytes=len(pdf_bytes), sha256="a" * 64, status="INGESTED",
                doc_id=doc_pdf.id,
            ),
            DatasetFile(
                id=new_uuid(), dataset_id=ds.id, relative_path=csv_key,
                filename="SRC-9001_CDR_02.csv", extension="csv",
                media_type="text/csv", file_kind="table",
                size_bytes=len(csv_bytes), sha256="b" * 64, status="INGESTED",
                doc_id=doc_csv.id, row_count=2,
            ),
        ])
        ref = SourceReference(
            doc_id=doc_csv.id, case_id=case.id, dataset_id=ds.id,
            origin_file=csv_key, source_type="csv", record_id="CDR9001-000",
            row_number=2, field_names=["caller_number", "callee_number"],
            field_values={"caller_number": "+919000000001", "callee_number": "+919000000002"},
            excerpt="CDR9001-000",
        )
        session.add(ref)
        await session.commit()
        ref_id = ref.id

        yield {
            "dataset_id": ds.id, "case_id": case.id, "pdf_key": pdf_key,
            "csv_key": csv_key, "pdf_bytes": pdf_bytes, "csv_bytes": csv_bytes,
            "doc_pdf_id": doc_pdf.id, "doc_csv_id": doc_csv.id, "reference_id": ref_id,
            "bucket": bucket,
        }

        await registry.purge_dataset_data(session, ds.id)
        await session.commit()


# --------------------------------------------------------------------------- #
# A / C / H: a stored object opens, with no workspace directory present
# --------------------------------------------------------------------------- #

async def test_preview_serves_a_file_that_lives_only_in_the_object_store(
    client, investigator_headers, object_store_only_dataset
):
    ctx = object_store_only_dataset
    resp = client.get(
        f"/api/v1/sources/preview?path={ctx['pdf_key']}", headers=investigator_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # C. Never the misleading workspace message.
    assert "workspace is unavailable" not in resp.text.lower()
    assert body["status"] == "AVAILABLE"
    assert body["openable"] is True
    # A. The actual stored file, rendered by the renderer its bytes imply.
    assert body["render_kind"] == "pdf"
    assert body["file"]["media_type"] == "application/pdf"
    assert body["file"]["size_bytes"] == len(ctx["pdf_bytes"])
    assert body["file"]["path"] == ctx["pdf_key"]
    # D. Metadata resolves from the ACTIVE dataset.
    assert body["file"]["dataset_id"] == ctx["dataset_id"]
    assert body["file"]["doc_id"] == ctx["doc_pdf_id"]
    assert body["file"]["filename"] == "SRC-9001_FIR_01.pdf"
    pdf = body["pdf"]
    assert pdf and pdf["text_available"], "a PDF preview must carry extracted page text"
    assert pdf["page_count"] >= 1
    joined = "\n".join(page["text"] for page in pdf["pages"])
    assert "Rakesh Mehta" in joined and "Vikram Verma" in joined


async def test_raw_returns_the_real_bytes(client, investigator_headers, object_store_only_dataset):
    ctx = object_store_only_dataset
    resp = client.get(
        f"/api/v1/sources/raw?path={ctx['pdf_key']}", headers=investigator_headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    # H. Byte-for-byte the stored object.
    assert resp.content == ctx["pdf_bytes"]
    assert resp.content.startswith(b"%PDF-")


async def test_csv_renders_as_a_table_from_the_object_store(
    client, investigator_headers, object_store_only_dataset
):
    ctx = object_store_only_dataset
    resp = client.get(
        f"/api/v1/sources/preview?path={ctx['csv_key']}", headers=investigator_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "AVAILABLE"
    assert body["render_kind"] == "csv"
    window = body["window"]
    assert "caller_number" in window["columns"]
    values = [row["values"]["call_id"] for row in window["rows"]]
    assert "CDR9001-000" in values


async def test_resolution_by_doc_id_and_dataset_file_id(
    client, investigator_headers, object_store_only_dataset
):
    """E. "Open original record" can resolve from the record id, not just a path."""
    ctx = object_store_only_dataset
    by_doc = client.get(
        f"/api/v1/sources/preview?path={ctx['doc_pdf_id']}", headers=investigator_headers
    )
    assert by_doc.status_code == 200, by_doc.text
    assert by_doc.json()["status"] == "AVAILABLE"
    assert by_doc.json()["file"]["path"] == ctx["pdf_key"]

    async with async_session() as session:
        df_id = (
            await session.execute(
                select(DatasetFile.id).where(DatasetFile.relative_path == ctx["csv_key"])
            )
        ).scalar_one()
    by_df = client.get(
        f"/api/v1/sources/preview?path={df_id}", headers=investigator_headers
    )
    assert by_df.status_code == 200, by_df.text
    assert by_df.json()["file"]["path"] == ctx["csv_key"]


# --------------------------------------------------------------------------- #
# B / S: a genuinely missing file is reported as missing, and named
# --------------------------------------------------------------------------- #

async def test_missing_source_is_a_404_naming_the_file(
    client, investigator_headers, object_store_only_dataset
):
    ctx = object_store_only_dataset
    missing = "evidence/SRC-9001/DOES_NOT_EXIST_99.pdf"

    raw = client.get(f"/api/v1/sources/raw?path={missing}", headers=investigator_headers)
    assert raw.status_code == 404
    assert missing in raw.json()["error"]["message"]
    assert "workspace" not in raw.json()["error"]["message"].lower()

    preview = client.get(
        f"/api/v1/sources/preview?path={missing}", headers=investigator_headers
    )
    # The preview endpoint answers with a state; the state is NOT_FOUND and it
    # names the file rather than blaming the dataset.
    assert preview.status_code == 200
    body = preview.json()
    assert body["status"] == "NOT_FOUND"
    assert body["openable"] is False
    assert missing in body["reason"]

    file_endpoint = client.get(
        f"/api/v1/sources/file?path={missing}", headers=investigator_headers
    )
    assert file_endpoint.status_code == 404
    assert missing in file_endpoint.json()["error"]["message"]


async def test_listing_and_preview_agree_on_availability(
    client, investigator_headers, object_store_only_dataset
):
    """The defect was a listing that said AVAILABLE over a preview that 404'd."""
    ctx = object_store_only_dataset
    listing = client.get("/api/v1/sources/files", headers=investigator_headers).json()
    rows = {item["path"]: item for item in listing["items"]}
    assert ctx["pdf_key"] in rows and rows[ctx["pdf_key"]]["status"] == "AVAILABLE"
    assert rows[ctx["pdf_key"]]["openable"] is True

    for path, row in rows.items():
        preview = client.get(
            f"/api/v1/sources/preview?path={path}", headers=investigator_headers
        ).json()
        if row["status"] == "AVAILABLE" and row["openable"]:
            assert preview["status"] != "NOT_FOUND", path
            assert preview["status"] not in {"UNSUPPORTED"} or preview["reason"], path


# --------------------------------------------------------------------------- #
# R: no traversal, no absolute paths, no server paths leaked
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "attempt",
    [
        "../../etc/passwd",
        "../../../etc/passwd",
        "/etc/passwd",
        "evidence/../../../etc/passwd",
        "C:/Windows/win.ini",
        "evidence/SRC-9001/../../../../../../etc/shadow",
    ],
)
async def test_traversal_and_absolute_paths_are_refused_not_served(
    client, investigator_headers, object_store_only_dataset, attempt
):
    for endpoint in ("preview", "raw", "file"):
        resp = client.get(
            f"/api/v1/sources/{endpoint}",
            params={"path": attempt},
            headers=investigator_headers,
        )
        assert resp.status_code in (400, 404, 422), (endpoint, attempt, resp.status_code)
        # A traversal attempt is a bad request about the *path*, never a
        # "workspace unavailable" story and never a served file.
        assert "workspace is unavailable" not in resp.text.lower()
        assert b"root:" not in resp.content


async def test_server_filesystem_paths_are_never_exposed(
    client, investigator_headers, object_store_only_dataset
):
    ctx = object_store_only_dataset
    for endpoint in ("preview", "raw"):
        resp = client.get(
            f"/api/v1/sources/{endpoint}",
            params={"path": ctx["pdf_key"]},
            headers=investigator_headers,
        )
        text = resp.text
        assert str(ctx["pdf_key"]) in text or endpoint == "raw"
        assert "/var/data/datasets" not in text
        assert "object_store_dir" not in text


# --------------------------------------------------------------------------- #
# G / I: the provenance chain is traversable end to end
# --------------------------------------------------------------------------- #

async def test_provenance_chain_case_to_evidence_to_source_to_file(
    client, investigator_headers, object_store_only_dataset
):
    ctx = object_store_only_dataset

    # CASE
    case = client.get(f"/api/v1/cases/{ctx['case_id']}", headers=investigator_headers)
    assert case.status_code == 200, case.text

    # EVIDENCE RECORD (CaseDocument) attached to that case
    docs = client.get(
        f"/api/v1/cases/{ctx['case_id']}/documents", headers=investigator_headers
    )
    assert docs.status_code == 200, docs.text
    payload = docs.json()
    items = payload.get("items", payload if isinstance(payload, list) else [])
    ids = {d["id"] for d in items}
    assert ctx["doc_csv_id"] in ids, ids

    # SOURCE RECORD (SourceReference) for that document
    refs = client.get(
        f"/api/v1/sources/documents/{ctx['doc_csv_id']}/references",
        headers=investigator_headers,
    )
    assert refs.status_code == 200, refs.text
    ref_rows = refs.json()["items"]
    assert any(r["record_id"] == "CDR9001-000" for r in ref_rows)

    # ...resolves to a positioned window of the ORIGINAL FILE
    one = client.get(
        f"/api/v1/sources/reference/{ctx['reference_id']}", headers=investigator_headers
    )
    assert one.status_code == 200, one.text
    body = one.json()
    assert body["status"] == "AVAILABLE", body
    assert body["case"]["id"] == ctx["case_id"]
    assert body["document"]["id"] == ctx["doc_csv_id"]
    assert body["window"] is not None
    assert body["window"]["source_type"] == "csv"
    # and the chain offers a way back to the bytes
    assert body["raw_url"].startswith("/api/v1/sources/raw?path=")

    # The chain hands back a signed link to the original bytes; follow it as-is.
    raw = client.get(body["raw_url"], headers=investigator_headers)
    assert raw.status_code == 200
    assert raw.content == ctx["csv_bytes"]


# --------------------------------------------------------------------------- #
# Active-dataset consistency: switching datasets closes the old files
# --------------------------------------------------------------------------- #

async def test_switching_dataset_closes_the_object_store_files(
    client, admin_headers, object_store_only_dataset
):
    ctx = object_store_only_dataset
    before = client.get(
        f"/api/v1/sources/raw?path={ctx['pdf_key']}", headers=admin_headers
    )
    assert before.status_code == 200

    async with async_session() as session:
        other = await registry.create_dataset(
            session, name="Another dataset", version="1", source_kind="folder"
        )
        await registry.activate(session, other)
        await session.commit()
        other_id = other.id

    after = client.get(
        f"/api/v1/sources/raw?path={ctx['pdf_key']}", headers=admin_headers
    )
    assert after.status_code == 404, after.text
    assert "workspace" not in after.text.lower()

    async with async_session() as session:
        await registry.purge_dataset_data(session, other_id)
        await session.commit()
