"""Launcher preflight: runtime context, endpoints, and service verification.

``run.py`` is the documented entry point (README §"Running ``python run.py``
against the real services"), so the decisions it makes before it starts anything
are pinned here:

* the runtime context, the adapter selection and the endpoint rewrites come from
  :mod:`app.runtime` — the same module ``app.config`` resolves them with, so the
  launcher and the backend cannot disagree about where PostgreSQL lives;
* a published host port Docker actually reports is used, but an explicit value
  in the environment/``.env`` still wins;
* a service that does not answer stops the launch **before** the bootstrap and
  before any dependency is installed, with the exact command that fixes it and
  without leaking a password;
* a reachable service is verified with a real TCP connect, the same thing the
  driver does a moment later.

The launcher lives at the repository root, so it is loaded by path rather than
imported as part of the ``app`` package.
"""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from app import runtime

RUN_PY = Path(__file__).resolve().parents[2] / "run.py"

#: The DSN shape `.env.example` ships: the Compose service name as host, which
#: is what the host runtime rewrites.
COMPOSE_POSTGRES_DSN = "postgresql+psycopg2://crimelink:s3cret@postgres:5432/crimelink"


@pytest.fixture(scope="module")
def run():
    """Load ``run.py`` as a module (it is a script, not part of ``app``).

    Registering it in ``sys.modules`` before execution is not optional:
    ``@dataclass`` resolution walks ``sys.modules[cls.__module__]``, so an
    unregistered module fails while processing ``LaunchPlan``.
    """
    spec = importlib.util.spec_from_file_location("crimelink_launcher", RUN_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture(autouse=True)
def no_docker(run, monkeypatch):
    """Keep the suite hermetic: never talk to a real Docker daemon."""
    monkeypatch.setattr(run, "docker_daemon_available", lambda: False)
    monkeypatch.setattr(run, "docker_compose_argv", lambda: None)


@pytest.fixture
def open_port() -> int:
    """A real listening TCP port on loopback, closed when the test ends."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        yield int(sock.getsockname()[1])


def closed_port() -> int:
    """A port that was just released, so nothing is listening on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def plan_with_port(run, service: str, port: int):
    """A minimal LaunchPlan whose single service points at ``127.0.0.1:port``."""
    return run.LaunchPlan(
        context="host",
        profile="production",
        environment="dev",
        infra_host="localhost",
        backends={
            "relational": "postgres",
            "graph": "embedded",
            "object_store": "local",
            "broker": "inline",
        },
        services=[service],
        endpoints={
            runtime.SERVICE_PRIMARY_FIELDS[service]: runtime.EndpointResolution(
                field=runtime.SERVICE_PRIMARY_FIELDS[service],
                service=service,
                value=f"127.0.0.1:{port}",
                host="127.0.0.1",
                port=port,
                configured_host="127.0.0.1",
                configured_port=port,
                rewritten=False,
                reason="test",
            )
        },
    )


# --------------------------------------------------------------------------- #
# 1. .env handling
# --------------------------------------------------------------------------- #

def test_read_env_file_handles_comments_quotes_and_export(tmp_path, run):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "\n"
        "CRIMELINK_PROFILE=production\n"
        "export CRIMELINK_ENVIRONMENT=dev\n"
        'CRIMELINK_POSTGRES_PASSWORD="p@ss w0rd"\n'
        "CRIMELINK_DEBUG='true'\n"
        "not_a_pair\n",
        encoding="utf-8",
    )
    values = run.read_env_file(env_file)
    assert values == {
        "CRIMELINK_PROFILE": "production",
        "CRIMELINK_ENVIRONMENT": "dev",
        "CRIMELINK_POSTGRES_PASSWORD": "p@ss w0rd",
        "CRIMELINK_DEBUG": "true",
    }


def test_read_env_file_of_missing_file_is_empty(tmp_path, run):
    assert run.read_env_file(tmp_path / "absent.env") == {}


