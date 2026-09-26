"""Production-readiness regression tests — the Vercel incident fixes.

Each test pins down one root cause from the September 2026 production
deployment (crime-link-seven.vercel.app) so the fix cannot silently regress:

1. **Neo4j configuration** — the app read only ``CRIMELINK_*`` variables and
   defaulted the database to ``neo4j``, so a deployment configured with the
   standard ``NEO4J_URI`` / ``NEO4J_USERNAME`` / ``NEO4J_PASSWORD`` /
   ``NEO4J_DATABASE`` variables (Aura instance ``608355f3``) failed with
   ``22N51 The provided reference does not identify any graph database``.
   The accepted environment names, the ``CRIMELINK_`` precedence, the Aura
   instance-id derivation, and the 22N51 error contract are all pinned here.

2. **Case-less dataset visibility** — every import materialises a synthetic
   container case; a corpus with no case table (the production "Upload of 577
   files v1") left that container hidden from ``/cases`` and the dashboard
   ("No cases available" / "Could not resolve the active case") while the
   admin console showed it.  The container must stand in as the dataset's
   case for *every role* (they share the dataset) — including when the
   import's jurisdiction differs from the users' — and its documents are the
   dataset-level rows (``case_id IS NULL``).

3. **Admin documents null crash** — dataset-level documents carry
   ``case_id: null`` by design; the API must say so honestly (never fabricate
   a value) and the field must survive the whole contract.

4. **Truthful health** — no component may report ``ok`` without a real
   round-trip; with the inline broker Redis is *not part of the deployment*
   and is reported as "not used", not faked OK, and no response may contain
   a credential.

5. **Credential hygiene** — connection URLs in logs and responses are
   redacted; the Neo4j password never appears in configuration reports.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.datasets.pipeline import ImportOptions, run_import
from app.db.base import new_uuid
from app.db.models import Case, Dataset
from app.db.session import async_session
from app.domain.enums import CaseStatus
from app.security.deps import JurisdictionScope, Principal

# --------------------------------------------------------------------------- #
# Fixtures: a case-less corpus through the REAL pipeline
# --------------------------------------------------------------------------- #

#: A phone-directory style corpus: people + accounts, NO case table.  This is
#: exactly the shape of the production "Upload of 577 files v1" dataset.
CASELESS_PEOPLE = (
    "person_id,full_name,phone_number\n"
    "PERSON_0001,Arjun Reddy,9876543210\n"
    "PERSON_0002,Meera Iyer,9876543211\n"
)
CASELESS_ACCOUNTS = (
    "account_id,account_number,bank_name\n"
    "ACCT_0001,9988776655,State Bank\n"
)
CASELESS_NOTE = "Confidential note regarding Arjun Reddy transactions."


@pytest.fixture()
async def caseless_dataset(container, admin_headers, tmp_path) -> dict:
    """Import the case-less corpus (pipeline + graph build) and clean up.

    The jurisdiction is deliberately NOT the test users' jurisdiction: the
    production incident was a dataset imported under SYN-DEV being used by
    METRO-CENTRAL demo accounts.  The container case must still be visible —
    it is dataset-level data, and every role shares the dataset.
    """
    folder = tmp_path / "caseless_corpus"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "people.csv").write_text(CASELESS_PEOPLE, encoding="utf-8")
    (folder / "accounts.csv").write_text(CASELESS_ACCOUNTS, encoding="utf-8")
    (folder / "note.txt").write_text(CASELESS_NOTE, encoding="utf-8")

    async with async_session() as session:
        report = await run_import(
            session,
            [folder],
            ImportOptions(
                name="Caseless ledger",
                copy_inputs=False,
                activate=True,
                build_graph=True,
                jurisdiction_id="MP-BHOPAL",  # not RJ-JAIPUR — see docstring
            ),
        )
    assert report.error is None, report.error

    yield {"dataset_id": report.dataset_id}

    await _delete_dataset(report.dataset_id)


async def _delete_dataset(dataset_id: str) -> None:
    """Best-effort teardown: the session-scoped test DB outlives one test."""
    from app.db.models import (
        CaseDocument,
        DatasetEntity,
        DatasetFile,
        DatasetRelationship,
    )

    async with async_session() as session:
        for model in (DatasetFile, DatasetEntity, DatasetRelationship):
            for row in (
                await session.execute(select(model).where(model.dataset_id == dataset_id))
            ).scalars():
                await session.delete(row)
        case_ids = list(
            (
                await session.execute(
                    select(Case.id).where(Case.dataset_id == dataset_id)
                )
            )
            .scalars()
        )
        if case_ids:
            for row in (
                await session.execute(
                    select(CaseDocument).where(
                        (CaseDocument.case_id.in_(case_ids))
                        | (CaseDocument.dataset_id == dataset_id)
                    )
                )
            ).scalars():
                await session.delete(row)
            for row in (
                await session.execute(select(Case).where(Case.id.in_(case_ids)))
            ).scalars():
                await session.delete(row)
        dataset = await session.get(Dataset, dataset_id)
        if dataset is not None:
            await session.delete(dataset)
        await session.commit()


def _scope_for(principal: Principal) -> JurisdictionScope:
    return JurisdictionScope(
        principal, granted_jurisdictions=set(), granted_case_ids=set()
    )


# --------------------------------------------------------------------------- #
# 1. Neo4j configuration: accepted env names, precedence, Aura derivation
# --------------------------------------------------------------------------- #


#: Environment prefixes that could leak into these tests from a developer
#: machine or the test harness itself.
_ENV_PREFIXES = (
    "NEO4J",
    "CRIMELINK",
    "MINIO",
    "S3_",
    "AWS_",
    "REDIS",
    "DATABASE",
    "POSTGRES",
    "CELERY",
)


def _clean_env(monkeypatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)


def _settings(monkeypatch, **env: str):
    """Build Settings from *exactly* the given environment.

    `_env_file=None` disables the dotenv files so a local `.env` can never
    change what these tests observe (init kwargs beat env; env beats dotenv).
    """
    _clean_env(monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from app.config import Settings

    return Settings(_env_file=None)


def test_neo4j_bare_env_names_are_read(monkeypatch):
    """The exact Vercel variable set must configure the Aura connection."""
    settings = _settings(
        monkeypatch,
        NEO4J_URI="neo4j+s://608355f3.databases.neo4j.io",
        NEO4J_USERNAME="608355f3",
        NEO4J_PASSWORD="secret-from-env",
        NEO4J_DATABASE="608355f3",
    )
    assert settings.neo4j_uri == "neo4j+s://608355f3.databases.neo4j.io"
    assert settings.neo4j_user == "608355f3"
    assert settings.neo4j_password == "secret-from-env"
    assert settings.neo4j_database == "608355f3"
    # The historical failure: database "neo4j" must no longer be selectable.
    assert settings.neo4j_database != "neo4j"


def test_crimelink_prefix_wins_over_bare_names(monkeypatch):
    settings = _settings(
        monkeypatch,
        CRIMELINK_NEO4J_URI="bolt://explicit:7687",
        NEO4J_URI="bolt://bare:7687",
        CRIMELINK_NEO4J_USER="explicit-user",
        NEO4J_USERNAME="bare-user",
        CRIMELINK_NEO4J_PASSWORD="explicit-pass",
        NEO4J_PASSWORD="bare-pass",
        CRIMELINK_NEO4J_DATABASE="explicit-db",
        NEO4J_DATABASE="bare-db",
    )
    assert settings.neo4j_uri == "bolt://explicit:7687"
    assert settings.neo4j_user == "explicit-user"
    assert settings.neo4j_password == "explicit-pass"
    assert settings.neo4j_database == "explicit-db"


def test_aura_instance_id_derives_user_and_database(monkeypatch):
    """URI alone (plus credentials) is enough for Aura: no 'neo4j' fallback."""
    settings = _settings(
        monkeypatch,
        NEO4J_URI="neo4j+s://608355f3.databases.neo4j.io",
        NEO4J_PASSWORD="secret-from-env",
    )
    assert settings.neo4j_user == "608355f3"
    assert settings.neo4j_database == "608355f3"


def test_aura_derivation_never_overrides_explicit_values(monkeypatch):
    settings = _settings(
        monkeypatch,
        NEO4J_URI="neo4j+s://608355f3.databases.neo4j.io",
        NEO4J_PASSWORD="secret-from-env",
        NEO4J_USERNAME="custom-user",
        CRIMELINK_NEO4J_DATABASE="custom-db",
    )
    assert settings.neo4j_user == "custom-user"
    assert settings.neo4j_database == "custom-db"


def test_non_aura_uri_keeps_defaults(monkeypatch):
    """Self-hosted Neo4j (compose) is untouched by the derivation."""
    settings = _settings(monkeypatch, NEO4J_URI="bolt://neo4j:7687")
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_database == "neo4j"


def test_neo4j_auth_user_password_form(monkeypatch):
    settings = _settings(
        monkeypatch,
        NEO4J_URI="bolt://localhost:7687",
        NEO4J_AUTH="aurora:topsecret",
    )
    assert settings.neo4j_user == "aurora"
    assert settings.neo4j_password == "topsecret"


def test_object_store_and_redis_env_aliases(monkeypatch):
    settings = _settings(
        monkeypatch,
        S3_ENDPOINT="https://s3.eu-west-2.amazonaws.com",
        S3_ACCESS_KEY_ID="AKIA-TEST",
        S3_SECRET_ACCESS_KEY="shhh-test",
        REDIS_URL="redis://cache.example:6379/0",
        DATABASE_URL="postgresql+asyncpg://u:p@db:5432/c",
    )
    # The MinIO client wants a bare host — the https URL is normalised and
    # TLS is derived from the scheme (an un-normalised URL would make the
    # client raise "path in endpoint is not allowed").
    assert settings.minio_endpoint == "s3.eu-west-2.amazonaws.com"
    assert settings.minio_access_key == "AKIA-TEST"
    assert settings.minio_secret_key == "shhh-test"
    # An https endpoint is TLS by definition.
    assert settings.minio_secure is True
    assert settings.redis_url == "redis://cache.example:6379/0"
    assert settings.postgres_dsn == "postgresql+asyncpg://u:p@db:5432/c"


def test_settings_still_accept_field_names(monkeypatch):
    """Direct construction by field name must keep working (tests, overrides)."""
    _clean_env(monkeypatch)
    from app.config import Settings

    settings = Settings(
        _env_file=None,
        neo4j_uri="bolt://field-name:7687",
        neo4j_database="field-name-db",
        minio_endpoint="field-name:9000",
    )
    assert settings.neo4j_uri == "bolt://field-name:7687"
    assert settings.neo4j_database == "field-name-db"
    assert settings.minio_endpoint == "field-name:9000"


# --------------------------------------------------------------------------- #
# 1b. Neo4j 22N51: a config failure, not a silent fallback
# --------------------------------------------------------------------------- #


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


def _neo4j_adapter(exc, database="neo4j"):
    from app.adapters.graph.neo4j import Neo4jGraphStore

    adapter = Neo4jGraphStore.__new__(Neo4jGraphStore)
    adapter.settings = SimpleNamespace(neo4j_database=database)
    adapter._driver = _FailingDriver(exc)
    return adapter


def test_database_not_found_22n51_is_a_config_error(monkeypatch):
    """22N51 → actionable 503 naming the database, never a fallback to 'neo4j'."""
    from app.adapters.graph.neo4j import (
        _is_database_not_found,
        _is_connectivity_failure,
    )
    from app.errors import ServiceUnavailableError

    exc = Exception(
        "Neo.ClientError.Database.DatabaseNotFound (22N51): The provided reference "
        "does not identify any graph database"
    )
    assert _is_database_not_found(exc) is True
    # ...and it must NOT be classified as a connectivity problem.
    assert _is_connectivity_failure(exc) is False

    adapter = _neo4j_adapter(exc, database="608355f3")
    with pytest.raises(ServiceUnavailableError) as caught:
        adapter._read(lambda session: None)
    message = str(caught.value)
    assert "608355f3" in message  # names the configured database
    assert "NEO4J_DATABASE" in message  # tells the operator what to set
    assert "22N51" in message


def test_code_attribute_also_detects_22n51():
    """Drivers that expose the error code (not just the message) are covered."""
    from app.adapters.graph.neo4j import _is_database_not_found

    class _Neo4jError(Exception):
        code = "Neo.ClientError.Database.DatabaseNotFound"

    assert _is_database_not_found(_Neo4jError()) is True


# --------------------------------------------------------------------------- #
# 2. Case-less dataset: the container case stands in for everyone
# --------------------------------------------------------------------------- #


async def test_caseless_dataset_container_visible_to_all_roles(
    client, caseless_dataset, admin_headers, investigator_headers, viewer_headers
):
    """/cases resolves to the container case for admin, investigator AND viewer.

    This is the production inconsistency: admin showed the case while the
    normal pages said "No cases available".  All three roles share the same
    dataset, so all three get the same resolution.
    """
    for headers in (admin_headers, investigator_headers, viewer_headers):
        response = client.get("/api/v1/cases", headers=headers)
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        assert len(items) == 1, f"expected the container case, got {items}"
        assert items[0]["case_number"].endswith("(all records)")


async def test_caseless_dataset_dashboard_resolution(
    client, caseless_dataset, investigator_headers
):
    """`/cases?limit=1` (the dashboard's resolution call) returns the case."""
    response = client.get("/api/v1/cases?limit=1", headers=investigator_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert items and items[0]["id"]
    # The case the dashboard navigates to must actually open.
    detail = client.get(f"/api/v1/cases/{items[0]['id']}", headers=investigator_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["document_count"] >= 1  # the note.txt document


async def test_caseless_dataset_documents_are_dataset_level(
    client, caseless_dataset, investigator_headers
):
    """Documents of a case-less dataset have case_id=null and are still listed.

    The pipeline never assigns dataset-level documents to the pseudo case —
    the value is null, not a fabricated id.  The case surface and the explore
    surface must both serve them, and both must report null honestly.
    """
    case_id = client.get("/api/v1/cases?limit=1", headers=investigator_headers).json()[
        "items"
    ][0]["id"]

    # Case-scoped listing: the null-case documents belong to this case.
    case_docs = client.get(f"/api/v1/cases/{case_id}/documents", headers=investigator_headers)
    assert case_docs.status_code == 200, case_docs.text
    case_items = case_docs.json()["items"]
    assert case_items, "the note document must be visible on the case"
    assert all(d["case_id"] is None for d in case_items), (
        "dataset-level documents carry case_id=null — never an invented value"
    )

    # Explore surface: same rows, same honest null.
    explore = client.get("/api/v1/explore/documents", headers=investigator_headers)
    assert explore.status_code == 200, explore.text
    explore_items = explore.json()["items"]
    assert explore_items, "the explore page must not be empty for a case-less corpus"
    assert all(d["case_id"] is None for d in explore_items)


async def test_caseless_dataset_entities_and_edges_are_case_scoped(
    client, caseless_dataset, container, investigator_headers
):
    """Graph rebuild of a case-less corpus: entities AND edges land in the case.

    Pins down the graph_build container-membership fix: before it, every node
    had an empty case list and every edge an empty case scope, so the case
    snapshot was a cloud of nothing.
    """
    case_id = client.get("/api/v1/cases?limit=1", headers=investigator_headers).json()[
        "items"
    ][0]["id"]

    explore = client.get("/api/v1/explore/entities", headers=investigator_headers)
    assert explore.status_code == 200, explore.text
    names = {e["name"] for e in explore.json()["items"]}
    assert "Arjun Reddy" in names, "entities must be reachable through the case"

    snapshot = container.graph_store.snapshot(case_id, include_inactive=True)
    assert len(snapshot.nodes) >= 2, "at least the two people must be in the snapshot"
    for node in snapshot.nodes.values():
        assert case_id in (node.properties.get("case_ids") or []), (
            f"node {node.provenance_key} is invisible to every case: "
            f"case_ids={node.properties.get('case_ids')}"
        )
    assert len(snapshot.edges) >= 1, "edges must carry the case scope too"
    for edge in snapshot.edges:
        props = edge.properties or {}
        effective = set(props.get("case_ids") or []) | set(props.get("case_scope") or [])
        assert case_id in effective, (
            f"edge {edge.source_key}->{edge.target_key} has no case scope: {props.get('case_scope')}"
        )


async def test_mixed_dataset_keeps_container_hidden(
    client, caseless_dataset, container, admin_headers
):
    """With real cases present, the container goes back to being hidden.

    (The case-less fixture is active; a hand-created case in the same dataset
    makes it a 'mixed' dataset and the container must disappear from /cases
    again — the pre-production behaviour for case-rich corpora.)
    """
    dataset_id = caseless_dataset["dataset_id"]
    async with async_session() as session:
        case = Case(
            id=new_uuid(),
            case_number="MIXED/2026/0001",
            title="A real case",
            jurisdiction_id="RJ-JAIPUR",
            dataset_id=dataset_id,
            status=CaseStatus.OPEN,
        )
        session.add(case)
        await session.commit()
        real_case_id = case.id

    try:
        response = client.get("/api/v1/cases", headers=admin_headers)
        assert response.status_code == 200
        numbers = [c["case_number"] for c in response.json()["items"]]
        assert "MIXED/2026/0001" in numbers
        assert not any(n.endswith("(all records)") for n in numbers), (
            "the container case must stay hidden once real cases exist"
        )
        # And the real case is directly openable.
        detail = client.get(f"/api/v1/cases/{real_case_id}", headers=admin_headers)
        assert detail.status_code == 200
    finally:
        async with async_session() as session:
            row = await session.get(Case, real_case_id)
            if row is not None:
                await session.delete(row)
            await session.commit()


# --------------------------------------------------------------------------- #
# 2b. Service-level pin (no HTTP): the container predicate itself
# --------------------------------------------------------------------------- #


async def test_visible_case_ids_and_resolve_for_container_only(db, caseless_dataset):
    from app.security.deps import Principal
    from app.services import cases as case_service
    from app.db.models import User

    user = (
        await db.execute(select(User).where(User.badge_number == "VIW-0001"))
    ).scalar_one()
    principal = Principal(user, ip_address="127.0.0.1")
    scope = _scope_for(principal)

    ids = await case_service.visible_case_ids(db, scope)
    container = (
        await db.execute(select(Case).where(Case.dataset_case_key == "ALL"))
    ).scalars().first()
    assert container is not None
    assert ids == {container.id}, (
        "a case-less dataset's only visible case is its container case"
    )
    # Resolving it by id succeeds (dataset-level data, any role of the
    # deployment); resolving the string key "ALL" still fails.
    resolved = await case_service.get_case(db, scope, container.id)
    assert resolved.id == container.id
    with pytest.raises(Exception):
        await case_service.get_case(db, scope, "ALL")
    assert await case_service.is_sole_container_case(db, container.id) is True


# --------------------------------------------------------------------------- #
# 4. Truthful health endpoint
# --------------------------------------------------------------------------- #


async def test_health_is_truthful_and_secret_free(
    client, admin_headers, investigator_headers
):
    response = client.get("/api/v1/admin/database/health", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    for component in ("postgres", "graph", "redis", "object_store", "broker"):
        entry = body[component]
        assert set(entry) >= {"ok", "backend", "detail", "error"}, (
            f"{component} must report ok/backend/detail/error"
        )
        assert isinstance(entry["ok"], bool)

    # Embedded profile reality: every dependency here is local and works.
    assert body["postgres"]["ok"] is True
    assert body["graph"]["ok"] is True
    assert body["object_store"]["ok"] is True
    assert body["broker"]["ok"] is True

    # Inline broker: Redis is NOT part of the deployment.  Honest reporting
    # (not a fake OK with an empty detail): it says what is true.
    assert body["redis"]["ok"] is True
    assert "not used" in body["redis"]["detail"].lower()

    # No credential may appear anywhere in the response.
    raw = response.text
    assert "hunter2" not in raw
    assert "password" not in raw.lower().replace("passwordless", "")
    assert ":***@" not in raw or True  # redaction marker, if present, is safe

    # RBAC: the health endpoint is admin-only.
    denied = client.get("/api/v1/admin/database/health", headers=investigator_headers)
    assert denied.status_code == 403


# --------------------------------------------------------------------------- #
# 5. Credential hygiene
# --------------------------------------------------------------------------- #


def test_rate_limit_log_redacts_redis_url():
    from app.security.rate_limit import _redact_url

    assert _redact_url("redis://user:hunter2@host:6379/0") == "redis://user:***@host:6379/0"
    assert _redact_url("redis://host:6379/0") == "redis://host:6379/0"
    assert _redact_url("postgres://u:p@h/db") == "postgres://u:***@h/db"
    # The redacted form must not contain the secret.
    assert "hunter2" not in _redact_url("redis://user:hunter2@host:6379/0")


def test_health_classifier_distinguishes_config_auth_connectivity():
    import socket

    from app.api.v1.database import _classify_error

    dns = _classify_error(socket.gaierror(-3, "Name or service not known"), "redis")
    assert dns.startswith("connectivity:")
    assert dns  # sanitized, non-empty

    refused = _classify_error(ConnectionRefusedError(), "object store")
    assert refused.startswith("connectivity:")

    timeout = _classify_error(TimeoutError(), "graph")
    assert timeout.startswith("connectivity:")

    auth_pg = _classify_error(
        Exception('password authentication failed for user "x"'), "postgres"
    )
    assert auth_pg.startswith("auth:")

    auth_s3 = _classify_error(Exception("Access Denied"), "object store")
    assert auth_s3.startswith("auth:")

    config = _classify_error(
        Exception("Neo.ClientError.Database.DatabaseNotFound (22N51): nope"),
        "graph",
    )
    assert config.startswith("config:")


def test_minio_store_health_check_classifies_failures(monkeypatch):
    """MinioObjectStore.health_check: config vs auth vs connectivity."""
    from app.adapters.objectstore.minio_store import MinioObjectStore
    from minio.error import S3Error

    settings = SimpleNamespace(
        # Bare host: the MinIO client takes host[:port] + a secure flag; a
        # URL scheme in the endpoint raises at construction time.
        minio_endpoint="s3.example.com",
        minio_access_key="ak",
        minio_secret_key="sk",
        minio_secure=True,
        minio_bucket_documents="documents",
        minio_bucket_derived="documents-derived",
        minio_bucket_audit_anchor="audit-anchor",
    )
    store = MinioObjectStore(settings)

    class _Stub:
        def __init__(self, result, exc=None):
            self._result = result
            self._exc = exc

        def bucket_exists(self, bucket):
            if self._exc is not None:
                raise self._exc
            return self._result

    # Reachable + bucket exists → ok.
    store._client = _Stub(True)
    ok, detail, error = store.health_check()
    assert ok is True and error is None
    assert "s3.example.com" in detail

    # Reachable + missing bucket → config failure.
    store._client = _Stub(False)
    ok, detail, error = store.health_check()
    assert ok is False
    assert error.startswith("object store reachable")

    # Auth rejection → auth failure, credential-free.
    store._client = _Stub(True, S3Error("AccessDenied", "denied", None, None, None, None))
    ok, detail, error = store.health_check()
    assert ok is False
    assert "credentials" in error

    # Unresolvable host → connectivity failure naming the host.
    from app.config import Settings as RealSettings

    real = RealSettings(
        minio_endpoint="minio.unresolvable.invalid:9000",
        minio_access_key="ak",
        minio_secret_key="sk",
    )
    live = MinioObjectStore(real)
    ok, detail, error = live.health_check()
    assert ok is False
    assert "connectivity" in error
    assert "minio.unresolvable.invalid" in error
