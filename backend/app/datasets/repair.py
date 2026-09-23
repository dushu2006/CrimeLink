"""Startup metadata repair — the smallest safe fix for a deployment whose
relational data and whose ``datasets`` bookkeeping have drifted apart.

Why this module exists
----------------------
``GET /api/v1/datasets/active`` resolves through :func:`app.datasets.registry.active_dataset`.
On a serverless deployment nothing outside the function guarantees that the
database it boots against is fully provisioned: migrations may have been
applied while a seed run died half-way, an operator may have restored tables
without the registry row, or a partial import may have left data rows whose
dataset registration never landed.  The API then answers ``{"active": null}``
(every page reports "no dataset is active") even though the investigative
records are present, or — before the startup hardening — the whole instance
failed to boot.

The repair is deliberately *metadata-only* and conservative:

* it never creates, modifies or deletes investigative rows (cases, documents,
  entities, relationships, findings, audit);
* it never fabricates a dataset: a registration row is only recovered when
  data rows that already carry a ``dataset_id`` prove the dataset exists;
* it only promotes a dataset that is fully ``READY`` (a half-finished import
  stays untouched), and only when *no* dataset is currently active;
* it is idempotent — a healthy database pays one indexed SELECT and nothing
  else.

The same routine backs ``scripts/repair_active_dataset.py`` so an operator can
run the repair against any configured database without redeploying.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import new_uuid, utcnow
from app.db.models import (
    Case,
    CaseDocument,
    Dataset,
    DatasetEntity,
    DatasetFile,
    User,
)
from app.logging import get_logger
from app.datasets import registry

log = get_logger("crimelink.datasets.repair")

#: Human-readable names for the built-in demo corpora, used only when a
#: registration row has to be recovered for data that already exists.
KNOWN_DATASET_NAMES = {
    "demo-dataset-002": "CrimeLink Demo Dataset v2",
    "demo-dataset-001": "CrimeLink Demo Dataset",
}

#: Tables probed (in order) for existing investigative rows that carry a
#: ``dataset_id`` when no registration rows exist at all.
_PROBE_MODELS = (DatasetFile, Case, CaseDocument, DatasetEntity)


async def _first_orphan_dataset_id(session: AsyncSession) -> str | None:
    """The dataset_id owned by existing data rows but missing its registry row."""
    known = set(
        (await session.execute(select(Dataset.id))).scalars().all()
    )
    for model in _PROBE_MODELS:
        rows = (
            await session.execute(
                select(model.dataset_id, func.count(model.id))
                .where(model.dataset_id.is_not(None))
                .group_by(model.dataset_id)
                .order_by(func.count(model.id).desc())
                .limit(5)
            )
        ).all()
        for dataset_id, _count in rows:
            if dataset_id and dataset_id not in known:
                return str(dataset_id)
    return None


async def _count_owned_rows(session: AsyncSession, dataset_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for model in (Case, CaseDocument, DatasetFile, DatasetEntity):
        counts[model.__tablename__] = int(
            (
                await session.execute(
                    select(func.count(model.id)).where(model.dataset_id == dataset_id)
                )
            ).scalar()
            or 0
        )
    return counts


async def repair_active_dataset(session: AsyncSession) -> dict[str, Any]:
    """Ensure exactly one READY dataset is active, without touching any data.

    Returns a small report dict (``status`` plus what was repaired) so both
    the startup path and the operator script can log/announce precisely what
    happened.  Statuses:

    ``ok``            an active dataset already existed — nothing was done;
    ``reactivated``   an existing READY registration row was made active;
    ``recovered``     data rows existed without a registration row; a
                      metadata-only row was created for their dataset_id and
                      activated;
    ``empty``         the database holds no dataset and no dataset-owned rows
                      (a fresh deployment — seeding is an operator action).
    """
    active = await registry.active_dataset(session)
    if active is not None:
        return {"status": "ok", "dataset_id": active.id, "name": active.name}

    datasets = await registry.list_datasets(session, limit=200)
    ready = [d for d in datasets if (d.status or "").upper() == "READY"]

    def _sort_key(d: Dataset):
        return (
            d.activated_at or d.created_at or utcnow(),
            d.created_at or utcnow(),
        )

    # The documented demo corpora win over arbitrary imports when several are
    # READY — the same preference the bootstrap verifier applies.
    demo_ids = registry_demo_ids()
    demo_rank = {demo_id: index for index, demo_id in enumerate(demo_ids)}
    candidate: Dataset | None = None
    preferred = [d for d in ready if d.id in demo_rank]
    if preferred:
        candidate = min(preferred, key=lambda d: demo_rank[d.id])
    elif ready:
        candidate = max(ready, key=_sort_key)

    if candidate is not None:
        await registry.set_only_active(session, candidate)
        await session.flush()
        log.warning(
            "datasets.metadata_repaired.reactivated",
            dataset_id=candidate.id,
            name=candidate.name,
            detail="no dataset was active; an existing READY dataset was reactivated (metadata only)",
        )
        return {
            "status": "reactivated",
            "dataset_id": candidate.id,
            "name": candidate.name,
        }

    if datasets:
        # Registration rows exist but none is READY (failed/incomplete
        # imports). Promoting one would expose half-built data: leave the
        # registry honest and report the state instead.
        return {
            "status": "empty",
            "detail": (
                "dataset rows exist but none is READY: "
                + ", ".join(f"{d.id}={d.status}" for d in datasets[:5])
            ),
        }

    orphan_id = await _first_orphan_dataset_id(session)
    if orphan_id is None:
        return {"status": "empty", "detail": "no datasets and no dataset-owned rows"}

    counts = await _count_owned_rows(session, orphan_id)
    recovered = Dataset(
        # Keep whatever id already exists on the data rows — this must never
        # fork the dataset into a second identity.
        id=orphan_id if len(orphan_id) <= 36 else new_uuid(),
        name=KNOWN_DATASET_NAMES.get(orphan_id, f"Dataset {orphan_id[:12]}"),
        version="1",
        status="READY",
        is_active=False,
        source_kind="builtin" if orphan_id in KNOWN_DATASET_NAMES else "recovered",
        root_path="",
        origin_note=(
            "Metadata row recovered at startup: investigative rows referencing "
            f"dataset_id={orphan_id} existed without a dataset registration. "
            "No investigative data was created or modified."
        ),
        stage_detail={
            "stage": "READY",
            "steps": [
                {"stage": "READY", "detail": "registration recovered", "at": utcnow().isoformat()}
            ],
        },
        stats={},
    )
    if len(orphan_id) > 36:
        # The primary key must stay within the column width; the data rows keep
        # their (longer) legacy id, so point the recovered row at the data by
        # rewriting nothing and logging loudly instead. This path is defensive
        # only — every CrimeLink dataset id is a UUID or a short demo id.
        log.error(
            "datasets.metadata_repair.id_too_long",
            dataset_id=orphan_id,
            counts=counts,
        )
        return {
            "status": "empty",
            "detail": f"data rows reference an unusable dataset_id ({orphan_id!r})",
        }
    session.add(recovered)
    await session.flush()
    await registry.set_only_active(session, recovered)
    await session.flush()
    log.warning(
        "datasets.metadata_repaired.recovered",
        dataset_id=recovered.id,
        name=recovered.name,
        counts=counts,
        detail="created the missing registration row for existing investigative data (metadata only)",
    )
    return {
        "status": "recovered",
        "dataset_id": recovered.id,
        "name": recovered.name,
        "counts": counts,
    }


def registry_demo_ids() -> tuple[str, ...]:
    """Demo dataset preference order (imported lazily to avoid cycles)."""
    from app.db.bootstrap import DEMO_DATASET_IDS

    return DEMO_DATASET_IDS


async def ensure_demo_users(session: AsyncSession) -> list[str]:
    """Create any of the three documented demo accounts that are missing.

    Idempotent and additive: existing users are never modified, reactivated or
    re-hashed — only genuinely absent badge numbers are created, with the same
    stable ids, names and credentials the seed scripts use, through the
    application's normal password-hashing mechanism. Returns the badge numbers
    that were created (empty on a provisioned database).
    """
    from app.db.bootstrap import DEMO_USERS
    from app.domain.enums import InformationClassification
    from app.security.passwords import hash_password

    created: list[str] = []
    for spec in DEMO_USERS:
        existing = (
            await session.execute(
                select(User).where(User.badge_number == spec["badge_number"])
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        # Reuse the seed's stable id unless it is somehow taken.
        user_id = spec.get("id") or new_uuid()
        clash = await session.get(User, user_id)
        if clash is not None:
            user_id = new_uuid()
        session.add(
            User(
                id=user_id,
                badge_number=spec["badge_number"],
                full_name=spec.get("full_name", spec["badge_number"]),
                hashed_password=hash_password(spec["password"]),
                role=spec["role"],
                station_id=spec.get("station_id", "STATION-01"),
                jurisdiction_id=spec.get("jurisdiction_id", "METRO-CENTRAL"),
                max_classification=InformationClassification.CONFIDENTIAL,
                is_active=True,
            )
        )
        created.append(spec["badge_number"])
    if created:
        await session.flush()
        log.warning(
            "auth.demo_users_created",
            badges=created,
            detail="missing demo accounts were provisioned through the normal auth mechanism",
        )
    return created
