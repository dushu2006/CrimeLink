"""Runtime context detection and infrastructure endpoint resolution.

These tests pin the behaviour that makes one ``.env`` work in three places:

* native Python on a developer machine (``python run.py`` on Windows) — Compose
  service hostnames such as ``postgres`` do not resolve, so they are rewritten
  to ``localhost`` plus the published host port;
* inside a container on the Compose network — service hostnames are
  authoritative and must survive untouched, or Compose networking breaks;
* production — endpoints are used exactly as configured and the existing
  fail-closed validation still applies.

Rewriting may only ever change *where* a service is reached, never *which*
backend is used: PostgreSQL must stay PostgreSQL in every context.
"""

from __future__ import annotations

import socket

import pytest

from app import runtime
from app.config import Settings

COMPOSE_DSN_SYNC = "postgresql+psycopg2://crimelink:s3cret@postgres:5432/crimelink"
COMPOSE_DSN_ASYNC = "postgresql+asyncpg://crimelink:s3cret@postgres:5432/crimelink"

#: Values that satisfy the production fail-closed validator, so these tests
#: exercise endpoint resolution rather than tripping security validation.
PRODUCTION_VALUES = {
    "profile": "production",
    "environment": "production",
    "debug": False,
    "secret_key": "test-secret-key-0123456789abcdefghijklmnop",
    "cors_origins": ["http://localhost"],
    "neo4j_password": "neo4j-test-secret",
    "minio_secret_key": "minio-test-secret",
    "relational_backend": "auto",
    "graph_backend": "auto",
    "object_store_backend": "auto",
    "broker_backend": "auto",
    "postgres_dsn": COMPOSE_DSN_ASYNC,
    "postgres_dsn_sync": COMPOSE_DSN_SYNC,
    "neo4j_uri": "bolt://neo4j:7687",
    "minio_endpoint": "minio:9000",
    "redis_url": "redis://redis:6379/0",
    "celery_broker_url": "redis://redis:6379/1",
    "celery_result_backend": "redis://redis:6379/2",
}

#: Environment variables pinned for every test in this module.  A developer's
#: ``.env`` is read by ``Settings`` (dotenv has lower priority than the real
#: environment but higher than field defaults), so without this a local
#: ``CRIMELINK_RELATIONAL_BACKEND=postgres`` would change what these tests
#: observe.  Environment variables beat the dotenv file, which keeps the module
#: hermetic on any machine.
HERMETIC_ENV = {
    "CRIMELINK_PROFILE": "embedded",
    "CRIMELINK_ENVIRONMENT": "dev",
    "CRIMELINK_DEBUG": "false",
    "CRIMELINK_RUNTIME_CONTEXT": "auto",
    "CRIMELINK_INFRA_HOST": "localhost",
    "CRIMELINK_RELATIONAL_BACKEND": "auto",
    "CRIMELINK_GRAPH_BACKEND": "auto",
    "CRIMELINK_OBJECT_STORE_BACKEND": "auto",
    "CRIMELINK_BROKER_BACKEND": "auto",
    "CRIMELINK_POSTGRES_HOST_PORT": "5432",
    "CRIMELINK_NEO4J_HOST_PORT": "7687",
    "CRIMELINK_MINIO_HOST_PORT": "9000",
    "CRIMELINK_REDIS_HOST_PORT": "6379",
}


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in HERMETIC_ENV.items():
        monkeypatch.setenv(key, value)


def make_settings(**overrides) -> Settings:
    """Build Settings from explicit values (init kwargs beat env and .env)."""
    values = dict(PRODUCTION_VALUES)
    values.update(overrides)
    return Settings(**values)


# --------------------------------------------------------------------------- #
# 1. Context detection
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("context", ["host", "docker", "production"])
def test_explicit_context_always_wins(context: str) -> None:
    """An operator-set CRIMELINK_RUNTIME_CONTEXT is never second-guessed."""
    assert runtime.resolve_runtime_context(
        explicit=context, profile="production", environment="production", in_container=False
    ) == context
    assert runtime.resolve_runtime_context(
        explicit=context, profile="embedded", environment="dev", in_container=True
    ) == context


def test_auto_detection_is_case_insensitive_and_ignores_auto() -> None:
    assert runtime.resolve_runtime_context(explicit="HOST", in_container=False) == "host"
    assert runtime.resolve_runtime_context(explicit="auto", in_container=False) == "host"
    assert runtime.resolve_runtime_context(explicit="", in_container=False) == "host"


