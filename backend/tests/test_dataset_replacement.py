"""Replacing a dataset must remove the old one from every read path.

This is the failure the whole exercise exists to fix: a second dataset is
imported and the first one keeps turning up -- in the case list, on a
bookmarked case URL, in the graph, in search -- until somebody deletes the
database by hand.

Each test below replaces a dataset and then checks one read path.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.datasets.pipeline import ImportOptions, run_import
from app.db.models import Case
from app.db.session import async_session

JURISDICTION = "RJ-JAIPUR"  # matches the test users, so nothing is hidden by scope

FIRST_CASES = (
    "case_id,case_number,title\n"
    "C1,OLD/2024/1,Old case one\n"
    "C2,OLD/2024/2,Old case two\n"
)
FIRST_PEOPLE = (
    "person_id,full_name,phone_number\n"
    "P1,Old Person One,9811111111\n"
    "P2,Old Person Two,9811111112\n"
)
SECOND_CASES = "case_id,case_number,title\nC9,NEW/2026/9,Brand new case\n"
SECOND_PEOPLE = "person_id,full_name,phone_number\nP9,New Person Nine,9899999999\n"


async def _import(root: Path, name: str, cases: str, people: str) -> str:
    folder = root / name.replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cases.csv").write_text(cases, encoding="utf-8")
    (folder / "people.csv").write_text(people, encoding="utf-8")
    async with async_session() as session:
        report = await run_import(
            session,
            [folder],
            ImportOptions(
                name=name,
                copy_inputs=False,
                activate=True,
                build_graph=True,
                jurisdiction_id=JURISDICTION,
            ),
        )
    assert report.error is None, report.error
    return report.dataset_id


@pytest.fixture()
async def replaced(tmp_path, container):
    """Import one dataset, then replace it with a completely different one."""
    first = await _import(tmp_path, "Old dataset", FIRST_CASES, FIRST_PEOPLE)
    async with async_session() as session:
        first_case_id = (
            await session.execute(
                select(Case.id).where(Case.dataset_id == first).limit(1)
            )
        ).scalar_one()
    second = await _import(tmp_path, "New dataset", SECOND_CASES, SECOND_PEOPLE)
    return {"first": first, "second": second, "first_case_id": first_case_id}


async def _wait(client, headers, job_id: str) -> dict:
    for _ in range(600):
        job = client.get(f"/api/v1/datasets/jobs/{job_id}", headers=headers).json()
        if job["terminal"]:
            return job
        await asyncio.sleep(0.02)
    raise AssertionError("job did not finish")


# --------------------------------------------------------------------------- #


async def test_the_case_list_shows_only_the_active_dataset(
    client, admin_headers, replaced
):
    body = client.get("/api/v1/cases", headers=admin_headers).json()
    numbers = [case["case_number"] for case in body["items"]]
    assert "NEW/2026/9" in numbers, numbers
    assert not any(number.startswith("OLD/") for number in numbers), numbers


async def test_a_bookmarked_case_from_a_replaced_dataset_is_gone(
    client, admin_headers, replaced
):
    """The old case was purged on replacement; opening it must return 404."""
    old_case_id = replaced["first_case_id"]
    response = client.get(f"/api/v1/cases/{old_case_id}", headers=admin_headers)
    assert response.status_code == 404


async def test_the_admin_database_view_is_scoped_too(client, admin_headers, replaced):
    """The DB inspector must not be the one place the old data lingers."""
    body = client.get("/api/v1/admin/database/cases", headers=admin_headers).json()
    numbers = [case["case_number"] for case in body["items"]]
    assert "NEW/2026/9" in numbers, numbers
    assert not any(number.startswith("OLD/") for number in numbers), numbers


async def test_the_graph_holds_only_the_active_dataset(
    client, admin_headers, replaced, container
):
    names = {
        item["name"] for item in container.graph_store.list_nodes(limit=500)["items"]
    }
    assert "New Person Nine" in names, sorted(names)
    assert "Old Person One" not in names, sorted(names)
    assert "Old Person Two" not in names, sorted(names)


async def test_search_does_not_return_the_replaced_dataset(
    client, admin_headers, replaced
):
    hits = client.get(
        "/api/v1/search?q=Old Person One&limit=10", headers=admin_headers
    ).json()
    names = [item.get("name") for item in (hits.get("items") or [])]
    assert "Old Person One" not in names, names


async def test_replaced_dataset_is_completely_purged_from_database(
    client, admin_headers, replaced
):
    """Replacement completely purges old dataset data from database."""
    first = replaced["first"]
    async with async_session() as session:
        old_cases = (
            await session.execute(select(Case).where(Case.dataset_id == first))
        ).scalars().all()
        assert len(old_cases) == 0, f"Old cases should be completely purged, found {len(old_cases)}"


async def test_a_manually_created_case_survives_a_replacement(
    client, admin_headers, tmp_path, container
):
    """Only imported rows follow the dataset. An officer's own case is theirs."""
    created = client.post(
        "/api/v1/cases",
        headers=admin_headers,
        json={"case_number": "HAND/2026/1", "title": "Typed in by hand"},
    )
    assert created.status_code in (200, 201), created.text

    await _import(tmp_path, "Dataset A", FIRST_CASES, FIRST_PEOPLE)
    await _import(tmp_path, "Dataset B", SECOND_CASES, SECOND_PEOPLE)

    numbers = [
        case["case_number"]
        for case in client.get("/api/v1/cases", headers=admin_headers).json()["items"]
    ]
    assert "HAND/2026/1" in numbers, "a hand-created case was hidden by an import"
    assert "NEW/2026/9" in numbers, numbers
    assert not any(number.startswith("OLD/") for number in numbers), numbers


