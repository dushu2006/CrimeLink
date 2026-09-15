#!/usr/bin/env python3
"""One-command CrimeLink launcher.

Installs Python and Node dependencies on first run, bootstraps storage and
the persistent 20-case demo dataset idempotently, starts the FastAPI backend
and Vite console, and opens a browser.

Quick start::

    python run.py
    python run.py --reinstall     # force pip + npm again
    python run.py --no-browser
    python run.py --reload        # auto-reload backend on code changes
    python run.py --start-infra   # also start the local infra containers

Startup path::

    Python 3.11+ (3.13 preferred)
      -> dependency verification (venv + pip, npm)
      -> determine runtime environment (host / docker / production)
      -> resolve infrastructure endpoints (PostgreSQL first)
      -> verify PostgreSQL -> Neo4j -> MinIO -> Redis (only those in use)
      -> verify/seed the persisted demo dataset (idempotent)
      -> start backend + frontend

Runtime environments (``CRIMELINK_RUNTIME_CONTEXT``, see backend/app/runtime.py):

- ``host`` — native Python on your machine, i.e. this launcher.  Docker Compose
  service hostnames (``postgres``, ``neo4j``, ``minio``, ``redis``) only resolve
  on the Compose network, so they are rewritten to ``CRIMELINK_INFRA_HOST``
  (default ``localhost``) plus the port published by ``docker-compose.infra.yml``.
  This is why ``python run.py`` on Windows no longer fails with
  ``could not translate host name "postgres"``.
- ``docker`` — inside a Compose container: service hostnames are authoritative
  and nothing is rewritten.
- ``production`` — a deployment: endpoints are used exactly as configured.

Rewriting changes *where* a service is reached, never *which* backend is used.
PostgreSQL stays mandatory wherever it is configured: there is no SQLite,
in-memory or local-filesystem fallback, and a service that cannot be reached
stops the launch with an actionable message instead of an opaque DNS error.

Profiles:
- embedded (default): In-process SQLite, NetworkX graph, and LocalObjectStore.
  Requires zero external containers.
- production: PostgreSQL, Neo4j, MinIO, and Redis.  Select it in ``.env``
  (or set ``CRIMELINK_RELATIONAL_BACKEND=postgres`` for PostgreSQL only) and
  start the data services with::

      docker compose -f docker-compose.infra.yml up -d

Demo Dataset:
- 20 Cases: CR-1024 through CR-1043
- 100 People, 216 Relationships, 300 Evidence records, 120 Sources
- 258 Timeline events, 10 Investigations (including INV-0042 hero)
- Real verifiable PDFs & CSVs in object storage

Demo Logins:
- DEMO-ADMIN        / DemoAdmin@2026
- DEMO-INVESTIGATOR / DemoInvestigator@2026
- DEMO-VIEWER       / DemoViewer@2026
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, NamedTuple

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
RUN_DIR = ROOT / ".run"
DATA_DIR = ROOT / "var" / "data"
OBJECT_DIR = ROOT / "var" / "objects"

#: Standard-library-only module that owns runtime detection and endpoint
#: resolution for the whole project.  The launcher imports it directly (before
#: the virtualenv exists) so there is exactly one configuration system.
RUNTIME_MODULE = BACKEND / "app" / "runtime.py"

#: Host-accessible infrastructure stack started by ``--start-infra``.  It only
#: creates/starts containers — the launcher never runs ``down``, never removes a
#: volume and never calls reset_demo.
INFRA_COMPOSE_FILE = ROOT / "docker-compose.infra.yml"
INFRA_COMPOSE_COMMAND = "docker compose -f docker-compose.infra.yml up -d"

#: Container names declared in docker-compose.yml / docker-compose.infra.yml.
INFRA_CONTAINER_NAMES = {
    "postgres": "crimelink-postgres",
    "neo4j": "crimelink-neo4j",
    "minio": "crimelink-minio",
    "redis": "crimelink-redis",
}

SERVICE_LABELS = {
    "postgres": "PostgreSQL",
    "neo4j": "Neo4j",
    "minio": "MinIO",
    "redis": "Redis",
}

API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 8000
WEB_HOST = "0.0.0.0"
DEFAULT_WEB_PORT = 5173

MIN_PYTHON = (3, 11)
MAX_PYTHON_TESTED = (3, 13)  # tested on 3.11–3.13; warn on 3.14+
MIN_NODE_MAJOR = 18


def die(message: str, code: int = 1) -> None:
    print(f"\n[CrimeLink] ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def info(message: str) -> None:
    print(f"[CrimeLink] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[CrimeLink] WARNING: {message}", file=sys.stderr, flush=True)


def venv_bin(name: str) -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / (name + (".exe" if not name.endswith(".exe") else ""))
    return VENV / "bin" / name


def venv_python() -> Path:
    return venv_bin("python.exe" if os.name == "nt" else "python")


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE pairs without overwriting variables already in the environment."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def apply_environment(*, api_url: str, web_port: int) -> dict[str, str]:
    """Configure environment variables for launcher execution."""
    load_dotenv(ROOT / ".env")
    load_dotenv(BACKEND / ".env")

    nvidia = (
        os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("CRIMELINK_NIM_API_KEY")
        or os.environ.get("CRIMELINK_AI_API_KEY")
    )
    if nvidia:
        os.environ["NVIDIA_API_KEY"] = nvidia
        os.environ["CRIMELINK_NIM_API_KEY"] = nvidia
        os.environ.setdefault("CRIMELINK_AI_API_KEY", nvidia)

    profile = os.environ.get("CRIMELINK_PROFILE", "embedded")
    os.environ["CRIMELINK_PROFILE"] = profile
    os.environ.setdefault("CRIMELINK_ENVIRONMENT", "dev" if profile == "embedded" else "production")
    os.environ.setdefault("CRIMELINK_DEBUG", "true" if profile == "embedded" else "false")
    os.environ.setdefault("CRIMELINK_DATA_DIR", str(DATA_DIR))
    os.environ.setdefault("CRIMELINK_OBJECT_STORE_DIR", str(OBJECT_DIR))
    os.environ["PYTHONPATH"] = str(BACKEND)
    os.environ["CRIMELINK_API"] = api_url
    os.environ.setdefault("CRIMELINK_SYNTHETIC_DATA_MODE", "external")
    os.environ.setdefault(
        "CRIMELINK_SYNTHETIC_DATA_ROOT", "backend/CrimeLink_Synthetic_Corpus_v1"
    )
    os.environ["CRIMELINK_CORS_ORIGINS"] = json.dumps([
        f"http://127.0.0.1:{web_port}",
        f"http://localhost:{web_port}",
        api_url,
    ])
    os.environ["CRIMELINK_SYNTHETIC_CORPUS_ENABLED"] = "false"

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OBJECT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    return os.environ.copy()


def require_python() -> str:
    if sys.version_info < MIN_PYTHON:
        die(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required (found "
            f"{sys.version.split()[0]}). Install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer "
            "from https://www.python.org/ (on Windows, tick 'Add Python to PATH')."
        )
    if sys.version_info <= MAX_PYTHON_TESTED:
        return sys.executable

    if os.name == "nt":
        compatible = shutil.which("py")
        if compatible:
            probe = subprocess.run(
                [compatible, "-3.13", "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
            )
            if probe.returncode == 0 and probe.stdout.strip():
                selected = probe.stdout.strip()
                info(
                    f"Python {sys.version_info.major}.{sys.version_info.minor} detected; "
                    f"using Python 3.13 at {selected}."
                )
                return selected

    die(
        f"Python {sys.version_info.major}.{sys.version_info.minor} is newer than the versions "
        f"we test against ({MIN_PYTHON[0]}.{MIN_PYTHON[1]}–{MAX_PYTHON_TESTED[0]}.{MAX_PYTHON_TESTED[1]}), "
        "and no compatible Python 3.13 interpreter was found. Install Python 3.13 from "
        "https://www.python.org/ (on Windows, the Python launcher must be enabled)."
    )
    return sys.executable


def require_node() -> str:
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        die(
            "Node.js 18+ (with npm) is required for the investigator console.\n"
            "  macOS:   brew install node\n"
            "  Ubuntu:  sudo apt install nodejs npm\n"
            "  Windows: download LTS from https://nodejs.org/ and tick 'Add to PATH'."
        )
    try:
        out = subprocess.check_output(
            [node, "-v"], text=True, stderr=subprocess.STDOUT
        ).strip().lstrip("v")
    except subprocess.CalledProcessError as exc:
        die(f"Could not run node: {exc.output or exc}")
    try:
        major = int(out.split(".")[0])
    except ValueError:
        die(f"Could not parse Node version '{out}'. Reinstall Node.js 18+ from https://nodejs.org/.")
    if major < MIN_NODE_MAJOR:
        die(
            f"Node.js {MIN_NODE_MAJOR}+ is required (found v{out}). Install the LTS release from "
            "https://nodejs.org/."
        )
    return npm


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


# --------------------------------------------------------------------------- #
# Runtime environment detection and infrastructure resolution
#
# All of the logic lives in backend/app/runtime.py (standard library only) so
# the launcher, the API, the workers, Alembic and the seed scripts resolve the
# same endpoints from the same variables.  run.py's job here is to (1) detect
# the context once, (2) pin it for every child process, (3) read the *actual*
# published host ports back from Docker instead of assuming them, and (4) fail
# with an actionable message when a required service is not reachable.
# --------------------------------------------------------------------------- #

class InfraPlan(NamedTuple):
    """Everything the launcher decided about this run's infrastructure."""

    context: str
    backends: dict[str, str]
    required: list[str]
    resolutions: dict[str, Any]
    endpoints: dict[str, Any]
    discovered_ports: dict[str, int]