def test_invalid_context_is_rejected() -> None:
    with pytest.raises(ValueError, match="CRIMELINK_RUNTIME_CONTEXT"):
        runtime.resolve_runtime_context(explicit="windows", in_container=False)


def test_native_execution_is_host_context() -> None:
    """python run.py on a workstation: no container markers -> host."""
    assert runtime.resolve_runtime_context(profile="production", in_container=False) == "host"
    assert runtime.resolve_runtime_context(profile="embedded", in_container=False) == "host"


def test_container_execution_is_docker_or_production() -> None:
    assert runtime.resolve_runtime_context(profile="embedded", in_container=True) == "docker"
    assert runtime.resolve_runtime_context(profile="production", in_container=True) == "production"
    assert (
        runtime.resolve_runtime_context(profile="embedded", environment="production", in_container=True)
        == "production"
    )


def test_detection_is_deterministic_and_side_effect_free() -> None:
    """Repeated detection gives the same answer and agrees with the resolver."""
    in_container = runtime.running_in_container()
    assert isinstance(in_container, bool)
    assert runtime.running_in_container() == in_container
    # The suite may run natively or inside CI containers, so assert the contract
    # rather than one particular answer: detection feeds the resolver exactly.
    expected = runtime.CONTEXT_DOCKER if in_container else runtime.CONTEXT_HOST
    assert runtime.resolve_runtime_context(profile="embedded", environment="dev") == expected


# --------------------------------------------------------------------------- #
# 2. Endpoint resolution primitives
# --------------------------------------------------------------------------- #

def test_host_context_rewrites_compose_service_names() -> None:
    resolution = runtime.resolve_service_endpoint(
        COMPOSE_DSN_SYNC, "postgres", context="host", field="postgres_dsn_sync"
    )
    assert resolution.rewritten is True
    assert resolution.host == "localhost"
    assert resolution.port == 5432
    assert resolution.value == "postgresql+psycopg2://crimelink:s3cret@localhost:5432/crimelink"


def test_rewrite_preserves_credentials_database_and_query() -> None:
    dsn = "postgresql+asyncpg://user:p%40ss@postgres:5432/crimelink?ssl=require"
    resolution = runtime.resolve_service_endpoint(dsn, "postgres", context="host")
    assert resolution.value == "postgresql+asyncpg://user:p%40ss@localhost:5432/crimelink?ssl=require"


def test_bare_host_port_endpoint_is_rewritten() -> None:
    """MinIO is configured as ``host:port`` with no scheme."""
    resolution = runtime.resolve_service_endpoint("minio:9000", "minio", context="host")
    assert resolution.rewritten is True
    assert resolution.value == "localhost:9000"


@pytest.mark.parametrize("context", ["docker", "production"])
def test_container_contexts_never_rewrite(context: str) -> None:
    """Renaming postgres -> localhost globally would break Compose networking."""
    for value, service in (
        (COMPOSE_DSN_SYNC, "postgres"),
        ("bolt://neo4j:7687", "neo4j"),
        ("minio:9000", "minio"),
        ("redis://redis:6379/0", "redis"),
    ):
        resolution = runtime.resolve_service_endpoint(value, service, context=context)
        assert resolution.rewritten is False
        assert resolution.value == value


@pytest.mark.parametrize(
    "value",
    [
        "postgresql+psycopg2://crimelink:s3cret@localhost:5432/crimelink",
        "postgresql+psycopg2://crimelink:s3cret@127.0.0.1:5432/crimelink",
        "postgresql+psycopg2://crimelink:s3cret@db.internal.example.com:5432/crimelink",
        "postgresql+psycopg2://crimelink:s3cret@10.0.0.5:5432/crimelink",
    ],
)
def test_host_context_leaves_reachable_hostnames_alone(value: str) -> None:
    """Only Compose service names are rewritten — a real hostname is respected."""
    resolution = runtime.resolve_service_endpoint(value, "postgres", context="host")
    assert resolution.rewritten is False
    assert resolution.value == value


