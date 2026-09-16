#!/usr/bin/env python3
"""One-command CrimeLink launcher.

What the launcher guarantees
----------------------------
``python run.py`` is the documented zero-friction entry point, so it has to fail
*before* it starts work, and it has to fail with the command that fixes the
problem.  A traceback from deep inside the bootstrap tells an operator nothing.
The order is therefore:

1. pick an interpreter (3.13 is the newest supported — see ``require_python``);
2. decide the **runtime context** (``host`` / ``docker`` / ``production``) and
   resolve every infrastructure endpoint through :mod:`app.runtime` — the same
   module ``app.config`` resolves them with, so the launcher and the backend can
   never disagree about where PostgreSQL lives;
3. optionally start ``docker-compose.infra.yml`` (``--start-infra``) and read the
   *actual* published host ports back from Docker instead of assuming them;
4. verify every service the selected adapters require with a real TCP connect
   and stop with an actionable message when one is down — no traceback, no
   half-bootstrapped database;
5. only then create/verify the virtualenv, bootstrap the demo dataset and start
   the API and the investigator console.

Nothing here is destructive: the launcher never runs ``docker compose down``,
never removes a volume, never recreates a container it did not start and never
reseeds an existing dataset.

``app.runtime`` is imported with the *system* interpreter on purpose: it is
standard library only, precisely so this file can apply the runtime contract
before the virtualenv exists.
"""

from __future__ import annotations

import argparse
import functools
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
ENV_FILE = ROOT / ".env"

INFRA_COMPOSE_FILE = "docker-compose.infra.yml"
INFRA_COMPOSE_PATH = ROOT / INFRA_COMPOSE_FILE
INFRA_START_COMMAND = f"docker compose -f {INFRA_COMPOSE_FILE} up -d"
INFRA_STATUS_COMMAND = f"docker compose -f {INFRA_COMPOSE_FILE} ps"
INFRA_CONTAINER_PREFIX = "crimelink-"

#: Seconds the launcher waits for infrastructure to become reachable.  Applies
#: both to ``--start-infra`` (containers becoming healthy) and to the
#: verification pass that follows it.
DEFAULT_INFRA_TIMEOUT = 120.0
#: Per-attempt TCP connect timeout while probing a service.
PROBE_CONNECT_TIMEOUT = 2.0

#: Human-readable service names, in the order they are verified.
SERVICE_LABELS = {
    "postgres": "PostgreSQL",
    "neo4j": "Neo4j",
    "minio": "MinIO",
    "redis": "Redis",
}

# `app.runtime` is standard library only (see its module docstring), so the
# launcher can share endpoint resolution with the backend before the virtualenv
# exists.  Inserted at the front of sys.path, and nothing else from `app` is
# imported here: `app.config` needs pydantic, which only the venv has.
sys.path.insert(0, str(BACKEND))
from app import runtime

# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #

