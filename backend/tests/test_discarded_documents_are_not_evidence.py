"""A discarded document is a tombstone, not evidence (§17 / §19).

`discard_quarantined` soft-deletes the row so the audit trail survives.  Every
endpoint that *lists* evidence must therefore exclude it — otherwise the
investigator sees a record that has been formally discarded, and the
Evidence page reports a count the database does not support.

Reproduced live before the fix: `/explore/documents` returned 362 rows for a
dataset holding 360 live documents, the two extra being documents that had
just been discarded.
"""

from __future__ import annotations

import pytest

from app.datasets import registry
from app.db.base import new_uuid
from app.db.models import Case, CaseDocument
from app.db.session import async_session
from app.domain.enums import CaseStatus, DocumentType


@pytest.fixture()
async def two_docs_one_discarded(container, users):
    """One live document and one discarded document in the same case."""
    async with async_session() as session:
        ds = await registry.create_dataset(
            session, name="Discard visibility", version="1", source_kind="builtin"
        )
        await registry.activate(session, ds)
        case = Case(
            id=new_uuid(), case_number="DSC-9001", title="Discard visibility",
            jurisdiction_id="RJ-JAIPUR", dataset_id=ds.id, status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.flush()

        live = CaseDocument(
            id="doc-dsc-live", case_id=case.id, dataset_id=ds.id,
            document_type=DocumentType.FIR, filename="DSC-9001_FIR_01.pdf",
            storage_key="evidence/DSC-9001/fir.pdf", content_hash="a" * 64,
            size_bytes=10, mime_type="application/pdf", is_deleted=False,
        )
        discarded = CaseDocument(
            id="doc-dsc-gone", case_id=case.id, dataset_id=ds.id,
            document_type=DocumentType.CDR, filename="DSC-9001_CDR_01.csv",
            storage_key="evidence/DSC-9001/cdr.csv", content_hash="b" * 64,
            size_bytes=10, mime_type="text/csv", is_deleted=True,
        )
        session.add_all([live, discarded])
        await session.commit()
        yield {"dataset_id": ds.id, "case_id": case.id}
        await registry.purge_dataset_data(session, ds.id)
        await session.commit()


async def test_the_evidence_list_excludes_discarded_documents(
    client, investigator_headers, two_docs_one_discarded
):
    case_id = two_docs_one_discarded["case_id"]

    response = client.get(
        f"/api/v1/explore/documents?case_id={case_id}",
        headers=investigator_headers,
    )
    assert response.status_code == 200
    payload = response.json()

    ids = {item["id"] for item in payload["items"]}
    assert "doc-dsc-live" in ids
    assert "doc-dsc-gone" not in ids, "a discarded document is not evidence"
    assert payload["total"] == len(payload["items"]) == 1, (
        "the count must agree with the rows, not include tombstones"
    )


async def test_the_case_document_list_excludes_discarded_documents(
    client, investigator_headers, two_docs_one_discarded
):
    case_id = two_docs_one_discarded["case_id"]

    response = client.get(
        f"/api/v1/cases/{case_id}/documents", headers=investigator_headers
    )
    assert response.status_code == 200
    payload = response.json()
    rows = payload.get("items", payload if isinstance(payload, list) else [])
    ids = {row["id"] for row in rows}

    assert "doc-dsc-live" in ids
    assert "doc-dsc-gone" not in ids


async def test_discarding_a_document_removes_it_from_the_evidence_list(
    client, admin_headers, investigator_headers, two_docs_one_discarded
):
    """End to end: the list changes when the operator discards a document."""
    case_id = two_docs_one_discarded["case_id"]

    before = client.get(
        f"/api/v1/explore/documents?case_id={case_id}", headers=investigator_headers
    ).json()
    assert {i["id"] for i in before["items"]} == {"doc-dsc-live"}

    # Put the second document back into play, then discard it through the API.
    async with async_session() as session:
        doc = await session.get(CaseDocument, "doc-dsc-gone")
        doc.is_deleted = False
        doc.quarantined = True
        await session.commit()

    during = client.get(
        f"/api/v1/explore/documents?case_id={case_id}", headers=investigator_headers
    ).json()
    assert {i["id"] for i in during["items"]} == {"doc-dsc-live", "doc-dsc-gone"}
    assert during["total"] == 2

    discard = client.post(
        "/api/v1/admin/quarantine/doc-dsc-gone/discard", headers=admin_headers
    )
    assert discard.status_code == 200
    body = discard.json()
    assert body["status"] == "DISCARDED"
    # The response reports the retirement rather than claiming a clean discard.
    assert "retired_nodes" in body and "retire_failed" in body
    assert body["retire_failed"] is False

    after = client.get(
        f"/api/v1/explore/documents?case_id={case_id}", headers=investigator_headers
    ).json()
    assert {i["id"] for i in after["items"]} == {"doc-dsc-live"}
    assert after["total"] == 1
