"""Regression tests for the serverless / production-readiness hardening.

Covers, in order:

1. serverless detection and the bounded PostgreSQL pool/timeout settings;
2. Neo4j connectivity failures surfacing as the 503 error contract instead
   of an unhandled ``ValueError`` (the production ``/graph/stats`` 500);
3. the metadata-only active-dataset repair (never fabricates data, never
   promotes a non-READY dataset, idempotent);
4. demo-account provisioning through the normal auth mechanism;
5. ``POST /auth/demo/login`` quick sign-in (whitelisted badges only,
   lockout-aware, switchable off);
6. the signed raw-source URL gate: a VIEWER reads the manifest but never
   receives a replayable URL for restricted source bytes.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import runtime
from app.config import Settings, get_settings
from app.db.session import (
    _async_engine_kwargs,
    _postgres_connect_args,
    _postgres_pool_sizes,
)
from app.errors import ServiceUnavailableError

# --------------------------------------------------------------------------- #
# 1. Serverless detection and PostgreSQL pool/timeout bounds
# --------------------------------------------------------------------------- #

def test_running_on_serverless_detection(monkeypatch):
    for name in runtime._SERVERLESS_VARS:
        monkeypatch.delenv(name, raising=False)
    assert runtime.running_on_serverless() is False
    monkeypatch.setenv("VERCEL", "1")
    assert runtime.running_on_serverless() is True


def test_pool_sizes_use_serverless_defaults_when_unset(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    settings = Settings()
    assert "postgres_pool_size" not in settings.model_fields_set
    pool_size, max_overflow = _postgres_pool_sizes(settings)
    assert pool_size == settings.postgres_serverless_pool_size
    assert max_overflow == settings.postgres_serverless_max_overflow
    assert pool_size + max_overflow <= settings.postgres_pool_size + settings.postgres_max_overflow


def test_pool_sizes_explicit_configuration_wins_on_serverless(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("CRIMELINK_POSTGRES_POOL_SIZE", "7")
    monkeypatch.setenv("CRIMELINK_POSTGRES_MAX_OVERFLOW", "8")
    pool_size, max_overflow = _postgres_pool_sizes(Settings())
    assert (pool_size, max_overflow) == (7, 8)


def test_pool_sizes_unchanged_off_serverless(monkeypatch):
    for name in runtime._SERVERLESS_VARS:
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert _postgres_pool_sizes(settings) == (
        settings.postgres_pool_size,
        settings.postgres_max_overflow,
    )


def test_connect_args_are_bounded():
    settings = Settings()
    async_args = _postgres_connect_args(settings, sync=False)
    assert async_args["timeout"] == settings.postgres_connect_timeout_s > 0
    assert async_args["command_timeout"] == settings.postgres_command_timeout_s > 0
    sync_args = _postgres_connect_args(settings, sync=True)
    assert sync_args["connect_timeout"] >= 1


def test_engine_kwargs_recycle_and_timeouts_for_postgres_only():
    settings = Settings()
    pg_kwargs = _async_engine_kwargs("postgresql+asyncpg://u:p@h:5432/db", settings)
    assert pg_kwargs["pool_recycle"] == settings.postgres_pool_recycle_s
    assert "timeout" in pg_kwargs["connect_args"]
    sqlite_kwargs = _async_engine_kwargs("sqlite+aiosqlite:///./x.db", settings)
    assert "pool_recycle" not in sqlite_kwargs
    assert sqlite_kwargs["connect_args"] == {"check_same_thread": False}


# --------------------------------------------------------------------------- #
# 2. Neo4j connectivity failures → 503 contract
# --------------------------------------------------------------------------- #

def test_connectivity_classification():
    from app.adapters.graph.neo4j import _is_connectivity_failure

    # The exact production failure: DNS for a compose hostname on Vercel.
    assert _is_connectivity_failure(ValueError("Cannot resolve address neo4j:7687"))
    assert _is_connectivity_failure(ConnectionRefusedError("connection refused"))
    assert _is_connectivity_failure(TimeoutError())
    assert _is_connectivity_failure(OSError("socket closed"))
    try:
        from neo4j.exceptions import ServiceUnavailable as Neo4jSU

        assert _is_connectivity_failure(Neo4jSU("unavailable"))
    except ImportError:  # pragma: no cover
        pass
    # Business/data errors must keep propagating unchanged.
    assert not _is_connectivity_failure(KeyError("node"))
    assert not _is_connectivity_failure(RuntimeError("cypher syntax"))
    assert not _is_connectivity_failure(ValueError("bad literal"))


class _FailingSession:
    def __init__(self, exc):
        self._exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute_read(self, fn, *args, **kwargs):
        raise self._exc

    execute_write = execute_read


class _FailingDriver:
    def __init__(self, exc):
        self._exc = exc

    def session(self, database=None):
        return _FailingSession(self._exc)


def _adapter_with_driver(exc):
    from app.adapters.graph.neo4j import Neo4jGraphStore

    adapter = Neo4jGraphStore.__new__(Neo4jGraphStore)
    adapter.settings = SimpleNamespace(neo4j_database="neo4j")
    adapter._driver = _FailingDriver(exc)
    return adapter


def test_read_converts_dns_failure_to_service_unavailable():
    adapter = _adapter_with_driver(ValueError("Cannot resolve address neo4j:7687"))
    with pytest.raises(ServiceUnavailableError):
        adapter._read(lambda session: None)


def test_write_converts_connection_refused_to_service_unavailable():
    adapter = _adapter_with_driver(ConnectionRefusedError())
    with pytest.raises(ServiceUnavailableError):
        adapter._write(lambda session: None)


def test_read_lets_business_errors_through():
    adapter = _adapter_with_driver(RuntimeError("cypher syntax error"))
    with pytest.raises(RuntimeError):
        adapter._read(lambda session: None)


# --------------------------------------------------------------------------- #
# 3. Metadata-only active-dataset repair
# --------------------------------------------------------------------------- #

def _dataset_row(dataset_id: str, *, status: str = "READY", is_active: bool = False):
    from app.db.models import Dataset

    return Dataset(
        id=dataset_id,
        name=f"Test {dataset_id}",
        version="1",
        status=status,
        is_active=is_active,
        source_kind="builtin",
        root_path="",
        origin_note="test",
    )


async def test_repair_noop_when_active_exists(db):
    from app.datasets.repair import repair_active_dataset

    db.add(_dataset_row("pr-repair-ok", is_active=True))
    await db.commit()
    report = await repair_active_dataset(db)
    assert report["status"] == "ok"
    assert report["dataset_id"] == "pr-repair-ok"


async def test_repair_reactivates_ready_dataset(db):
    from app.datasets.repair import repair_active_dataset
    from app.db.models import Dataset

    db.add(_dataset_row("pr-repair-ready"))
    await db.commit()
    report = await repair_active_dataset(db)
    assert report["status"] == "reactivated"
    assert report["dataset_id"] == "pr-repair-ready"
    row = await db.get(Dataset, "pr-repair-ready")
    assert row.is_active is True


async def test_repair_prefers_demo_dataset(db):
    from app.datasets.repair import repair_active_dataset

    db.add(_dataset_row("pr-other-ready"))
    db.add(_dataset_row("demo-dataset-002"))
    await db.commit()
    report = await repair_active_dataset(db)
    assert report["status"] == "reactivated"
    assert report["dataset_id"] == "demo-dataset-002"


async def test_repair_never_promotes_non_ready_dataset(db):
    from app.datasets.repair import repair_active_dataset
    from app.db.models import Dataset

    db.add(_dataset_row("pr-repair-failed", status="FAILED"))
    await db.commit()
    report = await repair_active_dataset(db)
    assert report["status"] == "empty"
    row = await db.get(Dataset, "pr-repair-failed")
    assert row.is_active is False


async def test_repair_recovers_registration_from_data_rows(db):
    """Data rows prove the dataset exists; only the registration is created."""
    from app.datasets.repair import repair_active_dataset
    from app.db.base import new_uuid
    from app.db.models import Dataset, DatasetFile

    orphan = "pr-orphan-ds"
    db.add(
        DatasetFile(
            id=new_uuid(),
            dataset_id=orphan,
            relative_path="operational/calls.csv",
            filename="calls.csv",
            extension=".csv",
            media_type="text/csv",
            file_kind="table",
            size_bytes=10,
            sha256="0" * 64,
        )
    )
    await db.commit()

    report = await repair_active_dataset(db)
    assert report["status"] == "recovered"
    assert report["dataset_id"] == orphan
    row = await db.get(Dataset, orphan)
    assert row is not None and row.is_active is True and row.status == "READY"
    # The investigative row itself was never touched.
    from sqlalchemy import select

    file_rows = (
        (
            await db.execute(
                select(DatasetFile).where(DatasetFile.dataset_id == orphan)
            )
        )
        .scalars()
        .all()
    )
    assert len(file_rows) == 1
    assert file_rows[0].relative_path == "operational/calls.csv"


async def test_repair_reports_honestly_on_empty_database(db):
    from app.datasets.repair import repair_active_dataset

    report = await repair_active_dataset(db)
    assert report["status"] in {"empty", "ok", "reactivated"}
    # Whatever earlier tests left behind, the report must match the registry.
    from app.datasets import registry

    active = await registry.active_dataset(db)
    if report["status"] == "empty":
        assert active is None


# --------------------------------------------------------------------------- #
# 4. Demo-account provisioning
# --------------------------------------------------------------------------- #

async def test_ensure_demo_users_is_idempotent(db):
    from sqlalchemy import select

    from app.datasets.repair import ensure_demo_users
    from app.db.models import User

    first = await ensure_demo_users(db)
    await db.commit()
    second = await ensure_demo_users(db)
    await db.commit()
    assert second == []
    badges = {"DEMO-ADMIN", "DEMO-INVESTIGATOR", "DEMO-VIEWER"}
    assert set(first) <= badges
    rows = (
        (await db.execute(select(User).where(User.badge_number.in_(badges)))).scalars().all()
    )
    by_badge = {u.badge_number: u for u in rows}
    assert set(by_badge) == badges
    roles = {
        by_badge[b].role.value if hasattr(by_badge[b].role, "value") else str(by_badge[b].role)
        for b in badges
    }
    assert roles == {"ADMIN", "INVESTIGATOR", "VIEWER"}
    # Passwords are real hashes, never plaintext.
    for user in by_badge.values():
        assert user.hashed_password.startswith("$argon2")
        assert user.is_active is True


# --------------------------------------------------------------------------- #
# 5. Quick sign-in endpoint
# --------------------------------------------------------------------------- #

def test_demo_login_signs_in_all_three_roles(client):
    expected = {
        "DEMO-ADMIN": "ADMIN",
        "DEMO-INVESTIGATOR": "INVESTIGATOR",
        "DEMO-VIEWER": "VIEWER",
    }
    for badge, role in expected.items():
        response = client.post("/api/v1/auth/demo/login", json={"badge_number": badge})
        assert response.status_code == 200, (badge, response.text)
        payload = response.json()
        assert payload["role"] == role
        assert payload["badge_number"] == badge
        assert payload["access_token"] and payload["refresh_token"]
        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {payload['access_token']}"},
        )
        assert me.status_code == 200 and me.json()["role"] == role


def test_demo_login_rejects_unknown_badge(client):
    response = client.post("/api/v1/auth/demo/login", json={"badge_number": "ROOT"})
    assert response.status_code == 401


def test_demo_login_respects_disabled_flag(client):
    settings = get_settings()
    object.__setattr__(settings, "demo_quick_login_enabled", False)
    try:
        response = client.post(
            "/api/v1/auth/demo/login", json={"badge_number": "DEMO-ADMIN"}
        )
        assert response.status_code == 401
    finally:
        object.__setattr__(settings, "demo_quick_login_enabled", True)


def test_demo_login_respects_lockout(client):
    from app.db.base import utcnow
    from app.db.models import User
    from app.db.session import sync_session

    with sync_session() as session:
        user = (
            session.query(User).filter(User.badge_number == "DEMO-VIEWER").one()
        )
        user.locked_until = utcnow() + timedelta(minutes=5)
        session.commit()
    try:
        response = client.post(
            "/api/v1/auth/demo/login", json={"badge_number": "DEMO-VIEWER"}
        )
        assert response.status_code == 423
    finally:
        with sync_session() as session:
            user = (
                session.query(User).filter(User.badge_number == "DEMO-VIEWER").one()
            )
            user.locked_until = None
            session.commit()


# --------------------------------------------------------------------------- #
# 6. Signed raw-source URL gating (the Viewer bypass)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def manifest_dataset(container):
    """An active dataset with one manifest file, one document and one
    provenance reference — the shape the Source Browser renders.

    Built through the sync session (the fixture itself is synchronous); the
    autouse ``_no_leaked_cases`` cleanup removes the dataset and every
    dataset-owned row afterwards.
    """
    from app.datasets.registry import set_only_active_sync
    from app.db.base import new_uuid
    from app.db.models import (
        Case,
        CaseDocument,
        Dataset,
        DatasetFile,
        SourceReference,
    )
    from app.db.session import sync_session
    from app.domain.enums import CaseStatus, DocumentType
    from app.domain.provenance import content_hash

    payload = b"call_id,msisdn\n1,9876543210\n"

    with sync_session() as session:
        dataset = Dataset(
            id="pr-manifest-ds",
            name="Manifest fixture",
            version="1",
            status="READY",
            is_active=False,
            source_kind="builtin",
            root_path="",
            origin_note="test",
        )
        session.add(dataset)
        session.flush()
        set_only_active_sync(session, dataset)
        case = Case(
            id=new_uuid(),
            case_number=f"PR-MANIFEST-{new_uuid()[:6]}",
            title="Manifest fixture case",
            jurisdiction_id="RJ-JAIPUR",
            dataset_id=dataset.id,
            status=CaseStatus.OPEN,
        )
        session.add(case)
        session.flush()
        doc = CaseDocument(
            id=new_uuid(),
            case_id=case.id,
            dataset_id=dataset.id,
            document_type=DocumentType.CDR,
            filename="calls.csv",
            storage_key="evidence/pr-manifest/calls.csv",
            content_hash=content_hash(payload),
            size_bytes=len(payload),
            mime_type="text/csv",
        )
        session.add(doc)
        session.add(
            DatasetFile(
                id=new_uuid(),
                dataset_id=dataset.id,
                relative_path="operational/calls.csv",
                filename="calls.csv",
                extension=".csv",
                media_type="text/csv",
                file_kind="table",
                size_bytes=len(payload),
                sha256=content_hash(payload),
                doc_id=doc.id,
            )
        )
        ref = SourceReference(
            id=new_uuid(),
            doc_id=doc.id,
            case_id=case.id,
            dataset_id=dataset.id,
            origin_file="operational/calls.csv",
            source_type="csv",
            row_number=1,
        )
        session.add(ref)
        session.commit()
        ids = {
            "dataset_id": dataset.id,
            "case_id": case.id,
            "doc_id": doc.id,
            "ref_id": ref.id,
            "path": "operational/calls.csv",
        }

    # The sources routes read the container's object store under the
    # documents bucket (see _get_object_store_for_sources).
    container.object_store.put(
        container.settings.minio_bucket_documents,
        "operational/calls.csv",
        payload,
        content_type="text/csv",
    )

    yield ids


def test_files_manifest_gates_signed_url_by_role(client, users, manifest_dataset):
    from tests.conftest import auth_headers

    viewer = auth_headers(client, "VIW-0001")
    investigator = auth_headers(client, "INV-0001")
    admin = auth_headers(client, "ADM-0001")

    response = client.get("/api/v1/sources/files", headers=viewer)
    assert response.status_code == 200
    items = response.json()["items"]
    assert items, "manifest fixture produced no items"
    for item in items:
        assert item["download_url"] is None, "VIEWER must not receive signed raw URLs"

    for headers in (investigator, admin):
        response = client.get("/api/v1/sources/files", headers=headers)
        assert response.status_code == 200
        items = response.json()["items"]
        matching = [i for i in items if i["path"] == manifest_dataset["path"]]
        assert matching
        assert matching[0]["download_url"].startswith("/api/v1/sources/raw?path=")


def test_reference_endpoint_gates_raw_url_by_role(client, users, manifest_dataset):
    from tests.conftest import auth_headers

    viewer = auth_headers(client, "VIW-0001")
    investigator = auth_headers(client, "INV-0001")
    url = f"/api/v1/sources/reference/{manifest_dataset['ref_id']}"

    response = client.get(url, headers=viewer)
    assert response.status_code == 200
    assert response.json()["raw_url"] is None

    response = client.get(url, headers=investigator)
    assert response.status_code == 200
    assert response.json()["raw_url"].startswith("/api/v1/sources/raw?path=")


def test_raw_bytes_rejected_for_viewer_bearer(client, users, manifest_dataset):
    """Even with a valid token, a VIEWER cannot stream raw source bytes."""
    from tests.conftest import auth_headers

    viewer = auth_headers(client, "VIW-0001")
    response = client.get(
        "/api/v1/sources/raw",
        params={"path": manifest_dataset["path"]},
        headers=viewer,
    )
    assert response.status_code in (403, 404)


def test_signed_url_from_manifest_works_for_investigator(client, users, manifest_dataset):
    """The gate must not break the legitimate flow: an INVESTIGATOR's signed
    URL still streams the original bytes."""
    from tests.conftest import auth_headers

    investigator = auth_headers(client, "INV-0001")
    listing = client.get("/api/v1/sources/files", headers=investigator).json()
    item = next(i for i in listing["items"] if i["path"] == manifest_dataset["path"])
    assert item["download_url"]
    response = client.get(item["download_url"])
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"call_id,msisdn")