def die(message: str, code: int = 1) -> None:
    print(f"\n[CrimeLink] ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def info(message: str) -> None:
    print(f"[CrimeLink] {message}", flush=True)


# --------------------------------------------------------------------------- #
# Interpreter / virtualenv / frontend
# --------------------------------------------------------------------------- #

def require_python() -> str:
    if sys.version_info[:2] <= (3, 13):
        return sys.executable
    if os.name == "nt":
        launcher = next((p for p in ("py",) if _which(p)), None)
        if launcher:
            probe = subprocess.run(
                [launcher, "-3.13", "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True, check=False
            )
            if probe.returncode == 0 and probe.stdout.strip():
                selected = probe.stdout.strip()
                info(f"Python {sys.version_info.major}.{sys.version_info.minor} detected; using Python 3.13 at {selected}.")
                return selected
    die(
        "Python 3.13 is required when running with a newer interpreter.\n"
        "Install Python 3.11–3.13 and re-run, or point this launcher at it "
        "directly:\n"
        "    py -3.13 run.py"
    )
    return sys.executable


def _which(name: str) -> str | None:
    return shutil.which(name)


def venv_python() -> Path:
    for candidate in (ROOT / ".venv", BACKEND / ".venv"):
        py = candidate / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if py.is_file():
            return py
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


#: Import probe used both to decide whether an install is needed and to verify
#: the bootstrap dependencies afterwards — the same four modules, so the
#: launcher cannot install one thing and check another.
DEPENDENCY_PROBE = "import psycopg2, asyncpg, sqlalchemy, alembic; print('ok')"


def dependencies_complete(py: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [str(py), "-c", DEPENDENCY_PROBE], capture_output=True, text=True, check=False
    )
    detail = (result.stderr.strip() or result.stdout.strip())
    return result.returncode == 0, detail


def ensure_venv(interpreter: str, *, reinstall: bool = False) -> Path:
    py = venv_python()
    if not py.exists():
        info("Creating backend virtualenv …")
        subprocess.check_call([interpreter, "-m", "venv", str(py.parent.parent)])

    complete, _detail = dependencies_complete(py)
    if complete and not reinstall:
        info("Backend virtualenv dependencies verified — skipping pip install.")
        return py

    if complete and reinstall:
        info("--reinstall: reinstalling backend dependencies …")
    else:
        info("Backend virtualenv dependencies incomplete — installing ./backend …")
    command = [str(py), "-m", "pip", "install", "--upgrade"]
    if reinstall:
        command.append("--force-reinstall")
    command.append(str(ROOT / "backend"))
    try:
        subprocess.check_call(command, cwd=ROOT)
    except subprocess.CalledProcessError as exc:
        die(
            f"Installing backend dependencies failed (exit code {exc.returncode}).\n"
            "If pip tried to build NumPy from source, upgrade pip first and retry:\n"
            f"    {py} -m pip install --upgrade pip setuptools wheel"
        )
    return py


def ensure_frontend(*, reinstall: bool = False) -> None:
    node_modules = FRONTEND / "node_modules"
    if node_modules.is_dir() and not reinstall:
        info("frontend/node_modules already present — skipping npm install.")
        return
    npm = _which("npm")
    if not npm:
        die("npm is required to install frontend dependencies.")
    if reinstall and (FRONTEND / "package-lock.json").is_file():
        subprocess.check_call([npm, "ci"], cwd=FRONTEND)
        return
    subprocess.check_call([npm, "install"], cwd=FRONTEND)


def verify_bootstrap_dependencies(py: Path) -> None:
    info("Verifying backend bootstrap dependencies …")
    complete, detail = dependencies_complete(py)
    if not complete:
        die(
            "Backend bootstrap dependencies are incomplete.\n"
            "Install them into the backend virtualenv and retry:\n"
            f"    {py} -m pip install {ROOT / 'backend'}\n"
            f"    python run.py --reinstall\n{detail}"
        )
    info("Backend bootstrap dependencies verified.")


# --------------------------------------------------------------------------- #
# .env reading
#
# The backend reads `.env` through pydantic-settings; the launcher cannot import
# pydantic before the virtualenv exists, so it parses the same file with the
# same precedence: a real environment variable wins over the file.
# --------------------------------------------------------------------------- #

def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse `KEY=value` pairs from *path* (missing file -> empty mapping).

    Deliberately minimal but faithful to what pydantic-settings accepts:
    comments and blank lines are skipped, an optional ``export`` prefix is
    ignored, and one layer of matching quotes is removed from the value.
    """
    values: dict[str, str] = {}
    target = ENV_FILE if path is None else path
    if not target.is_file():
        return values
    for raw_line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def launcher_configuration(file_values: dict[str, str] | None = None) -> dict[str, str]:
    """`.env` values overlaid with the real environment (environment wins)."""
    values = dict(read_env_file() if file_values is None else file_values)
    values.update(
        {key: value for key, value in os.environ.items() if key.startswith("CRIMELINK_")}
    )
    return values


# --------------------------------------------------------------------------- #
# Docker interaction (never destructive)
# --------------------------------------------------------------------------- #

@functools.lru_cache(maxsize=1)
def docker_compose_argv() -> tuple[str, ...] | None:
    """``("docker", "compose")`` or the legacy ``docker-compose`` binary."""
    if _which("docker"):
        probe = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, text=True, check=False
        )
        if probe.returncode == 0:
            return ("docker", "compose")
    legacy = _which("docker-compose")
    return (legacy,) if legacy else None


@functools.lru_cache(maxsize=1)
def docker_daemon_available() -> bool:
    """True when the Docker CLI can talk to a daemon (Docker Desktop running)."""
    if not _which("docker"):
        return False
    try:
        probe = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def published_host_port(service: str) -> int | None:
    """Read the actual published host port of *service* back from Docker.

    ``docker-compose.infra.yml`` publishes ``${CRIMELINK_*_HOST_PORT:-<default>}``
    on loopback, so the authoritative answer is what Docker reports, not what the
    launcher assumes.  Returns ``None`` when the container is absent or Docker is
    unreachable — the configured value then stands.
    """
    if not docker_daemon_available():
        return None
    container_port = runtime.SERVICE_CONTAINER_PORTS.get(service)
    if not container_port:
        return None
    try:
        result = subprocess.run(
            ["docker", "port", f"{INFRA_CONTAINER_PREFIX}{service}", str(container_port)],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        _, _, port = line.strip().rpartition(":")
        if port.isdigit():
            return int(port)
    return None


@dataclass(frozen=True)
class ContainerState:
    """What Docker reports about one infrastructure container."""

    running: bool
    #: ``healthy`` / ``starting`` / ``unhealthy`` / ``none`` (no healthcheck) /
    #: ``missing`` (no such container).
    health: str

    @property
    def summary(self) -> str:
        return self.health if self.running else f"not running, last health {self.health}"


def container_state(service: str) -> ContainerState:
    """Read running state and health of ``crimelink-<service>``.

    Both halves matter: Docker keeps the *last* health status of a stopped
    container, so health alone would call a stopped PostgreSQL "healthy".
    """
    if not docker_daemon_available():
        return ContainerState(running=False, health="missing")
    try:
        result = subprocess.run(
            [
                "docker", "inspect", "--format",
                "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                f"{INFRA_CONTAINER_PREFIX}{service}",
            ],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ContainerState(running=False, health="missing")
    if result.returncode != 0:
        return ContainerState(running=False, health="missing")
    running_text, _, health = result.stdout.strip().partition(" ")
    return ContainerState(
        running=running_text.strip().lower() == "true",
        health=(health.strip() or "none").lower(),
    )


def discover_host_ports(
    services: list[str], configured: dict[str, str]
) -> dict[str, str]:
    """Published host ports Docker reports, as ``CRIMELINK_*_HOST_PORT`` values.

    An explicit value in the environment or in ``.env`` always wins (README:
    "an explicit value in `.env` still wins"), so a discovered port only fills a
    gap — and only where it actually differs from the default the launcher would
    otherwise use.
    """
    discovered: dict[str, str] = {}
    if not services or not docker_daemon_available():
        return discovered
    for service in services:
        variable = runtime.SERVICE_HOST_PORT_VARS.get(service)
        if not variable or configured.get(variable):
            continue
        port = published_host_port(service)
        if port and port != runtime.SERVICE_CONTAINER_PORTS.get(service):
            discovered[variable] = str(port)
    return discovered


# --------------------------------------------------------------------------- #
# Reachability
# --------------------------------------------------------------------------- #

def probe_tcp(host: str, port: int, timeout: float = PROBE_CONNECT_TIMEOUT) -> bool:
    """True when something accepts a TCP connection on ``host:port``.

    Every address the name resolves to is tried (``localhost`` is commonly
    ``::1`` *and* ``127.0.0.1``), which is exactly what psycopg2 does — so this
    probe cannot pass where the driver would fail, or fail where it would pass.
    """
    if not host or not port:
        return False
    try:
        addresses = socket.getaddrinfo(host, int(port), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, ValueError):
        return False
    for family, socktype, proto, _canonname, sockaddr in addresses:
        try:
            with socket.socket(family, socktype, proto) as sock:
                sock.settimeout(timeout)
                sock.connect(sockaddr)
            return True
        except OSError:
            continue
    return False


# --------------------------------------------------------------------------- #
# The launch plan: runtime context, adapters, endpoints
# --------------------------------------------------------------------------- #

@dataclass
class LaunchPlan:
    """Everything the launcher decided before it started anything."""

    context: str
    profile: str
    environment: str
    infra_host: str
    backends: dict[str, str]
    services: list[str]
    endpoints: dict[str, runtime.EndpointResolution]
    #: Variables pinned into every child process (bootstrap, API, console).
    child_env: dict[str, str] = field(default_factory=dict)

    @property
    def context_description(self) -> str:
        return runtime.CONTEXT_DESCRIPTIONS.get(self.context, "")

    def endpoint_for(self, service: str) -> runtime.EndpointResolution | None:
        return self.endpoints.get(runtime.SERVICE_PRIMARY_FIELDS.get(service, ""))

    @property
    def backend_summary(self) -> str:
        order = ("relational", "graph", "object_store", "broker")
        return ", ".join(f"{kind}={self.backends[kind]}" for kind in order)


def build_plan(
    configured: dict[str, str] | None = None,
    *,
    cli_context: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> LaunchPlan:
    """Resolve the runtime context, adapters and endpoints for this launch.

    Uses :mod:`app.runtime` exclusively, so the launcher's answer is the same
    answer ``app.config`` reaches when the backend starts a moment later.
    """
    values = launcher_configuration() if configured is None else dict(configured)
    if extra_env:
        values.update(extra_env)

    profile = (values.get("CRIMELINK_PROFILE") or "embedded").strip().lower()
    environment = (values.get("CRIMELINK_ENVIRONMENT") or "dev").strip().lower()
    explicit = cli_context or values.get(runtime.RUNTIME_CONTEXT_VAR)
    context = runtime.resolve_runtime_context(
        explicit=explicit, profile=profile, environment=environment
    )

    backends = runtime.effective_backends(
        profile=profile,
        relational=values.get("CRIMELINK_RELATIONAL_BACKEND"),
        graph=values.get("CRIMELINK_GRAPH_BACKEND"),
        object_store=values.get("CRIMELINK_OBJECT_STORE_BACKEND"),
        broker=values.get("CRIMELINK_BROKER_BACKEND"),
    )
    services = runtime.required_services(backends)

    # Docker only matters in the host context: that is where a published port is
    # how native Python reaches a container at all.
    discovered = (
        discover_host_ports(services, values) if context == runtime.CONTEXT_HOST else {}
    )
    if discovered:
        values.update(discovered)

    infra_host = (
        values.get(runtime.INFRA_HOST_VAR) or runtime.DEFAULT_INFRA_HOST
    ).strip() or runtime.DEFAULT_INFRA_HOST
    endpoints = runtime.resolve_infrastructure(
        context=context,
        env=values,
        infra_host=infra_host,
        values=runtime.endpoint_values_from_env(values),
    )

    child_env = {runtime.RUNTIME_CONTEXT_VAR: context, **discovered}
    return LaunchPlan(
        context=context,
        profile=profile,
        environment=environment,
        infra_host=infra_host,
        backends=backends,
        services=services,
        endpoints=endpoints,
        child_env=child_env,
    )


def print_plan(plan: LaunchPlan) -> None:
    info(
        f"Runtime environment: {plan.context} — {plan.context_description}"
    )
    info(
        f"Profile: {plan.profile} (environment {plan.environment}) — "
        f"adapters follow the profile unless overridden"
    )
    info(f"Adapters: {plan.backend_summary}")
    for service in plan.services:
        resolution = plan.endpoint_for(service)
        if resolution is None:
            continue
        note = ""
        if resolution.rewritten:
            note = (
                f"  [host runtime: Compose name '{resolution.configured_host}' -> "
                f"{resolution.host}:{resolution.port}]"
            )
        info(f"  {SERVICE_LABELS.get(service, service):<10} : {resolution.address}{note}")


# --------------------------------------------------------------------------- #
# Verification and (optional) infrastructure start
# --------------------------------------------------------------------------- #

def verify_services(plan: LaunchPlan, timeout: float) -> list[str]:
    """TCP-verify every required service; return the ones still unreachable.

    Each service is announced once, then retried until *timeout* so a stack that
    is still warming up gets the same chance the bootstrap gives it.  A service
    is reported as soon as it answers.
    """
    if not plan.services:
        info(
            "No infrastructure services required — embedded adapters "
            f"({plan.backend_summary})."
        )
        return []

    pending: list[str] = []
    for service in plan.services:
        resolution = plan.endpoint_for(service)
        if resolution is None:
            continue
        pending.append(service)
        info(
            f"Verifying {SERVICE_LABELS.get(service, service)} at "
            f"{resolution.address} …"
        )

    deadline = time.time() + max(timeout, 1.0)
    while pending:
        still_pending: list[str] = []
        for service in pending:
            resolution = plan.endpoint_for(service)
            if resolution is None:
                continue
            if probe_tcp(resolution.host, resolution.port or 0):
                info(
                    f"{SERVICE_LABELS.get(service, service)} is reachable at "
                    f"{resolution.address}."
                )
            else:
                still_pending.append(service)
        pending = still_pending
        if not pending or time.time() >= deadline:
            break
        time.sleep(1.0)

    for service in pending:
        resolution = plan.endpoint_for(service)
        if resolution is None:
            continue
        label = SERVICE_LABELS.get(service, service)
        info(f"{label} is not reachable at {resolution.address}.")
    return pending


def start_infra(services: list[str], timeout: float, *, host: str) -> None:
    """Start ``docker-compose.infra.yml`` and wait for the services to be ready.

    Idempotent and non-destructive: ``up -d`` creates what is missing and leaves
    running containers, volumes and seeded data alone.  Only the services the
    selected adapters actually need are named, so an embedded-profile launch does
    not drag in Neo4j.
    """
    compose = docker_compose_argv()
    if compose is None:
        die(
            "Docker is required by --start-infra but no Docker CLI was found.\n"
            "Install Docker Desktop, or start the services yourself:\n"
            f"    {INFRA_START_COMMAND}"
        )
    if not docker_daemon_available():
        die(
            "Docker is installed but the daemon is not responding "
            "(`docker info` failed).\n"
            "Start Docker Desktop, wait for it to finish starting, and retry:\n"
            "    python run.py --start-infra"
        )
    if not INFRA_COMPOSE_PATH.is_file():
        die(
            f"{INFRA_COMPOSE_FILE} is missing from {ROOT}.\n"
            "run.py must live at the CrimeLink repository root."
        )
    if not ENV_FILE.is_file():
        die(
            f"{INFRA_COMPOSE_FILE} needs the CRIMELINK_* passwords from a .env file.\n"
            "Create one first (then set the passwords and CRIMELINK_SECRET_KEY):\n"
            "    cp .env.example .env"
        )

    command = [*compose, "-f", INFRA_COMPOSE_FILE, "up", "-d", *(services or [])]
    info(f"Starting infrastructure: {' '.join(command)}")
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        die(
            f"{' '.join(command)} failed (exit code {result.returncode}).\n"
            "Common cause: CRIMELINK_POSTGRES_PASSWORD / CRIMELINK_NEO4J_PASSWORD /\n"
            "CRIMELINK_MINIO_SECRET_KEY are still the placeholders from .env.example."
        )

    wanted = services or list(SERVICE_LABELS)
    deadline = time.time() + max(timeout, 1.0)
    info(f"Waiting up to {int(timeout)}s for {', '.join(wanted)} to become healthy …")
    while True:
        not_ready = []
        for service in wanted:
            state = container_state(service)
            ready = state.running and state.health == "healthy"
            if not ready and state.running and state.health == "none":
                # `none` means the image defines no healthcheck: the published
                # port answering a TCP connect is the only signal available.
                port = published_host_port(service) or runtime.SERVICE_CONTAINER_PORTS.get(service, 0)
                ready = probe_tcp(host, port)
            if not ready:
                not_ready.append(f"{service} ({state.summary})")
        if not not_ready or time.time() >= deadline:
            if not_ready:
                die(
                    "Infrastructure did not become healthy in time: "
                    + ", ".join(not_ready)
                    + "\nCheck the logs with:\n"
                    f"    {' '.join(compose)} -f {INFRA_COMPOSE_FILE} logs --tail=50"
                )
            info("Infrastructure is up.")
            return
        time.sleep(2.0)


def infrastructure_failure_message(plan: LaunchPlan, unreachable: list[str]) -> str:
    """Actionable explanation of exactly what is missing and how to fix it."""
    lines = ["Required infrastructure is not running.", ""]
    for service in plan.services:
        resolution = plan.endpoint_for(service)
        if resolution is None:
            continue
        label = SERVICE_LABELS.get(service, service)
        state = "NOT REACHABLE" if service in unreachable else "ok"
        lines.append(f"  {label:<10} : {resolution.address}  [{state}]")
    lines += [
        "",
        "Start the CrimeLink infrastructure services and retry:",
        f"    {INFRA_START_COMMAND}",
        "or let this launcher start them for you:",
        "    python run.py --start-infra",
        "Check what is running with:",
        f"    {INFRA_STATUS_COMMAND}",
        "Existing containers, volumes and the persisted demo dataset are left untouched.",
    ]
    if not docker_daemon_available():
        lines += [
            "",
            "Note: the Docker CLI is not answering on this machine. Start Docker",
            "Desktop (or your Docker daemon) first — the stack cannot be started",
            "without it.",
        ]
    lines += [
        "",
        f"Runtime context: {plan.context} ({plan.context_description})",
        f"Adapters       : {plan.backend_summary}",
    ]
    for line in _endpoint_lines(plan):
        lines.append(f"                 {line}")
    lines += [
        "",
        "CrimeLink does not fall back to SQLite or an in-memory database: the",
        "configured adapter is the system of record for this launch.",
        "",
        "To run without any containers instead, select the embedded profile in .env:",
        "    CRIMELINK_PROFILE=embedded",
    ]
    return "\n".join(lines)


def _endpoint_lines(plan: LaunchPlan) -> list[str]:
    """Secret-free endpoint summary (credentials stripped by app.runtime)."""
    lines: list[str] = []
    for service in plan.services:
        resolution = plan.endpoint_for(service)
        if resolution is not None:
            lines.append(f"{service}: {resolution.redacted_value}")
    return lines


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-command CrimeLink launcher.")
    parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )
    parser.add_argument(
        "--start-infra",
        action="store_true",
        help=f"start {INFRA_COMPOSE_FILE} before bootstrapping (never destructive)",
    )
    parser.add_argument(
        "--runtime-context",
        choices=[c for c in runtime.RUNTIME_CONTEXTS if c != runtime.CONTEXT_AUTO],
        default=None,
        help="override runtime context detection (default: auto-detect)",
    )
    parser.add_argument(
        "--infra-timeout",
        type=float,
        default=DEFAULT_INFRA_TIMEOUT,
        help=(
            "seconds to wait for infrastructure to become reachable "
            f"(default: {DEFAULT_INFRA_TIMEOUT:.0f})"
        ),
    )
    parser.add_argument(
        "--reinstall",
        action="store_true",
        help="reinstall backend (pip) and frontend (npm) dependencies",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    interpreter = require_python()
    if not BACKEND.is_dir() or not FRONTEND.is_dir():
        die("run.py must live at the CrimeLink repository root (next to backend/ and frontend/).")
    if not ENV_FILE.is_file():
        info(
            "No .env found — using the embedded profile defaults "
            "(SQLite, embedded graph, local object store)."
        )

    # 1. Decide the runtime context and resolve every endpoint, before anything
    #    is installed or started: the answer determines what must be running.
    plan = build_plan(cli_context=args.runtime_context)
    print_plan(plan)

    # 2. Optionally start the host-accessible stack, then re-read the published
    #    ports so verification and the backend agree with Docker, not with us.
    if args.start_infra:
        start_infra(plan.services, args.infra_timeout, host=plan.infra_host)
        plan = build_plan(cli_context=args.runtime_context)
        print_plan(plan)

    # 3. Verify the services the selected adapters require.  This is the check
    #    that used to happen as a traceback inside the bootstrap.  Without
    #    --start-infra the wait is short: nothing is being started, so a service
    #    that does not answer promptly is down.
    wait = args.infra_timeout if args.start_infra else min(args.infra_timeout, 15.0)
    unreachable = verify_services(plan, wait)
    if unreachable:
        die(infrastructure_failure_message(plan, unreachable))

    # 4. Now that the data services answer, prepare the interpreters.
    py = ensure_venv(interpreter, reinstall=args.reinstall)
    ensure_frontend(reinstall=args.reinstall)
    verify_bootstrap_dependencies(py)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env.update(plan.child_env)

    info("Bootstrapping storage, database, and demo dataset …")
    bootstrap = subprocess.run(
        [str(py), "-c", "from app.db.bootstrap import bootstrap_demo_dataset; bootstrap_demo_dataset()"],
        cwd=BACKEND,
        env=env,
        check=False,
    )
    if bootstrap.returncode != 0:
        die(f"Bootstrap failed (exit code {bootstrap.returncode}). See error above.")

    info("Starting CrimeLink backend …")
    api = subprocess.Popen(
        [str(py), "-m", "uvicorn", "app.main:create_app", "--factory", "--app-dir", str(BACKEND), "--host", "0.0.0.0", "--port", "8000"],
        cwd=ROOT,
        env=env,
    )
    try:
        npm = _which("npm")
        if not npm:
            die("npm is required to start the frontend.")
        info("Starting CrimeLink console …")
        web = subprocess.Popen(
            [npm, "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"],
            cwd=FRONTEND,
            env=env,
        )
        try:
            if not args.no_browser:
                import webbrowser
                webbrowser.open("http://127.0.0.1:5173", new=2)
            api.wait()
        finally:
            web.terminate()
    finally:
        api.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
