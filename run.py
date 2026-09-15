#!/usr/bin/env python3
"""One-command CrimeLink launcher.

Installs Python and Node dependencies on first run, bootstraps storage and
the persistent 20-case demo dataset idempotently, starts the FastAPI backend
and Vite console, and opens a browser.

Quick start::

    python run.py
    python run.py --reinstall
    python run.py --no-browser
    python run.py --reload
    python run.py --start-infra

Startup path::

    dependency verification
      -> runtime environment detection
      -> infrastructure endpoint resolution
      -> PostgreSQL -> Neo4j -> MinIO -> Redis verification
      -> idempotent demo bootstrap
      -> backend + frontend
"""

# Preserve the canonical launcher implementation from the merged runtime-aware
# branch. The only repaired section is the previously committed Git conflict:
# both dependency preflight AND runtime/infrastructure verification are required.

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

def resolve_venv() -> Path:
    """Resolve the virtualenv directory, preferring an existing venv in ROOT or backend/."""
    for candidate in (ROOT / ".venv", BACKEND / ".venv"):
        if candidate.is_dir():
            script_dir = candidate / ("Scripts" if os.name == "nt" else "bin")
            py_name = "python.exe" if os.name == "nt" else "python"
            if (script_dir / py_name).is_file():
                return candidate
    return ROOT / ".venv"

VENV = resolve_venv()
RUN_DIR = ROOT / ".run"
DATA_DIR = ROOT / "var" / "data"
OBJECT_DIR = ROOT / "var" / "objects"
RUNTIME_MODULE = BACKEND / "app" / "runtime.py"
INFRA_COMPOSE_FILE = ROOT / "docker-compose.infra.yml"
INFRA_COMPOSE_COMMAND = "docker compose -f docker-compose.infra.yml up -d"
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
MAX_PYTHON_TESTED = (3, 13)
MIN_NODE_MAJOR = 18
REQUIRED_BACKEND_MODULES: tuple[str, ...] = (
    "fastapi", "uvicorn", "pydantic", "pydantic_settings", "sqlalchemy",
    "psycopg2", "asyncpg", "aiosqlite", "alembic", "networkx", "structlog",
)

