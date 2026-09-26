"""A record with no case is unassigned — never ``ALL``, never a 500.

The production incident (crime-link-seven.vercel.app, deployment
``dpl_58ALhz4rJxvuMyygSQrbB7Hz81XU``)::

    GET /api/v1/evidence/51c6ca1b-.../provenance
    → 500 AttributeError: 'NoneType' object has no attribute 'strip'
      app/services/cases.py:269 in resolve_case_ref

Content-first ingestion leaves a document unassigned (``case_id IS NULL``)
whenever nothing in the data proves a case association — a dataset-level
resource in a case-less corpus, or a document that is ambiguous between two
cases.  ``evidence_provenance`` handed that NULL straight to
``require_case`` → ``resolve_case_ref``, which assumed a string.

Pinned here:

* ``resolve_case_ref(None)`` is a controlled 404, and ``None`` never means
  ``ALL`` (that would hand the caller every case in the deployment);
* the provenance endpoint answers with the record and ``case: null`` — the
  existing API contract, which the frontend type already allows;
* an unassigned record stays unassigned: no case is invented, attached, or
  written back;
* assigned records, ``ALL``, foreign jurisdictions and replaced datasets all
  behave exactly as before (authorization is not relaxed anywhere).
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.datasets.pipeline import ImportOptions, run_import
from app.db.base import new_uuid
from app.db.models import Case, CaseDocument, Dataset, SourceReference, User
from app.db.session import async_session
from app.domain.enums import DocumentType, IngestionStatus
from app.errors import NotFoundError
from app.security.deps import JurisdictionScope, Principal
from app.services import cases as case_service

#: The corpus the tests run against: two real cases, one document whose content
#: names a case (assigned) and one whose content names nothing (unassigned).
#: Both shapes are produced by the REAL pipeline — no row is planted to fake a
#: NULL case_id.
JURISDICTION = "RJ-JAIPUR"  # matches the test users, so scope hides nothing

CASES_CSV = (
    "case_id,case_number,title\n"
    "CASE_0001,AMB/2026/1,Assigned case\n"
    "CASE_0002,AMB/2026/2,Second case\n"
)
PEOPLE_CSV = (
    "person_id,full_name,phone_number\n"
    "PERSON_0001,Assigned Person,9811222333\n"
    "PERSON_0002,Second Person,9811222334\n"
)
ASSIGNED_TXT = (
    "Statement recorded under CASE_0001.\n"
    "Assigned Person was questioned about the recovered vehicle.\n"
)
UNASSIGNED_TXT = (
    "An unattached sheet found with the papers.\n"
    "It names no case, no person and no identifier of any kind.\n"
)


def _scope_for(principal: Principal, expected_dataset_id: str | None = None) -> JurisdictionScope:
    return JurisdictionScope(
        principal,
        granted_jurisdictions=set(),
        granted_case_ids=set(),
        expected_dataset_id=expected_dataset_id,
    )


async def _principal(session, badge: str) -> Principal:
    user = (
        await session.execute(select(User).where(User.badge_number == badge))
    ).scalar_one()
    return Principal(user, ip_address="127.0.0.1")


@pytest.fixture()
async def mixed_dataset(container, admin_headers, tmp_path) -> dict:
    """One imported dataset holding an assigned and an unassigned document."""
    folder = tmp_path / "ambiguous_corpus"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cases.csv").write_text(CASES_CSV, encoding="utf-8")
    (folder / "people.csv").write_text(PEOPLE_CSV, encoding="utf-8")
    (folder / "assigned_statement.txt").write_text(ASSIGNED_TXT, encoding="utf-8")
    (folder / "loose_sheet.txt").write_text(UNASSIGNED_TXT, encoding="utf-8")

    async with async_session() as session:
        report = await run_import(
            session,
            [folder],
            ImportOptions(
                name="Ambiguous corpus",
                copy_inputs=False,
                activate=True,
                build_graph=True,
                jurisdiction_id=JURISDICTION,
            ),
        )
    assert report.error is None, report.error

    async with async_session() as session:
        rows = (
            await session.execute(
                select(CaseDocument).where(CaseDocument.dataset_id == report.dataset_id)
            )
        ).scalars().all()
        by_name = {row.filename: row for row in rows}
        cases = {
            case.dataset_case_key: case.id
            for case in (
                await session.execute(select(Case).where(Case.dataset_id == report.dataset_id))
            ).scalars()
        }

    assert "loose_sheet.txt" in by_name, sorted(by_name)
    assert by_name["loose_sheet.txt"].case_id is None, (
        "the corpus was built so that one document stays unassigned; "
        f"it resolved to {by_name['loose_sheet.txt'].case_id}"
    )
    assert by_name["assigned_statement.txt"].case_id == cases["CASE_0001"], (
        "the document that names CASE_0001 must be assigned to it"
    )

    yield {
        "dataset_id": report.dataset_id,
        "unassigned_doc_id": by_name["loose_sheet.txt"].id,
        "assigned_doc_id": by_name["assigned_statement.txt"].id,
        "assigned_case_id": cases["CASE_0001"],
        "other_case_id": cases["CASE_0002"],
        "container_case_id": cases.get("ALL"),
    }

    from app.datasets import registry

    async with async_session() as session:
        await registry.purge_dataset_data(session, report.dataset_id)
        await session.execute(
            Case.__table__.delete().where(Case.dataset_id == report.dataset_id)
        )
        await session.execute(
            Dataset.__table__.delete().where(Dataset.id == report.dataset_id)
        )
        await session.commit()


# --------------------------------------------------------------------------- #
# 1. Service level: a NULL reference is refused, and is never "ALL"
# --------------------------------------------------------------------------- #


async def test_resolve_case_ref_none_is_a_controlled_not_found(db, mixed_dataset, users):
    """``resolve_case_ref(None)`` raises NotFoundError — not AttributeError."""
    scope = _scope_for(Principal(users["ADM-0001"], ip_address="127.0.0.1"))
    with pytest.raises(NotFoundError):
        await case_service.resolve_case_ref(db, scope, None)
    with pytest.raises(NotFoundError):
        await case_service.require_case(db, scope, None)


@pytest.mark.parametrize("blank", ["", "   ", "\t", None])
async def test_blank_case_references_are_refused(db, mixed_dataset, users, blank):
    """Whitespace-only and missing references are the same controlled 404."""
    scope = _scope_for(Principal(users["INV-0001"], ip_address="127.0.0.1"))
    with pytest.raises(NotFoundError):
        await case_service.resolve_case_ref(db, scope, blank)


async def test_none_never_means_all(db, mixed_dataset, users):
    """``None`` must not resolve to the container case (or to any case)."""
    scope = _scope_for(Principal(users["ADM-0001"], ip_address="127.0.0.1"))
    container_id = mixed_dataset["container_case_id"]
    assert container_id, "every import materialises the dataset container case"

    with pytest.raises(NotFoundError) as missing:
        await case_service.resolve_case_ref(db, scope, None)
    assert "ALL" not in str(missing.value.detail)

    # The string key is refused exactly as before: the container is reachable
    # only by id, and only while it is the dataset's sole case.
    with pytest.raises(NotFoundError):
        await case_service.resolve_case_ref(db, scope, "ALL")
    with pytest.raises(NotFoundError):
        await case_service.resolve_case_ref(db, scope, " all ")


async def test_a_valid_case_id_and_number_still_resolve(db, mixed_dataset, users):
    scope = _scope_for(Principal(users["INV-0001"], ip_address="127.0.0.1"))
    by_id = await case_service.resolve_case_ref(db, scope, mixed_dataset["assigned_case_id"])
    assert by_id.id == mixed_dataset["assigned_case_id"]

    by_number = await case_service.resolve_case_ref(db, scope, "AMB/2026/2")
    assert by_number.id == mixed_dataset["other_case_id"]


async def test_another_jurisdiction_is_still_refused(db, mixed_dataset, users):
    """Authorization is unchanged: a Kota officer cannot open a Jaipur case."""
    scope = _scope_for(Principal(users["INV-0002"], ip_address="127.0.0.1"))
    assert users["INV-0002"].jurisdiction_id != JURISDICTION
    with pytest.raises(Exception) as denied:
        await case_service.resolve_case_ref(db, scope, mixed_dataset["assigned_case_id"])
    assert denied.value.http_status == 404


async def test_require_case_for_record_returns_none_for_an_unassigned_row(
    db, mixed_dataset, users
):
    """The unassigned document is authorised and reported as unassigned."""
    scope = _scope_for(Principal(users["INV-0001"], ip_address="127.0.0.1"))
    document = await db.get(CaseDocument, mixed_dataset["unassigned_doc_id"])
    assert document.case_id is None

    resolved = await case_service.require_case_for_record(db, scope, document)
    assert resolved is None, "no case may be invented for an unassigned record"
    assert document.case_id is None, "the record must still be unassigned"


async def test_require_case_for_record_resolves_an_assigned_row(db, mixed_dataset, users):
    scope = _scope_for(Principal(users["INV-0001"], ip_address="127.0.0.1"))
    document = await db.get(CaseDocument, mixed_dataset["assigned_doc_id"])
    resolved = await case_service.require_case_for_record(db, scope, document)
    assert resolved is not None and resolved.id == mixed_dataset["assigned_case_id"]


async def test_require_case_for_record_refuses_an_assigned_row_out_of_scope(
    db, mixed_dataset, users
):
    scope = _scope_for(Principal(users["INV-0002"], ip_address="127.0.0.1"))
    document = await db.get(CaseDocument, mixed_dataset["assigned_doc_id"])
    with pytest.raises(Exception) as denied:
        await case_service.require_case_for_record(db, scope, document)
    assert denied.value.http_status == 404


async def test_an_unassigned_row_of_a_replaced_dataset_stays_closed(
    db, mixed_dataset, users
):
    """The dataset boundary still applies to rows with no case."""
    from app.datasets import registry

    scope = _scope_for(Principal(users["ADM-0001"], ip_address="127.0.0.1"))
    stale = CaseDocument(
        id=new_uuid(),
        case_id=None,
        dataset_id="dataset-that-is-not-active",
        document_type=DocumentType.FIR,
        filename="stale_unassigned.txt",
        storage_key="stale_unassigned.txt",
        content_hash="0" * 64,
        ingestion_status=IngestionStatus.COMPLETE,
    )
    db.add(stale)
    await db.commit()
    try:
        with pytest.raises(NotFoundError):
            await case_service.require_case_for_record(db, scope, stale)
        assert await registry.active_dataset_id(db) == mixed_dataset["dataset_id"]
    finally:
        await db.delete(stale)
        await db.commit()


async def test_an_explicit_stale_dataset_header_is_refused(db, mixed_dataset, users):
    """``X-Dataset-Id`` pointing at a replaced dataset closes the record."""
    document = await db.get(CaseDocument, mixed_dataset["unassigned_doc_id"])
    scope = _scope_for(
        Principal(users["ADM-0001"], ip_address="127.0.0.1"),
        expected_dataset_id="some-other-dataset",
    )
    with pytest.raises(NotFoundError):
        await case_service.require_case_for_record(db, scope, document)


# --------------------------------------------------------------------------- #
# 2. The endpoint that returned 500 in production
# --------------------------------------------------------------------------- #


async def test_provenance_of_an_unassigned_document_is_not_a_500(
    client, admin_headers, mixed_dataset
):
    doc_id = mixed_dataset["unassigned_doc_id"]
    response = client.get(
        f"/api/v1/evidence/{doc_id}/provenance", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["document"]["id"] == doc_id
    # The honest contract: no case, and the chain says so rather than ticking.
    assert body["case"] is None
    chain = {step["step"]: step for step in body["chain"]}
    assert chain["CASE"]["resolved"] is False
    assert chain["CASE"]["ref"] is None
    assert chain["EVIDENCE"]["resolved"] is True

    # Provenance itself is intact: the original file and the source reference
    # recorded at ingestion are both resolved from stored data.
    assert body["dataset_file"] is not None
    assert body["dataset_file"]["relative_path"] == "loose_sheet.txt"
    assert body["file"]["available"] is True
    assert body["file"]["relative_path"] == "loose_sheet.txt"
    assert body["source_references"], "ingestion recorded a source reference"
    assert body["source_references"][0]["origin_file"] == "loose_sheet.txt"
    assert body["checks"]["traceable_to_original"]["ok"] is True


async def test_provenance_for_every_role_is_consistent(
    client, admin_headers, investigator_headers, viewer_headers, mixed_dataset
):
    """Dataset-level evidence is dataset-scoped, so every role gets one answer."""
    doc_id = mixed_dataset["unassigned_doc_id"]
    for headers in (admin_headers, investigator_headers, viewer_headers):
        response = client.get(
            f"/api/v1/evidence/{doc_id}/provenance", headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["case"] is None


async def test_the_other_evidence_routes_accept_an_unassigned_document(
    client, admin_headers, mixed_dataset
):
    """Every route that read ``document.case_id`` shares the same fix."""
    doc_id = mixed_dataset["unassigned_doc_id"]

    evidence = client.get(f"/api/v1/evidence/{doc_id}", headers=admin_headers)
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["case_id"] is None

    verify = client.get(f"/api/v1/evidence/{doc_id}/verify", headers=admin_headers)
    assert verify.status_code == 200, verify.text
    assert verify.json()["document_id"] == doc_id

    document = client.get(f"/api/v1/documents/{doc_id}", headers=admin_headers)
    assert document.status_code == 200, document.text
    assert document.json()["case_id"] is None

    detail = client.get(f"/api/v1/explore/documents/{doc_id}", headers=admin_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["case"] is None

    references = client.get(
        f"/api/v1/sources/documents/{doc_id}/references", headers=admin_headers
    )
    assert references.status_code == 200, references.text
    assert references.json()["case_id"] is None
    assert references.json()["total"] >= 1

    lookup = client.get(
        "/api/v1/sources/lookup?origin_file=loose_sheet.txt", headers=admin_headers
    )
    assert lookup.status_code == 200, lookup.text
    assert lookup.json()["count"] >= 1, (
        "an unassigned provenance row must not be silently dropped"
    )

    reference_id = references.json()["items"][0]["id"]
    one = client.get(
        f"/api/v1/sources/reference/{reference_id}", headers=admin_headers
    )
    assert one.status_code == 200, one.text
    assert one.json()["case"] is None


async def test_provenance_of_an_assigned_document_names_its_case(
    client, admin_headers, mixed_dataset
):
    doc_id = mixed_dataset["assigned_doc_id"]
    response = client.get(
        f"/api/v1/evidence/{doc_id}/provenance", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["case"] is not None
    assert body["case"]["id"] == mixed_dataset["assigned_case_id"]
    assert body["case"]["case_number"] == "AMB/2026/1"
    chain = {step["step"]: step for step in body["chain"]}
    assert chain["CASE"]["resolved"] is True


async def test_provenance_is_refused_for_another_jurisdiction(client, mixed_dataset):
    """An out-of-jurisdiction officer probing a Jaipur case's evidence learns nothing.

    A dedicated investigator rather than the suite's Kota fixture: the
    access-grant tests lawfully open RJ-JAIPUR to INV-0002 for a day, so
    asserting on that badge would make this check depend on test order (the
    same reason ``test_context_endpoint_respects_jurisdiction_scope`` uses its
    own out-of-jurisdiction user).
    """
    from tests.conftest import _make_user, auth_headers

    _make_user("INV-0098", "Inspector Bikaner", "INVESTIGATOR", "RJ-BIKANER")
    headers = auth_headers(client, "INV-0098")
    response = client.get(
        f"/api/v1/evidence/{mixed_dataset['assigned_doc_id']}/provenance",
        headers=headers,
    )
    assert response.status_code == 404, response.text


async def test_the_container_case_key_is_refused_by_the_case_route(
    client, admin_headers, mixed_dataset
):
    response = client.get("/api/v1/cases/ALL", headers=admin_headers)
    assert response.status_code == 404, response.text


# --------------------------------------------------------------------------- #
# 3. Unassigned stays unassigned
# --------------------------------------------------------------------------- #


async def test_reading_provenance_never_assigns_a_case(
    client, admin_headers, mixed_dataset
):
    doc_id = mixed_dataset["unassigned_doc_id"]
    async with async_session() as session:
        before = await session.scalar(
            select(func.count()).select_from(Case).where(
                Case.dataset_id == mixed_dataset["dataset_id"]
            )
        )

    for _ in range(2):
        assert client.get(
            f"/api/v1/evidence/{doc_id}/provenance", headers=admin_headers
        ).status_code == 200
    assert client.get(f"/api/v1/evidence/{doc_id}", headers=admin_headers).status_code == 200
    assert client.get(
        f"/api/v1/explore/documents/{doc_id}", headers=admin_headers
    ).status_code == 200

    async with async_session() as session:
        document = await session.get(CaseDocument, doc_id)
        assert document.case_id is None, "a read must never assign a case"
        assert (document.source_metadata or {}).get("case_id") is None
        after = await session.scalar(
            select(func.count()).select_from(Case).where(
                Case.dataset_id == mixed_dataset["dataset_id"]
            )
        )
        references = await session.scalar(
            select(func.count())
            .select_from(SourceReference)
            .where(SourceReference.doc_id == doc_id)
        )
    assert after == before, "no case may be created to hang the document on"
    assert references >= 1

    async with async_session() as session:
        rows = (
            await session.execute(
                select(SourceReference.case_id).where(SourceReference.doc_id == doc_id)
            )
        ).scalars().all()
    assert all(case_id is None for case_id in rows), (
        "the source references stay unassigned too"
    )
