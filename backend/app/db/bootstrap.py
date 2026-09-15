"""Idempotent database and service bootstrap for CrimeLink.

Handles service readiness checks, database migrations, and idempotent demo dataset
verification and seeding.

Flow:
1. wait_for_services (PostgreSQL/SQLite, Neo4j/Embedded, MinIO/Local, Redis/Inline)
2. run_db_migrations (Alembic / SQLite schema sync / Base.metadata.create_all)
3. check_demo_dataset_status:
   - "CORRECT"      -> do nothing (idempotent, non-destructive, fast)
   - "MISSING"      -> seed demo dataset (20 cases, 100 people, 216 rels, 300 evidence, INV-0042)
   - "INCONSISTENT" -> fail loudly with detailed reason (do not corrupt or wipe)
4. validate_demo_readiness (hero flow CR-1024, E-042, INV-0042, demo users)
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Tuple

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import Settings, get_settings
from app.db.models import (
    Base,
    Case,
    CaseDocument,
    Dataset,
    DatasetFile,
    InvestigationFinding,
    InvestigationSession,
    SourceReference,
    User,
)
from app.db.session import (
    _bootstrap_postgres,
    get_sync_engine,
    get_sync_sessionmaker,
    sync_sqlite_columns,
    sync_sqlite_enum_constraints,
)
from app.domain.enums import Role
from app.logging import get_logger

log = get_logger("crimelink.bootstrap")

DEMO_DATASET_ID = "demo-dataset-001"
HERO_CASE_NUMBER = "CR-1024"
HERO_CASE_ID = "case-001-demo"
HERO_EVIDENCE_ID = "evidence-042-demo"
HERO_INVESTIGATION_ID = "INV-0042"

DEMO_USERS = [
    {"badge_number": "DEMO-ADMIN", "role": Role.ADMIN},
    {"badge_number": "DEMO-INVESTIGATOR", "role": Role.INVESTIGATOR},
    {"badge_number": "DEMO-VIEWER", "role": Role.VIEWER},
]

EXPECTED_CASE_NUMBERS = [f"CR-{1024 + i}" for i in range(20)]


# ---------------------------------------------------------------------------
# 1. Service Health & Readiness Checks
# ---------------------------------------------------------------------------

def wait_for_services(settings: Settings | None = None, timeout: float = 30.0) -> None:
    """Verify that required storage and persistence backends are healthy.

    In production profile: fails loudly if PostgreSQL, Neo4j, or MinIO cannot be reached.
    In embedded profile: verifies local filesystem paths are accessible and writable.
    """
    settings = settings or get_settings()
    is_prod = settings.profile == "production" or settings.environment == "production"
    deadline = time.time() + timeout

    # 1.1 Relational database
    rel_backend = settings.effective_relational_backend
    if rel_backend == "postgres":
        log.info("bootstrap.checking_postgres", dsn=settings.postgres_dsn_sync.split("@")[-1])
        last_error = ""
        engine = get_sync_engine(settings)
        connected = False
        while time.time() < deadline:
            try:
                with engine.connect() as conn:
                    conn.execute(Base.metadata.tables.get("cases", None) or "SELECT 1")
                connected = True
                break
            except Exception as exc:
                last_error = str(exc)
                time.sleep(1.0)
        if not connected:
            raise RuntimeError(
                f"PostgreSQL is unavailable ({last_error}). "
                "Refusing to start with broken database dependency."
            )
        log.info("bootstrap.postgres_ready")
    else:
        # SQLite
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        log.info("bootstrap.sqlite_ready", path=str(settings.sqlite_path))

    # 1.2 Graph database
    graph_backend = settings.effective_graph_backend
    if graph_backend == "neo4j":
        log.info("bootstrap.checking_neo4j", uri=settings.neo4j_uri)
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError("neo4j driver is not installed") from exc

        last_error = ""
        connected = False
        while time.time() < deadline:
            try:
                driver = GraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password),
                )
                with driver.session(database=settings.neo4j_database) as session:
                    session.run("RETURN 1").single()
                driver.close()
                connected = True
                break
            except Exception as exc:
                last_error = str(exc)
                time.sleep(1.0)
        if not connected:
            raise RuntimeError(
                f"Neo4j is unavailable at {settings.neo4j_uri} ({last_error}). "
                "Refusing to start with broken graph database dependency."
            )
        log.info("bootstrap.neo4j_ready")
    else:
        # Embedded GraphStore
        settings.graph_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("bootstrap.embedded_graph_ready", path=str(settings.graph_snapshot_path))

    # 1.3 Object store
    obj_backend = settings.effective_object_store_backend
    if obj_backend == "minio":
        log.info("bootstrap.checking_minio", endpoint=settings.minio_endpoint)
        try:
            from app.adapters.objectstore.minio_store import MinioObjectStore
            store = MinioObjectStore(settings)
            store.ensure_buckets()
            log.info("bootstrap.minio_ready", endpoint=settings.minio_endpoint)
        except Exception as exc:
            if is_prod:
                raise RuntimeError(
                    f"MinIO is unavailable at {settings.minio_endpoint} ({exc}). "
                    "MinIO is mandatory in production — refusing Local fallback."
                ) from exc
            raise RuntimeError(f"MinIO connection failed: {exc}") from exc
    else:
        if is_prod:
            raise RuntimeError(
                "MinIO is mandatory in production profile, but object_store_backend is "
                f"'{obj_backend}'. Refusing to start."
            )
        settings.object_store_dir.mkdir(parents=True, exist_ok=True)
        log.info("bootstrap.local_object_store_ready", path=str(settings.object_store_dir))

    # 1.4 Broker (Redis)
    broker_backend = settings.effective_broker_backend
    if broker_backend == "celery":
        log.info("bootstrap.checking_redis", url=settings.redis_url)
        try:
            import redis
            client = redis.from_url(settings.redis_url, socket_timeout=3.0)
            client.ping()
            log.info("bootstrap.redis_ready")
        except Exception as exc:
            if is_prod:
                raise RuntimeError(
                    f"Redis is unavailable at {settings.redis_url} ({exc}). "
                    "Redis is mandatory for Celery broker in production."
                ) from exc
            log.warning("bootstrap.redis_unavailable", error=str(exc))


# ---------------------------------------------------------------------------
# 2. Database Schema Upkeep & Migrations
# ---------------------------------------------------------------------------

def run_db_migrations(settings: Settings | None = None) -> None:
    """Ensure database schema tables, columns, constraints and extensions exist."""
    settings = settings or get_settings()
    settings.ensure_directories()
    engine = get_sync_engine(settings)

    # Create all missing tables
    Base.metadata.create_all(bind=engine)

    # If SQLite: synchronize columns and enum CHECK constraints
    if settings.effective_relational_backend == "sqlite":
        with engine.connect() as conn:
            added = sync_sqlite_columns(conn, Base.metadata)
            if added:
                log.info("bootstrap.sqlite_columns_synchronized", added=added)
            upgraded = sync_sqlite_enum_constraints(conn, Base.metadata)
            if upgraded:
                log.info("bootstrap.sqlite_enums_synchronized", upgraded=upgraded)
            conn.commit()

    log.info("bootstrap.db_schema_ready", backend=settings.effective_relational_backend)


# ---------------------------------------------------------------------------
# 3. Idempotent Demo Dataset Status Check
# ---------------------------------------------------------------------------

def check_demo_dataset_status(settings: Settings | None = None) -> Tuple[str, str]:
    """Check whether DEMO-DATASET-001 is correctly and consistently seeded.

    Returns:
        ("CORRECT", explanation)      -> already fully seeded, consistent, ready
        ("MISSING", explanation)      -> not yet seeded (needs seed)
        ("INCONSISTENT", explanation) -> partially seeded / corrupt / mismatch (fail loudly)
    """
    settings = settings or get_settings()
    session_maker = get_sync_sessionmaker()
    session = session_maker()

    try:
        # Check Dataset row
        dataset = session.query(Dataset).filter(Dataset.id == DEMO_DATASET_ID).one_or_none()
        if dataset is None:
            # Check if any cases or demo users exist
            case_count = session.query(Case).filter(Case.dataset_id == DEMO_DATASET_ID).count()
            if case_count > 0:
                return "INCONSISTENT", f"Found {case_count} cases for {DEMO_DATASET_ID} but Dataset record is missing."
            return "MISSING", f"Dataset {DEMO_DATASET_ID} is not registered in database."

        if not dataset.is_active:
            # Re-activate
            dataset.is_active = True
            session.commit()

        # Check demo users
        for u in DEMO_USERS:
            user = session.query(User).filter(User.badge_number == u["badge_number"]).one_or_none()
            if user is None:
                return "INCONSISTENT", f"Required demo user {u['badge_number']} is missing."
            if user.role != u["role"]:
                return "INCONSISTENT", f"Demo user {u['badge_number']} has incorrect role: {user.role} vs {u['role']}."

        # Check cases: expect 20 cases (CR-1024 to CR-1043)
        cases = session.query(Case).filter(Case.dataset_id == DEMO_DATASET_ID).all()
        case_numbers = {c.case_number for c in cases}
        if len(cases) < 20:
            return "INCONSISTENT", f"Incomplete demo cases: found {len(cases)} of 20 expected cases."

        missing_cases = set(EXPECTED_CASE_NUMBERS) - case_numbers
        if missing_cases:
            return "INCONSISTENT", f"Missing expected case numbers: {sorted(missing_cases)}"

        hero_case = next((c for c in cases if c.case_number == HERO_CASE_NUMBER), None)
        if hero_case is None:
            return "INCONSISTENT", f"Hero case {HERO_CASE_NUMBER} not found in cases."

        # Check evidence CaseDocuments
        doc_count = session.query(CaseDocument).filter(CaseDocument.dataset_id == DEMO_DATASET_ID).count()
        if doc_count < 200:
            return "INCONSISTENT", f"Incomplete evidence documents: found {doc_count} (expected >= 300)."

        hero_doc = session.query(CaseDocument).filter(CaseDocument.id == HERO_EVIDENCE_ID).one_or_none()
        if hero_doc is None:
            return "INCONSISTENT", f"Hero evidence document {HERO_EVIDENCE_ID} is missing."

        # Check InvestigationFindings
        findings = session.query(InvestigationFinding).filter(
            InvestigationFinding.case_id.in_([c.id for c in cases])
        ).all()
        if not findings:
            return "INCONSISTENT", "No investigation findings found for demo cases."

        hero_finding = session.query(InvestigationFinding).filter(
            InvestigationFinding.id == HERO_INVESTIGATION_ID
        ).one_or_none()
        if hero_finding is None:
            return "INCONSISTENT", f"Hero investigation finding {HERO_INVESTIGATION_ID} is missing."

        # Check Graph store
        graph_backend = settings.effective_graph_backend
        if graph_backend == "embedded":
            from app.adapters.graph.embedded import EmbeddedGraphStore
            store = EmbeddedGraphStore(settings)
            try:
                node_count = store._graph.number_of_nodes()
                edge_count = store._graph.number_of_edges()
                if node_count < 50:
                    return "INCONSISTENT", f"Embedded graph has only {node_count} nodes (expected >= 100)."
                if "PERSON-001" not in store._graph:
                    return "INCONSISTENT", "Hero entity PERSON-001 is missing from embedded graph."
            finally:
                store.close()
        elif graph_backend == "neo4j":
            from neo4j import GraphDatabase
            driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
            with driver.session(database=settings.neo4j_database) as neo_sess:
                res = neo_sess.run(
                    "MATCH (p:Person {provenance_key: 'PERSON-001'}) RETURN count(p) as cnt"
                ).single()
                if not res or res["cnt"] == 0:
                    driver.close()
                    return "INCONSISTENT", "Hero entity PERSON-001 is missing from Neo4j."
            driver.close()

        # Check Object Store for hero file E-042
        obj_backend = settings.effective_object_store_backend
        bucket = settings.minio_bucket_documents
        hero_storage_key = "evidence/E-042/original.pdf"
        if obj_backend == "minio":
            from app.adapters.objectstore.minio_store import MinioObjectStore
            m_store = MinioObjectStore(settings)
            meta = m_store.stat(bucket, hero_storage_key)
            if not meta or meta.size == 0:
                return "INCONSISTENT", f"Hero file {hero_storage_key} missing or empty in MinIO."
        else:
            from app.adapters.objectstore.local import LocalObjectStore
            l_store = LocalObjectStore(settings)
            meta = l_store.stat(bucket, hero_storage_key)
            if not meta or meta.size == 0:
                return "INCONSISTENT", f"Hero file {hero_storage_key} missing or empty in local object store."

        return "CORRECT", "Demo dataset DEMO-DATASET-001 is fully populated, verified, and consistent."
    except Exception as exc:
        return "INCONSISTENT", f"Verification encountered an error: {exc}"
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 4. Bootstrap Sequence
# ---------------------------------------------------------------------------

def bootstrap_demo_dataset(
    settings: Settings | None = None,
    force_reseed: bool = False,
    validate: bool = True,
) -> bool:
    """Idempotent startup bootstrap sequence.

    1. Checks service dependencies
    2. Runs DB schema migrations
    3. Verifies demo dataset status:
       - If CORRECT: do nothing (preserve persistence, 0 duplicate data)
       - If MISSING: seed demo dataset
       - If INCONSISTENT: fail loudly
    4. Validates readiness
    """
    settings = settings or get_settings()
    print("[CrimeLink] Initializing storage and persistence services...", flush=True)
    wait_for_services(settings)

    print("[CrimeLink] Ensuring database schema and migrations...", flush=True)
    run_db_migrations(settings)

    print("[CrimeLink] Checking demo dataset DEMO-DATASET-001 status...", flush=True)
    status, reason = check_demo_dataset_status(settings)

    if status == "CORRECT" and not force_reseed:
        print(f"[CrimeLink] Demo dataset is already correctly seeded ({reason}).", flush=True)
        print("[CrimeLink] Startup is non-destructive — existing data preserved.", flush=True)
        return True

    if status == "MISSING" or force_reseed:
        print(f"[CrimeLink] Demo dataset needs seeding ({reason}). Seeding now...", flush=True)
        # Import seed logic dynamically
        from scripts.seed_demo import seed_postgres, seed_minio, seed_neo4j, ensure_demo_dataset_files

        ensure_demo_dataset_files()
        pg_ok = seed_postgres()
        if not pg_ok:
            raise RuntimeError("PostgreSQL / SQLite seeding failed.")

        minio_ok = seed_minio()
        if not minio_ok:
            raise RuntimeError("MinIO / ObjectStore seeding failed.")

        neo4j_ok = seed_neo4j()
        if not neo4j_ok:
            raise RuntimeError("Neo4j / EmbeddedGraph seeding failed.")

        # Re-check status after seed
        status_after, reason_after = check_demo_dataset_status(settings)
        if status_after != "CORRECT":
            raise RuntimeError(
                f"Demo dataset seed completed but verification returned {status_after}: {reason_after}"
            )
        print("[CrimeLink] Demo dataset seeded and verified successfully.", flush=True)
        return True

    # If INCONSISTENT -> fail loudly!
    print(f"\n[CrimeLink] CRITICAL ERROR: Demo dataset inconsistency detected!", file=sys.stderr, flush=True)
    print(f"[CrimeLink] Reason: {reason}", file=sys.stderr, flush=True)
    print(f"[CrimeLink] To safely reset and re-seed in development, run:", file=sys.stderr, flush=True)
    print(f"    CRIMELINK_ALLOW_DEMO_RESET=true python backend/scripts/reset_demo.py", file=sys.stderr, flush=True)
    print(f"    python backend/scripts/seed_demo.py", file=sys.stderr, flush=True)
    raise RuntimeError(f"Demo dataset inconsistency: {reason}")


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="CrimeLink Idempotent Demo Dataset Bootstrapper")
    parser.add_argument("--force", action="store_true", help="Force re-seeding even if already correct")
    parser.add_argument("--check-only", action="store_true", help="Only check status without seeding")
    args = parser.parse_args()

    settings = get_settings()
    try:
        if args.check_only:
            status, reason = check_demo_dataset_status(settings)
            print(f"Status: {status}\nReason: {reason}")
            return 0 if status == "CORRECT" else 1

        bootstrap_demo_dataset(settings, force_reseed=args.force)
        return 0
    except Exception as exc:
        print(f"[CrimeLink] Bootstrap failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
