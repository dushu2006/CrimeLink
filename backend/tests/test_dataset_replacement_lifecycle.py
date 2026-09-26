"""Dataset replacement: the lifecycle, the ownership boundary, and the rollback.

CrimeLink runs on **one active dataset**.  Importing a new corpus therefore has
to end with the new dataset active and everything the replaced dataset owned
*actually gone* — Postgres rows, graph projection, uploaded bytes, staged
copies — while identity, audit history and system configuration are untouched.
And a new dataset that fails halfway, or that turns out not to be usable, must
cost nothing: the dataset the deployment was running on stays active and stays
whole.

The scenarios below are the acceptance criteria for that lifecycle:

1.  replacement ends with exactly one active dataset
2.  every row the replaced dataset owned is gone (scoped by ``dataset_id``)
3.  identity, audit chain and system configuration survive
4.  the replaced dataset's evidence bytes leave the object store
5.  the replaced dataset's workspace copy is removed
6.  the graph holds only the surviving dataset (no mixing)
7.  search and AI retrieval read only the active dataset
8.  a failed import never becomes active and costs the old dataset nothing
9.  an unusable dataset is refused *before* the old one is retired
10. a rolled-back activation destroys no storage
11. a dataset that was never active is not a replacement casualty
12. rows with no dataset (an officer's own work) are never deleted
13. a key both corpora use ends up holding the survivor's bytes
14. storage reclamation is measured, reported and idempotent
15. upload staging is cleaned; operator folders are not
16. the activate endpoint reports what was retired
17. purging a non-active dataset is scoped to its own rows

Every corpus here is written into ``tmp_path`` and imported through the real
pipeline against the isolated container fixture, so nothing in this file can
reach a deployment's data.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.datasets import graph_build, registry, retirement
from app.datasets.pipeline import ImportOptions, run_import
from app.db import models as db_models
from app.db.base import new_uuid
from app.db.models import (
    Case,
    CaseDocument,
    Dataset,
    DatasetEntity,
    DatasetFile,
    DetectedPattern,
    InvestigationSession,
    InvestigatorNote,
    SourceReference,
)
from app.db.session import async_session, get_async_sessionmaker
from app.domain.enums import (
    CaseStatus,
    DocumentType,
    IngestionStatus,
    PatternStatus,
    PatternType,
)
from app.domain.provenance import content_hash

JURISDICTION = "RJ-JAIPUR"  # matches the test users, so nothing is scope-hidden

OLD_FOLDER = "OldCorpus"
NEW_FOLDER = "NewCorpus"
OLD_FILES = {
    "cases.csv": (
        "case_id,case_number,title\n"
        "C1,OLD/2024/1,Old case one\n"
        "C2,OLD/2024/2,Old case two\n"
    ),
    "people.csv": (
        "person_id,full_name,phone_number\n"
        "P1,Old Person One,9811111111\n"
        "P2,Old Person Two,9811111112\n"
    ),
    "notes/old_brief.txt": (
        "Old Person One was seen near PS-01 on 14/08/2024.\n"
        "Old Person Two made the threatening call.\n"
    ),
}
NEW_FILES = {
    "cases.csv": "case_id,case_number,title\nC9,NEW/2026/9,Brand new case\n",
    "people.csv": "person_id,full_name,phone_number\nP9,New Person Nine,9899999999\n",
    "notes/new_brief.txt": "New Person Nine runs the ledger out of PS-02.\n",
}
OLD_DOC_KEY = f"{OLD_FOLDER}/notes/old_brief.txt"
NEW_DOC_KEY = f"{NEW_FOLDER}/notes/new_brief.txt"

_MODELS_BY_TABLE = {
    value.__tablename__: value
    for value in vars(db_models).values()
    if isinstance(value, type) and hasattr(value, "__tablename__")
}


def _models_with(column: str) -> list:
    """Every mapped model that carries ``column`` (dataset_id / case_id)."""
    return [
        model
        for name, model in sorted(_MODELS_BY_TABLE.items())
        if hasattr(model, column)
    ]


# --------------------------------------------------------------------------- #
# Corpus building and state inspection
# --------------------------------------------------------------------------- #


def _write_corpus(root: Path, folder: str, files: dict[str, str]) -> Path:
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    for relative, body in files.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return directory


async def _import(
    root: Path,
    name: str,
    folder: str,
    files: dict[str, str],
    *,
    activate: bool = True,
    build_graph: bool = True,
):
    """Run the real pipeline over a freshly written corpus folder."""
    directory = _write_corpus(root, folder, files)
    async with async_session() as session:
        return await run_import(
            session,
            [directory],
            ImportOptions(
                name=name,
                copy_inputs=True,
                activate=activate,
                build_graph=build_graph,
                jurisdiction_id=JURISDICTION,
            ),
        )


async def _count(session, model, *conditions) -> int:
    statement = select(func.count()).select_from(model)
    for condition in conditions:
        statement = statement.where(condition)
    return int((await session.execute(statement)).scalar() or 0)


async def _dataset_state(dataset_id: str) -> dict | None:
    """Counted state of one dataset, read in a fresh session."""
    async with async_session() as session:
        dataset = await session.get(Dataset, dataset_id)
        if dataset is None:
            return None
        case_ids = list(
            (
                await session.execute(
                    select(Case.id).where(Case.dataset_id == dataset_id)
                )
            ).scalars()
        )
        return {
            "dataset_id": dataset_id,
            "name": dataset.name,
            "is_active": bool(dataset.is_active),
            "status": str(dataset.status),
            "root_path": dataset.root_path,
            "case_ids": case_ids,
            "cases": len(case_ids),
            "documents": await _count(session, CaseDocument, CaseDocument.dataset_id == dataset_id),
            "entities": await _count(session, DatasetEntity, DatasetEntity.dataset_id == dataset_id),
            "files": await _count(session, DatasetFile, DatasetFile.dataset_id == dataset_id),
            "references": await _count(session, SourceReference, SourceReference.dataset_id == dataset_id),
        }


async def _preserved_counts() -> dict[str, int]:
    """Row counts of every table a replacement must never touch."""
    async with async_session() as session:
        return {
            table: await _count(session, _MODELS_BY_TABLE[table])
            for table in retirement.PRESERVED_TABLES
            if table in _MODELS_BY_TABLE
        }


async def _active_dataset_ids() -> list[str]:
    async with async_session() as session:
        return list(
            (
                await session.execute(
                    select(Dataset.id).where(Dataset.is_active.is_(True))
                )
            ).scalars()
        )


def _graph_nodes(container) -> list[dict]:
    return container.graph_store.list_nodes(limit=500)["items"]


@pytest.fixture()
def bucket(container) -> str:
    return container.settings.minio_bucket_documents


@pytest.fixture()
async def swap(tmp_path, container, store, bucket):
    """Import one corpus, then replace it with a completely different one."""
    old_report = await _import(tmp_path / "old", "Old corpus", OLD_FOLDER, OLD_FILES)
    assert old_report.error is None, old_report.error
    before = await _dataset_state(old_report.dataset_id)
    preserved_before = await _preserved_counts()

    new_report = await _import(tmp_path / "new", "New corpus", NEW_FOLDER, NEW_FILES)
    assert new_report.error is None, new_report.error
    after = await _dataset_state(new_report.dataset_id)

    return {
        "old_report": old_report,
        "new_report": new_report,
        "old": before,
        "new": after,
        "preserved_before": preserved_before,
        "preserved_after": await _preserved_counts(),
    }


# --------------------------------------------------------------------------- #
# 1-3. The end state, and what survives it
# --------------------------------------------------------------------------- #


async def test_replacement_ends_with_exactly_one_active_dataset(swap):
    """One active dataset; the replaced one keeps only its registration row."""
    assert await _active_dataset_ids() == [swap["new"]["dataset_id"]]

    retired = await _dataset_state(swap["old"]["dataset_id"])
    assert retired is not None, "the registration row is the audit record of the import"
    assert retired["is_active"] is False
    assert retired["cases"] == 0
    assert retired["documents"] == 0
    assert retired["entities"] == 0
    assert retired["files"] == 0
    assert retired["references"] == 0


async def test_every_row_the_replaced_dataset_owned_is_gone(swap):
    """Scoped deletion: every corpus-owned table is empty for the retired id.

    Counted per model rather than asserted from a hand-written list, so a table
    added later is covered by this test the moment it carries ``dataset_id`` or
    ``case_id`` — unless it is on one of the two documented exclusion lists
    (identity/audit/config, and the record of human activity).
    """
    old = swap["old"]
    excluded = set(retirement.PRESERVED_TABLES) | set(retirement.RETAINED_AFTER_RETIREMENT)
    checked: list[str] = []
    async with async_session() as session:
        for model in _models_with("dataset_id"):
            if model.__tablename__ in excluded:
                continue
            count = await _count(session, model, model.dataset_id == old["dataset_id"])
            assert count == 0, f"{model.__tablename__} still holds {count} retired rows"
            checked.append(model.__tablename__)
        for model in _models_with("case_id"):
            if model.__tablename__ in excluded:
                continue
            count = await _count(session, model, model.case_id.in_(old["case_ids"]))
            assert count == 0, f"{model.__tablename__} still holds {count} retired case rows"
            checked.append(model.__tablename__)
    # The ownership map is not accidentally empty: the corpus really did own
    # rows in all of these tables, and all of them are now gone.
    assert len(set(checked)) >= 10, sorted(set(checked))
    for expected in ("cases", "case_documents", "source_references", "dataset_files",
                     "dataset_entities", "dataset_relationships"):
        assert expected in checked, expected


async def test_identity_audit_and_system_configuration_survive_replacement(users, swap):
    """Users, tokens, the audit chain, grants and config are not dataset data."""
    before, after = swap["preserved_before"], swap["preserved_after"]
    assert set(before) == set(after)
    for table, count in before.items():
        # Append-only tables may grow during a replacement; none may shrink.
        assert after[table] >= count, f"{table} lost rows: {count} -> {after[table]}"
    assert after["users"] == before["users"], "a replacement must never touch users"
    assert before["users"] > 0, "the fixture should have users to protect"

    # Not just the count: the actual accounts, with their credentials and roles.
    async with async_session() as session:
        for badge, user in users.items():
            row = await session.get(db_models.User, user.id)
            assert row is not None, f"{badge} was deleted by a dataset replacement"
            assert row.hashed_password == user.hashed_password
            assert str(row.role) == str(user.role)


async def test_the_record_of_human_activity_survives_a_replacement(
    users, tmp_path, container
):
    """Threads, notes and the registration row are history, not corpus data.

    Replacing the haystack does not un-happen an investigation, and the API's
    cross-dataset guarantee — resuming a thread that belongs to a retired
    dataset is refused with an explanatory 422 rather than a bare 404 — needs
    the thread row to still exist.
    """
    old_report = await _import(tmp_path / "old", "Old corpus", OLD_FOLDER, OLD_FILES)
    assert old_report.error is None, old_report.error
    old = await _dataset_state(old_report.dataset_id)
    thread_id, note_id = new_uuid(), new_uuid()
    async with async_session() as session:
        session.add(
            InvestigationSession(
                id=thread_id,
                dataset_id=old_report.dataset_id,
                case_id=old["case_ids"][0],
                scope="case",
                title="Thread opened before the swap",
                state={"objective": "who is connected to whom"},
            )
        )
        session.add(
            InvestigatorNote(
                id=note_id,
                case_id=old["case_ids"][0],
                author_id=users["INV-0001"].id,
                text="Written by an investigator before the swap.",
            )
        )
        await session.commit()

    new_report = await _import(tmp_path / "new", "New corpus", NEW_FOLDER, NEW_FILES)
    assert new_report.error is None, new_report.error

    async with async_session() as session:
        assert await session.get(InvestigationSession, thread_id) is not None
        assert await session.get(InvestigatorNote, note_id) is not None
        assert await session.get(Dataset, old_report.dataset_id) is not None
        # The corpus the thread was pinned to is genuinely gone.
        assert await _count(session, Case, Case.dataset_id == old_report.dataset_id) == 0


# --------------------------------------------------------------------------- #
# 4-7. Storage and every read path
# --------------------------------------------------------------------------- #


async def test_the_replaced_datasets_evidence_bytes_leave_the_object_store(
    swap, store, bucket
):
    """Reclaimed, not merely hidden: the retired keys are gone from the store."""
    assert store.exists(bucket, OLD_DOC_KEY) is False
    assert store.exists(bucket, NEW_DOC_KEY) is True
    assert OLD_DOC_KEY not in store.list_keys(bucket)


async def test_the_replaced_datasets_workspace_copy_is_removed(swap):
    """The staged copy of the retired corpus is deleted; the survivor's stays."""
    old_root = Path(swap["old"]["root_path"])
    new_root = Path(swap["new"]["root_path"])
    assert old_root.exists() is False, old_root
    assert new_root.is_dir() is True, new_root
    assert (new_root / NEW_DOC_KEY).is_file()


