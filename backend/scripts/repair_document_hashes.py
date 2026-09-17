"""Re-derive ``content_hash`` / ``size_bytes`` from the stored object.

Chain of custody says the **stored bytes** are the truth and the recorded hash
is derived metadata about them.  When the two disagree the record is wrong,
not the file — and ``GET /api/v1/evidence/{doc_id}/verify`` exists to surface
exactly that disagreement before it reaches a courtroom.

The v2 demo seeder used to generate each PDF twice (once to store, once to
hash).  ReportLab stamps a creation timestamp into every PDF, so the second
generation produced different bytes and every PDF's recorded SHA-256 was
wrong while its CSV siblings — which are byte-deterministic — were fine.  The
seeder is fixed; this script repairs records written before the fix.

It is deliberately narrow and non-destructive:

* reads every ``CaseDocument`` (optionally one dataset),
* reads the stored object,
* rewrites ``content_hash`` / ``size_bytes`` **only** when they differ,
* never touches the object store, never deletes a row, never recreates a case.

Usage::

    python -m scripts.repair_document_hashes              # dry run
    python -m scripts.repair_document_hashes --apply      # write the corrections
    python -m scripts.repair_document_hashes --apply --dataset-id demo-dataset-002
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.container import Container, get_container  # noqa: E402
from app.db.models import CaseDocument  # noqa: E402
from app.db.session import async_session, dispose_engines  # noqa: E402
from app.domain.provenance import content_hash  # noqa: E402


async def repair(*, apply: bool, dataset_id: str | None) -> int:
    container: Container = get_container()
    bucket = container.settings.minio_bucket_documents

    async with async_session() as session:
        stmt = select(CaseDocument)
        if dataset_id:
            stmt = stmt.where(CaseDocument.dataset_id == dataset_id)
        documents = list((await session.execute(stmt)).scalars())

        checked = mismatched = unreadable = fixed = 0
        for doc in documents:
            checked += 1
            if not doc.storage_key:
                unreadable += 1
                continue
            try:
                raw = container.object_store.get(bucket, doc.storage_key)
            except Exception as exc:  # noqa: BLE001 - report and move on
                unreadable += 1
                print(f"  UNREADABLE {doc.id} {doc.storage_key}: {exc}")
                continue
            actual = content_hash(raw)
            if actual == doc.content_hash and len(raw) == (doc.size_bytes or 0):
                continue
            mismatched += 1
            print(
                f"  MISMATCH   {doc.id} {doc.filename}\n"
                f"             recorded={doc.content_hash}\n"
                f"             computed={actual} size={len(raw)}"
            )
            if apply:
                doc.content_hash = actual
                doc.size_bytes = len(raw)
                fixed += 1
        if apply:
            await session.commit()

        print(
            f"\nchecked={checked} mismatched={mismatched} unreadable={unreadable} "
            f"{'repaired' if apply else 'would repair'}={fixed if apply else mismatched}"
        )
        if not apply and mismatched:
            print("Dry run only — re-run with --apply to write the corrections.")
        return 0 if (mismatched == 0 or apply) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the corrections")
    parser.add_argument("--dataset-id", default=None, help="restrict to one dataset")
    args = parser.parse_args()
    try:
        return asyncio.run(repair(apply=args.apply, dataset_id=args.dataset_id))
    finally:
        try:
            asyncio.run(dispose_engines())
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
