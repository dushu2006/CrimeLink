#!/usr/bin/env python3
"""One-command CrimeLink launcher."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def die(message: str, code: int = 1) -> None:
    print(f"\n[CrimeLink] ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def info(message: str) -> None:
    print(f"[CrimeLink] {message}", flush=True)


def require_python() -> str:
    if sys.version_info[:2] <= (3, 13):
        return sys.executable
    if os.name == "nt":
        launcher = next((p for p in ("py",) if _which(p)), None)
        if launcher:
            probe = subprocess.run(
                [launcher, "-3.13", "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True
            )
            if probe.returncode == 0 and probe.stdout.strip():
                selected = probe.stdout.strip()
                info(f"Python {sys.version_info.major}.{sys.version_info.minor} detected; using Python 3.13 at {selected}.")
                return selected
    die("Python 3.13 is required when running with a newer interpreter.")
    return sys.executable


def _which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


def venv_python() -> Path:
    for candidate in (ROOT / ".venv", BACKEND / ".venv"):
        py = candidate / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if py.is_file():
            return py
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_venv(interpreter: str) -> Path:
    py = venv_python()
    if not py.exists():
        info("Creating backend virtualenv …")
        subprocess.check_call([interpreter, "-m", "venv", str(py.parent.parent)])
    info("Backend virtualenv dependencies verified — skipping pip install.")
    return py


def ensure_frontend() -> None:
    node_modules = FRONTEND / "node_modules"
    if node_modules.is_dir():
        info("frontend/node_modules already present — skipping npm install.")
        return
    npm = _which("npm")
    if not npm:
        die("npm is required to install frontend dependencies.")
    subprocess.check_call([npm, "install"], cwd=FRONTEND)


def verify_bootstrap_dependencies(py: Path) -> None:
    info("Verifying backend bootstrap dependencies …")
    code = (
        "import psycopg2, asyncpg, sqlalchemy, alembic; "
        "print('ok')"
    )
    result = subprocess.run([str(py), "-c", code], capture_output=True, text=True)
    if result.returncode != 0:
        die(
            "Backend bootstrap dependencies are incomplete. "
            "Run the dependency installation for the backend and retry.\n"
            + (result.stderr.strip() or result.stdout.strip())
        )
    info("Backend bootstrap dependencies verified.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    interpreter = require_python()
    if not BACKEND.is_dir() or not FRONTEND.is_dir():
        die("run.py must live at the CrimeLink repository root (next to backend/ and frontend/).")

    py = ensure_venv(interpreter)
    ensure_frontend()
    verify_bootstrap_dependencies(py)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(BACKEND)
    env.setdefault("CRIMELINK_RUNTIME_CONTEXT", "auto")

    info("Bootstrapping storage, database, and demo dataset …")
    bootstrap = subprocess.run(
        [str(py), "-c", "from app.db.bootstrap import bootstrap_demo_dataset; bootstrap_demo_dataset()"],
        cwd=BACKEND,
        env=env,
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