"""The bundled corpus is imported through the same data-driven pipeline as uploads."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from app.datasets.builtin import bootstrap_builtin_corpus
from app.datasets.storage import dataset_object_key
from app.db.models import (
    Case,
    CaseDocument,
    Dataset,
    DatasetEntity,
    DatasetFile,
    DatasetRelationship,
    SourceReference,
)
from app.db.session import async_session

CORPUS_ROOT = Path(__file__).resolve().parents[1] / "CrimeLink_Synthetic_Corpus_v1"


@pytest.mark.asyncio
async def test_checked_in_corpus_is_a_real_dataset_with_verified_sources(container, store):
    settings = container.settings.model_copy(update={"synthetic_data_root": CORPUS_ROOT})
    imported = await bootstrap_builtin_corpus(settings)
    assert imported["status"] == "imported", imported
    dataset_id = imported["dataset_id"]

    async with async_session() as session:
        dataset = await session.get(Dataset, dataset_id)
        assert dataset is not None and dataset.is_active is True
        # The generic importer creates one non-user-facing ALL container so
        # unassigned rows remain reachable; the tracked corpus has 10 cases.
        cases = (
            await session.execute(
                select(Case).where(
                    Case.dataset_id == dataset_id,
                    Case.dataset_case_key != "ALL",
                )
            )
        ).scalars().all()
        assert len(cases) == 10
        assert {case.case_number for case in cases} >= {"FIR/2024/00101", "FIR/2024/00110"}
        assert {case.status.value for case in cases} >= {"OPEN", "CLOSED"}

        entities = (
            await session.execute(
                select(DatasetEntity).where(DatasetEntity.dataset_id == dataset_id)
            )
        ).scalars().all()
        relationships = (
            await session.execute(
                select(DatasetRelationship).where(DatasetRelationship.dataset_id == dataset_id)
            )
        ).scalars().all()
        assert {entity.entity_type for entity in entities} >= {
            "CASE", "PERSON", "PHONE", "ACCOUNT", "VEHICLE"
        }
        assert relationships, "source-linked relationships must reach canonical storage"

        files = (
            await session.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == dataset_id)
            )
        ).scalars().all()
        documents = (
            await session.execute(
                select(CaseDocument).where(CaseDocument.dataset_id == dataset_id)
            )
        ).scalars().all()
        refs = (
            await session.execute(
                select(SourceReference).where(SourceReference.dataset_id == dataset_id)
            )
        ).scalars().all()
        assert len(files) >= 180
        assert len(documents) > 20 and refs
        assert all(doc.storage_key.startswith(f"{dataset_id}/") for doc in documents)

    bucket = container.settings.minio_bucket_documents
    cases_path = "operational/cases.csv"
    cases_key = dataset_object_key(dataset_id, cases_path)
    stored_cases = store.get(bucket, cases_key)
    source_cases = (CORPUS_ROOT / cases_path).read_bytes()
    assert stored_cases == source_cases
    assert hashlib.sha256(stored_cases).hexdigest() == hashlib.sha256(source_cases).hexdigest()

    graph = container.graph_store
    assert graph.stats()["nodes"] > 20
    projected = graph.list_nodes(limit=5000)["items"]
    assert projected
    for item in projected:
        node = graph.get_node(item["id"])
        assert node is not None
        assert node.properties.get("dataset_id") == dataset_id
        if item["label"] != "Case":
            assert item["id"].startswith(f"ds:{dataset_id}:")

    # A warm/cold startup after successful activation is idempotent.
    again = await bootstrap_builtin_corpus(settings)
    assert again["status"] == "already_active"
    assert again["dataset_id"] == dataset_id


@pytest.mark.asyncio
async def test_known_legacy_builtin_is_upgraded_from_the_checked_in_corpus(container):
    settings = container.settings.model_copy(update={"synthetic_data_root": CORPUS_ROOT})
    async with async_session() as session:
        session.add(
            Dataset(
                id="demo-dataset-002",
                name="Legacy built-in demo",
                version="2",
                status="READY",
                is_active=True,
                source_kind="builtin",
                root_path="/legacy/demo",
                stats={},
            )
        )
        await session.commit()

    result = await bootstrap_builtin_corpus(settings)
    assert result["status"] == "imported", result
    assert result["dataset_id"] != "demo-dataset-002"
    async with async_session() as session:
        active = await session.get(Dataset, result["dataset_id"])
        assert active is not None and active.is_active is True
        assert active.name == "CrimeLink Synthetic Corpus"
        assert active.version == "2.1"


@pytest.mark.asyncio
async def test_builtin_bootstrap_preserves_an_operator_imported_active_dataset(container):
    settings = container.settings.model_copy(update={"synthetic_data_root": CORPUS_ROOT})
    async with async_session() as session:
        dataset = Dataset(
            id="operator-managed-dataset",
            name="Operator investigation workspace",
            version="4",
            status="READY",
            is_active=True,
            source_kind="folder",
            root_path="/operator/owned/source",
            stats={"cases": 1},
        )
        session.add(dataset)
        await session.commit()

    result = await bootstrap_builtin_corpus(settings)
    assert result == {
        "status": "preserved_existing_dataset",
        "dataset_id": "operator-managed-dataset",
        "dataset_name": "Operator investigation workspace",
    }
    async with async_session() as session:
        active = await session.get(Dataset, "operator-managed-dataset")
        assert active is not None and active.is_active is True
