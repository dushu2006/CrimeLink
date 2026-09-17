"""Repair script: register the seeded **source documents** as first-class records.

`persist()` builds a `CaseDocument` row for every source file, but
`doc_id_for` used to map ``S-0000`` onto the same id as ``E-0000``
(``doc-d2-0000``).  The insert was then skipped as a duplicate, so the 40
source PDFs stayed in the object store and in `dataset_files` but had no
document row — and every graph node or edge that cited one resolved to an
unrelated evidence file instead.

This inserts the missing rows with the corrected `doc-s2-` namespace.  It is
additive: nothing existing is modified or removed.

    python -m scripts.repair_source_documents            # dry run
    python -m scripts.repair_source_documents --apply
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.db.models import CaseDocument, DatasetFile
from app.db.session import async_session
from app.domain.enums import (
    InformationClassification,
    IngestionStatus,
    SourceConfidence,
)
from app.domain.provenance import content_hash
from scripts.seed_demo_v2 import (
    DEMO_DATASET_ID,
    build_dataset,
    doc_id_for,
    gen_source_bytes,
)


async def run(apply_changes: bool) -> int:
    dataset = build_dataset()
    created = skipped = missing_file = 0

    async with async_session() as session:
        for src in dataset["sources"]:
            did = doc_id_for(src["source_id"])
            if (
                await session.execute(
                    select(CaseDocument.id).where(CaseDocument.id == did)
                )
            ).scalar_one_or_none():
                skipped += 1
                continue

            file_row = (
                await session.execute(
                    select(DatasetFile).where(
                        DatasetFile.dataset_id == DEMO_DATASET_ID,
                        DatasetFile.relative_path == src["storage_key"],
                    )
                )
            ).scalars().first()

            size = file_row.size_bytes if file_row else 0
            digest = file_row.sha256 if file_row else ""
            if not size:
                missing_file += 1
                continue

            if apply_changes:
                session.add(
                    CaseDocument(
                        id=did,
                        case_id=src["case_id"],
                        dataset_id=DEMO_DATASET_ID,
                        document_type=src["doc_type"],
                        filename=src["filename"],
                        storage_key=src["storage_key"],
                        content_hash=digest,
                        size_bytes=size,
                        mime_type=src["mime_type"],
                        ingestion_status=IngestionStatus.COMPLETE,
                        ingestion_stage=6,
                        source_confidence=SourceConfidence.VERIFIED,
                        classification=InformationClassification.CONFIDENTIAL,
                        quarantined=False,
                    )
                )
            created += 1

        # `dataset_files.doc_id` was written with the collided id too, so the
        # 40 source rows point at an *evidence* document.  Re-point them at the
        # source document that actually owns the file.
        relinked = 0
        for src in dataset["sources"]:
            did = doc_id_for(src["source_id"])
            row = (
                await session.execute(
                    select(DatasetFile).where(
                        DatasetFile.dataset_id == DEMO_DATASET_ID,
                        DatasetFile.relative_path == src["storage_key"],
                    )
                )
            ).scalars().first()
            if row is not None and row.doc_id != did:
                if apply_changes:
                    row.doc_id = did
                relinked += 1

        if apply_changes:
            await session.commit()

    print(
        f"dataset_files relinked to their own source document: {relinked}"
    )
    print(
        f"source documents: {created} to register · {skipped} already present · "
        f"{missing_file} with no stored file ({'APPLIED' if apply_changes else 'dry run'})"
    )
    return 0 if missing_file == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the rows")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.apply)))


if __name__ == "__main__":
    main()
