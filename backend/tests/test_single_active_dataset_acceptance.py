"""End-to-end acceptance test for Single Active Dataset Isolation and Empty Initial State.

Verifies:
1. Fresh start empty state across cases, entities, documents, relationships, sources, graph, search, and AI.
2. Dataset A import and activation with entities (Arjun Reddy, ACCT_0001, VEH_0001, CASE_0001).
3. Dataset B replacement with same IDs (CASE_0001, PERSON_0001 = Vikram Malhotra).
4. Complete purging of Dataset A from DB, Graph, Search, and Sources.
5. Strict 404 on Dataset A URLs and resources.
6. Dataset B's CASE_0001 active exclusively.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.datasets.pipeline import ImportOptions, run_import
from app.db.models import Case, Dataset, DatasetEntity, DatasetFile
from app.db.session import async_session

JURISDICTION = "RJ-JAIPUR"

DATASET_A_CASES = (
    "case_id,case_number,title\n"
    "CASE_0001,BL/2024/0001,Blue Ledger Scam\n"
)
DATASET_A_PEOPLE = (
    "person_id,full_name,phone_number\n"
    "PERSON_0001,Arjun Reddy,9876543210\n"
)
DATASET_A_ACCOUNTS = (
    "account_id,account_number,bank_name\n"
    "ACCT_0001,9988776655,State Bank\n"
)
DATASET_A_VEHICLES = (
    "vehicle_id,registration_number,make_model\n"
    "VEH_0001,TS09AB1234,Toyota Fortuner\n"
)

DATASET_B_CASES = (
    "case_id,case_number,title\n"
    "CASE_0001,GF/2026/0002,Green Forest Syndicate\n"
)
DATASET_B_PEOPLE = (
    "person_id,full_name,phone_number\n"
    "PERSON_0001,Vikram Malhotra,9123456789\n"
)


async def _import_dataset(root: Path, name: str, files: dict[str, str]) -> str:
    folder = root / name.replace(" ", "_")
    folder.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (folder / fname).write_text(content, encoding="utf-8")

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


@pytest.mark.asyncio
async def test_single_active_dataset_acceptance_lifecycle(
    client, admin_headers, container, tmp_path, caplog
):
    # -----------------------------------------------------------------------
    # Step 1: Fresh Start / Empty State Verification
    # -----------------------------------------------------------------------
    # Deactivate / ensure no active dataset exists initially
    async with async_session() as session:
        active_datasets = (
            await session.execute(
                select(Dataset).where(Dataset.status == "ACTIVE")
            )
        ).scalars().all()
        for d in active_datasets:
            d.status = "STAGED"
        await session.commit()

    # Cases: 0 items
    res = client.get("/api/v1/cases", headers=admin_headers)
    assert res.status_code == 200
    assert len(res.json()["items"]) == 0

    # Entities: 0 items
    res = client.get("/api/v1/explore/entities", headers=admin_headers)
    assert res.status_code == 200
    assert len(res.json()["items"]) == 0

    # Documents: 0 items
    res = client.get("/api/v1/explore/documents", headers=admin_headers)
    assert res.status_code == 200
    assert len(res.json()["items"]) == 0

    # Relationships: 0 items
    res = client.get("/api/v1/explore/relationships", headers=admin_headers)
    assert res.status_code == 200
    assert len(res.json()["items"]) == 0

    # Sources: 0 files, no active dataset
    res = client.get("/api/v1/sources/files", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["counts"]["files"] == 0
    assert body["dataset_id"] is None
    assert body["dataset_name"] is None

    # Sources preview / raw: 404 when no dataset is active
    res = client.get("/api/v1/sources/preview?path=cases.csv", headers=admin_headers)
    assert res.status_code == 404

    # Graph: 0 nodes, 0 edges
    nodes = container.graph_store.list_nodes(limit=100)["items"]
    assert len(nodes) == 0

    # Search: 0 hits
    res = client.get("/api/v1/search?q=Arjun Reddy", headers=admin_headers)
    assert res.status_code == 200
    assert len(res.json().get("items") or []) == 0

    # AI Greeting: fast greeting path
    res = client.post(
        "/api/v1/ai/ask",
        headers=admin_headers,
        json={"question": "Hi"},
    )
    assert res.status_code == 200
    assert len(res.json().get("finding", {}).get("summary", "")) > 0

    # AI Investigative Query: returns explicit no active dataset message
    res = client.post(
        "/api/v1/ai/ask",
        headers=admin_headers,
        json={"question": "Who is connected to Arjun Reddy?"},
    )
    assert res.status_code == 200
    ai_summary = res.json()["finding"]["summary"]
    assert "No dataset is currently active. Import a dataset before running an investigation query." in ai_summary

    # -----------------------------------------------------------------------
    # Step 2: Import Dataset A (Blue Ledger)
    # -----------------------------------------------------------------------
    dataset_a_id = await _import_dataset(
        tmp_path / "corpus_a",
        "Blue Ledger",
        {
            "cases.csv": DATASET_A_CASES,
            "people.csv": DATASET_A_PEOPLE,
            "accounts.csv": DATASET_A_ACCOUNTS,
            "vehicles.csv": DATASET_A_VEHICLES,
            "evidence_note.txt": "Confidential note regarding Arjun Reddy transactions.",
        },
    )

    # Verify Dataset A is active
    res = client.get("/api/v1/datasets/active", headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["active"]["id"] == dataset_a_id

    # Verify Dataset A Cases
    res = client.get("/api/v1/cases", headers=admin_headers)
    cases_a = res.json()["items"]
    assert any(c["case_number"] == "BL/2024/0001" for c in cases_a)
    dataset_a_case_uuid = [c["id"] for c in cases_a if c["case_number"] == "BL/2024/0001"][0]

    # Verify Dataset A Entities
    res = client.get("/api/v1/explore/entities", headers=admin_headers)
    entity_names = [e["name"] for e in res.json()["items"]]
    assert "Arjun Reddy" in entity_names

    # Verify Dataset A Sources
    res = client.get("/api/v1/sources/files", headers=admin_headers)
    src_paths = {f["path"] for f in res.json()["items"]}
    assert "cases.csv" in src_paths
    assert "evidence_note.txt" in src_paths

    # Verify Dataset A Search
    res = client.get("/api/v1/search?q=Arjun Reddy", headers=admin_headers)
    assert any("Arjun Reddy" in (item.get("name") or "") for item in res.json()["items"])

    # Verify Dataset A Graph
    nodes_a = {n["name"] for n in container.graph_store.list_nodes(limit=100)["items"]}
    assert "Arjun Reddy" in nodes_a

    # -----------------------------------------------------------------------
    # Step 3: Import Dataset B (Green Forest Syndicate) with same IDs
    # -----------------------------------------------------------------------
    dataset_b_id = await _import_dataset(
        tmp_path / "corpus_b",
        "Green Forest Syndicate",
        {
            "cases.csv": DATASET_B_CASES,
            "people.csv": DATASET_B_PEOPLE,
            "forest_log.txt": "Timber movement logs.",
        },
    )
    assert dataset_b_id != dataset_a_id

    # -----------------------------------------------------------------------
    # Step 4: Verification of Complete Dataset A Purge & Replacement
    # -----------------------------------------------------------------------
    # 4a. Persistence Level: 0 records of Dataset A in DB
    async with async_session() as session:
        cases_left = (
            await session.execute(
                select(Case).where(Case.dataset_id == dataset_a_id)
            )
        ).scalars().all()
        assert len(cases_left) == 0, "Dataset A cases must be completely purged"

        entities_left = (
            await session.execute(
                select(DatasetEntity).where(DatasetEntity.dataset_id == dataset_a_id)
            )
        ).scalars().all()
        assert len(entities_left) == 0, "Dataset A entities must be completely purged"

        files_left = (
            await session.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == dataset_a_id)
            )
        ).scalars().all()
        assert len(files_left) == 0, "Dataset A files must be completely purged"

    # 4b. Graph Level: 0 Dataset A nodes in Graph
    nodes_b = {n["name"] for n in container.graph_store.list_nodes(limit=100)["items"]}
    assert "Arjun Reddy" not in nodes_b
    assert "Vikram Malhotra" in nodes_b

    # 4c. Search Level: 0 results for Dataset A identifiers
    res = client.get("/api/v1/search?q=Arjun Reddy", headers=admin_headers)
    assert len(res.json().get("items") or []) == 0

    res = client.get("/api/v1/search?q=TS09AB1234", headers=admin_headers)
    assert len(res.json().get("items") or []) == 0

    # 4d. Sources Level: Dataset A files are gone, only Dataset B files visible
    res = client.get("/api/v1/sources/files", headers=admin_headers)
    body = res.json()
    assert body["dataset_id"] == dataset_b_id
    src_paths_b = {f["path"] for f in body["items"]}
    assert "forest_log.txt" in src_paths_b
    assert "evidence_note.txt" not in src_paths_b

    # Requesting Dataset A source file returns 404
    res = client.get("/api/v1/sources/preview?path=evidence_note.txt", headers=admin_headers)
    assert res.json()["status"] == "NOT_FOUND"

    res = client.get("/api/v1/sources/raw?path=evidence_note.txt", headers=admin_headers)
    assert res.status_code == 404

    # 4e. URL / Case Scoping: Old Dataset A case UUID returns 404
    res = client.get(f"/api/v1/cases/{dataset_a_case_uuid}", headers=admin_headers)
    assert res.status_code == 404

    # Requesting with old dataset header returns 404
    res = client.get(
        "/api/v1/cases",
        headers={**admin_headers, "X-Dataset-Id": dataset_a_id},
    )
    assert res.status_code == 404

    # 4f. Same-ID Replacement: Active CASE_0001 is Dataset B's case only
    res = client.get("/api/v1/cases", headers=admin_headers)
    active_cases = res.json()["items"]
    case_numbers = [c["case_number"] for c in active_cases]
    assert "GF/2026/0002" in case_numbers
    assert "BL/2024/0001" not in case_numbers