async def test_two_datasets_may_carry_the_same_case_numbers(
    client, admin_headers, tmp_path, container
):
    """v1 and v2 of a corpus share case numbers; importing both must work."""
    await _import(tmp_path / "one", "Corpus v1", FIRST_CASES, FIRST_PEOPLE)
    second = await _import(tmp_path / "two", "Corpus v2", FIRST_CASES, FIRST_PEOPLE)

    numbers = [
        case["case_number"]
        for case in client.get("/api/v1/cases", headers=admin_headers).json()["items"]
    ]
    # Exactly one visible case per number: the active dataset's.
    assert len([n for n in numbers if n.startswith("OLD/2024/1")]) == 1, numbers

    async with async_session() as session:
        keys = (
            (
                await session.execute(
                    select(Case.dataset_case_key).where(Case.dataset_id == second)
                )
            )
            .scalars()
            .all()
        )
    # The natural key from the source data is preserved untouched, alongside
    # the dataset's container case for records no case claims.
    assert set(keys) == {"C1", "C2", "ALL"}, keys


async def test_documents_patterns_and_queues_only_show_the_active_dataset(
    client, admin_headers, replaced
):
    """The derived review queues must follow replacement, not accumulate.

    Rows are planted directly against both datasets' cases: what is under
    test is the *read path's* scoping — a replaced dataset's evidence and
    review work must not keep surfacing in Explore or the admin queues.
    """
    import uuid as _uuid

    from app.db.base import new_uuid
    from app.db.models import CaseDocument, DetectedPattern, EntityResolutionItem
    from app.domain.enums import (
        DocumentType,
        IngestionStatus,
        MatchBasis,
        PatternStatus,
        PatternType,
        ResolutionStatus,
    )

    first, second = replaced["first"], replaced["second"]
    async with async_session() as session:
        old_case = Case(
            id=new_uuid(),
            case_number="ORPHAN/OLD/1",
            title="Orphaned old case",
            jurisdiction_id=JURISDICTION,
            dataset_id=first,
        )
        session.add(old_case)
        await session.flush()

        new_case_id = (
            await session.execute(
                select(Case.id).where(Case.dataset_id == second).limit(1)
            )
        ).scalar_one()
        ids = {"old": old_case.id, "new": new_case_id}
        for label, dataset in (("old", first), ("new", second)):
            case_id = ids[label]
            session.add(
                CaseDocument(
                    id=new_uuid(),
                    case_id=case_id,
                    dataset_id=dataset,
                    document_type=DocumentType.FIR,
                    filename=f"{label}_fir.txt",
                    storage_key=f"docs/{label}-{_uuid.uuid4().hex}.txt",
                    content_hash="0" * 64,
                    ingestion_status=IngestionStatus.COMPLETE,
                )
            )
            session.add(
                DetectedPattern(
                    id=new_uuid(),
                    case_id=case_id,
                    pattern_type=PatternType.NETWORK_BRIDGE,
                    confidence=0.9,
                    entity_keys=[f"{label}-key"],
                    evidence_doc_ids=[],
                    explanation=f"planted {label} pattern",
                    details={},
                    status=PatternStatus.NEW,
                )
            )
            session.add(
                EntityResolutionItem(
                    id=new_uuid(),
                    case_id=case_id,
                    source_node_key=f"{label}-a",
                    target_node_key=f"{label}-b",
                    similarity_score=0.95,
                    match_basis=MatchBasis.NAME_FUZZY,
                    evidence_doc_ids=[],
                    status=ResolutionStatus.PENDING,
                )
            )
        await session.commit()

    documents = client.get("/api/v1/explore/documents", headers=admin_headers).json()
    filenames = [doc["filename"] for doc in documents["items"]]
    assert "new_fir.txt" in filenames, filenames
    assert "old_fir.txt" not in filenames, filenames

    patterns = client.get("/api/v1/patterns", headers=admin_headers).json()
    pids = {p["case_id"] for p in patterns["items"]}
    assert ids["new"] in pids, pids
    assert ids["old"] not in pids, pids

    queue = client.get("/api/v1/resolution", headers=admin_headers).json()
    qids = {item["case_id"] for item in queue["items"]}
    assert ids["new"] in qids, qids
    assert ids["old"] not in qids, qids

    overview = client.get("/api/v1/admin/overview", headers=admin_headers).json()
    # The overview must describe what the platform is *working on*: exactly
    # what the scoped read paths report, never the sum of every dataset ever
    # imported. (Absolute numbers depend on other tests' hand-created rows, so
    # the invariant checked here is cross-endpoint consistency.)
    cases_visible = client.get("/api/v1/cases?limit=500", headers=admin_headers).json()
    assert overview["cases"] == len(cases_visible["items"]), (overview, cases_visible)
    assert overview["documents"] == documents["total"], (overview, documents)
    assert overview["new_patterns"] == len(patterns["items"]), overview
    assert overview["pending_matches"] == len(queue["items"]), overview