def test_published_host_port_override_wins() -> None:
    """A stack published on 15432 is reached on 15432, not on an assumed port."""
    resolution = runtime.resolve_service_endpoint(
        COMPOSE_DSN_SYNC, "postgres", context="host", host_port=15432
    )
    assert resolution.value == "postgresql+psycopg2://crimelink:s3cret@localhost:15432/crimelink"

    from_env = runtime.resolve_service_endpoint(
        COMPOSE_DSN_SYNC,
        "postgres",
        context="host",
        env={"CRIMELINK_POSTGRES_HOST_PORT": "25432"},
    )
    assert from_env.port == 25432

    assert runtime.host_port_for("postgres", env={}) == 5432
    assert runtime.host_port_for("neo4j", env={}) == 7687
    assert runtime.host_port_for("minio", env={}) == 9000
    assert runtime.host_port_for("redis", env={}) == 6379


def test_custom_infra_host_is_honoured() -> None:
    resolution = runtime.resolve_service_endpoint(
        COMPOSE_DSN_SYNC, "postgres", context="host", infra_host="192.168.1.50"
    )
    assert resolution.host == "192.168.1.50"
    assert resolution.value.endswith("@192.168.1.50:5432/crimelink")


def test_split_endpoint_handles_urls_and_bare_endpoints() -> None:
    assert runtime.split_endpoint("bolt://neo4j:7687") == ("neo4j", 7687)
    assert runtime.split_endpoint("minio:9000") == ("minio", 9000)
    assert runtime.split_endpoint("postgresql+asyncpg://u:p@postgres:5432/db") == ("postgres", 5432)
    assert runtime.split_endpoint("redis://redis:6379/2") == ("redis", 6379)
    assert runtime.split_endpoint("localhost") == ("localhost", None)
    assert runtime.split_endpoint("localhost", 5432) == ("localhost", 5432)
    assert runtime.split_endpoint("") == ("", None)


# --------------------------------------------------------------------------- #
# 3. Backend selection — the context never downgrades a backend
# --------------------------------------------------------------------------- #

def test_auto_backends_follow_the_profile() -> None:
    assert runtime.effective_backends(profile="production") == {
        "relational": "postgres",
        "graph": "neo4j",
        "object_store": "minio",
        "broker": "celery",
    }
    assert runtime.effective_backends(profile="embedded") == {
        "relational": "sqlite",
        "graph": "embedded",
        "object_store": "local",
        "broker": "inline",
    }


def test_explicit_backend_choice_is_honoured_on_the_embedded_profile() -> None:
    """Native Python against real PostgreSQL, embedded adapters elsewhere."""
    backends = runtime.effective_backends(profile="embedded", relational="postgres")
    assert backends["relational"] == "postgres"
    assert backends["graph"] == "embedded"
    assert runtime.required_services(backends) == ["postgres"]


def test_required_services_order_is_postgres_neo4j_minio_redis() -> None:
    assert runtime.required_services(runtime.effective_backends(profile="production")) == [
        "postgres",
        "neo4j",
        "minio",
        "redis",
    ]
    assert runtime.required_services(runtime.effective_backends(profile="embedded")) == []


def test_host_runtime_keeps_postgres_mandatory() -> None:
    """The rewrite must never turn PostgreSQL into SQLite or an in-memory store."""
    settings = make_settings(runtime_context="host")
    assert settings.effective_relational_backend == "postgres"
    assert settings.postgres_dsn_sync.startswith("postgresql+psycopg2://")
    assert settings.postgres_dsn.startswith("postgresql+asyncpg://")
    assert settings.effective_object_store_backend == "minio"
    assert settings.effective_graph_backend == "neo4j"
    assert settings.effective_broker_backend == "celery"
    assert settings.required_infrastructure == ["postgres", "neo4j", "minio", "redis"]


# --------------------------------------------------------------------------- #
# 4. Settings integration
# --------------------------------------------------------------------------- #

def test_settings_rewrite_every_service_endpoint_in_host_context() -> None:
    settings = make_settings(runtime_context="host")
    assert settings.resolved_runtime_context == "host"
    assert settings.postgres_dsn_sync == "postgresql+psycopg2://crimelink:s3cret@localhost:5432/crimelink"
    assert settings.postgres_dsn == "postgresql+asyncpg://crimelink:s3cret@localhost:5432/crimelink"
    assert settings.neo4j_uri == "bolt://localhost:7687"
    assert settings.minio_endpoint == "localhost:9000"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.celery_broker_url == "redis://localhost:6379/1"
    assert settings.celery_result_backend == "redis://localhost:6379/2"
    assert settings.postgres_endpoint == ("localhost", 5432)
    assert settings.neo4j_endpoint == ("localhost", 7687)
    assert settings.minio_endpoint_address == ("localhost", 9000)
    assert settings.redis_endpoint == ("localhost", 6379)
    assert len(settings.endpoint_rewrites) == 7