async def test_the_graph_holds_only_the_surviving_dataset(swap, container):
    """No mixing: every projected entity node belongs to the active dataset."""
    items = _graph_nodes(container)
    names = {item.get("name") for item in items}
    assert "New Person Nine" in names, sorted(n for n in names if n)
    assert "Old Person One" not in names, sorted(names)
    assert "Old Person Two" not in names, sorted(names)

    new_id = swap["new"]["dataset_id"]
    old_id = swap["old"]["dataset_id"]
    for item in items:
        node_id = str(item.get("id"))
        assert old_id not in node_id, node_id
        if node_id.startswith("ds:"):
            assert node_id.startswith(f"ds:{new_id}:"), node_id


async def test_search_and_ai_retrieval_read_only_the_active_dataset(
    client, admin_headers, swap, container
):
    """Search and AI retrieval read the same projection, so both follow the swap."""
    stale = client.get(
        "/api/v1/search?q=Old Person One&limit=10", headers=admin_headers
    ).json()
    stale_names = [item.get("name") for item in (stale.get("items") or [])]
    assert "Old Person One" not in stale_names, stale_names

    fresh = client.get(
        "/api/v1/search?q=New Person Nine&limit=10", headers=admin_headers
    ).json()
    fresh_names = [item.get("name") for item in (fresh.get("items") or [])]
    assert "New Person Nine" in fresh_names, fresh_names

    # The AI gateway retrieves from this store; there is no second index.
    assert container.graph_store.search("Old Person One") == []
    assert any(
        node.name == "New Person Nine"
        for node in container.graph_store.search("New Person Nine")
    )