def load_runtime_module() -> Any:
    """Import ``backend/app/runtime.py`` with the *system* interpreter.

    The module deliberately has no third-party imports, so the launcher can
    share the backend's detection and endpoint resolution before the virtualenv
    exists — one configuration system instead of two competing ones.
    """
    if not RUNTIME_MODULE.is_file():
        die(
            f"{RUNTIME_MODULE} is missing. run.py must live at the CrimeLink "
            "repository root (next to backend/ and frontend/)."
        )
    spec = importlib.util.spec_from_file_location("crimelink_runtime", RUNTIME_MODULE)
    if spec is None or spec.loader is None:
        die(f"Could not import {RUNTIME_MODULE}.")
    module = importlib.util.module_from_spec(spec)
    # Register before executing: ``dataclasses`` looks the defining module up in
    # ``sys.modules`` while it processes annotations, so a module that is only
    # exec'd would raise AttributeError('NoneType' has no '__dict__').
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - import-time failure
        sys.modules.pop(spec.name, None)
        die(f"Could not load {RUNTIME_MODULE}: {exc}")
    return module


def determine_runtime_context(rt: Any, env: dict[str, str], override: str | None) -> str:
    """Detect (or take the explicit) runtime context and pin it for children."""
    explicit = (override or env.get(rt.RUNTIME_CONTEXT_VAR) or "").strip() or None
    try:
        context = rt.resolve_runtime_context(
            explicit=explicit,
            profile=env.get("CRIMELINK_PROFILE", "embedded"),
            environment=env.get("CRIMELINK_ENVIRONMENT", "dev"),
        )
    except ValueError as exc:
        die(str(exc))
        return rt.CONTEXT_HOST  # unreachable; keeps type checkers quiet
    # Pin the decision so the bootstrap subprocess, the API and the console all
    # resolve endpoints identically instead of re-detecting.
    env[rt.RUNTIME_CONTEXT_VAR] = context
    return context


