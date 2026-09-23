"""A failed graph build must report its own error, never a greenlet artefact.

The production symptom these tests pin down: rebuilding the graph of an
existing dataset reached **19%** and the Administration console showed

    MissingGreenlet: greenlet_spawn has not been called; can't call await_only()
    here. Was IO attempted in an unexpected place?

with no hint of the actual fault, and the dataset row left sitting on
``BUILDING_GRAPH``.

Mechanism: ``pipeline.rebuild_graph`` rolled the session back and then read
``dataset.id`` to re-fetch the row.  ``Session.rollback()`` expires every ORM
instance in the session (``expire_on_commit=False`` only covers ``commit()``),
so that read is not an attribute read at all -- it triggers a refresh SELECT.
The refresh runs from ordinary async code, i.e. *outside* the greenlet context
SQLAlchemy establishes only for its own awaited calls, so the async DBAPI shim
raises ``MissingGreenlet``.  The secondary error replaced the real one, which
is why the graph database's own message never reached the operator.

The second half of the contract is the happy path: the same job, run against a
working graph store, must cross the batch boundary (the old failure point) and
finish READY.
"""

from __future__ import annotations

import asyncio

import pytest

from app.datasets import registry
from app.datasets.pipeline import ImportOptions
from app.db.models import Dataset, DatasetEntity, DatasetRelationship
from app.db.session import async_session
from app.errors import ServiceUnavailableError
from app.services import dataset_jobs

#: The percentage the operator saw when the graph build died. The entity
#: projection reports 20% of its own scale, which the rebuild job rescales to
#: 19% -- the last honest number before the failure.
REPORTED_FAILURE_PCT = 19


async def _make_dataset(name: str, *, entities: int, relationships: int) -> str:
    """A dataset with canonical rows but no ``Case`` rows.

    Mirrors the production dataset: the entity projection is the first write
    that reaches the graph store.
    """
    async with async_session() as session:
        dataset = await registry.create_dataset(
            session, name=name, version="1", source_kind="upload"
        )
        dataset_id = dataset.id
        session.add_all(
            [
                DatasetEntity(
                    dataset_id=dataset_id,
                    canonical_id=f"PERSON:P{i:05d}",
                    entity_type="PERSON",
                    name=f"Person {i}",
                    display_name=f"Person {i}",
                    normalized_value=f"PERSON{i}",
                    attributes={},
                    provenance={"file": "people.csv", "row": i + 1},
                )
                for i in range(entities)
            ]
        )
        session.add_all(
            [
                DatasetRelationship(
                    dataset_id=dataset_id,
                    source_canonical_id=f"PERSON:P{i:05d}",
                    target_canonical_id=f"PERSON:P{(i + 1) % entities:05d}",
                    rel_type="CALLED",
                    confidence=1.0,
                    edge_key=f"edge-{i}",
                    attributes={},
                    provenance={"file": "calls.csv", "row": i + 1},
                    case_ids=[],
                )
                for i in range(relationships)
            ]
        )
        await session.commit()
    return dataset_id


async def _run_rebuild_job(dataset_id: str) -> dict:
    """Start a rebuild exactly the way ``POST /datasets/{id}/graph/rebuild`` does."""
    job = await dataset_jobs.start_job(
        kind="build_graph",
        dataset_id=dataset_id,
        requested_by=None,
        work=lambda reporter: dataset_jobs.run_graph_rebuild(dataset_id, reporter),
    )
    deadline = asyncio.get_event_loop().time() + 30.0
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        body = await dataset_jobs.get_job(job["id"])
        if body and body["terminal"]:
            return body
    raise AssertionError(f"rebuild job {job['id']} never reached a terminal state")


@pytest.fixture()
def broken_graph_store(container, monkeypatch):
    """A graph store that is unreachable, as the Neo4j adapter behaves when its
    endpoint is wrong: the purge calls swallow the failure, and the first
    *entity* write is where it surfaces."""
    store = container.graph_store

    def unavailable(*_args, **_kwargs):
        raise ServiceUnavailableError(
            "The graph database (Neo4j) is unreachable. (ClientError)"
        )

    monkeypatch.setattr(store, "upsert_nodes", unavailable)
    monkeypatch.setattr(store, "purge_dataset", lambda *a, **k: 0)
    monkeypatch.setattr(store, "purge_other_datasets", lambda *a, **k: 0)
    return store