# --------------------------------------------------------------------------- #
# 8-10. Failure, refusal and rollback
# --------------------------------------------------------------------------- #


async def test_a_failed_import_never_becomes_active_and_costs_the_old_dataset_nothing(
    tmp_path, container, store, bucket, monkeypatch
):
    """stage → parse → normalize → persist → verify → activate → remove old.

    The failure below happens before "activate", so "remove old" never runs.
    """
    old_report = await _import(tmp_path / "old", "Old corpus", OLD_FOLDER, OLD_FILES)
    assert old_report.error is None, old_report.error
    before = await _dataset_state(old_report.dataset_id)

    real_projection = graph_build.project_dataset

    async def explode_on_the_new_dataset(session, dataset, *args, **kwargs):
        if dataset.name == "New corpus":
            raise RuntimeError("graph backend exploded")
        return await real_projection(session, dataset, *args, **kwargs)

    monkeypatch.setattr(graph_build, "project_dataset", explode_on_the_new_dataset)
    failed = await _import(tmp_path / "new", "New corpus", NEW_FOLDER, NEW_FILES)

    assert failed.error is not None
    assert "graph backend exploded" in failed.error
    assert failed.status == "FAILED"

    after = await _dataset_state(old_report.dataset_id)
    assert after is not None, "the active dataset was removed by a failed import"
    assert after["is_active"] is True
    assert after["cases"] == before["cases"]
    assert after["documents"] == before["documents"]
    assert after["entities"] == before["entities"]
    assert store.exists(bucket, OLD_DOC_KEY) is True
    assert Path(after["root_path"]).is_dir() is True
    # The exclusive projection the failed import evicted was put back.
    assert "Old Person One" in {item.get("name") for item in _graph_nodes(container)}

    failed_state = await _dataset_state(failed.dataset_id)
    assert failed_state is not None and failed_state["is_active"] is False
    assert failed_state["status"] == "FAILED"


