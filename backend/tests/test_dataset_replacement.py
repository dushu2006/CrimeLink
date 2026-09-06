"""Replacing a dataset must remove the old one from every read path.

This is the failure the whole exercise exists to fix: a second dataset is
imported and the first one keeps turning up -- in the case list, on a
bookmarked case URL, in the graph, in search -- until somebody deletes the
database by hand.

Each test below replaces a dataset and then checks one read path.
"""

from __future__ import annotations

import asyncio
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
    second = await _import(tmp_path, "New dataset", SECOND_CASES, SECOND_PEOPLE)
    return first, second


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
    """The row still exists; opening it must not work, and must say why."""
    first, _second = replaced
    async with async_session() as session:
        old_case_id = (
            await session.execute(
                select(Case.id).where(Case.dataset_id == first).limit(1)
            )
        ).scalar_one()

    response = client.get(f"/api/v1/cases/{old_case_id}", headers=admin_headers)
    assert response.status_code == 404
    assert "no longer active" in response.text


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


async def test_reactivating_the_old_dataset_brings_it_back(
    client, admin_headers, replaced
):
    """Replacement hides a dataset; it does not destroy it.

    An operator who imported the wrong corpus must be able to switch back,
    otherwise "activate" is a euphemism for "delete".
    """
    first, _second = replaced
    response = client.post(f"/api/v1/datasets/{first}/activate", headers=admin_headers)
    assert response.status_code == 200, response.text

    job_id = response.json().get("job_id")
    assert job_id, "activation must rebuild the graph so it matches the tables"
    job = await _wait(client, admin_headers, job_id)
    assert job["status"] == "SUCCEEDED", job

    numbers = [
        case["case_number"]
        for case in client.get("/api/v1/cases", headers=admin_headers).json()["items"]
    ]
    assert "OLD/2024/1" in numbers, numbers
    assert "NEW/2026/9" not in numbers, numbers


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
