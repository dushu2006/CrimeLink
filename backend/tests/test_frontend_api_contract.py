"""Every API path the console calls must exist on the backend.

This is the audit that found a dead feature: ``masterPersonNetwork()`` called
``GET /graph/master/person/{key}`` while the backend serves
``GET /graph/master/person/{key}/network``.  The PERSON NETWORK scope 404'd on
every request, and nothing else in the suite could notice because the client
function and the route were each individually fine.

The test reads the console's sources, extracts every ``api(...)`` literal, and
resolves it against the real FastAPI route table.  A template interpolation
becomes a single path segment; a query string is dropped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.main import create_app

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

_CALL = re.compile(r"\bapi(?:<[^>]*>)?\(\s*[`\"']([^`\"']+)[`\"']")


def _route_patterns() -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for route in create_app().routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        parts = re.split(r"(\{[^}]+\})", path)
        body = "".join(
            "[^/]+" if part.startswith("{") else re.escape(part) for part in parts
        )
        patterns.append(re.compile(f"^{body}$"))
    return patterns


def _frontend_call_paths() -> list[tuple[str, str]]:
    """(raw literal, normalised /api/v1 path) for every api() call site."""
    if not FRONTEND_SRC.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for file in sorted(FRONTEND_SRC.rglob("*.ts*")):
        for match in _CALL.finditer(file.read_text(encoding="utf-8")):
            raw = match.group(1)
            if raw.startswith("http"):
                continue
            # Drop the query string and any `${ ... ?` ternary that builds one.
            path = re.split(r"\?|\$\{[^}]*\?", raw)[0]
            path = re.sub(r"\$\{[^}]*\}", "X", path)
            if not path.startswith("/"):
                # Composed from a variable — not a literal route, skip.
                continue
            full = ("/api/v1" + path).rstrip("/") or "/api/v1/"
            out.append((raw, full))
    return out


def test_the_console_source_tree_is_present():
    assert FRONTEND_SRC.is_dir(), (
        f"expected the console sources at {FRONTEND_SRC}; the audit cannot run "
        "without them"
    )


def test_every_frontend_api_path_resolves_to_a_real_route():
    patterns = _route_patterns()
    calls = _frontend_call_paths()
    assert len(calls) > 50, f"only {len(calls)} api() call sites found — extraction broke"

    unresolved: list[str] = []
    for raw, full in calls:
        if any(p.match(full) or p.match(full + "/") for p in patterns):
            continue
        unresolved.append(f"{raw}  ->  {full}")

    assert not unresolved, (
        "the console calls routes the backend does not serve:\n  "
        + "\n  ".join(sorted(set(unresolved)))
    )


def test_person_network_route_is_the_one_the_client_calls():
    """Regression pin for the dead PERSON NETWORK scope."""
    client = (FRONTEND_SRC / "api" / "client.ts").read_text(encoding="utf-8")
    assert "/graph/master/person/${encodeURIComponent(personKey)}/network" in client, (
        "masterPersonNetwork() must call /graph/master/person/{key}/network"
    )

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert "/api/v1/graph/master/person/{person_key}/network" in paths


@pytest.mark.parametrize(
    "route",
    [
        "/api/v1/graph/master/relationships",
        "/api/v1/graph/master/relationship-evidence",
        "/api/v1/graph/cases/{case_id}/relationships",
        "/api/v1/evidence/{doc_id}/provenance",
        "/api/v1/evidence/{doc_id}/verify",
        "/api/v1/sources/preview",
        "/api/v1/sources/raw",
        "/api/v1/sources/file",
        "/api/v1/sources/files",
    ],
)
def test_investigator_critical_routes_exist(route):
    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert route in paths, f"{route} is missing"
