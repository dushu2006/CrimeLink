"""Safe serverless bootstrap for the repository-owned sample corpus.

This is deliberately separate from ``python run.py`` and the local demo
bootstrap. Cloud deployments import the checked-in corpus using the same generic
pipeline as user datasets; no seed-only canonical rows or cloud-only mock data
are introduced here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select, text

from app.config import Settings, get_settings
from app.datasets import registry
from app.datasets.pipeline import ImportOptions, run_import
from app.db.models import Dataset
from app.db.session import async_session
from app.logging import get_logger

log = get_logger("crimelink.datasets.builtin")

CORPUS_NAME = "CrimeLink Synthetic Corpus"
CORPUS_VERSION = "2.1"
LEGACY_BUILTIN_IDS = {"demo-dataset-001", "demo-dataset-002"}
# Stable PostgreSQL advisory-lock key, shared by all workers and instances.
_BOOTSTRAP_LOCK_ID = 0x4352494D454C494E


async def _active_dataset(session) -> Dataset | None:
    return await registry.active_dataset(session)


async def _import_if_needed(settings: Settings) -> dict[str, Any]:
    root = Path(settings.synthetic_data_root).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"The bundled corpus directory is unavailable: {root}")

    async with async_session() as session:
        active = await _active_dataset(session)
        if active is not None:
            if active.name == CORPUS_NAME and active.version == CORPUS_VERSION:
                log.info("builtin_corpus.already_active", dataset_id=active.id)
                return {"status": "already_active", "dataset_id": active.id}
            managed_builtin = active.source_kind == "builtin" and active.name == CORPUS_NAME
            if active.id not in LEGACY_BUILTIN_IDS and not managed_builtin:
                log.info(
                    "builtin_corpus.preserved_existing_dataset",
                    dataset_id=active.id,
                    dataset_name=active.name,
                )
                return {
                    "status": "preserved_existing_dataset",
                    "dataset_id": active.id,
                    "dataset_name": active.name,
                }

        report = await run_import(
            session,
            [root],
            ImportOptions(
                name=CORPUS_NAME,
                version=CORPUS_VERSION,
                source_kind="builtin",
                origin_note=(
                    "Checked-in CrimeLink synthetic corpus; imported on serverless "
                    "startup through the standard dataset pipeline."
                ),
                activate=True,
                build_graph=True,
                ingest_documents=True,
                copy_inputs=False,
            ),
        )
        if report.status != "READY":
            raise RuntimeError(
                f"Bundled corpus import did not reach READY: "
                f"{report.error or report.status}"
            )
        log.info(
            "builtin_corpus.imported",
            dataset_id=report.dataset_id,
            cases=report.cases_created,
            documents=report.documents_created,
            graph=report.graph,
        )
        return {
            "status": "imported",
            "dataset_id": report.dataset_id,
            "cases": report.cases_created,
            "documents": report.documents_created,
        }


async def bootstrap_builtin_corpus(settings: Settings | None = None) -> dict[str, Any]:
    """Make the built-in corpus available on serverless deployments, safely.

    A PostgreSQL session-level advisory lock serializes cold starts across
    function instances. Existing operator-imported active datasets are never
    replaced; only an empty database or the repository's known legacy demo seed
    is eligible for this one-time migration.
    """
    settings = settings or get_settings()
    if settings.effective_relational_backend != "postgres":
        return await _import_if_needed(settings)

    from app.db.session import get_async_engine

    engine = get_async_engine()
    async with engine.connect() as lock_connection:
        await lock_connection.execute(
            text("SELECT pg_advisory_lock(:lock_id)"),
            {"lock_id": _BOOTSTRAP_LOCK_ID},
        )
        await lock_connection.commit()
        try:
            return await _import_if_needed(settings)
        finally:
            await lock_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": _BOOTSTRAP_LOCK_ID},
            )
            await lock_connection.commit()