@pytest.mark.parametrize("context", ["docker", "production"])
def test_settings_keep_compose_hostnames_in_container_contexts(context: str) -> None:
    settings = make_settings(runtime_context=context)
    assert settings.resolved_runtime_context == context
    assert settings.postgres_dsn_sync == COMPOSE_DSN_SYNC
    assert settings.postgres_dsn == COMPOSE_DSN_ASYNC
    assert settings.neo4j_uri == "bolt://neo4j:7687"
    assert settings.minio_endpoint == "minio:9000"
    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.postgres_endpoint == ("postgres", 5432)
    assert settings.endpoint_rewrites == []


def test_auto_context_follows_detection_and_rewrites_only_on_the_host() -> None:
    """``auto`` is not a fourth behaviour: it is exactly the detected context."""
    settings = make_settings(runtime_context="auto")
    expected = runtime.resolve_runtime_context(
        explicit="auto", profile=settings.profile, environment=settings.environment
    )
    assert settings.resolved_runtime_context == expected
    if expected == runtime.CONTEXT_HOST:
        assert settings.is_host_runtime is True
        assert "@localhost:5432/" in settings.postgres_dsn_sync
        assert settings.endpoint_rewrites
    else:
        assert settings.is_host_runtime is False
        assert settings.postgres_dsn_sync == COMPOSE_DSN_SYNC
        assert settings.endpoint_rewrites == []


def test_auto_detection_inside_a_container_keeps_service_hostnames(monkeypatch) -> None:
    """Simulated Compose execution: `postgres` must survive, or networking breaks."""
    monkeypatch.setattr(runtime, "running_in_container", lambda: True)

    production = make_settings(runtime_context="auto")
    assert production.resolved_runtime_context == "production"
    assert production.postgres_dsn_sync == COMPOSE_DSN_SYNC
    assert production.minio_endpoint == "minio:9000"
    assert production.endpoint_rewrites == []

    embedded = Settings(
        profile="embedded",
        environment="dev",
        runtime_context="auto",
        postgres_dsn=COMPOSE_DSN_ASYNC,
        postgres_dsn_sync=COMPOSE_DSN_SYNC,
        neo4j_uri="bolt://neo4j:7687",
        minio_endpoint="minio:9000",
        redis_url="redis://redis:6379/0",
    )
    assert embedded.resolved_runtime_context == "docker"
    assert embedded.postgres_dsn_sync == COMPOSE_DSN_SYNC
    assert embedded.postgres_endpoint == ("postgres", 5432)
    assert embedded.endpoint_rewrites == []


def test_explicit_host_context_wins_even_inside_a_container(monkeypatch) -> None:
    """An operator override is deterministic — e.g. host network mode testing."""
    monkeypatch.setattr(runtime, "running_in_container", lambda: True)
    settings = make_settings(runtime_context="host")
    assert settings.resolved_runtime_context == "host"
    assert "@localhost:5432/" in settings.postgres_dsn_sync


def test_host_ports_and_infra_host_are_configurable() -> None:
    settings = make_settings(
        runtime_context="host", infra_host="127.0.0.1", postgres_host_port=15432, neo4j_host_port=17687
    )
    assert settings.postgres_endpoint == ("127.0.0.1", 15432)
    assert settings.neo4j_endpoint == ("127.0.0.1", 17687)
    assert settings.postgres_dsn_sync.endswith("@127.0.0.1:15432/crimelink")


def test_endpoint_report_is_secret_free() -> None:
    settings = make_settings(runtime_context="host")
    report = settings.endpoint_report()
    assert report["runtime_context"] == "host"
    assert report["backends"]["relational"] == "postgres"
    assert report["endpoints"]["postgres_dsn_sync"]["host"] == "localhost"
    assert report["endpoints"]["postgres_dsn_sync"]["value"] == (
        "postgresql+psycopg2://***@localhost:5432/crimelink"
    )
    assert "s3cret" not in str(report)
    # The real endpoint is still available to the code that must connect.
    assert "s3cret" in settings.postgres_dsn_sync
    assert "s3cret" not in " ".join(runtime.summarize(settings.resolved_endpoints.values()))