async def test_an_unusable_dataset_is_refused_before_the_old_one_is_retired(
    tmp_path, container, store, bucket, monkeypatch
):
    """Verification is a gate, not a formality: nothing usable, nothing replaced."""
    old_report = await _import(tmp_path / "old", "Old corpus", OLD_FOLDER, OLD_FILES)
    assert old_report.error is None, old_report.error
    before = await _dataset_state(old_report.dataset_id)

    real_projection = graph_build.project_dataset

    async def project_nothing_for_the_new_dataset(session, dataset, *args, **kwargs):
        if dataset.name == "New corpus":
            return {
                "nodes_written": 0,
                "edges_written": 0,
                "entities_considered": 0,
                "relationships_considered": 0,
                "entities_skipped": 0,
                "relationships_skipped": 0,
                "cases": 0,
                "graph": {},
            }
        return await real_projection(session, dataset, *args, **kwargs)

    monkeypatch.setattr(graph_build, "project_dataset", project_nothing_for_the_new_dataset)
    refused = await _import(tmp_path / "new", "New corpus", NEW_FOLDER, NEW_FILES)

    assert refused.error is not None
    assert "not usable" in refused.error
    assert await _active_dataset_ids() == [old_report.dataset_id]

    after = await _dataset_state(old_report.dataset_id)
    assert after["is_active"] is True
    assert after["cases"] == before["cases"]
    assert after["documents"] == before["documents"]
    assert store.exists(bucket, OLD_DOC_KEY) is True
    assert "Old Person One" in {item.get("name") for item in _graph_nodes(container)}


