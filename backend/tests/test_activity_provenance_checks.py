"""The activity feed's provenance ticks must be computed, not decorative.

The feed printed two permanent ticks — "✓ Evidence verified ✓ Source
traceable" — next to every finding, whatever the finding actually cited.  A
tick that is not backed by a check is worse than no tick: it tells an
investigator the chain was verified when nobody verified anything.

`_provenance_checks` now computes both from stored records.  These tests pin
the important half: that the checks *fail* when the underlying data is
missing.  A check that can only ever pass is the original bug with more code.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.api.v1.investigator_activity import _provenance_checks
from app.datasets import registry
from app.db.base import new_uuid
from app.db.models import Case, CaseDocument, InvestigationFinding, SourceReference
from app.db.session import async_session
from app.domain.enums import CaseStatus, DocumentType
from app.domain.provenance import content_hash


@pytest.fixture()
async def finding_env(container):
    """A case, one real evidence document with stored bytes, and a finding."""
    payload = b"FIR body for the provenance-check fixture.\n"
    async with async_session() as session:
        ds = await registry.create_dataset(
            session, name="Provenance checks", version="1", source_kind="builtin"
        )
        await registry.activate(session, ds)
        case = Case(
            id=new_uuid(), case_number="PRV-9001", title="Provenance checks",
            jurisdiction_id="RJ-JAIPUR", dataset_id=ds.id, status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.flush()

        doc = CaseDocument(
            id="doc-prv-live", case_id=case.id, dataset_id=ds.id,
            document_type=DocumentType.FIR, filename="PRV-9001_FIR_01.pdf",
            storage_key="evidence/PRV-9001/fir.pdf",
            content_hash=content_hash(payload),
            size_bytes=len(payload), mime_type="application/pdf",
        )
        session.add(doc)
        await session.flush()

        session.add(
            SourceReference(
                doc_id=doc.id, case_id=case.id, dataset_id=ds.id,
                origin_file="evidence/PRV-9001/fir.pdf", source_type="pdf",
                record_id="PRV-0001", excerpt="FIR body",
            )
        )

        finding = InvestigationFinding(
            id=new_uuid(), case_id=case.id, finding_type="ASSOCIATION",
            title="Fixture finding", narrative="Fixture narrative", reason="fixture",
            confidence=0.8, confidence_band="HIGH",
            evidence=[{"doc_id": doc.id, "evidence_id": "E-9001"}],
        )
        session.add(finding)
        await session.commit()

        container.object_store.put(
            container.settings.minio_bucket_documents,
            doc.storage_key,
            payload,
            content_type="application/pdf",
        )

        yield {"dataset_id": ds.id, "case_id": case.id, "finding_id": finding.id}

        await registry.purge_dataset_data(session, ds.id)
        await session.commit()


async def _finding(finding_id: str) -> InvestigationFinding:
    async with async_session() as session:
        found = await session.get(InvestigationFinding, finding_id)
        assert found is not None
        return found


async def _checks(finding: InvestigationFinding) -> dict:
    """Run the real check with a real session."""
    async with async_session() as session:
        return await _provenance_checks(session, finding)


async def test_a_well_supported_finding_passes_both_checks(container, finding_env):
    finding = await _finding(finding_env["finding_id"])
    checks = await _checks(finding)  # session unused when cited resolves

    assert checks["evidence_verified"] is True
    assert checks["source_traceable"] is True
    assert checks["evidence_cited"] == checks["evidence_resolved"] == 1


async def test_a_finding_citing_a_missing_document_fails(container, finding_env):
    """The whole point: the check must be able to say no."""
    finding = await _finding(finding_env["finding_id"])
    finding.evidence = [{"doc_id": "doc-does-not-exist", "evidence_id": "E-0000"}]

    checks = await _checks(finding)

    assert checks["evidence_verified"] is False, checks
    assert checks["source_traceable"] is False, checks
    assert checks["evidence_cited"] == 1
    assert checks["evidence_resolved"] == 0
    assert "do not resolve" in checks["detail"]


async def test_a_finding_citing_no_evidence_at_all_fails(container, finding_env):
    finding = await _finding(finding_env["finding_id"])
    finding.evidence = []

    checks = await _checks(finding)

    assert checks["evidence_verified"] is False
    assert checks["source_traceable"] is False
    assert checks["evidence_cited"] == 0
    assert "cites no evidence document" in checks["detail"]


async def test_a_finding_citing_a_discarded_document_fails(container, finding_env):
    """A tombstone is not evidence, so citing only one cannot verify."""
    async with async_session() as session:
        doc = await session.get(CaseDocument, "doc-prv-live")
        doc.is_deleted = True
        await session.commit()

    finding = await _finding(finding_env["finding_id"])
    checks = await _checks(finding)

    assert checks["evidence_verified"] is False, checks
    assert checks["evidence_resolved"] == 0

    async with async_session() as session:
        doc = await session.get(CaseDocument, "doc-prv-live")
        doc.is_deleted = False
        await session.commit()


async def test_a_wrong_recorded_hash_breaks_the_chain_of_custody(container, finding_env):
    """The recorded hash must be re-checked against the stored bytes.

    The object store is write-once — overwriting a key with different content
    raises ``ConflictError`` — so tampering is prevented structurally rather
    than detected after the fact.  The realistic way this check fails is a
    recorded hash that no longer describes the stored object: a bad import, a
    half-applied repair, a hand-edited row.
    """
    async with async_session() as session:
        doc = await session.get(CaseDocument, "doc-prv-live")
        doc.content_hash = "f" * 64  # no longer the hash of the stored bytes
        await session.commit()

    finding = await _finding(finding_env["finding_id"])
    checks = await _checks(finding)

    assert checks["evidence_verified"] is False, checks
    assert checks["evidence_resolved"] == 1, "the record resolves; its hash does not"
    assert "no longer match their recorded hash" in checks["detail"]


async def test_write_once_storage_refuses_a_tampered_overwrite(container, finding_env):
    """Pin the guarantee the check above relies on: storage cannot be rewritten."""
    from app.errors import ConflictError

    with pytest.raises(ConflictError):
        container.object_store.put(
            container.settings.minio_bucket_documents,
            "evidence/PRV-9001/fir.pdf",
            b"tampered after ingestion",
            content_type="application/pdf",
        )


async def test_the_endpoint_returns_the_computed_checks(client, investigator_headers, finding_env):
    response = client.get("/api/v1/investigator-activity", headers=investigator_headers)
    assert response.status_code == 200
    activities = response.json()["activities"]

    ours = [a for a in activities if a["id"] == finding_env["finding_id"]]
    assert ours, "the fixture finding should be in the activity feed"
    checks = ours[0]["provenanceChecks"]
    assert checks["evidence_verified"] is True
    assert checks["source_traceable"] is True
    assert "resolve and match their recorded hash" in checks["detail"]
