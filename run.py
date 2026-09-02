#!/usr/bin/env python3
"""One-command CrimeLink launcher (embedded / laptop profile).

First run installs Python and Node dependencies, starts the FastAPI backend
and the Vite console, then opens a browser.  The database starts EMPTY —
create the first administrator in the console.  No demo data is inserted.

Quick start::

    python run.py
    python run.py --reinstall     # force pip + npm again
    python run.py --no-browser

Requires Python 3.11+ and Node.js 18+ (with npm).  No Docker, Postgres, Neo4j,
Redis or MinIO is needed for this profile.

Optional: set ``NVIDIA_API_KEY`` (or ``CRIMELINK_AI_API_KEY``) in ``.env`` to
enable AI extraction/reasoning/explanation through NVIDIA NIM.  Without a key
the system runs fully offline on the heuristic extractor and still produces a
complete graph.

Troubleshooting Windows
-----------------------
The embedded profile uses loose NumPy / NetworkX pins so pip always picks a
prebuilt wheel — you do NOT need Visual Studio Build Tools.  If ``pip`` still
tries to compile NumPy from source, upgrade pip first::

    python -m pip install --upgrade pip setuptools wheel
    python run.py
"""

from __future__ import annotations

import argparse
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

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
RUN_DIR = ROOT / ".run"
DATA_DIR = ROOT / "var" / "data"
OBJECT_DIR = ROOT / "var" / "objects"

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


def apply_embedded_env(*, api_url: str, web_port: int) -> dict[str, str]:
    """Force the no-containers profile and map the NVIDIA/AI keys the README documents."""
    load_dotenv(ROOT / ".env")
    load_dotenv(BACKEND / ".env")

    nvidia = (os.environ.get("NVIDIA_API_KEY")
              or os.environ.get("CRIMELINK_NIM_API_KEY")
              or os.environ.get("CRIMELINK_AI_API_KEY"))
    if nvidia:
        os.environ["NVIDIA_API_KEY"] = nvidia
        os.environ["CRIMELINK_NIM_API_KEY"] = nvidia
        os.environ.setdefault("CRIMELINK_AI_API_KEY", nvidia)

    os.environ["CRIMELINK_PROFILE"] = "embedded"
    os.environ["CRIMELINK_ENVIRONMENT"] = "dev"
    os.environ["CRIMELINK_DEBUG"] = os.environ.get("CRIMELINK_DEBUG", "true")
    os.environ["CRIMELINK_DATA_DIR"] = str(DATA_DIR)
    os.environ["CRIMELINK_OBJECT_STORE_DIR"] = str(OBJECT_DIR)
    os.environ["PYTHONPATH"] = str(BACKEND)
    os.environ["CRIMELINK_API"] = api_url
    os.environ["CRIMELINK_CORS_ORIGINS"] = json.dumps([
        f"http://127.0.0.1:{web_port}",
        f"http://localhost:{web_port}",
        api_url,
    ])
    # Security: never auto-generate demo data on startup.
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
                info(f"Python {sys.version_info.major}.{sys.version_info.minor} detected; using Python 3.13 at {selected}.")
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
        out = subprocess.check_output([node, "-v"], text=True, stderr=subprocess.STDOUT).strip().lstrip("v")
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


def wait_http(url: str, timeout: float, label: str) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    info(f"{label} is ready.")
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = str(exc)
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
        info("Upgrading pip/setuptools/wheel first so NumPy uses a prebuilt wheel on Windows.")
        run([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], env=env)
        run([str(py), "-m", "pip", "install", str(BACKEND)], env=env)
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
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            proc.wait(timeout=8)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=8)
    except Exception:
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass
    log_file = getattr(proc, "_crimelink_log", None)
    if log_file:
        try:
            log_file.close()
        except Exception:
            pass


def banner(*, nlp_key: bool, ai_key: bool, open_url: str, api_url: str) -> None:
    print()
    print("=" * 72)
    print("  CrimeLink is running (embedded profile — no containers).")
    print()
    print(f"  Investigator console : {open_url}")
    print(f"  API                  : {api_url}")
    print(f"  Interactive API docs : {api_url}/api/docs")
    print()
    print("  The database starts EMPTY. Create the administrator account in")
    print("  the browser on first launch, then sign in with that badge number")
    print("  and password.  No demo data is inserted automatically.")
    print()
    print("  To generate a realistic synthetic development corpus:")
    print("    .venv/Scripts/python -m app.synthetic_corpus.generate     (Windows)")
    print("    .venv/bin/python -m app.synthetic_corpus.generate         (macOS/Linux)")
    print("  Add --help to see options (seed, size, clear, regenerate).")
    print()
    if nlp_key or ai_key:
        print("  NLP/AI: API key detected — model extraction & AI gateway enabled.")
    else:
        print("  NLP/AI: no API key set — running fully offline on heuristics.")
        print("          Set NVIDIA_API_KEY in .env for NIM-based extraction/reasoning.")
    print()
    print("  Press Ctrl+C to stop both servers.")
    print("=" * 72)
    print()


def _check_env(env: dict[str, str]) -> None:
    """Surface actionable errors for common missing-config situations."""
    if env.get("CRIMELINK_SECRET_KEY", "").startswith("change-me"):
        warn("CRIMELINK_SECRET_KEY is the placeholder value. This is fine for local"
             " development; change it before exposing CrimeLink to a network.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install (first time) and run CrimeLink.")
    parser.add_argument("--reinstall", action="store_true", help="Re-run pip and npm install.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser tab.")
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    args = parser.parse_args()

    api_port = args.api_port
    web_port = args.web_port
    open_url = f"http://127.0.0.1:{web_port}"
    api_url = f"http://127.0.0.1:{api_port}"
    health_url = f"{api_url}/api/v1/health/live"

    interpreter = require_python()
    npm = require_node()

    if not BACKEND.is_dir() or not FRONTEND.is_dir():
        die("run.py must live at the CrimeLink repository root (next to backend/ and frontend/).")

    env = apply_embedded_env(api_url=api_url, web_port=web_port)
    _check_env(env)
    py = ensure_venv(interpreter, env, reinstall=args.reinstall)
    ensure_frontend(npm, env, reinstall=args.reinstall)

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
    api_proc = spawn(
        [
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
        ],
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
        wait_http(health_url, timeout=90, label="API")
        if api_proc.poll() is not None:
            die(f"API exited early (code {api_proc.returncode}). See {api_log}")
        wait_http(open_url, timeout=90, label="Console")
        if web_proc.poll() is not None:
            die(f"Frontend exited early (code {web_proc.returncode}). See {web_log}")

        nlp_key = bool(env.get("CRIMELINK_NIM_API_KEY") or env.get("NVIDIA_API_KEY"))
        ai_key = bool(env.get("CRIMELINK_AI_API_KEY")) or nlp_key
        banner(nlp_key=nlp_key, ai_key=ai_key, open_url=open_url, api_url=api_url)

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
