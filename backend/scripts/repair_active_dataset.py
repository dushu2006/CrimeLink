#!/usr/bin/env python3
"""Metadata-only dataset repair for an already-deployed database.

    python scripts/repair_active_dataset.py            # report + repair
    python scripts/repair_active_dataset.py --dry-run  # report only

This is the operator twin of the startup self-repair in
``app.datasets.repair``: it runs the exact same routine against whatever
database the environment selects (the Vercel production PostgreSQL when the
matching ``CRIMELINK_POSTGRES_DSN``/``..._DSN_SYNC`` are exported, a local
PostgreSQL/SQLite otherwise) and prints what it found and did.

What it will ever change — registry rows only:

* no active dataset, but a READY registration row exists  → that row is
  activated (the documented demo corpora are preferred);
* no registration rows at all, but investigative rows carry a ``dataset_id``
  → a metadata-only ``datasets`` row is created for that id and activated;
* the three documented demo accounts are missing          → they are created
  through the normal auth mechanism (same ids/credentials as the seed).

It never creates, edits or deletes cases, documents, entities, relationships,
findings or audit rows, and it never touches a dataset whose status is not
READY.  Exit code is 0 when an active dataset exists afterwards (or already
existed), 1 otherwise.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings
from app.db.session import async_session


async def main(*, dry_run: bool) -> int:
    settings = get_settings()
    print(f"database : {settings.postgres_dsn_sync.split('@')[-1]}")
    print(f"profile  : {settings.profile}")
    print(f"mode     : {'DRY RUN (no writes)' if dry_run else 'repair'}")
    print("-" * 60)

    from app.datasets import registry
    from app.datasets.repair import ensure_demo_users, repair_active_dataset

    async with async_session() as session:
        if dry_run:
            active = await registry.active_dataset(session)
            datasets = await registry.list_datasets(session, limit=200)
            if active is not None:
                print(f"active dataset : {active.id} ({active.name}) — nothing to do")
                return 0
            print(f"active dataset : NONE ({len(datasets)} registration rows found)")
            for ds in datasets:
                print(f"  - {ds.id}  status={ds.status}  active={ds.is_active}")
            report = await repair_active_dataset(session)
            print(f"would repair   : {report}")
            await session.rollback()
            return 0 if report.get("status") != "empty" else 1

        report = await repair_active_dataset(session)
        created = await ensure_demo_users(session)
        await session.commit()

    print(f"repair         : {report}")
    print(f"demo users     : created={created or 'none missing'}")

    async with async_session() as session:
        active = await registry.active_dataset(session)
    if active is None:
        print("result         : STILL NO ACTIVE DATASET — see the report above;")
        print("                 an empty database is a seeding decision, not a repair:")
        print("                 CRIMELINK_DEMO_MODE=true python -m app.db.bootstrap")
        return 1
    print(f"result         : active dataset {active.id} ({active.name})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(dry_run="--dry-run" in sys.argv)))