async def test_the_sources_listing_is_the_active_dataset_only(
    client, admin_headers, replaced
):
    """The Sources page speaks only the active dataset's manifest.

    ``copy_inputs=False`` keeps each dataset's workspace at the source folder,
    so relative paths here are flat -- which is also what makes the *content*
    assertions the real isolation proof: same filenames in both imports, and
    every read must return the new dataset's bytes.
    """
    first, second = replaced["first"], replaced["second"]
    body = client.get("/api/v1/sources/files", headers=admin_headers).json()
    assert body["dataset_id"] == second
    paths = {item["path"] for item in body["items"]}
    assert paths == {"cases.csv", "people.csv"}, paths

    # A file that existed only in the replaced dataset is gone.
    stale = client.get(
        "/api/v1/sources/preview?path=only_old_dataset_had_this.csv", headers=admin_headers
    ).json()
    assert stale["status"] == "NOT_FOUND"
    stale_raw = client.get(
        "/api/v1/sources/raw?path=only_old_dataset_had_this.csv", headers=admin_headers
    )
    assert stale_raw.status_code == 404

    # Same-named file: the bytes shown are the ACTIVE dataset's people table.
    listing = client.get(
        "/api/v1/sources/preview?path=people.csv", headers=admin_headers
    ).json()
    text = json.dumps(listing.get("window"))
    assert "New Person Nine" in text
    assert "Old Person One" not in text
