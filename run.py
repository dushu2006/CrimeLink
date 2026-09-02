#!/usr/bin/env python3
"""One-command CrimeLink launcher (embedded / laptop profile).

First run installs Python and Node dependencies, starts the FastAPI backend
and the Vite console, then opens a browser.  The database starts empty —
create the first administrator in the console.

    python run.py
    python run.py --reinstall     # force pip + npm again
    python run.py --no-browser

Requires Python 3.11+ and Node.js 18+ (with npm).  No Docker, Postgres,
Neo4j, Redis or MinIO is needed for this profile.

Optional: set NVIDIA_API_KEY (or CRIMELINK_NIM_API_KEY) in the environment
or in a `.env` file to use NVIDIA NIM for NLP extraction.  Without a key
the heuristic extractor runs fully offline.
"""

from __future__ import annotations

import argparse
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


def die(message: str, code: int = 1) -> None:
    print(f"\n[CrimeLink] ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def info(message: str) -> None:
    print(f"[CrimeLink] {message}", flush=True)


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
    """Force the no-containers profile and map the NVIDIA key the README documents."""
    load_dotenv(ROOT / ".env")
    load_dotenv(BACKEND / ".env")

    nvidia = os.environ.get("NVIDIA_API_KEY") or os.environ.get("CRIMELINK_NIM_API_KEY")
    if nvidia:
        os.environ["NVIDIA_API_KEY"] = nvidia
        os.environ["CRIMELINK_NIM_API_KEY"] = nvidia

    os.environ["CRIMELINK_PROFILE"] = "embedded"
    os.environ["CRIMELINK_ENVIRONMENT"] = "dev"
    os.environ["CRIMELINK_DEBUG"] = os.environ.get("CRIMELINK_DEBUG", "true")
    os.environ["CRIMELINK_DATA_DIR"] = str(DATA_DIR)
    os.environ["CRIMELINK_OBJECT_STORE_DIR"] = str(OBJECT_DIR)
    os.environ["PYTHONPATH"] = str(BACKEND)
    os.environ["CRIMELINK_API"] = api_url
    os.environ.setdefault(
        "CRIMELINK_CORS_ORIGINS",
        f"http://127.0.0.1:{web_port},http://localhost:{web_port},{api_url}",
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OBJECT_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    return os.environ.copy()


def require_python() -> None:
    if sys.version_info < (3, 11):
        die(
            f"Python 3.11+ is required (found {sys.version.split()[0]}). "
            "Install Python 3.11 or newer and re-run."
        )


def require_node() -> str:
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        die(
            "Node.js 18+ (with npm) is required for the investigator console.\n"
            "  macOS:   brew install node\n"
            "  Ubuntu:  sudo apt install nodejs npm\n"
            "  Windows: https://nodejs.org/"
        )
    try:
        out = subprocess.check_output([node, "-v"], text=True).strip().lstrip("v")
    except subprocess.CalledProcessError as exc:
        die(f"Could not run node: {exc}")
    major = int(out.split(".")[0])
    if major < 18:
        die(f"Node.js 18+ is required (found v{out}).")
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


def ensure_venv(env: dict[str, str], reinstall: bool) -> Path:
    py = venv_python()
    if reinstall and VENV.exists():
        info("Removing existing virtualenv (--reinstall).")
        shutil.rmtree(VENV)
    if not py.is_file():
        info(f"Creating virtualenv at {VENV} …")
        run([sys.executable, "-m", "venv", str(VENV)])
        py = venv_python()
        if not py.is_file():
            die(f"Virtualenv was created but {py} is missing.")

    marker = VENV / ".crimelink-installed"
    need_install = reinstall or not marker.is_file()
    if not need_install:
        probe = subprocess.run(
            [str(py), "-c", "import fastapi, uvicorn, sqlalchemy"],
            capture_output=True,
        )
        need_install = probe.returncode != 0

    if need_install:
        info("Installing backend Python dependencies (first time can take a few minutes) …")
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


def banner(*, nlp_key: bool, open_url: str, api_url: str) -> None:
    print()
    print("=" * 72)
    print("  CrimeLink is running (embedded profile — no containers).")
    print()
    print(f"  Investigator console : {open_url}")
    print(f"  API                  : {api_url}")
    print(f"  Interactive API docs : {api_url}/api/docs")
    print()
    print("  First launch: create the administrator account in the browser.")
    print("  After that, sign in with that badge number and password.")
    print("  Add investigators and viewers from Administration → Users.")
    print()
    if nlp_key:
        print("  NLP: NVIDIA NIM key detected — model extraction is enabled.")
    else:
        print("  NLP: no NVIDIA_API_KEY set — running fully offline (heuristic).")
        print("       Get a key at https://build.nvidia.com and put it in .env")
        print("       as NVIDIA_API_KEY=nvapi-… to enable NIM extraction.")
    print()
    print("  Press Ctrl+C to stop both servers.")
    print("=" * 72)
    print()


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

    require_python()
    npm = require_node()

    if not BACKEND.is_dir() or not FRONTEND.is_dir():
        die("run.py must live at the CrimeLink repository root (next to backend/ and frontend/).")

    env = apply_embedded_env(api_url=api_url, web_port=web_port)
    py = ensure_venv(env, reinstall=args.reinstall)
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
        wait_http(health_url, timeout=60, label="API")
        if api_proc.poll() is not None:
            die(f"API exited early. See {api_log}")
        wait_http(open_url, timeout=60, label="Console")
        if web_proc.poll() is not None:
            die(f"Frontend exited early. See {web_log}")

        nlp_key = bool(env.get("CRIMELINK_NIM_API_KEY") or env.get("NVIDIA_API_KEY"))
        banner(nlp_key=nlp_key, open_url=open_url, api_url=api_url)

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
