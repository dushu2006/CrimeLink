"""Runtime context detection and infrastructure endpoint resolution.

Why this module exists
----------------------
``docker-compose.yml`` addresses the data services by Compose service name —
``postgres``, ``neo4j``, ``minio``, ``redis``.  Those DNS names exist **only on
the Compose network**.  A native launch (``python run.py`` on Windows, macOS or
Linux) reads the very same ``.env``, so the backend tries to resolve
``postgres`` from the host and dies with::

    psycopg2.OperationalError: could not translate host name "postgres" to
    address: Name or service not known

Renaming ``postgres`` to ``localhost`` everywhere is *not* the fix: that breaks
container networking, where ``localhost`` is the container itself.  The fix is
to know **where this process is running** and to rewrite Compose service
hostnames only for native execution.

Three runtime contexts
----------------------
``host``
    Native Python on the developer machine (``python run.py``).  Compose
    service hostnames are rewritten to ``CRIMELINK_INFRA_HOST`` (default
    ``localhost``) plus the port published by ``docker-compose.infra.yml``.
    Rewriting only ever changes *where* a service is reached — never *which*
    backend is used.  PostgreSQL stays PostgreSQL; there is no SQLite,
    in-memory or LocalObjectStore fallback anywhere in this module.
``docker``
    Inside a container on the Compose network.  Service hostnames are
    authoritative and are never rewritten.
``production``
    A production deployment (the ``docker-compose.yml`` topology, Kubernetes,
    or managed services).  Endpoints are used exactly as configured: nothing is
    rewritten and nothing falls back.

``CRIMELINK_RUNTIME_CONTEXT`` (``host``/``docker``/``production``) always wins
over auto-detection, which keeps the outcome deterministic for operators, CI
and containers.  ``run.py`` detects the context once and pins it into the
environment of every child process (backend, bootstrap, seed) so the whole
startup path agrees.

This module is **standard library only**, on purpose: ``run.py`` imports it
with the system interpreter before the virtualenv exists, and ``app.config``
imports it while building ``Settings``.  Nothing here may depend on pydantic,
SQLAlchemy or any other third-party package.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

# --------------------------------------------------------------------------- #
# Runtime contexts
# --------------------------------------------------------------------------- #

CONTEXT_HOST = "host"
CONTEXT_DOCKER = "docker"
CONTEXT_PRODUCTION = "production"
CONTEXT_AUTO = "auto"

#: Every accepted value of ``CRIMELINK_RUNTIME_CONTEXT`` (``auto`` included).
RUNTIME_CONTEXTS = (CONTEXT_AUTO, CONTEXT_HOST, CONTEXT_DOCKER, CONTEXT_PRODUCTION)

#: Contexts in which Compose service hostnames are authoritative (no rewrite).
CONTAINER_CONTEXTS = (CONTEXT_DOCKER, CONTEXT_PRODUCTION)

#: Human-readable description of each context, used by ``run.py`` and logs.
CONTEXT_DESCRIPTIONS = {
    CONTEXT_HOST: "native Python on this machine — infrastructure reached through published host ports",
    CONTEXT_DOCKER: "inside a container on the Compose network — service hostnames used as configured",
    CONTEXT_PRODUCTION: "production deployment — endpoints used exactly as configured, no rewriting",
}

# --------------------------------------------------------------------------- #
# Compose services, container ports and published host ports
# --------------------------------------------------------------------------- #

SERVICE_POSTGRES = "postgres"
SERVICE_NEO4J = "neo4j"
SERVICE_MINIO = "minio"
SERVICE_REDIS = "redis"

#: Container-internal port of each service (``docker-compose.yml``).  These are
#: also the defaults published on the host by ``docker-compose.infra.yml``.
SERVICE_CONTAINER_PORTS: dict[str, int] = {
    SERVICE_POSTGRES: 5432,
    SERVICE_NEO4J: 7687,
    SERVICE_MINIO: 9000,
    SERVICE_REDIS: 6379,
}

#: Hostnames that only resolve on the Compose network.  A configured endpoint
#: pointing at one of these is rewritten **only** in the ``host`` context.
COMPOSE_SERVICE_HOSTNAMES = frozenset(SERVICE_CONTAINER_PORTS)

#: Environment variable that overrides the published host port of a service.
#: ``run.py`` populates these from ``docker port`` when it discovers a running
#: ``docker-compose.infra.yml`` stack publishing something different.
SERVICE_HOST_PORT_VARS: dict[str, str] = {
    SERVICE_POSTGRES: "CRIMELINK_POSTGRES_HOST_PORT",
    SERVICE_NEO4J: "CRIMELINK_NEO4J_HOST_PORT",
    SERVICE_MINIO: "CRIMELINK_MINIO_HOST_PORT",
    SERVICE_REDIS: "CRIMELINK_REDIS_HOST_PORT",
}

#: Settings field -> Compose service.  ``app.config`` resolves exactly these
#: fields and nothing else; ``run.py`` reads the same table so the launcher and
#: the backend can never disagree about an endpoint.
ENDPOINT_FIELDS: tuple[tuple[str, str], ...] = (
    ("postgres_dsn", SERVICE_POSTGRES),
    ("postgres_dsn_sync", SERVICE_POSTGRES),
    ("neo4j_uri", SERVICE_NEO4J),
    ("redis_url", SERVICE_REDIS),
    ("celery_broker_url", SERVICE_REDIS),
    ("celery_result_backend", SERVICE_REDIS),
    ("minio_endpoint", SERVICE_MINIO),
)

#: Settings field -> environment variable (``CRIMELINK_`` prefix + upper field).
ENDPOINT_ENV_VARS: dict[str, str] = {
    field: f"CRIMELINK_{field.upper()}" for field, _ in ENDPOINT_FIELDS
}

#: Settings field that represents each service for reachability checks and for
#: error messages ("set CRIMELINK_POSTGRES_DSN_SYNC …").
SERVICE_PRIMARY_FIELDS: dict[str, str] = {
    SERVICE_POSTGRES: "postgres_dsn_sync",
    SERVICE_NEO4J: "neo4j_uri",
    SERVICE_MINIO: "minio_endpoint",
    SERVICE_REDIS: "redis_url",
}

#: Settings field that carries the published host port of each service.
HOST_PORT_FIELDS: dict[str, str] = {
    SERVICE_POSTGRES: "postgres_host_port",
    SERVICE_NEO4J: "neo4j_host_port",
    SERVICE_MINIO: "minio_host_port",
    SERVICE_REDIS: "redis_host_port",
}

#: Address used for host-native access to published container ports.
DEFAULT_INFRA_HOST = "localhost"
INFRA_HOST_VAR = "CRIMELINK_INFRA_HOST"
RUNTIME_CONTEXT_VAR = "CRIMELINK_RUNTIME_CONTEXT"

#: Hostnames that are already reachable from the machine running the process.
LOCALLY_REACHABLE_HOSTNAMES = frozenset(
    {"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal", "gateway.docker.internal"}
)

# --------------------------------------------------------------------------- #
# Endpoint defaults (single source of truth for app.config field defaults)
# --------------------------------------------------------------------------- #

DEFAULT_POSTGRES_DSN = "postgresql+asyncpg://crimelink:crimelink@localhost:5432/crimelink"
DEFAULT_POSTGRES_DSN_SYNC = "postgresql+psycopg2://crimelink:crimelink@localhost:5432/crimelink"
DEFAULT_NEO4J_URI = "bolt://localhost:7687"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_CELERY_BROKER_URL = "redis://localhost:6379/1"
DEFAULT_CELERY_RESULT_BACKEND = "redis://localhost:6379/2"
DEFAULT_MINIO_ENDPOINT = "localhost:9000"

#: Default value of each endpoint field, keyed by Settings field name.
DEFAULT_ENDPOINT_VALUES: dict[str, str] = {
    "postgres_dsn": DEFAULT_POSTGRES_DSN,
    "postgres_dsn_sync": DEFAULT_POSTGRES_DSN_SYNC,
    "neo4j_uri": DEFAULT_NEO4J_URI,
    "redis_url": DEFAULT_REDIS_URL,
    "celery_broker_url": DEFAULT_CELERY_BROKER_URL,
    "celery_result_backend": DEFAULT_CELERY_RESULT_BACKEND,
    "minio_endpoint": DEFAULT_MINIO_ENDPOINT,
}

# --------------------------------------------------------------------------- #
# Container detection
# --------------------------------------------------------------------------- #

#: Files that only exist inside a container (Docker / Podman / Compose).
_CONTAINER_MARKER_FILES = ("/.dockerenv", "/run/.containerenv")

#: Substrings that identify a container cgroup.
_CGROUP_MARKERS = ("docker", "containerd", "kubepods", "libpod", "podman", "crio")

#: Environment variables Kubernetes injects into every pod.
_KUBERNETES_VARS = ("KUBERNETES_SERVICE_HOST", "KUBERNETES_PORT")


def running_in_container() -> bool:
    """Best-effort detection of "this process runs inside a container".

    Checked in order: the Docker/Podman marker files, the Kubernetes service
    variables, then the cgroup of PID 1 / this process.  On native Windows the
    ``/proc`` reads simply fail and the marker files do not exist, so the
    answer is ``False``.

    Detection is a convenience, not a contract: ``CRIMELINK_RUNTIME_CONTEXT``
    overrides it, and ``docker-compose.yml`` sets that variable explicitly for
    every CrimeLink service so production never depends on a heuristic.
    """
    for marker in _CONTAINER_MARKER_FILES:
        if Path(marker).exists():
            return True
    if any(os.environ.get(name) for name in _KUBERNETES_VARS):
        return True
    for path in ("/proc/1/cgroup", "/proc/self/cgroup"):
        try:
            content = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(marker in content for marker in _CGROUP_MARKERS):
            return True
    return False


def is_production_selection(profile: str | None, environment: str | None) -> bool:
    """True when the operator selected the production profile/environment.

    Mirrors ``Settings``: either ``CRIMELINK_PROFILE=production`` or
    ``CRIMELINK_ENVIRONMENT=production`` means production.
    """
    return profile == "production" or environment == "production"


def resolve_runtime_context(
    *,
    explicit: str | None = None,
    profile: str | None = None,
    environment: str | None = None,
    in_container: bool | None = None,
) -> str:
    """Return the runtime context: ``host``, ``docker`` or ``production``.

    Deterministic and side-effect free:

    1. an explicit ``CRIMELINK_RUNTIME_CONTEXT`` always wins;
    2. otherwise a container is ``production`` when the production
       profile/environment is selected, and ``docker`` otherwise;
    3. otherwise (native Python) the context is ``host``.
    """
    if explicit:
        value = str(explicit).strip().lower()
        if value and value != CONTEXT_AUTO:
            if value not in RUNTIME_CONTEXTS:
                raise ValueError(
                    f"{RUNTIME_CONTEXT_VAR}={explicit!r} is not valid; "
                    f"expected one of {', '.join(RUNTIME_CONTEXTS)}."
                )
            return value
    if in_container is None:
        in_container = running_in_container()
    if in_container:
        return CONTEXT_PRODUCTION if is_production_selection(profile, environment) else CONTEXT_DOCKER
    return CONTEXT_HOST


def context_is_host(context: str) -> bool:
    return context == CONTEXT_HOST


# --------------------------------------------------------------------------- #
# Backend selection (shared with app.config and run.py)
# --------------------------------------------------------------------------- #

def resolve_backend(
    choice: str | None,
    *,
    profile: str | None,
    production_backend: str,
    embedded_backend: str,
) -> str:
    """Resolve an ``auto`` backend choice against the selected profile.

    ``auto`` follows the profile (production -> the real service, embedded ->
    the in-process adapter).  An explicit choice is honoured verbatim, which is
    how a developer runs native Python against real PostgreSQL while staying on
    the embedded profile for the other adapters.
    """
    if choice and str(choice).strip().lower() not in ("", "auto"):
        return str(choice).strip().lower()
    return production_backend if profile == "production" else embedded_backend


def effective_backends(
    *,
    profile: str | None,
    relational: str | None = "auto",
    graph: str | None = "auto",
    object_store: str | None = "auto",
    broker: str | None = "auto",
) -> dict[str, str]:
    """Resolve all four adapter selections at once."""
    return {
        "relational": resolve_backend(
            relational, profile=profile, production_backend="postgres", embedded_backend="sqlite"
        ),
        "graph": resolve_backend(
            graph, profile=profile, production_backend="neo4j", embedded_backend="embedded"
        ),
        "object_store": resolve_backend(
            object_store, profile=profile, production_backend="minio", embedded_backend="local"
        ),
        "broker": resolve_backend(
            broker, profile=profile, production_backend="celery", embedded_backend="inline"
        ),
    }


#: Which Compose service each resolved backend depends on (``None`` = no service).
BACKEND_SERVICE_REQUIREMENTS: dict[str, dict[str, str | None]] = {
    "relational": {"postgres": SERVICE_POSTGRES, "sqlite": None},
    "graph": {"neo4j": SERVICE_NEO4J, "embedded": None},
    "object_store": {"minio": SERVICE_MINIO, "local": None},
    "broker": {"celery": SERVICE_REDIS, "inline": None},
}


def required_services(backends: Mapping[str, str]) -> list[str]:
    """Ordered list of infrastructure services the resolved backends need."""
    required: list[str] = []
    for kind in ("relational", "graph", "object_store", "broker"):
        table = BACKEND_SERVICE_REQUIREMENTS.get(kind, {})
        service = table.get(backends.get(kind, ""))
        if service and service not in required:
            required.append(service)
    return required


# --------------------------------------------------------------------------- #
# Endpoint parsing / rewriting
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class EndpointResolution:
    """Outcome of resolving one configured endpoint for the current context."""

    field: str
    service: str
    #: Value to use (identical to the configured one unless rewritten).
    value: str
    host: str
    port: int | None
    #: Host/port as configured, before any rewrite.
    configured_host: str
    configured_port: int | None
    rewritten: bool
    reason: str

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}" if self.port else self.host

    @property
    def redacted_value(self) -> str:
        """The resolved endpoint with credentials stripped — safe to display."""
        return redact_credentials(self.value)

    def as_dict(self) -> dict[str, Any]:
        """Secret-free description, suitable for logs and API responses."""
        return {
            "field": self.field,
            "service": self.service,
            "value": self.redacted_value,
            "host": self.host,
            "port": self.port,
            "configured_host": self.configured_host,
            "configured_port": self.configured_port,
            "rewritten": self.rewritten,
            "reason": self.reason,
        }


def _host_port_pair(authority: str) -> tuple[str, str | None]:
    """Split ``host[:port]`` keeping IPv6 literals intact."""
    authority = authority.strip()
    if authority.startswith("["):
        end = authority.find("]")
        if end != -1:
            host = authority[1:end]
            rest = authority[end + 1:]
            return host, (rest.lstrip(":") or None)
        return authority, None
    host, sep, port = authority.partition(":")
    return host, (port if sep else None)


def _to_int(port: str | int | None) -> int | None:
    if port is None or port == "":
        return None
    try:
        return int(port)
    except (TypeError, ValueError):
        return None


def split_endpoint(value: str | None, default_port: int | None = None) -> tuple[str, int | None]:
    """Return ``(host, port)`` for a URL (``bolt://neo4j:7687``) or a bare
    ``host:port`` endpoint (``minio:9000``).

    Missing ports fall back to ``default_port``.  Unparseable values yield an
    empty host so callers can leave them untouched instead of corrupting them.
    """
    text = (value or "").strip()
    if not text:
        return "", default_port
    if "://" in text:
        try:
            parts = urlsplit(text)
            host = parts.hostname or ""
            port = _to_int(parts.port)
        except ValueError:
            return "", default_port
        return host, port if port is not None else default_port
    authority = text.split("/", 1)[0]
    if "@" in authority:  # tolerate a full URL without a scheme
        authority = authority.rsplit("@", 1)[1]
    host, port = _host_port_pair(authority)
    return host, _to_int(port) if port is not None else default_port


def _format_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _replace_authority_host(authority: str, host: str, port: int | None) -> str:
    """Swap host/port inside a ``[userinfo@]host[:port]`` authority."""
    userinfo, at, _hostport = authority.rpartition("@")
    replacement = _format_host(host) + (f":{port}" if port else "")
    return f"{userinfo}@{replacement}" if at else replacement


def redact_credentials(value: str | None) -> str:
    """Strip userinfo from a URL or endpoint so it can be logged or reported.

    Mirrors ``app.db.session._redact``: ``scheme://***@host:port/db``.  Anything
    that reaches a log line, an API response or a terminal message must go
    through this — a DSN carries the database password.
    """
    text = (value or "").strip()
    if not text:
        return value or ""
    if "://" in text:
        scheme, rest = text.split("://", 1)
        authority, slash, tail = rest.partition("/")
        if "@" in authority:
            authority = "***@" + authority.rsplit("@", 1)[1]
        return f"{scheme}://{authority}{slash}{tail}"
    authority, slash, tail = text.partition("/")
    if "@" in authority:
        authority = "***@" + authority.rsplit("@", 1)[1]
    return f"{authority}{slash}{tail}"


def replace_endpoint_host(value: str, host: str, port: int | None) -> str:
    """Rewrite the host/port of a URL or bare endpoint, preserving everything else.

    Credentials, database name, query string and path are copied verbatim — only
    the authority's host and port change.
    """
    text = (value or "").strip()
    if not text:
        return value or ""
    if "://" in text:
        parts = urlsplit(text)
        netloc = _replace_authority_host(parts.netloc, host, port)
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    authority, slash, rest = text.partition("/")
    return _replace_authority_host(authority, host, port) + slash + rest


def host_port_for(
    service: str,
    *,
    host_port: int | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    """Published host port of *service*: explicit override, env var, then default."""
    if host_port:
        return int(host_port)
    if env is not None:
        variable = SERVICE_HOST_PORT_VARS.get(service)
        if variable:
            from_env = _to_int(env.get(variable))
            if from_env:
                return from_env
    return SERVICE_CONTAINER_PORTS.get(service, 0)


def resolve_service_endpoint(
    value: str | None,
    service: str,
    *,
    context: str,
    field: str = "",
    infra_host: str | None = None,
    host_port: int | None = None,
    env: Mapping[str, str] | None = None,
) -> EndpointResolution:
    """Resolve one configured endpoint for *context*.

    Rewriting happens **only** when all of these hold:

    * the context is ``host`` (native Python — Compose DNS does not exist);
    * the configured host is a Compose service name (``postgres`` …);
    * a replacement address is available.

    Everything else is returned untouched, so a remote production hostname, a
    ``localhost`` DSN or a container-side ``postgres`` DSN are all preserved
    exactly as the operator wrote them.
    """
    default_port = SERVICE_CONTAINER_PORTS.get(service)
    configured_host, configured_port = split_endpoint(value, default_port)
    resolved_port = host_port_for(service, host_port=host_port, env=env)
    base = EndpointResolution(
        field=field or service,
        service=service,
        value=value or "",
        host=configured_host,
        port=configured_port,
        configured_host=configured_host,
        configured_port=configured_port,
        rewritten=False,
        reason="",
    )

    if context in CONTAINER_CONTEXTS:
        return replace(base, reason=f"{context} context: service hostnames are authoritative")
    if not configured_host:
        return replace(base, reason="no host configured")
    if configured_host.lower() not in COMPOSE_SERVICE_HOSTNAMES:
        return replace(
            base,
            reason=f"'{configured_host}' is not a Compose service name; used as configured",
        )

    target_host = (infra_host or DEFAULT_INFRA_HOST).strip() or DEFAULT_INFRA_HOST
    target_port = resolved_port or default_port
    reason = (
        f"Compose service name '{configured_host}' does not resolve outside the Compose "
        f"network; host runtime uses {target_host}:{target_port}"
    )
    return replace(
        base,
        value=replace_endpoint_host(value or "", target_host, target_port),
        host=target_host,
        port=target_port,
        rewritten=True,
        reason=reason,
    )


def endpoint_values_from_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Configured endpoint values keyed by Settings field, defaults included.

    ``run.py`` uses this before the virtualenv exists, so the launcher and
    ``app.config`` read the same variables with the same defaults.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    values: dict[str, str] = {}
    for field, _service in ENDPOINT_FIELDS:
        variable = ENDPOINT_ENV_VARS[field]
        configured = (source.get(variable) or "").strip()
        values[field] = configured or DEFAULT_ENDPOINT_VALUES[field]
    return values


def resolve_infrastructure(
    *,
    context: str,
    env: Mapping[str, str] | None = None,
    infra_host: str | None = None,
    values: Mapping[str, str] | None = None,
) -> dict[str, EndpointResolution]:
    """Resolve every infrastructure endpoint for *context* (keyed by field)."""
    source: Mapping[str, str] = os.environ if env is None else env
    resolved_values = dict(values or endpoint_values_from_env(source))
    host = (infra_host or source.get(INFRA_HOST_VAR) or DEFAULT_INFRA_HOST).strip() or DEFAULT_INFRA_HOST
    resolutions: dict[str, EndpointResolution] = {}
    for field, service in ENDPOINT_FIELDS:
        resolutions[field] = resolve_service_endpoint(
            resolved_values.get(field, DEFAULT_ENDPOINT_VALUES[field]),
            service,
            context=context,
            field=field,
            infra_host=host,
            env=source,
        )
    return resolutions


def primary_service_endpoints(
    resolutions: Mapping[str, EndpointResolution],
) -> dict[str, EndpointResolution]:
    """One endpoint per service, for reachability checks and logging."""
    chosen: dict[str, EndpointResolution] = {}
    for service, field in SERVICE_PRIMARY_FIELDS.items():
        resolution = resolutions.get(field)
        if resolution is not None:
            chosen[service] = resolution
    return chosen


def is_host_reachable(host: str) -> bool:
    """True when *host* is already a host-local address (no rewrite needed)."""
    return host.strip().lower() in LOCALLY_REACHABLE_HOSTNAMES


def summarize(resolutions: Iterable[EndpointResolution]) -> list[str]:
    """Human-readable lines describing what was resolved and what changed.

    Credentials are stripped: these lines are meant for logs and terminals.
    """
    lines: list[str] = []
    for resolution in resolutions:
        marker = "rewritten" if resolution.rewritten else "as configured"
        lines.append(f"{resolution.field}={resolution.redacted_value} ({marker})")
    return lines