def test_redact_credentials_handles_urls_and_bare_endpoints() -> None:
    assert runtime.redact_credentials(COMPOSE_DSN_SYNC) == "postgresql+psycopg2://***@postgres:5432/crimelink"
    assert runtime.redact_credentials("bolt://neo4j:7687") == "bolt://neo4j:7687"
    assert runtime.redact_credentials("minio:9000") == "minio:9000"
    assert runtime.redact_credentials("user:pass@minio:9000") == "***@minio:9000"
    assert runtime.redact_credentials("") == ""


def test_production_fail_closed_validation_survives_the_resolver() -> None:
    """Endpoint resolution must not weaken the production security checks."""
    with pytest.raises(ValueError, match="CRIMELINK_POSTGRES_DSN"):
        make_settings(runtime_context="host", postgres_dsn="postgresql+asyncpg://crimelink:crimelink@postgres:5432/crimelink")
    with pytest.raises(ValueError, match="CRIMELINK_SECRET_KEY"):
        make_settings(runtime_context="host", secret_key="change-me")
    with pytest.raises(ValueError, match="CRIMELINK_CORS_ORIGINS"):
        make_settings(runtime_context="host", cors_origins=["*"])
    with pytest.raises(ValueError, match="CRIMELINK_NEO4J_PASSWORD"):
        make_settings(runtime_context="host", neo4j_password="neo4j")


def test_embedded_profile_defaults_are_untouched() -> None:
    """The zero-container experience still needs no infrastructure at all."""
    settings = Settings(
        profile="embedded",
        environment="dev",
        relational_backend="auto",
        graph_backend="auto",
        object_store_backend="auto",
        broker_backend="auto",
        postgres_dsn=runtime.DEFAULT_POSTGRES_DSN,
        postgres_dsn_sync=runtime.DEFAULT_POSTGRES_DSN_SYNC,
        neo4j_uri=runtime.DEFAULT_NEO4J_URI,
        minio_endpoint=runtime.DEFAULT_MINIO_ENDPOINT,
        redis_url=runtime.DEFAULT_REDIS_URL,
        celery_broker_url=runtime.DEFAULT_CELERY_BROKER_URL,
        celery_result_backend=runtime.DEFAULT_CELERY_RESULT_BACKEND,
    )
    assert settings.effective_relational_backend == "sqlite"
    assert settings.effective_graph_backend == "embedded"
    assert settings.effective_object_store_backend == "local"
    assert settings.effective_broker_backend == "inline"
    assert settings.required_infrastructure == []
    assert settings.endpoint_rewrites == []


# --------------------------------------------------------------------------- #
# 5. Bootstrap error reporting
# --------------------------------------------------------------------------- #

def test_name_resolution_failures_are_recognised() -> None:
    from app.db.bootstrap import is_name_resolution_failure

    dns_error = Exception(
        '(psycopg2.OperationalError) could not translate host name "postgres" to address: '
        "Name or service not known"
    )
    assert is_name_resolution_failure(dns_error) is True
    assert is_name_resolution_failure(socket.gaierror(-2, "Name or service not known")) is True
    assert is_name_resolution_failure(Exception(dns_error)) is True
    assert is_name_resolution_failure(ConnectionRefusedError(111, "Connection refused")) is False
    assert is_name_resolution_failure(None) is False


def test_unreachable_postgres_message_is_actionable() -> None:
    from app.db.bootstrap import postgres_unavailable_message

    settings = make_settings(runtime_context="host")
    message = postgres_unavailable_message(settings, "Connection refused", name_resolution=False)
    assert "PostgreSQL is not running (localhost:5432)." in message
    assert "docker compose -f docker-compose.infra.yml up -d" in message
    assert "does not fall back to SQLite" in message
    assert "s3cret" not in message


def test_compose_hostname_message_points_at_the_runtime_context() -> None:
    """The exact reported failure must explain itself instead of naming DNS."""
    from app.db.bootstrap import postgres_unavailable_message

    settings = make_settings(runtime_context="docker")
    message = postgres_unavailable_message(
        settings,
        'could not translate host name "postgres" to address: Name or service not known',
        name_resolution=True,
    )
    assert "PostgreSQL is not running (postgres:5432)." in message
    assert "CRIMELINK_RUNTIME_CONTEXT=host" in message
    assert "python run.py" in message