async def test_a_rolled_back_activation_destroys_no_storage(
    tmp_path, container, store, bucket
):
    """Retirement is transactional; only committed replacements reclaim storage.

    Deleting a row can be rolled back, deleting an object cannot — so the
    irreversible half is queued and runs after the commit.  A rollback must
    leave the queue empty instead of executing it later.
    """
    old_report = await _import(tmp_path / "old", "Old corpus", OLD_FOLDER, OLD_FILES)
    assert old_report.error is None, old_report.error
    candidate = await _import(
        tmp_path / "new", "New corpus", NEW_FOLDER, NEW_FILES, activate=False
    )
    assert candidate.error is None, candidate.error

    session = get_async_sessionmaker()()
    try:
        dataset = await session.get(Dataset, candidate.dataset_id)
        await registry.activate(session, dataset)
        await session.rollback()
        # The queue was dropped with the transaction: nothing to finalize.
        summary = await retirement.finalize_pending(session)
    finally:
        await session.close()

    assert summary["finalized"] is False
    assert summary["objects_deleted"] == 0
    assert await _active_dataset_ids() == [old_report.dataset_id]
    assert store.exists(bucket, OLD_DOC_KEY) is True
    old_state = await _dataset_state(old_report.dataset_id)
    assert old_state["cases"] > 0 and old_state["documents"] > 0


# --------------------------------------------------------------------------- #
# 11-13. Ownership boundaries
# --------------------------------------------------------------------------- #


async def test_a_successful_replacement_cleans_stale_inactive_candidates(
    tmp_path, container, store, bucket
):
    """A successful replacement leaves one operational corpus, not old candidates.

    The limited-capacity deployment deliberately cleans stale datasets left by
    earlier uploads as part of the same dependency-ordered retirement path.
    The registration row may remain for lifecycle history, but its corpus data,
    graph projection, workspace and owned evidence bytes are reclaimed.
    """
    old_report = await _import(tmp_path / "old", "Old corpus", OLD_FOLDER, OLD_FILES)
    assert old_report.error is None, old_report.error
    candidate = await _import(
        tmp_path / "candidate", "Candidate corpus", "CandidateCorpus", NEW_FILES,
        activate=False,
    )
    assert candidate.error is None, candidate.error
    candidate_before = await _dataset_state(candidate.dataset_id)
    assert candidate_before is not None
    assert candidate_before["cases"] > 0

    new_report = await _import(tmp_path / "new", "New corpus", NEW_FOLDER, NEW_FILES)
    assert new_report.error is None, new_report.error

    candidate_after = await _dataset_state(candidate.dataset_id)
    assert candidate_after is not None, "the dataset registration is retained as lifecycle history"
    assert candidate_after["is_active"] is False
    assert candidate_after["cases"] == 0
    assert candidate_after["documents"] == 0
    assert candidate_after["entities"] == 0
    assert candidate_after["files"] == 0
    assert Path(candidate_after["root_path"]).exists() is False
    assert store.exists(bucket, "CandidateCorpus/notes/new_brief.txt") is False

    retired = await _dataset_state(old_report.dataset_id)
    assert retired is not None and retired["is_active"] is False
    assert retired["cases"] == 0 and retired["documents"] == 0
    assert await _active_dataset_ids() == [new_report.dataset_id]