def test_real_environment_wins_over_the_env_file(tmp_path, run, monkeypatch):
    """pydantic-settings precedence: process env > .env > defaults."""
    env_file = tmp_path / ".env"
    env_file.write_text("CRIMELINK_PROFILE=embedded\n", encoding="utf-8")
    monkeypatch.setattr(run, "ENV_FILE", env_file)
    monkeypatch.setenv("CRIMELINK_PROFILE", "production")

    values = run.launcher_configuration()
    assert values["CRIMELINK_PROFILE"] == "production"


def test_launcher_configuration_reads_the_repository_env_file(run, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("CRIMELINK_GRAPH_BACKEND=neo4j\n", encoding="utf-8")
    monkeypatch.setattr(run, "ENV_FILE", env_file)
    monkeypatch.delenv("CRIMELINK_GRAPH_BACKEND", raising=False)
    assert run.launcher_configuration()["CRIMELINK_GRAPH_BACKEND"] == "neo4j"


# --------------------------------------------------------------------------- #
# 2. Runtime context, adapters and required services
# --------------------------------------------------------------------------- #

EMBEDDED = {"CRIMELINK_PROFILE": "embedded", "CRIMELINK_ENVIRONMENT": "dev"}


def test_embedded_profile_requires_no_infrastructure(run):
    plan = run.build_plan(dict(EMBEDDED))
    assert plan.context == "host"
    assert plan.backend_summary == (
        "relational=sqlite, graph=embedded, object_store=local, broker=inline"
    )
    assert plan.services == []
    assert plan.child_env["CRIMELINK_RUNTIME_CONTEXT"] == "host"


def test_production_profile_requires_all_four_services(run):
    plan = run.build_plan({"CRIMELINK_PROFILE": "production", "CRIMELINK_ENVIRONMENT": "dev"})
    assert plan.services == ["postgres", "neo4j", "minio", "redis"]
    assert plan.backend_summary == (
        "relational=postgres, graph=neo4j, object_store=minio, broker=celery"
    )


def test_launcher_rewrites_compose_names_exactly_like_the_backend(run):
    """The launcher's address must equal what app.config will connect to."""
    plan = run.build_plan(
        {
            "CRIMELINK_PROFILE": "production",
            "CRIMELINK_POSTGRES_DSN_SYNC": "postgresql+psycopg2://crimelink:s3cret@postgres:5432/crimelink",
            "CRIMELINK_NEO4J_URI": "bolt://neo4j:7687",
            "CRIMELINK_MINIO_ENDPOINT": "minio:9000",
            "CRIMELINK_REDIS_URL": "redis://redis:6379/0",
        }
    )
    postgres = plan.endpoint_for("postgres")
    assert postgres is not None
    assert (postgres.host, postgres.port) == ("localhost", 5432)
    assert postgres.rewritten is True
    assert postgres.configured_host == "postgres"
    assert plan.endpoint_for("neo4j").address == "localhost:7687"
    assert plan.endpoint_for("minio").address == "localhost:9000"
    assert plan.endpoint_for("redis").address == "localhost:6379"


def test_postgres_only_selection_requires_only_postgres(run):
    """The documented hybrid: real PostgreSQL, embedded everything else."""
    plan = run.build_plan(
        {
            "CRIMELINK_PROFILE": "embedded",
            "CRIMELINK_RELATIONAL_BACKEND": "postgres",
        }
    )
    assert plan.services == ["postgres"]
    assert plan.backends["graph"] == "embedded"


def test_cli_runtime_context_wins_over_the_env_file(run):
    plan = run.build_plan(dict(EMBEDDED, CRIMELINK_RUNTIME_CONTEXT="host"), cli_context="production")
    assert plan.context == "production"
    assert plan.child_env["CRIMELINK_RUNTIME_CONTEXT"] == "production"


def test_container_context_keeps_service_hostnames(run):
    plan = run.build_plan(
        {
            "CRIMELINK_PROFILE": "production",
            "CRIMELINK_RUNTIME_CONTEXT": "production",
            "CRIMELINK_POSTGRES_DSN_SYNC": "postgresql+psycopg2://crimelink:s3cret@postgres:5432/crimelink",
            "CRIMELINK_NEO4J_URI": "bolt://neo4j:7687",
        },
    )
    postgres = plan.endpoint_for("postgres")
    assert postgres is not None
    assert postgres.rewritten is False
    assert postgres.host == "postgres"
    assert plan.endpoint_for("neo4j").host == "neo4j"


# --------------------------------------------------------------------------- #
# 3. Published host ports come from Docker, unless configured
# --------------------------------------------------------------------------- #

def test_docker_port_output_is_parsed(run, monkeypatch):
    monkeypatch.setattr(run, "docker_daemon_available", lambda: True)

    class Result:
        returncode = 0
        stdout = "0.0.0.0:15432\n[::]:15432\n"

    monkeypatch.setattr(run.subprocess, "run", lambda *a, **k: Result())
    assert run.published_host_port("postgres") == 15432


def test_discovered_port_is_used_when_nothing_is_configured(run, monkeypatch):
    monkeypatch.setattr(run, "docker_daemon_available", lambda: True)
    monkeypatch.setattr(run, "published_host_port", lambda service: 15432)

    discovered = run.discover_host_ports(["postgres"], {})
    assert discovered == {"CRIMELINK_POSTGRES_HOST_PORT": "15432"}

    plan = run.build_plan(
        {
            "CRIMELINK_PROFILE": "production",
            "CRIMELINK_POSTGRES_DSN_SYNC": COMPOSE_POSTGRES_DSN,
        },
        extra_env=discovered,
    )
    assert plan.endpoint_for("postgres").address == "localhost:15432"


def test_explicit_host_port_wins_over_docker_discovery(run, monkeypatch):
    monkeypatch.setattr(run, "docker_daemon_available", lambda: True)
    monkeypatch.setattr(run, "published_host_port", lambda service: 15432)

    configured = {"CRIMELINK_POSTGRES_HOST_PORT": "25432"}
    assert run.discover_host_ports(["postgres"], configured) == {}

    plan = run.build_plan(
        {"CRIMELINK_PROFILE": "production", "CRIMELINK_POSTGRES_DSN_SYNC": COMPOSE_POSTGRES_DSN, **configured}
    )
    assert plan.endpoint_for("postgres").address == "localhost:25432"


def test_host_port_override_matches_app_config(run, monkeypatch):
    """Launcher and backend must resolve the same address, override or not.

    ``CRIMELINK_POSTGRES_HOST_PORT`` sets the target of the Compose-name
    rewrite; an explicit non-Compose DSN wins over it in *both* places, so a
    launcher that agreed with the operator instead of with ``app.config`` would
    verify one address while the API connected to another.
    """
    from app.config import Settings

    monkeypatch.setenv("CRIMELINK_POSTGRES_HOST_PORT", "25432")

    launcher = run.build_plan(
        {"CRIMELINK_PROFILE": "production", "CRIMELINK_POSTGRES_DSN_SYNC": COMPOSE_POSTGRES_DSN},
        extra_env={"CRIMELINK_POSTGRES_HOST_PORT": "25432"},
    )
    settings = Settings(
        profile="production",
        environment="production",
        debug=False,
        secret_key="test-secret-key-0123456789abcdefghijklmnop",
        cors_origins=["http://localhost"],
        neo4j_password="neo4j-test-secret",
        minio_secret_key="minio-test-secret",
        postgres_host_port=25432,
        postgres_dsn=COMPOSE_POSTGRES_DSN.replace("psycopg2", "asyncpg"),
        postgres_dsn_sync=COMPOSE_POSTGRES_DSN,
        neo4j_uri="bolt://neo4j:7687",
        minio_endpoint="minio:9000",
        redis_url="redis://redis:6379/0",
        runtime_context="host",
    )
    host, port = settings.postgres_endpoint
    assert launcher.endpoint_for("postgres").address == f"{host}:{port}"
    assert settings.postgres_endpoint == ("localhost", 25432)

    # An explicit localhost DSN is left alone by both.
    local_dsn = COMPOSE_POSTGRES_DSN.replace("@postgres:", "@localhost:")
    launcher_local = run.build_plan(
        {"CRIMELINK_PROFILE": "production", "CRIMELINK_POSTGRES_DSN_SYNC": local_dsn},
        extra_env={"CRIMELINK_POSTGRES_HOST_PORT": "25432"},
    )
    settings_local = Settings(
        profile="production",
        environment="production",
        debug=False,
        secret_key="test-secret-key-0123456789abcdefghijklmnop",
        cors_origins=["http://localhost"],
        neo4j_password="neo4j-test-secret",
        minio_secret_key="minio-test-secret",
        postgres_host_port=25432,
        postgres_dsn=local_dsn.replace("psycopg2", "asyncpg"),
        postgres_dsn_sync=local_dsn,
        neo4j_uri="bolt://neo4j:7687",
        minio_endpoint="minio:9000",
        redis_url="redis://redis:6379/0",
        runtime_context="host",
    )
    assert settings_local.postgres_endpoint == ("localhost", 5432)
    local_host, local_port = settings_local.postgres_endpoint
    assert launcher_local.endpoint_for("postgres").address == f"{local_host}:{local_port}"


def test_no_discovery_without_a_docker_daemon(run, monkeypatch):
    monkeypatch.setattr(run, "docker_daemon_available", lambda: False)
    called = []
    monkeypatch.setattr(
        run, "published_host_port", lambda service: called.append(service) or 15432
    )
    assert run.discover_host_ports(["postgres", "neo4j"], {}) == {}
    assert called == []


def test_discovery_never_overrides_the_default_with_itself(run, monkeypatch):
    """A default stack publishes 5432; that needs no child-env override."""
    monkeypatch.setattr(run, "docker_daemon_available", lambda: True)
    monkeypatch.setattr(
        run, "published_host_port", lambda service: runtime.SERVICE_CONTAINER_PORTS[service]
    )
    assert run.discover_host_ports(["postgres", "redis"], {}) == {}


# --------------------------------------------------------------------------- #
# 4. Reachability probes
# --------------------------------------------------------------------------- #

def test_probe_tcp_detects_a_real_listener(run, open_port):
    assert run.probe_tcp("127.0.0.1", open_port) is True
    assert run.probe_tcp("localhost", open_port) is True


def test_probe_tcp_fails_closed(run):
    assert run.probe_tcp("127.0.0.1", closed_port()) is False
    assert run.probe_tcp("does-not-resolve.invalid", 5432, timeout=0.2) is False
    assert run.probe_tcp("", 0) is False


def test_verify_services_reports_a_reachable_service(run, open_port, capsys):
    plan = plan_with_port(run, "postgres", open_port)
    assert run.verify_services(plan, timeout=1.0) == []
    out = capsys.readouterr().out
    assert f"Verifying PostgreSQL at 127.0.0.1:{open_port} …" in out
    assert f"PostgreSQL is reachable at 127.0.0.1:{open_port}." in out


def test_verify_services_reports_an_unreachable_service(run, capsys):
    port = closed_port()
    plan = plan_with_port(run, "postgres", port)
    assert run.verify_services(plan, timeout=1.0) == ["postgres"]
    out = capsys.readouterr().out
    assert f"PostgreSQL is not reachable at 127.0.0.1:{port}." in out


def test_embedded_launch_skips_verification_entirely(run, capsys):
    plan = run.build_plan(dict(EMBEDDED))
    assert run.verify_services(plan, timeout=1.0) == []
    assert "No infrastructure services required" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 5. The failure the user actually sees
# --------------------------------------------------------------------------- #

def test_failure_message_names_the_service_and_the_fix(run):
    plan = run.build_plan(
        {
            "CRIMELINK_PROFILE": "production",
            "CRIMELINK_POSTGRES_DSN_SYNC": "postgresql+psycopg2://crimelink:s3cret@postgres:5432/crimelink",
        }
    )
    message = run.infrastructure_failure_message(plan, ["postgres"])
    assert "PostgreSQL is not running" in message or "NOT REACHABLE" in message
    assert "docker compose -f docker-compose.infra.yml up -d" in message
    assert "python run.py --start-infra" in message
    assert "docker compose -f docker-compose.infra.yml ps" in message
    assert "localhost:5432" in message
    assert "Runtime context: host" in message
    assert "CRIMELINK_PROFILE=embedded" in message


def test_failure_message_never_leaks_credentials(run):
    plan = run.build_plan(
        {
            "CRIMELINK_PROFILE": "production",
            "CRIMELINK_POSTGRES_DSN_SYNC": "postgresql+psycopg2://crimelink:s3cret@postgres:5432/crimelink",
        }
    )
    message = run.infrastructure_failure_message(plan, ["postgres"])
    assert "s3cret" not in message
    assert "***@localhost:5432" in message


def test_print_plan_matches_the_documented_output(run, capsys):
    plan = run.build_plan(
        {
            "CRIMELINK_PROFILE": "production",
            "CRIMELINK_GRAPH_BACKEND": "embedded",
            "CRIMELINK_OBJECT_STORE_BACKEND": "local",
            "CRIMELINK_BROKER_BACKEND": "inline",
            "CRIMELINK_POSTGRES_DSN_SYNC": "postgresql+psycopg2://crimelink:s3cret@postgres:5432/crimelink",
        }
    )
    run.print_plan(plan)
    out = capsys.readouterr().out
    assert "[CrimeLink] Runtime environment: host — native Python on this machine" in out
    assert (
        "[CrimeLink] Adapters: relational=postgres, graph=embedded, "
        "object_store=local, broker=inline" in out
    )
    assert (
        "  PostgreSQL : localhost:5432  "
        "[host runtime: Compose name 'postgres' -> localhost:5432]" in out
    )
    assert "s3cret" not in out


def test_main_stops_before_installing_anything_when_a_service_is_down(
    run, monkeypatch, capsys, tmp_path
):
    """The regression this module exists for: no traceback, no half-bootstrap."""
    monkeypatch.setattr(run, "ENV_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(
        run,
        "ensure_venv",
        lambda *a, **k: pytest.fail("dependencies must not be installed before verification"),
    )
    monkeypatch.setattr(
        run,
        "verify_bootstrap_dependencies",
        lambda *a, **k: pytest.fail("bootstrap dependencies must not be checked yet"),
    )
    for key, value in {
        "CRIMELINK_PROFILE": "production",
        "CRIMELINK_GRAPH_BACKEND": "embedded",
        "CRIMELINK_OBJECT_STORE_BACKEND": "local",
        "CRIMELINK_BROKER_BACKEND": "inline",
        "CRIMELINK_POSTGRES_HOST_PORT": str(closed_port()),
    }.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(SystemExit) as excinfo:
        run.main(["--no-browser", "--infra-timeout", "1"])

    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    assert "Required infrastructure is not running" in err
    assert "docker compose -f docker-compose.infra.yml up -d" in err
    assert "Traceback" not in err


def test_start_infra_refuses_without_a_docker_cli(run, monkeypatch, capsys):
    monkeypatch.setattr(run, "docker_compose_argv", lambda: None)
    with pytest.raises(SystemExit) as excinfo:
        run.start_infra(["postgres"], timeout=1.0, host="localhost")
    assert excinfo.value.code == 1
    assert "Docker is required by --start-infra" in capsys.readouterr().err


def test_start_infra_refuses_when_the_daemon_is_down(run, monkeypatch, capsys):
    monkeypatch.setattr(run, "docker_compose_argv", lambda: ("docker", "compose"))
    monkeypatch.setattr(run, "docker_daemon_available", lambda: False)
    with pytest.raises(SystemExit):
        run.start_infra(["postgres"], timeout=1.0, host="localhost")
    assert "daemon is not responding" in capsys.readouterr().err


def test_start_infra_never_runs_a_destructive_command(run, monkeypatch, capsys, tmp_path):
    """`up -d` only: the launcher must not remove containers or volumes."""
    monkeypatch.setattr(run, "docker_compose_argv", lambda: ("docker", "compose"))
    monkeypatch.setattr(run, "docker_daemon_available", lambda: True)
    monkeypatch.setattr(run, "INFRA_COMPOSE_PATH", tmp_path / "docker-compose.infra.yml")
    (tmp_path / "docker-compose.infra.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(run, "ENV_FILE", tmp_path / ".env")
    (tmp_path / ".env").write_text("CRIMELINK_POSTGRES_PASSWORD=x\n", encoding="utf-8")
    monkeypatch.setattr(
        run, "container_state", lambda service: run.ContainerState(True, "healthy")
    )

    issued: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        issued.append(list(command))

        class Result:
            returncode = 0
            stdout = ""

        return Result()

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    run.start_infra(["postgres"], timeout=5.0, host="localhost")

    assert issued == [["docker", "compose", "-f", "docker-compose.infra.yml", "up", "-d", "postgres"]]
    assert "Infrastructure is up." in capsys.readouterr().out
    joined = " ".join(" ".join(command) for command in issued)
    for forbidden in ("down", "-v", "rm "):
        assert forbidden not in joined


def test_start_infra_reports_a_container_that_never_becomes_healthy(
    run, monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(run, "docker_compose_argv", lambda: ("docker", "compose"))
    monkeypatch.setattr(run, "docker_daemon_available", lambda: True)
    monkeypatch.setattr(run, "INFRA_COMPOSE_PATH", tmp_path / "docker-compose.infra.yml")
    (tmp_path / "docker-compose.infra.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(run, "ENV_FILE", tmp_path / ".env")
    (tmp_path / ".env").write_text("CRIMELINK_POSTGRES_PASSWORD=x\n", encoding="utf-8")
    monkeypatch.setattr(run.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(
        run, "container_state", lambda service: run.ContainerState(True, "starting")
    )

    with pytest.raises(SystemExit):
        run.start_infra(["postgres"], timeout=1.0, host="localhost")
    err = capsys.readouterr().err
    assert "did not become healthy" in err
    assert "logs --tail=50" in err


def test_container_state_treats_a_stopped_container_as_not_ready(run, monkeypatch):
    """Docker keeps the last health of a stopped container; running state wins."""

    class Result:
        returncode = 0
        stdout = "false healthy"

    monkeypatch.setattr(run, "docker_daemon_available", lambda: True)
    monkeypatch.setattr(run.subprocess, "run", lambda *a, **k: Result())
    state = run.container_state("postgres")
    assert state.running is False
    assert state.health == "healthy"
    assert "not running" in state.summary


# --------------------------------------------------------------------------- #
# 6. CLI surface
# --------------------------------------------------------------------------- #

def test_documented_flags_exist(run):
    args = run.parse_args(
        ["--start-infra", "--runtime-context", "host", "--infra-timeout", "45", "--reinstall"]
    )
    assert args.start_infra is True
    assert args.runtime_context == "host"
    assert args.infra_timeout == 45.0
    assert args.reinstall is True
    assert args.no_browser is False


def test_defaults_are_safe(run):
    args = run.parse_args([])
    assert args.start_infra is False
    assert args.runtime_context is None
    assert args.reinstall is False
    assert args.infra_timeout == run.DEFAULT_INFRA_TIMEOUT


def test_invalid_runtime_context_is_rejected(run):
    with pytest.raises(SystemExit):
        run.parse_args(["--runtime-context", "windows"])