async def test_rebuild_reports_the_real_graph_error(container, broken_graph_store):
    """The operator must see why the build failed -- not MissingGreenlet."""
    dataset_id = await _make_dataset("broken store", entities=3, relationships=1)

    body = await _run_rebuild_job(dataset_id)

    assert body["status"] == "FAILED"
    assert "MissingGreenlet" not in (body["error"] or "")
    assert "ServiceUnavailableError" in (body["error"] or "")
    assert "unreachable" in (body["error"] or "")
    # The failure happened where production saw it: during the entity
    # projection, after "Projecting entities" and before relationships.
    assert body["progress_pct"] == REPORTED_FAILURE_PCT

    async with async_session() as session:
        dataset = await session.get(Dataset, dataset_id)
        assert dataset.status == "FAILED"
        assert "MissingGreenlet" not in (dataset.error or "")
        assert dataset.error == body["error"]


async def test_rebuild_still_reports_the_real_error_when_recording_it_fails(
    container, broken_graph_store, monkeypatch
):
    """Recording the failure is best effort; it may never replace the error."""
    dataset_id = await _make_dataset("angry registry", entities=2, relationships=1)

    original_set_stage = registry.set_stage

    async def flaky_set_stage(session, dataset, stage, **kwargs):
        if stage == "FAILED":
            raise RuntimeError("the relational database refused the write too")
        return await original_set_stage(session, dataset, stage, **kwargs)

    monkeypatch.setattr(registry, "set_stage", flaky_set_stage)

    body = await _run_rebuild_job(dataset_id)

    assert body["status"] == "FAILED"
    assert "ServiceUnavailableError" in (body["error"] or "")
    assert "MissingGreenlet" not in (body["error"] or "")
    assert "relational database refused" not in (body["error"] or "")


async def test_rebuild_crosses_the_batch_boundary_and_reaches_ready(container):
    """A healthy store: the same job goes past 19% and finishes READY.

    The dataset is deliberately larger than the projection's 1000-row batch, so
    the run exercises the intermediate batch flush -- the code the production
    build was inside when it died.
    """
    entities = 1200
    dataset_id = await _make_dataset(
        "batch boundary", entities=entities, relationships=entities
    )

    body = await _run_rebuild_job(dataset_id)

    assert body["status"] == "SUCCEEDED", body["error"]
    assert body["progress_pct"] == 100
    assert body["result"]["nodes_written"] == entities
    assert body["result"]["edges_written"] == entities

    async with async_session() as session:
        dataset = await session.get(Dataset, dataset_id)
        assert dataset.status == "READY"
        assert dataset.graph_built_at is not None
        assert dataset.error is None

    # Every projected node is really in the store, which is what makes the
    # INDEXING/READY steps that follow truthful.
    store = container.graph_store
    for canonical_id in (f"PERSON:P0{i:04d}" for i in (0, 500, 999, 1199)):
        assert store.get_node(f"ds:{dataset_id}:{canonical_id}") is not None


async def test_rebuild_graph_import_path_is_unchanged(container, tmp_path):
    """The import path still builds the graph as part of the import."""
    from app.datasets.pipeline import run_import

    source = tmp_path / "corpus"
    source.mkdir()
    (source / "cases.csv").write_text(
        "case_id,case_number,title\nC1,IMP/2026/1,Imported case\n", encoding="utf-8"
    )
    (source / "people.csv").write_text(
        "person_id,full_name,phone_number\nP1,Imported Person,9811111111\n",
        encoding="utf-8",
    )

    async with async_session() as session:
        report = await run_import(
            session,
            [source],
            ImportOptions(
                name="import path",
                copy_inputs=True,
                activate=True,
                build_graph=True,
                ingest_documents=False,
            ),
        )

    assert report.error is None, report.error
    assert report.status == "READY"
    assert report.graph["entities_considered"] >= 2
    async with async_session() as session:
        dataset = await session.get(Dataset, report.dataset_id)
        assert dataset.status == "READY"
        assert dataset.graph_built_at is not None