async def test_rows_with_no_dataset_are_never_deleted(tmp_path, container, store, bucket):
    """An officer's hand-created case, document and analysis rows are theirs."""
    hand_case_id = new_uuid()
    hand_doc_id = new_uuid()
    hand_storage_key = f"hand/{uuid.uuid4().hex}.txt"
    async with async_session() as session:
        session.add(
            Case(
                id=hand_case_id,
                case_number=f"HAND/{uuid.uuid4().hex[:6]}",
                title="Typed in by hand",
                jurisdiction_id=JURISDICTION,
                status=CaseStatus.OPEN,
                dataset_id=None,
            )
        )
        await session.flush()
        session.add(
            CaseDocument(
                id=hand_doc_id,
                case_id=hand_case_id,
                dataset_id=None,
                document_type=DocumentType.FIR,
                filename="hand_written_fir.txt",
                storage_key=hand_storage_key,
                content_hash="c" * 64,
                size_bytes=12,
                ingestion_status=IngestionStatus.COMPLETE,
            )
        )
        session.add(
            SourceReference(
                id=new_uuid(),
                doc_id=hand_doc_id,
                case_id=hand_case_id,
                dataset_id=None,
                origin_file="hand/hand_written_fir.txt",
                source_type="txt",
                line_start=1,
                line_end=1,
                excerpt="typed in by hand",
            )
        )
        session.add(
            DetectedPattern(
                id=new_uuid(),
                case_id=hand_case_id,
                pattern_type=PatternType.NETWORK_BRIDGE,
                confidence=0.9,
                entity_keys=["hand-key"],
                evidence_doc_ids=[hand_doc_id],
                explanation="planted by the test",
                details={},
                status=PatternStatus.NEW,
            )
        )
        await session.commit()

    old_report = await _import(tmp_path / "old", "Old corpus", OLD_FOLDER, OLD_FILES)
    assert old_report.error is None, old_report.error
    new_report = await _import(tmp_path / "new", "New corpus", NEW_FOLDER, NEW_FILES)
    assert new_report.error is None, new_report.error

    async with async_session() as session:
        assert await session.get(Case, hand_case_id) is not None
        assert await session.get(CaseDocument, hand_doc_id) is not None
        assert await _count(session, SourceReference, SourceReference.doc_id == hand_doc_id) == 1
        assert await _count(session, DetectedPattern, DetectedPattern.case_id == hand_case_id) == 1


async def test_a_key_both_corpora_use_holds_the_survivors_bytes_afterwards(
    tmp_path, container, store, bucket
):
    """Re-uploading the same folder name must not leave the old bytes behind.

    The object store is write-once and keyed by dataset-relative path, so the
    incoming corpus cannot overwrite the key the *still active* dataset holds.
    The import reports the collision, and once the old dataset is retired the
    survivor's own bytes are written in their place — hash-verified against the
    document row, so evidence can never point at another dataset's content.
    """
    old_body = "OLD BRIEF: the previous corpus wrote this file.\n"
    new_body = "NEW BRIEF: the replacing corpus wrote this file.\n"
    old_report = await _import(
        tmp_path / "old", "Old corpus", "Corpus",
        {
            "cases.csv": OLD_FILES["cases.csv"],
            "people.csv": OLD_FILES["people.csv"],
            "notes/brief.txt": old_body,
        },
    )
    assert old_report.error is None, old_report.error
    new_report = await _import(
        tmp_path / "new", "New corpus", "Corpus",
        {
            "cases.csv": NEW_FILES["cases.csv"],
            "people.csv": NEW_FILES["people.csv"],
            "notes/brief.txt": new_body,
        },
    )
    assert new_report.error is None, new_report.error
    assert any(
        "already holds different bytes" in warning for warning in new_report.warnings
    ), new_report.warnings

    key = "Corpus/notes/brief.txt"
    stored = store.get(bucket, key)
    assert stored == new_body.encode("utf-8")
    async with async_session() as session:
        recorded = (
            await session.execute(
                select(CaseDocument.content_hash).where(
                    CaseDocument.dataset_id == new_report.dataset_id,
                    CaseDocument.storage_key == key,
                )
            )
        ).scalar_one()
    assert content_hash(stored) == recorded
    assert new_report.replacement["objects_restored"] >= 1


# --------------------------------------------------------------------------- #
# 14-17. Accounting, staging and the API surface
# --------------------------------------------------------------------------- #