def die(message: str, code: int = 1) -> None:
    print(f"\n[CrimeLink] ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)

def info(message: str) -> None:
    print(f"[CrimeLink] {message}", flush=True)

def warn(message: str) -> None:
    print(f"[CrimeLink] WARNING: {message}", file=sys.stderr, flush=True)

def venv_bin(name: str, venv: Path | None = None) -> Path:
    target = venv or VENV
    if os.name == "nt":
        return target / "Scripts" / (name + (".exe" if not name.endswith(".exe") else ""))
    return target / "bin" / name

def venv_python(venv: Path | None = None) -> Path:
    return venv_bin("python.exe" if os.name == "nt" else "python", venv=venv)

def load_dotenv(path: Path) -> None:
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
    os.environ.setdefault("CRIMELINK_SYNTHETIC_DATA_ROOT", "backend/CrimeLink_Synthetic_Corpus_v1")
    os.environ["CRIMELINK_CORS_ORIGINS"] = json.dumps([
        f"http://127.0.0.1:{web_port}", f"http://localhost:{web_port}", api_url,
    ])
    os.environ["CRIMELINK_SYNTHETIC_CORPUS_ENABLED"] = "false"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OBJECT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    return os.environ.copy()

def require_python() -> str:
    if sys.version_info < MIN_PYTHON:
        die(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required (found {sys.version.split()[0]}).")
    if sys.version_info <= MAX_PYTHON_TESTED:
        return sys.executable
    if os.name == "nt":
        compatible = shutil.which("py")
        if compatible:
            probe = subprocess.run([compatible, "-3.13", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True)
            if probe.returncode == 0 and probe.stdout.strip():
                selected = probe.stdout.strip()
                info(f"Python {sys.version_info.major}.{sys.version_info.minor} detected; using Python 3.13 at {selected}.")
                return selected
    die(f"Python {sys.version_info.major}.{sys.version_info.minor} is newer than the tested range and no Python 3.13 interpreter was found.")
    return sys.executable

def require_node() -> str:
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        die("Node.js 18+ (with npm) is required for the investigator console.")
    out = subprocess.check_output([node, "-v"], text=True, stderr=subprocess.STDOUT).strip().lstrip("v")
    try:
        major = int(out.split(".")[0])
    except ValueError:
        die(f"Could not parse Node version '{out}'.")
    if major < MIN_NODE_MAJOR:
        die(f"Node.js {MIN_NODE_MAJOR}+ is required (found v{out}).")
    return npm

def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0

class InfraPlan(NamedTuple):
    context: str
    backends: dict[str, str]
    required: list[str]
    resolutions: dict[str, Any]
    endpoints: dict[str, Any]
    discovered_ports: dict[str, int]

def load_runtime_module() -> Any:
    if not RUNTIME_MODULE.is_file():
        die(f"{RUNTIME_MODULE} is missing.")
    spec = importlib.util.spec_from_file_location("crimelink_runtime", RUNTIME_MODULE)
    if spec is None or spec.loader is None:
        die(f"Could not import {RUNTIME_MODULE}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(spec.name, None)
        die(f"Could not load {RUNTIME_MODULE}: {exc}")
    return module

def determine_runtime_context(rt: Any, env: dict[str, str], override: str | None) -> str:
    explicit = (override or env.get(rt.RUNTIME_CONTEXT_VAR) or "").strip() or None
    try:
        context = rt.resolve_runtime_context(explicit=explicit, profile=env.get("CRIMELINK_PROFILE", "embedded"), environment=env.get("CRIMELINK_ENVIRONMENT", "dev"))
    except ValueError as exc:
        die(str(exc))
    env[rt.RUNTIME_CONTEXT_VAR] = context
    return context

def docker_executable() -> str | None:
    return shutil.which("docker")

def docker_compose_command() -> list[str] | None:
    docker = docker_executable()
    return [docker, "compose"] if docker else None

def discover_published_host_ports(rt: Any, env: dict[str, str]) -> dict[str, int]:
    result: dict[str, int] = {}
    docker = docker_executable()
    if not docker:
        return result
    for service, container in INFRA_CONTAINER_NAMES.items():
        try:
            probe = subprocess.run([docker, "port", container, str(rt.SERVICE_CONTAINER_PORTS[service])], capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        if probe.returncode != 0:
            continue
        text = probe.stdout.strip().splitlines()
        for line in text:
            if ":" in line:
                try:
                    result[service] = int(line.rsplit(":", 1)[1])
                    break
                except ValueError:
                    pass
    return result

def build_infra_plan(rt: Any, env: dict[str, str], context: str) -> InfraPlan:
    try:
        plan = rt.build_infrastructure_plan(env, context)
    except AttributeError:
        plan = rt.build_infra_plan(env, context)
    return InfraPlan(plan.context, dict(plan.backends), list(plan.required), dict(plan.resolutions), dict(plan.endpoints), {})

def report_runtime(rt: Any, plan: InfraPlan) -> None:
    info(f"Runtime environment: {plan.context}")
    for name, endpoint in plan.endpoints.items():
        info(f"  {SERVICE_LABELS.get(name, name)} : {getattr(endpoint, 'address', endpoint)}")

def verify_infrastructure(rt: Any, plan: InfraPlan, timeout: int, start: bool = False) -> None:
    if start:
        docker = docker_compose_command()
        if not docker or not INFRA_COMPOSE_FILE.is_file():
            die("Docker Compose is required for --start-infra, but it is unavailable.")
        result = subprocess.run(docker + ["-f", str(INFRA_COMPOSE_FILE), "up", "-d"], text=True)
        if result.returncode != 0:
            die("Could not start the local infrastructure services.")
    try:
        rt.verify_infrastructure(plan, timeout=timeout)
    except AttributeError:
        verifier = getattr(rt, "verify_required_infrastructure", None)
        if verifier is None:
            die("Runtime module has no infrastructure verifier.")
        verifier(plan, timeout=timeout)

def ensure_venv(interpreter: str, env: dict[str, str], reinstall: bool = False) -> Path:
    py = venv_python()
    if not py.exists():
        info("Creating backend virtualenv …")
        subprocess.check_call([interpreter, "-m", "venv", str(VENV)])
    if reinstall:
        subprocess.check_call([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    verify = subprocess.run([str(py), "-c", "import fastapi, sqlalchemy, psycopg2, asyncpg, aiosqlite, alembic, networkx, structlog"], capture_output=True, text=True)
    if verify.returncode != 0 or reinstall:
        info("Installing/synchronizing backend dependencies …")
        requirements = BACKEND / "requirements.txt"
        if requirements.is_file():
            subprocess.check_call([str(py), "-m", "pip", "install", "-r", str(requirements)])
        else:
            die("backend/requirements.txt is missing.")
    else:
        info("Backend virtualenv dependencies verified — skipping pip install.")
    return py

def ensure_frontend(npm: str, env: dict[str, str], reinstall: bool = False) -> None:
    if reinstall or not (FRONTEND / "node_modules").is_dir():
        info("Installing frontend dependencies …")
        subprocess.check_call([npm, "install"], cwd=FRONTEND, env=env)
    else:
        info("frontend/node_modules already present — skipping npm install.")

def verify_bootstrap_dependencies(py: Path) -> None:
    info("Verifying backend bootstrap dependencies …")
    probe = subprocess.run([str(py), "-c", "import psycopg2, asyncpg, sqlalchemy, alembic"], capture_output=True, text=True)
    if probe.returncode != 0:
        die("Backend bootstrap dependencies are incomplete. Re-run with --reinstall.")
    info("Backend bootstrap dependencies verified.")

def run_bootstrap(py: Path, env: dict[str, str]) -> None:
    code = "from app.db.bootstrap import run_bootstrap; run_bootstrap()"
    result = subprocess.run([str(py), "-c", code], cwd=BACKEND, env=env, text=True)
    if result.returncode != 0:
        die(f"Bootstrap failed (exit code {result.returncode}). See error above.")

def spawn(cmd: list[str], cwd: Path, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    log = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)

def stop(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

def wait_http(url: str, timeout: int, label: str, proc: subprocess.Popen | None, log_path: Path) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            die(f"{label} process exited with code {proc.returncode}. See {log_path}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)
    die(f"Timed out waiting for {label} at {url}. See {log_path}")

def banner(*, nlp_key: bool, ai_key: bool, open_url: str, api_url: str, profile: str, runtime_context: str, database: str) -> None:
    print("\n" + "=" * 72)
    print("CrimeLink is running")
    print(f"Console: {open_url}")
    print(f"API: {api_url}")
    print(f"Profile: {profile}")
    print(f"Runtime: {runtime_context}")
    print(f"Database: {database}")
    print(f"NLP key: {'configured' if nlp_key else 'not configured'}")
    print(f"AI key: {'configured' if ai_key else 'not configured'}")
    print("=" * 72)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-command CrimeLink launcher")
    parser.add_argument("--reinstall", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--start-infra", action="store_true")
    parser.add_argument("--infra-timeout", type=int, default=45)
    parser.add_argument("--runtime-context", choices=("host", "docker", "production"), default=None)
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    return parser

def main() -> int:
    args = build_parser().parse_args()
    api_port = args.api_port
    web_port = args.web_port
    api_url = f"http://127.0.0.1:{api_port}"
    open_url = f"http://127.0.0.1:{web_port}"
    health_url = f"{api_url}/api/v1/health/live"

    interpreter = require_python()
    npm = require_node()
    if not BACKEND.is_dir() or not FRONTEND.is_dir():
        die("run.py must live at the CrimeLink repository root (next to backend/ and frontend/).")
    env = apply_environment(api_url=api_url, web_port=web_port)
    py = ensure_venv(interpreter, env, reinstall=args.reinstall)
    ensure_frontend(npm, env, reinstall=args.reinstall)

    # Both sides of the previously committed merge are required.
    # Dependency preflight prevents an incomplete venv from reaching bootstrap.
    verify_bootstrap_dependencies(py)

    # Runtime-aware infrastructure verification must happen before bootstrap.
    rt = load_runtime_module()
    context = determine_runtime_context(rt, env, args.runtime_context)
    discovered = discover_published_host_ports(rt, env)
    plan = build_infra_plan(rt, env, context)
    if discovered:
        # Preserve the plan's immutable shape while allowing runtime-discovered ports.
        plan = plan._replace(discovered_ports=discovered)
    report_runtime(rt, plan)
    if discovered:
        info("Host ports read from Docker: " + ", ".join(f"{SERVICE_LABELS.get(s, s)}={p}" for s, p in sorted(discovered.items())))
    verify_infrastructure(rt, plan, timeout=args.infra_timeout, start=args.start_infra)

    run_bootstrap(py, env)

    for port, name in ((api_port, "API"), (web_port, "console")):
        if port_open(port):
            die(f"Port {port} is already in use ({name}). Stop the other process or pass the corresponding port flag.")

    api_proc: subprocess.Popen | None = None
    web_proc: subprocess.Popen | None = None
    api_log = RUN_DIR / "api.log"
    web_log = RUN_DIR / "web.log"
    info(f"Starting API on {API_HOST}:{api_port}  (logs: {api_log})")
    uvicorn_cmd = [str(py), "-m", "uvicorn", "app.main:create_app", "--factory", "--app-dir", str(BACKEND), "--host", API_HOST, "--port", str(api_port)]
    if args.reload:
        uvicorn_cmd.append("--reload")
    api_proc = spawn(uvicorn_cmd, cwd=BACKEND, env=env, log_path=api_log)
    web_env = env.copy()
    web_env["CRIMELINK_API"] = api_url
    info(f"Starting console on {WEB_HOST}:{web_port}  (logs: {web_log})")
    web_proc = spawn([npm, "run", "dev", "--", "--host", WEB_HOST, "--port", str(web_port)], cwd=FRONTEND, env=web_env, log_path=web_log)

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
        banner(nlp_key=nlp_key, ai_key=ai_key, open_url=open_url, api_url=api_url, profile=env.get("CRIMELINK_PROFILE", "embedded"), runtime_context=plan.context, database=database)
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
