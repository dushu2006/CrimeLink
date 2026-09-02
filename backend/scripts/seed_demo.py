"""Bootstrap users and load the synthetic demo case.

    python scripts/seed_demo.py               # users + demo case + wait for processing
    python scripts/seed_demo.py --users-only  # just create the three default roles

Everything it creates is synthetic.  The default passwords exist only for a
demonstration instance; a real deployment rotates them on first login and the
ADMIN account is issued through the station's own process.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import Settings, get_settings
from app.container import Container, set_container
from app.db.base import new_uuid
from app.db.models import (
    Case,
    CaseDocument,
    DetectedPattern,
    EntityResolutionItem,
    IngestionJob,
    User,
)
from app.db.session import async_session, init_db
from app.domain.enums import (
    DocumentType,
    JobStatus,
    PatternStatus,
    ResolutionStatus,
    Role,
    SourceConfidence,
)
from app.logging import configure_logging, get_logger
from app.security.passwords import hash_password
from app.services import documents as document_service

log = get_logger("crimelink.seed")

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = BACKEND_ROOT.parent / "samples"

DEFAULT_USERS = [
    ("ADM-0001", "Asha Verma", "ADMIN", "PS-MIROAD", "RJ-JAIPUR", "CrimeLink@Admin1"),
    ("INV-0001", "Rajesh Khanna", "INVESTIGATOR", "PS-MIROAD", "RJ-JAIPUR", "CrimeLink@Inv1"),
    ("VIW-0001", "Constable Manish", "VIEWER", "PS-MIROAD", "RJ-JAIPUR", "CrimeLink@View1"),
    # An officer from another jurisdiction, used to demonstrate that scoping
    # works: they cannot see the Jaipur case without an approved request.
    ("INV-0002", "Priya Sharma", "INVESTIGATOR", "PS-KOTACITY", "RJ-KOTA", "CrimeLink@Inv2"),
]

DEMO_CASE_NUMBER = "FIR/2024/0231/PS-MIROAD"
DEMO_CASE_TITLE = "Rangdari (extortion) and hawala network — M.I. Road, Jaipur"

SAMPLE_FILES = [
    ("fir_001_english.txt", DocumentType.FIR, SourceConfidence.VERIFIED),
    ("fir_002_hindi.txt", DocumentType.FIR, SourceConfidence.VERIFIED),
    ("cdr_jio.csv", DocumentType.CDR, SourceConfidence.VERIFIED),
    ("bank_transactions.csv", DocumentType.FINANCIAL, SourceConfidence.VERIFIED),
    ("surveillance.csv", DocumentType.SURVEILLANCE, SourceConfidence.UNVERIFIED),
    ("social_media_export.json", DocumentType.SOCIAL_MEDIA, SourceConfidence.UNVERIFIED),
    ("criminal_history.csv", DocumentType.CRIMINAL_HISTORY, SourceConfidence.UNVERIFIED),
]


async def ensure_users(session) -> dict[str, User]:
    created: dict[str, User] = {}
    for badge, name, role, station, jurisdiction, password in DEFAULT_USERS:
        existing = (
            await session.execute(select(User).where(User.badge_number == badge))
        ).scalar_one_or_none()
        if existing is not None:
            created[badge] = existing
            continue
        user = User(
            id=new_uuid(),
            badge_number=badge,
            full_name=name,
            hashed_password=hash_password(password),
            role=Role(role),
            station_id=station,
            jurisdiction_id=jurisdiction,
            is_active=True,
        )
        session.add(user)
        await session.flush()
        created[badge] = user
        log.info("seed.user_created", badge=badge, role=role, jurisdiction=jurisdiction)
    return created


async def ensure_case(session, users: dict[str, User]) -> Case:
    existing = (
        await session.execute(select(Case).where(Case.case_number == DEMO_CASE_NUMBER))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    case = Case(
        id=new_uuid(),
        case_number=DEMO_CASE_NUMBER,
        title=DEMO_CASE_TITLE,
        jurisdiction_id=users["INV-0001"].jurisdiction_id,
        created_by=users["INV-0001"].id,
    )
    session.add(case)
    await session.flush()
    log.info("seed.case_created", case_number=DEMO_CASE_NUMBER)
    return case


async def upload_samples(session, container: Container, case: Case, users: dict[str, User]) -> list[str]:
    investigator = users["INV-0001"]
    job_ids: list[str] = []
    for filename, doc_type, confidence in SAMPLE_FILES:
        path = SAMPLES / filename
        if not path.exists():
            log.warning("seed.sample_missing", filename=filename)
            continue
        payload = path.read_bytes()
        try:
            document, job = await document_service.upload_document(
                session,
                container=container,
                case=case,
                principal=_principal(investigator),
                filename=filename,
                payload=payload,
                document_type=doc_type,
                source_confidence=confidence,
                mime_type=_mime(filename),
            )
            job_ids.append(job.id)
            log.info("seed.uploaded", filename=filename, doc_id=document.id, job_id=job.id)
        except Exception as exc:  # noqa: BLE001
            log.warning("seed.upload_failed", filename=filename, error=str(exc))
    return job_ids


class _principal:
    """Minimal Principal stand-in for seeding (the API path is not involved)."""

    def __init__(self, user: User) -> None:
        self.user = user
        self.id = user.id
        self.badge_number = user.badge_number
        self.role = user.role
        self.jurisdiction_id = user.jurisdiction_id
        self.station_id = user.station_id

    def require(self, *roles: str) -> None:
        if self.role.value not in roles:
            raise PermissionError(f"{self.badge_number} lacks one of {roles}")

    def has_role(self, *roles: str) -> bool:
        return self.role.value in roles


def _mime(filename: str) -> str:
    return {
        ".txt": "text/plain",
        ".csv": "text/csv",
        ".json": "application/json",
        ".pdf": "application/pdf",
    }.get(Path(filename).suffix.lower(), "application/octet-stream")


async def wait_for_jobs(session_factory, job_ids: list[str], timeout_s: int = 180) -> None:
    """Poll until every job leaves QUEUED/RUNNING (or the budget runs out)."""
    deadline = time.time() + timeout_s
    pending: list[IngestionJob] = []
    while time.time() < deadline:
        async with session_factory() as session:
            jobs = (
                await session.execute(
                    select(IngestionJob).where(IngestionJob.id.in_(job_ids))
                )
            ).scalars().all()
            pending = [j for j in jobs if j.status in (JobStatus.QUEUED, JobStatus.RUNNING)]
            if not pending:
                return
        await asyncio.sleep(0.5)
    log.warning(
        "seed.wait_timeout",
        pending=[f"{j.doc_id}:{j.status.value}" for j in pending],
    )


async def main(users_only: bool = False, wait: bool = True) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=False)
    container = Container(settings)
    set_container(container)
    await init_db()

    from app.db.session import get_async_sessionmaker

    factory = get_async_sessionmaker()

    async with async_session() as session:
        users = await ensure_users(session)
        if users_only:
            print("\nDefault accounts created:")
            for badge, _name, role, _station, jurisdiction, password in DEFAULT_USERS:
                print(f"  {badge:<10} {role:<13} {jurisdiction:<10} password: {password}")
            return
        case = await ensure_case(session, users)

    async with async_session() as session:
        job_ids = await upload_samples(session, container, case, users)

    if wait and job_ids:
        await wait_for_jobs(factory, job_ids)

    async with async_session() as session:
        documents = (
            await session.execute(
                select(CaseDocument).where(CaseDocument.case_id == case.id)
            )
        ).scalars().all()
        matches = (
            await session.execute(
                select(EntityResolutionItem).where(EntityResolutionItem.case_id == case.id)
            )
        ).scalars().all()
        patterns = (
            await session.execute(
                select(DetectedPattern).where(DetectedPattern.case_id == case.id)
            )
        ).scalars().all()

    print("\n=== CrimeLink demo case ready ===")
    print(f"Case          : {case.case_number} — {case.title}")
    print(f"Documents     : {len(documents)}")
    for document in documents:
        print(
            f"   - {document.filename:<28} {document.document_type.value:<16}"
            f" {document.ingestion_status.value:<10} lang={document.language}"
        )
    print(
        f"Identity matches queued : {sum(1 for m in matches if m.status == ResolutionStatus.PENDING)}"
    )
    print(f"Pattern findings        : {len(patterns)}")
    for pattern in patterns:
        print(f"   - {pattern.pattern_type.value:<16} {pattern.status.value:<10} {pattern.explanation[:70]}…")
    stats = container.graph_store.stats()
    print(f"Graph                   : {stats['nodes']} nodes / {stats['edges']} relationships")
    print("\nSign in with INV-0001 / CrimeLink@Inv1 (or see scripts/seed_demo.py).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the CrimeLink demo environment")
    parser.add_argument("--users-only", action="store_true", help="Create default users only")
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for pipeline completion")
    args = parser.parse_args()
    asyncio.run(main(users_only=args.users_only, wait=not args.no_wait))