def docker_executable() -> str | None:
    return shutil.which("docker")


def docker_compose_command() -> list[str] | None:
    """``docker compose`` when available, else the legacy ``docker-compose``."""
    docker = docker_executable()
    if docker:
        try:
            probe = subprocess.run(
                [docker, "compose", "version"], capture_output=True, text=True, timeout=20
            )
            if probe.returncode == 0:
                return [docker, "compose"]
        except (OSError, subprocess.SubprocessError):
            pass
    legacy = shutil.which("docker-compose")
    return [legacy] if legacy else None


def docker_container_state(container_name: str) -> str:
    """State of a container: running/exited/created, not-created, docker-unavailable."""
    docker = docker_executable()
    if not docker:
        return "docker-unavailable"
    try:
        out = subprocess.run(
            [docker, "inspect", "-f", "{{.State.Status}}", container_name],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if out.returncode != 0:
        return "not-created"
    return out.stdout.strip() or "unknown"


def discover_published_host_ports(rt: Any, env: dict[str, str]) -> dict[str, int]:
    """Read the *actual* host ports Docker publishes for the infra containers.

    Requirement: never assume a port when the stack already declares one.  An
    explicit value in the environment or ``.env`` always wins; otherwise the
    discovered port is exported so the backend resolves the same address the
    launcher is about to probe.
    """
    docker = docker_executable()
    discovered: dict[str, int] = {}
    if not docker:
        return discovered
    for service, container_port in sorted(rt.SERVICE_CONTAINER_PORTS.items()):
        container = INFRA_CONTAINER_NAMES.get(service)
        if not container:
            continue
        try:
            out = subprocess.run(
                [docker, "port", container, str(container_port)],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode != 0:
            continue
        port = 0
        for line in out.stdout.splitlines():
            candidate = line.strip().rsplit(":", 1)[-1]
            if candidate.isdigit():
                port = int(candidate)
                break
        if not port:
            continue
        discovered[service] = port
        variable = rt.SERVICE_HOST_PORT_VARS.get(service)
        if not variable:
            continue
        configured = (env.get(variable) or "").strip()
        if not configured:
            env[variable] = str(port)
        elif configured.isdigit() and int(configured) != port:
            warn(
                f"{container} publishes {SERVICE_LABELS.get(service, service)} on host port "
                f"{port}, but {variable}={configured}. Honouring your explicit value — "
                "align one of them if the connection fails."
            )
    return discovered


def build_infra_plan(rt: Any, env: dict[str, str], context: str) -> InfraPlan:
    """Resolve adapters, endpoints and the services this run actually needs."""
    backends = rt.effective_backends(
        profile=env.get("CRIMELINK_PROFILE", "embedded"),
        relational=env.get("CRIMELINK_RELATIONAL_BACKEND", "auto"),
        graph=env.get("CRIMELINK_GRAPH_BACKEND", "auto"),
        object_store=env.get("CRIMELINK_OBJECT_STORE_BACKEND", "auto"),
        broker=env.get("CRIMELINK_BROKER_BACKEND", "auto"),
    )
    resolutions = rt.resolve_infrastructure(context=context, env=env)
    return InfraPlan(
        context=context,
        backends=backends,
        required=rt.required_services(backends),
        resolutions=resolutions,
        endpoints=rt.primary_service_endpoints(resolutions),
        discovered_ports={},
    )


def report_runtime(rt: Any, plan: InfraPlan) -> None:
    """Print the determined environment and every resolved endpoint."""
    info(f"Runtime environment: {plan.context} — {rt.CONTEXT_DESCRIPTIONS.get(plan.context, '')}")
    info(
        "Adapters: "
        f"relational={plan.backends['relational']}, graph={plan.backends['graph']}, "
        f"object_store={plan.backends['object_store']}, broker={plan.backends['broker']}"
    )
    for service in ("postgres", "neo4j", "minio", "redis"):
        endpoint = plan.endpoints.get(service)
        if endpoint is None:
            continue
        label = SERVICE_LABELS.get(service, service)
        needed = "" if service in plan.required else "  (not used by this profile)"
        if endpoint.rewritten:
            note = f"  [host runtime: Compose name '{endpoint.configured_host}' -> {endpoint.address}]"
        else:
            note = "  [used exactly as configured]"
        info(f"  {label:<11}: {endpoint.address}{note}{needed}")
    if not plan.required:
        info("  No external infrastructure is required by the selected adapters.")


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    """Poll a TCP endpoint until it accepts a connection or *timeout* elapses."""
    if not host or not port:
        return False
    deadline = time.time() + max(0.0, timeout)
    attempt_timeout = min(2.0, max(0.3, timeout))
    while True:
        try:
            with socket.create_connection((host, int(port)), timeout=attempt_timeout):
                return True
        except OSError:
            if time.time() >= deadline:
                return False
            time.sleep(0.5)


def start_infrastructure() -> None:
    """Start the host-accessible infra stack (opt-in; never destructive)."""
    if not INFRA_COMPOSE_FILE.is_file():
        die(f"{INFRA_COMPOSE_FILE.name} is missing; cannot start the infrastructure services.")
    compose = docker_compose_command()
    if not compose:
        die(
            "Docker Compose is not available. Install Docker Desktop "
            "(https://www.docker.com/products/docker-desktop/) or start the "
            "infrastructure services yourself."
        )
    info("Starting the CrimeLink infrastructure services (no volume is ever removed) …")
    run([*compose, "-f", str(INFRA_COMPOSE_FILE), "up", "-d"], cwd=str(ROOT))


def infra_failure_message(rt: Any, service: str, endpoint: Any, plan: InfraPlan) -> str:
    """Actionable replacement for an opaque DNS/connection error."""
    label = SERVICE_LABELS.get(service, service)
    container = INFRA_CONTAINER_NAMES.get(service, f"crimelink-{service}")
    state = docker_container_state(container)
    config_var = rt.ENDPOINT_ENV_VARS.get(rt.SERVICE_PRIMARY_FIELDS.get(service, ""), "")
    lines = [
        f"{label} is not running ({endpoint.address}).",
        "Start the CrimeLink infrastructure services and retry:",
        f"    {INFRA_COMPOSE_COMMAND}",
        "",
        f"  Container       : {container} ({state})",
        f"  Runtime context : {plan.context}",
        f"  Endpoint        : {endpoint.redacted_value}",
    ]
    if endpoint.rewritten:
        lines.append(
            f"  Resolved from   : Compose service '{endpoint.configured_host}' "
            "(host runtime cannot resolve Compose DNS names)"
        )
    if service == "postgres":
        lines += [
            "",
            "CrimeLink does not fall back to SQLite or an in-memory database: PostgreSQL is",
            "the relational system of record for this configuration "
            f"(relational backend '{plan.backends.get('relational')}').",
        ]
    if state == "docker-unavailable":
        lines += [
            "",
            "Docker was not found on PATH. Either start Docker Desktop, or point CrimeLink at",
            "a server that already runs:",
            f"    {config_var}=<your endpoint>",
        ]
    elif state == "not-created":
        lines += [
            "",
            f"No container named '{container}' exists yet. Create it with the command above",
            "(or pass --start-infra). Existing containers and data are never removed.",
        ]
    else:
        lines += [
            "",
            f"Inspect it with:  docker logs {container}",
        ]
    if config_var:
        lines += [
            "",
            f"Override the endpoint with {config_var} (and "
            f"{rt.SERVICE_HOST_PORT_VARS.get(service, '')} / CRIMELINK_INFRA_HOST for the",
            "published host port) — no code change is needed.",
        ]
    return "\n".join(line for line in lines if line is not None)


def verify_infrastructure(rt: Any, plan: InfraPlan, *, timeout: float, start: bool) -> None:
    """Verify PostgreSQL -> Neo4j -> MinIO -> Redis, in that order.

    Only the services the selected adapters actually use are checked, and a
    service that cannot be reached stops the launch.  This never bypasses the
    backend's own fail-closed checks in ``app.db.bootstrap``; it just reports
    the problem earlier and in words an operator can act on.
    """
    if not plan.required:
        info("Infrastructure verification: not required for the selected adapters.")
        return
    if start:
        start_infrastructure()
    for service in plan.required:
        endpoint = plan.endpoints.get(service)
        if endpoint is None or not endpoint.host:
            continue
        label = SERVICE_LABELS.get(service, service)
        info(f"Verifying {label} at {endpoint.address} …")
        if wait_for_port(endpoint.host, endpoint.port or 0, timeout):
            info(f"{label} is reachable at {endpoint.address}.")
            continue
        die(infra_failure_message(rt, service, endpoint, plan))


def wait_http(
    url: str,
    timeout: float,
    label: str,
    proc: subprocess.Popen | None = None,
    log_path: Path | None = None,
) -> None:
    deadline = time.time() + timeout
    last = ""
    start = time.time()
    last_reported = 0
    info(f"Waiting for {label} to respond at {url} …")
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            die(f"{label} exited early (code {proc.returncode}). See {log_path or 'logs'}")
        if log_path and log_path.exists():
            try:
                content = log_path.read_text(encoding="utf-8", errors="ignore")
                if "Traceback (most recent call last):" in content:
                    tb_tail = content.split("Traceback (most recent call last):")[-1]
                    if "Uvicorn running on" not in tb_tail and ("Error:" in tb_tail or "Exception:" in tb_tail):
                        lines = [l for l in tb_tail.strip().splitlines() if l.strip()]
                        die(f"{label} crashed during startup:\n" + "\n".join(lines[-8:]) + f"\nSee {log_path}")
            except Exception:
                pass
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    info(f"{label} is ready.")
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = str(exc)
        elapsed = int(time.time() - start)
        if elapsed > 0 and elapsed % 10 == 0 and elapsed != last_reported:
            last_reported = elapsed
            info(f"Still waiting for {label} ({elapsed}s elapsed) …")
        time.sleep(0.4)
    die(f"Timed out waiting for {label} at {url}. Last error: {last or 'no response'}")


def run(cmd: list[str], **kwargs) -> None:
    info(" ".join(cmd))
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        die(f"Command failed ({result.returncode}): {' '.join(cmd)}")


def ensure_venv(interpreter: str, env: dict[str, str], reinstall: bool) -> Path:
    py = venv_python()
    if reinstall and VENV.exists():
        info("Removing existing virtualenv (--reinstall).")
        shutil.rmtree(VENV)
    if not py.is_file():
        info(f"Creating virtualenv at {VENV} …")
        run([interpreter, "-m", "venv", str(VENV)])
        py = venv_python()
        if not py.is_file():
            die(f"Virtualenv was created but {py} is missing.")

    marker = VENV / ".crimelink-installed"
    need_install = reinstall or not marker.is_file()
    if not need_install:
        probe = subprocess.run(
            [str(py), "-c", "import fastapi, uvicorn, sqlalchemy, networkx"],
            capture_output=True,
        )
        need_install = probe.returncode != 0

    if need_install:
        info("Installing backend Python dependencies (first time can take a few minutes) …")
        info("Upgrading pip/setuptools/wheel first so prebuilt wheels are preferred.")
        run([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], env=env)
        run([str(py), "-m", "pip", "install", "-e", str(BACKEND)], env=env)
        marker.write_text("ok\n", encoding="utf-8")
        info("Backend dependencies installed.")
    else:
        info("Backend virtualenv already present — skipping pip install.")
    return py


def ensure_frontend(npm: str, env: dict[str, str], reinstall: bool) -> None:
    modules = FRONTEND / "node_modules"
    if reinstall and modules.exists():
        info("Removing frontend/node_modules (--reinstall).")
        shutil.rmtree(modules)
    if not modules.exists():
        info("Installing frontend npm packages (first time can take a few minutes) …")
        run([npm, "install"], cwd=str(FRONTEND), env=env)
        info("Frontend dependencies installed.")
    else:
        info("frontend/node_modules already present — skipping npm install.")


def run_bootstrap(py: Path, env: dict[str, str]) -> None:
    """Execute idempotent service checks, DB migrations, and demo dataset bootstrap."""
    info("Bootstrapping storage, database, and demo dataset …")
    result = subprocess.run(
        [str(py), "-m", "app.db.bootstrap"],
        cwd=ROOT,
        env=env,
    )
    if result.returncode != 0:
        die(f"Bootstrap failed (exit code {result.returncode}). See error above.")
    info("Bootstrap complete and verified.")


def spawn(cmd: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    kwargs: dict = {
        "cwd": str(cwd),
        "env": env,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    proc._crimelink_log = log_file  # type: ignore[attr-defined]
    return proc


def stop(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    pid = proc.pid
    if os.name == "nt":
        if proc.poll() is None:
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                proc.wait(timeout=2)
            except Exception:
                pass
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            pass
    else:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=4)
            except Exception:
                pass
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
    log_file = getattr(proc, "_crimelink_log", None)
    if log_file:
        try:
            log_file.close()
        except Exception:
            pass


def banner(
    *,
    nlp_key: bool,
    ai_key: bool,
    open_url: str,
    api_url: str,
    profile: str,
    runtime_context: str = "",
    database: str = "",
) -> None:
    print()
    print("=" * 72)
    print(f"  CrimeLink is running ({profile} profile).")
    if runtime_context:
        print(f"  Runtime environment  : {runtime_context}")
    if database:
        print(f"  Relational store     : {database}")
    print()
    print(f"  Investigator console : {open_url}")
    print(f"  API                  : {api_url}")
    print(f"  Interactive API docs : {api_url}/api/docs")
    print()
    print("  Preloaded Demo Accounts:")
    print("    Administrator : DEMO-ADMIN        / DemoAdmin@2026")
    print("    Investigator  : DEMO-INVESTIGATOR / DemoInvestigator@2026")
    print("    Viewer        : DEMO-VIEWER       / DemoViewer@2026")
    print()
    print("  Persistent Demo Dataset (DEMO-DATASET-001):")
    print("    20 interconnected cases (CR-1024 to CR-1043), 100 people,")
    print("    216 relationships, 300 evidence records, 120 sources,")
    print("    258 timeline events, and 10 investigations.")
    print()
    print("  Hero Investigation Workflow (CR-1024):")
    print("    CR-1024 -> PERSON-001 <-> PERSON-002 -> Communication -> Why?")
    print("    -> E-042 -> S-001 -> real PDF -> Timeline -> INV-0042")
    print()
    if nlp_key or ai_key:
        print("  NLP/AI: API key detected -- model extraction & AI gateway enabled.")
    else:
        print("  NLP/AI: no API key set -- running fully offline on heuristics.")
        print("          Set NVIDIA_API_KEY in .env for NIM-based extraction/reasoning.")
    print()
    print("  Press Ctrl+C to stop both servers.")
    print("=" * 72)
    print()


def _check_env(env: dict[str, str]) -> None:
    """Surface actionable errors for common missing-config situations."""
    if env.get("CRIMELINK_SECRET_KEY", "").startswith("change-me"):
        warn(
            "CRIMELINK_SECRET_KEY is the placeholder value. This is fine for local"
            " development; change it before exposing CrimeLink to a network."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Install (first time) and run CrimeLink.")
    parser.add_argument("--reinstall", action="store_true", help="Re-run pip and npm install.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab.")
    parser.add_argument("--reload", action="store_true", help="Auto-reload API on backend code changes.")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument(
        "--runtime-context",
        choices=["auto", "host", "docker", "production"],
        default=None,
        help=(
            "Override runtime environment detection. 'auto' (default) detects it: "
            "'host' for native Python (Compose service names rewritten to "
            "CRIMELINK_INFRA_HOST + published host port), 'docker' inside a container, "
            "'production' for a deployment (endpoints used exactly as configured)."
        ),
    )
    parser.add_argument(
        "--start-infra",
        action="store_true",
        help=(
            "Start the host-accessible infrastructure containers "
            "(docker-compose.infra.yml) if a required service is missing. "
            "Never removes containers, volumes or data."
        ),
    )
    parser.add_argument(
        "--infra-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for each required infrastructure service (default: 30).",
    )
    args = parser.parse_args()

    api_port = args.api_port
    web_port = args.web_port
    open_url = f"http://127.0.0.1:{web_port}"
    api_url = f"http://127.0.0.1:{api_port}"
    health_url = f"{api_url}/api/v1/health/live"

    # 1. Interpreter / toolchain
    interpreter = require_python()
    npm = require_node()

    if not BACKEND.is_dir() or not FRONTEND.is_dir():
        die("run.py must live at the CrimeLink repository root (next to backend/ and frontend/).")

    env = apply_environment(api_url=api_url, web_port=web_port)
    _check_env(env)

    # 2. Dependency verification
    py = ensure_venv(interpreter, env, reinstall=args.reinstall)
    ensure_frontend(npm, env, reinstall=args.reinstall)

    # 3. Determine the runtime environment (host / docker / production)
    rt = load_runtime_module()
    context = determine_runtime_context(rt, env, args.runtime_context)

    # 4. Resolve infrastructure endpoints — using the ports Docker actually
    #    publishes rather than an assumed mapping.
    discovered = discover_published_host_ports(rt, env)
    plan = build_infra_plan(rt, env, context)
    plan = plan._replace(discovered_ports=discovered)
    report_runtime(rt, plan)
    if discovered:
        info(
            "Host ports read from Docker: "
            + ", ".join(
                f"{SERVICE_LABELS.get(s, s)}={p}" for s, p in sorted(discovered.items())
            )
        )

    # 5. Verify PostgreSQL -> Neo4j -> MinIO -> Redis (only those in use)
    verify_infrastructure(rt, plan, timeout=args.infra_timeout, start=args.start_infra)

    # 6. Idempotent storage checks, migrations, and demo dataset bootstrap
    run_bootstrap(py, env)

    for port, name in ((api_port, "API"), (web_port, "console")):
        if port_open(port):
            die(
                f"Port {port} is already in use ({name}). "
                f"Stop the other process or pass --{'api' if name == 'API' else 'web'}-port."
            )

    api_proc: subprocess.Popen | None = None
    web_proc: subprocess.Popen | None = None
    api_log = RUN_DIR / "api.log"
    web_log = RUN_DIR / "web.log"
    info(f"Starting API on {API_HOST}:{api_port}  (logs: {api_log})")
    uvicorn_cmd = [
        str(py),
        "-m",
        "uvicorn",
        "app.main:create_app",
        "--factory",
        "--app-dir",
        str(BACKEND),
        "--host",
        API_HOST,
        "--port",
        str(api_port),
    ]
    if args.reload:
        uvicorn_cmd.extend(["--reload", "--reload-dir", str(BACKEND / "app")])
    api_proc = spawn(
        uvicorn_cmd,
        cwd=ROOT,
        env=env,
        log_path=api_log,
    )

    web_env = env.copy()
    web_env["CRIMELINK_API"] = api_url
    info(f"Starting console on {WEB_HOST}:{web_port}  (logs: {web_log})")
    web_proc = spawn(
        [npm, "run", "dev", "--", "--host", WEB_HOST, "--port", str(web_port)],
        cwd=FRONTEND,
        env=web_env,
        log_path=web_log,
    )

    def shutdown(_signum=None, _frame=None) -> None:
        info("Shutting down …")
        stop(web_proc)
        stop(api_proc)
        raise SystemExit(0)

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        wait_http(health_url, timeout=90, label="API", proc=api_proc, log_path=api_log)
        wait_http(open_url, timeout=90, label="Console", proc=web_proc, log_path=web_log)

        nlp_key = bool(env.get("CRIMELINK_NIM_API_KEY") or env.get("NVIDIA_API_KEY"))
        ai_key = bool(env.get("CRIMELINK_AI_API_KEY")) or nlp_key
        if plan.backends["relational"] == "postgres":
            postgres_endpoint = plan.endpoints.get("postgres")
            database = f"PostgreSQL {postgres_endpoint.address}" if postgres_endpoint else "PostgreSQL"
        else:
            database = f"SQLite {DATA_DIR / 'crimelink.db'}"
        banner(
            nlp_key=nlp_key,
            ai_key=ai_key,
            open_url=open_url,
            api_url=api_url,
            profile=env.get("CRIMELINK_PROFILE", "embedded"),
            runtime_context=plan.context,
            database=database,
        )

        if not args.no_browser:
            info(f"Opening {open_url} in your browser …")
            try:
                webbrowser.open(open_url, new=2)
            except Exception as exc:
                info(f"Could not open a browser automatically ({exc}). Open {open_url} yourself.")

        while True:
            if api_proc.poll() is not None:
                die(f"API process exited with code {api_proc.returncode}. See {api_log}")
            if web_proc.poll() is not None:
                die(f"Frontend process exited with code {web_proc.returncode}. See {web_log}")
            time.sleep(0.5)
    except SystemExit:
        stop(web_proc)
        stop(api_proc)
        raise
    except KeyboardInterrupt:
        shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
