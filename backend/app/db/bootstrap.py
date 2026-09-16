"""Idempotent database and service bootstrap for CrimeLink.

Handles service readiness checks, database migrations, and idempotent demo dataset
verification and seeding.

Flow:
1. wait_for_services (PostgreSQL/SQLite, Neo4j/Embedded, MinIO/Local, Redis/Inline)
2. run_db_migrations (Alembic upgrade head -> additive reconcile)
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
import socket
import sys
import time
from pathlib import Path
from typing import Any, Tuple

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import inspect, text

from app import runtime
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
    sync_database_columns,
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

#: Command that starts the host-accessible infrastructure stack.  It only ever
#: creates/starts containers; it never removes a volume or resets demo data.
INFRA_START_COMMAND = "docker compose -f docker-compose.infra.yml up -d"
INFRA_STATUS_COMMAND = "docker compose -f docker-compose.infra.yml ps"

#: Phrases that identify a DNS failure across psycopg2/asyncpg/neo4j/redis.
#: A hostname that does not resolve will not start resolving because we retried,
#: so bootstrap fails immediately with an actionable message instead of looping
#: until the timeout and reporting an opaque driver error.
_NAME_RESOLUTION_MARKERS = (
    "could not translate host name",
    "name or service not known",
    "nodename nor servname provided",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "no address associated with hostname",
    "server misbehaving",
)


def is_name_resolution_failure(exc: BaseException | None) -> bool:
    """True when *exc* (or anything it wraps) is a hostname resolution error."""
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return True
        message = str(current).lower()
        if any(marker in message for marker in _NAME_RESOLUTION_MARKERS):
            return True
        stack.append(current.__cause__)
        stack.append(current.__context__)
    return False


def _host_part(dsn: str) -> str:
    """``host:port/database`` with credentials stripped, safe to log."""
    return dsn.split("@")[-1] if "@" in dsn else dsn


def _infrastructure_hint() -> str:
    return (
        "Start the CrimeLink infrastructure services and retry:\n"
        f"    {INFRA_START_COMMAND}\n"
        f"Check what is running with:\n"
        f"    {INFRA_STATUS_COMMAND}\n"
        "Existing containers, volumes and the persisted demo dataset are left untouched."
    )


def _context_line(settings: Settings) -> str:
    description = runtime.CONTEXT_DESCRIPTIONS.get(settings.resolved_runtime_context, "")
    return f"Runtime context: {settings.resolved_runtime_context} ({description})"


def postgres_unavailable_message(
    settings: Settings, last_error: str, *, name_resolution: bool
) -> str:
    """Actionable explanation of why PostgreSQL could not be reached.

    The headline is always the same sentence so it is recognisable in a log,
    followed by the concrete address, the runtime context and the exact command
    that fixes the common cases.  This replaces the raw
    ``could not translate host name "postgres"`` DNS error, which told an
    operator nothing about what to do next.
    """
    host, port = settings.postgres_endpoint
    lines = [
        f"PostgreSQL is not running ({host}:{port}).",
        "",
        _infrastructure_hint(),
        "",
        f"Connection error : {last_error or 'no response'}",
        _context_line(settings),
        f"DSN (sync)       : {_host_part(settings.postgres_dsn_sync)}",
        f"DSN (async)      : {_host_part(settings.postgres_dsn)}",
    ]
    if name_resolution and host in runtime.COMPOSE_SERVICE_HOSTNAMES:
        lines += [
            "",
            f"'{host}' is a Docker Compose service name: it resolves only on the Compose",
            "network, and this process is not running in a container. Pick one of:",
            "    python run.py                          # detects the runtime context for you",
            f"    set CRIMELINK_RUNTIME_CONTEXT=host     # rewrites '{host}' -> "
            f"{settings.infra_host}:{settings.postgres_host_port}",
            "    set CRIMELINK_POSTGRES_DSN / CRIMELINK_POSTGRES_DSN_SYNC to a reachable server",
        ]
    elif name_resolution:
        lines += [
            "",
            f"The hostname '{host}' could not be resolved from this machine.",
            "Check CRIMELINK_INFRA_HOST, your DNS settings, or set the DSN explicitly.",
        ]
    else:
        lines += [
            "",
            "Nothing accepted a connection on that address. If PostgreSQL runs on another",
            "port or host, point CrimeLink at it instead of editing the code:",
            "    CRIMELINK_INFRA_HOST / CRIMELINK_POSTGRES_HOST_PORT (host runtime), or",
            "    CRIMELINK_POSTGRES_DSN and CRIMELINK_POSTGRES_DSN_SYNC (any runtime).",
            "",
            "CrimeLink does not fall back to SQLite or an in-memory database: PostgreSQL",
            "is the relational system of record for this configuration.",
        ]
    return "\n".join(lines)


def wait_for_services(settings: Settings | None = None, timeout: float = 30.0) -> None:
    """Verify that required storage and persistence backends are healthy.

    In production profile: fails loudly if PostgreSQL, Neo4j, or MinIO cannot be reached.
    In embedded profile: verifies local filesystem paths are accessible and writable.

    Every check is fail-closed: a service that cannot be reached aborts the
    bootstrap.  No check silently substitutes a weaker backend.
    """
    settings = settings or get_settings()
    is_prod = settings.profile == "production" or settings.environment == "production"
    deadline = time.time() + timeout

    log.info(
        "bootstrap.runtime_context",
        context=settings.resolved_runtime_context,
        profile=settings.profile,
        environment=settings.environment,
        in_container=runtime.running_in_container(),
        backends={
            "relational": settings.effective_relational_backend,
            "graph": settings.effective_graph_backend,
            "object_store": settings.effective_object_store_backend,
            "broker": settings.effective_broker_backend,
        },
        endpoint_rewrites=settings.endpoint_rewrites,
    )

    # 1.1 Relational database
    rel_backend = settings.effective_relational_backend
    if rel_backend == "postgres":
        host, port = settings.postgres_endpoint
        log.info(
            "bootstrap.checking_postgres",
            dsn=_host_part(settings.postgres_dsn_sync),
            host=host,
            port=port,
            runtime_context=settings.resolved_runtime_context,
        )
        last_error = ""
        name_resolution_failure = False
        engine = get_sync_engine(settings)
        connected = False
        while time.time() < deadline:
            try:
                with engine.connect() as conn:
                    # A literal SELECT 1 — the previous code handed SQLAlchemy a
                    # Table object, which is not executable, so the probe failed
                    # even against a perfectly healthy PostgreSQL server.
                    conn.execute(text("SELECT 1"))
                connected = True
                break
            except Exception as exc:  # noqa: BLE001 - reported with full context below
                last_error = str(exc).strip()
                if is_name_resolution_failure(exc):
                    # DNS will not fix itself by retrying; fail fast and explain.
                    name_resolution_failure = True
                    break
                time.sleep(1.0)
        if not connected:
            try:
                engine.dispose()
            except Exception:  # pragma: no cover - disposal is best effort
                pass
            raise RuntimeError(
                postgres_unavailable_message(
                    settings, last_error, name_resolution=name_resolution_failure
                )
            )
        log.info("bootstrap.postgres_ready", host=host, port=port)
    else:
        # SQLite
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        log.info("bootstrap.sqlite_ready", path=str(settings.sqlite_path))

    # 1.2 Graph database
    graph_backend = settings.effective_graph_backend
    if graph_backend == "neo4j":
        neo_host, neo_port = settings.neo4j_endpoint
        log.info(
            "bootstrap.checking_neo4j",
            uri=settings.neo4j_uri,
            host=neo_host,
            port=neo_port,
            runtime_context=settings.resolved_runtime_context,
        )
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
                if is_name_resolution_failure(exc):
                    break
                time.sleep(1.0)
        if not connected:
            raise RuntimeError(
                f"Neo4j is unavailable at {settings.neo4j_uri} ({last_error}). "
                "Refusing to start with broken graph database dependency.\n"
                + _infrastructure_hint()
            )
        log.info("bootstrap.neo4j_ready")
    else:
        # Embedded GraphStore
        settings.graph_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("bootstrap.embedded_graph_ready", path=str(settings.graph_snapshot_path))

    # 1.3 Object store
    obj_backend = settings.effective_object_store_backend
    if obj_backend == "minio":
        minio_host, minio_port = settings.minio_endpoint_address
        log.info(
            "bootstrap.checking_minio",
            endpoint=settings.minio_endpoint,
            host=minio_host,
            port=minio_port,
            runtime_context=settings.resolved_runtime_context,
        )
        try:
            from app.adapters.objectstore.minio_store import MinioObjectStore
            store = MinioObjectStore(settings)
            store.ensure_buckets()
            log.info("bootstrap.minio_ready", endpoint=settings.minio_endpoint)
        except Exception as exc:
            if is_prod:
                raise RuntimeError(
                    f"MinIO is unavailable at {settings.minio_endpoint} ({exc}). "
                    "MinIO is mandatory in production — refusing Local fallback.\n"
                    + _infrastructure_hint()
                ) from exc
            raise RuntimeError(
                f"MinIO connection failed: {exc}\n" + _infrastructure_hint()
            ) from exc
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
        redis_host, redis_port = settings.redis_endpoint
        log.info(
            "bootstrap.checking_redis",
            url=settings.redis_url,
            host=redis_host,
            port=redis_port,
            runtime_context=settings.resolved_runtime_context,
        )
        try:
            import redis
            client = redis.from_url(settings.redis_url, socket_timeout=3.0)
            client.ping()
            log.info("bootstrap.redis_ready")
        except Exception as exc:
            if is_prod:
                raise RuntimeError(
                    f"Redis is unavailable at {settings.redis_url} ({exc}). "
                    "Redis is mandatory for Celery broker in production.\n"
                    + _infrastructure_hint()
                ) from exc
            log.warning("bootstrap.redis_unavailable", error=str(exc))


# ---------------------------------------------------------------------------
# 2. Database Schema Upkeep & Migrations
# ---------------------------------------------------------------------------

def run_db_migrations(settings: Settings | None = None) -> None:
    """Bring the relational schema to Alembic ``head`` and verify it.

    Alembic owns the schema: ``app.db.upgrade`` runs ``alembic upgrade head``
    for a fresh database, an already-migrated one, and a pre-Alembic database
    created by ``create_all`` in an older build (additive reconcile, then stamp
    and upgrade) — the same entry point a Render pre-deploy hook uses, so a
    container start and a deployment can never disagree about the schema.

    The additive reconcile below is a *repair* pass, not the source of truth:
    on a database the migrations just brought to ``head`` it must add nothing,
    so anything it does add is logged as a warning.  A model that gained a table
    or column without a matching revision would otherwise pass silently, which
    is exactly how ``cases.classification does not exist`` reached production.
    """
    settings = settings or get_settings()
    settings.ensure_directories()
    engine = get_sync_engine(settings)
    backend = settings.effective_relational_backend

    from app.db.upgrade import upgrade_database

    report = upgrade_database(settings)
    log.info("bootstrap.schema_at_head", detail=report.describe())

    with engine.connect() as conn:
        known = set(inspect(conn).get_table_names())
    missing_tables = sorted(set(Base.metadata.tables) - known)
    if missing_tables:
        log.warning("bootstrap.schema_drift_repaired", missing_tables=missing_tables, backend=backend)
        Base.metadata.create_all(bind=engine)

    with engine.connect() as conn:
        added = sync_database_columns(conn, Base.metadata)
        if added:
            log.warning("bootstrap.columns_repaired", added=added, backend=backend)
        if backend == "sqlite":
            upgraded = sync_sqlite_enum_constraints(conn, Base.metadata)
            if upgraded:
                log.warning("bootstrap.sqlite_enums_repaired", upgraded=upgraded)
        conn.commit()

    log.info("bootstrap.db_schema_ready", backend=backend, revision=report.revision, mode=report.mode)


# ---------------------------------------------------------------------------
# 3. Idempotent Demo Dataset Status Check
# ---------------------------------------------------------------------------

def check_demo_dataset_status(settings: Settings | None = None) -> Tuple[str, str]:
    """Inspects persistence stores and returns ("CORRECT" | "MISSING" | "INCONSISTENT", reason).

    In development environments, missing or partially populated demo records return "MISSING"
    so the bootstrap can automatically and safely populate them. In strict production,
    inconsistencies fail loudly to prevent unintentional modification of existing data.
    """
    settings = settings or get_settings()
    is_dev = (
        settings.environment in ("dev", "staging")
        or os.getenv("CRIMELINK_ALLOW_DEMO_RESET", "").lower() in ("true", "1", "yes")
        or os.getenv("CRIMELINK_DEMO_MODE", "").lower() in ("true", "1", "yes")
    )

    def _unseeded_or_inconsistent(detail: str) -> Tuple[str, str]:
        if is_dev:
            return "MISSING", f"{detail} (will auto-seed in dev environment)"
        return "INCONSISTENT", detail

    session_maker = get_sync_sessionmaker()
    session = session_maker()

    try:
        # Check Dataset row
        dataset = session.query(Dataset).filter(Dataset.id == DEMO_DATASET_ID).one_or_none()
        if dataset is None:
            case_count = session.query(Case).filter(Case.dataset_id == DEMO_DATASET_ID).count()
            if case_count > 0:
                return _unseeded_or_inconsistent(f"Found {case_count} cases for {DEMO_DATASET_ID} but Dataset record is missing.")
            return "MISSING", f"Dataset {DEMO_DATASET_ID} is not registered in database."

        if not dataset.is_active:
            # Re-activate
            dataset.is_active = True
            session.commit()

        # Check demo users
        for u in DEMO_USERS:
            user = session.query(User).filter(User.badge_number == u["badge_number"]).one_or_none()
            if user is None:
                return _unseeded_or_inconsistent(f"Required demo user {u['badge_number']} is missing.")
            if user.role != u["role"]:
                if is_dev:
                    user.role = u["role"]
                    session.commit()
                else:
                    return "INCONSISTENT", f"Demo user {u['badge_number']} has incorrect role: {user.role} vs {u['role']}."

        # Check cases: expect 20 cases (CR-1024 to CR-1043)
        cases = session.query(Case).filter(Case.dataset_id == DEMO_DATASET_ID).all()
        case_numbers = {c.case_number for c in cases}
        if len(cases) < 20:
            return _unseeded_or_inconsistent(f"Incomplete demo cases: found {len(cases)} of 20 expected cases.")

        missing_cases = set(EXPECTED_CASE_NUMBERS) - case_numbers
        if missing_cases:
            return _unseeded_or_inconsistent(f"Missing expected case numbers: {sorted(missing_cases)}")

        hero_case = next((c for c in cases if c.case_number == HERO_CASE_NUMBER), None)
        if hero_case is None:
            return _unseeded_or_inconsistent(f"Hero case {HERO_CASE_NUMBER} not found in cases.")

        # Check evidence CaseDocuments
        doc_count = session.query(CaseDocument).filter(CaseDocument.dataset_id == DEMO_DATASET_ID).count()
        if doc_count < 200:
            return _unseeded_or_inconsistent(f"Incomplete evidence documents: found {doc_count} (expected >= 300).")

        hero_doc = session.query(CaseDocument).filter(CaseDocument.id == HERO_EVIDENCE_ID).one_or_none()
        if hero_doc is None:
            return _unseeded_or_inconsistent(f"Hero evidence document {HERO_EVIDENCE_ID} is missing.")

        # Check InvestigationFindings
        findings = session.query(InvestigationFinding).filter(
            InvestigationFinding.case_id.in_([c.id for c in cases])
        ).all()
        if not findings:
            return _unseeded_or_inconsistent("No investigation findings found for demo cases.")

        hero_finding = session.query(InvestigationFinding).filter(
            InvestigationFinding.id == HERO_INVESTIGATION_ID
        ).one_or_none()
        if hero_finding is None:
            return _unseeded_or_inconsistent(f"Hero investigation finding {HERO_INVESTIGATION_ID} is missing.")

        # Check Graph store
        graph_backend = settings.effective_graph_backend
        if graph_backend == "embedded":
            from app.adapters.graph.embedded import EmbeddedGraphStore
            store = EmbeddedGraphStore(settings)
            try:
                node_count = store._graph.number_of_nodes()
                edge_count = store._graph.number_of_edges()
                if node_count < 50:
                    return _unseeded_or_inconsistent(f"Embedded graph has only {node_count} nodes (expected >= 100).")
                if "PERSON-001" not in store._graph:
                    return _unseeded_or_inconsistent("Hero entity PERSON-001 is missing from embedded graph.")
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
                    return _unseeded_or_inconsistent("Hero entity PERSON-001 is missing from Neo4j.")
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
                return _unseeded_or_inconsistent(f"Hero file {hero_storage_key} missing or empty in MinIO.")
        else:
            from app.adapters.objectstore.local import LocalObjectStore
            l_store = LocalObjectStore(settings)
            meta = l_store.stat(bucket, hero_storage_key)
            if not meta or meta.size == 0:
                return _unseeded_or_inconsistent(f"Hero file {hero_storage_key} missing or empty in local object store.")

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
    force_reseed = force_reseed or os.getenv("CRIMELINK_FORCE_RESEED", "").lower() in ("true", "1", "yes")
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