async def test_storage_reclamation_is_measured_reported_and_idempotent(
    swap, store, bucket
):
    """What a replacement reclaimed is a number in the report, not an assumption."""
    report = swap["new_report"]
    reclaimed = report.replacement
    assert reclaimed["finalized"] is True
    assert reclaimed["active_dataset_id"] == swap["new"]["dataset_id"]
    assert reclaimed["previous_active_dataset_id"] == swap["old"]["dataset_id"]
    assert reclaimed["objects_deleted"] >= 1
    assert reclaimed["bytes_reclaimed"] > 0
    assert reclaimed["workspace_dirs_removed"]
    assert reclaimed["failures"] == []

    # Finalizing again has nothing left to do and destroys nothing.
    async with async_session() as session:
        again = await retirement.finalize_pending(session)
    assert again["finalized"] is False
    assert again["objects_deleted"] == 0
    assert store.exists(bucket, NEW_DOC_KEY) is True


async def test_upload_staging_is_cleaned_and_operator_folders_are_not(tmp_path, container):
    """The browser's temporary upload copy goes; an operator's folder never does."""
    staging_root = (Path(get_settings().data_dir) / "uploads").resolve()
    staged = staging_root / uuid.uuid4().hex
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "cases.csv").write_text(OLD_FILES["cases.csv"], encoding="utf-8")

    operator_folder = tmp_path / "operator_folder"
    operator_folder.mkdir()
    (operator_folder / "notes.txt").write_text("operator's own files", encoding="utf-8")

    removed = retirement.cleanup_upload_staging(
        [staged, operator_folder, registry.datasets_root()]
    )

    assert str(staged) in removed
    assert staged.exists() is False
    assert operator_folder.is_dir() is True
    assert (operator_folder / "notes.txt").is_file()
    assert registry.datasets_root().is_dir() is True


async def test_the_activate_endpoint_reports_what_was_retired(
    client, tmp_path, container, users
):
    """``POST /datasets/{id}/activate`` performs the same retirement, and says so."""
    from tests.conftest import PASSWORD

    old_report = await _import(tmp_path / "old", "Old corpus", OLD_FOLDER, OLD_FILES)
    assert old_report.error is None, old_report.error
    new_report = await _import(
        tmp_path / "new", "New corpus", NEW_FOLDER, NEW_FILES, activate=False
    )
    assert new_report.error is None, new_report.error

    headers = {"Authorization": f"Bearer {_login(client, 'ADM-0001', PASSWORD)}"}
    response = client.post(
        f"/api/v1/datasets/{new_report.dataset_id}/activate", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_active"] is True
    assert body["retired"]["finalized"] is True

    job_id = body.get("job_id")
    if job_id:
        _wait_for_job(client, headers, job_id)

    listing = client.get("/api/v1/datasets", headers=headers).json()["items"]
    assert [item["id"] for item in listing] == [new_report.dataset_id]
    assert all(item["is_active"] for item in listing)

    # Identity survived the replacement: the same badge still authenticates.
    assert _login(client, "ADM-0001", PASSWORD)


async def test_purging_a_non_active_dataset_is_scoped_to_its_own_rows(
    tmp_path, container, store, bucket
):
    """The explicit purge shares one ownership implementation with replacement."""
    active_report = await _import(tmp_path / "old", "Old corpus", OLD_FOLDER, OLD_FILES)
    assert active_report.error is None, active_report.error
    candidate = await _import(
        tmp_path / "candidate", "Candidate corpus", "CandidateCorpus", NEW_FILES,
        activate=False,
    )
    assert candidate.error is None, candidate.error
    before = await _preserved_counts()

    async with async_session() as session:
        removed = await registry.purge_dataset_data(session, candidate.dataset_id)
        await session.commit()

    assert sum(removed.values()) > 0
    state = await _dataset_state(candidate.dataset_id)
    # Purging clears a dataset's data; it does not retire the dataset itself.
    assert state is not None
    assert state["cases"] == 0 and state["documents"] == 0 and state["entities"] == 0
    active_state = await _dataset_state(active_report.dataset_id)
    assert active_state is not None and active_state["cases"] > 0
    assert await _active_dataset_ids() == [active_report.dataset_id]
    after = await _preserved_counts()
    for table, count in before.items():
        assert after[table] >= count, f"{table} lost rows during a purge"


# --------------------------------------------------------------------------- #
# Small HTTP helpers
# --------------------------------------------------------------------------- #


def _login(client, badge: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"badge_number": badge, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _wait_for_job(client, headers, job_id: str) -> dict:
    for _ in range(600):
        job = client.get(f"/api/v1/datasets/jobs/{job_id}", headers=headers).json()
        if job.get("terminal"):
            return job
        time.sleep(0.02)
    raise AssertionError("graph rebuild job did not finish")
