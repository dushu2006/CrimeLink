"""Central application configuration.

Every tunable in CrimeLink is expressed here and is overridable through the
environment with the ``CRIMELINK_`` prefix (see ``.env.example``).

Two deployment profiles exist:

``embedded`` (default)
    Single-process profile used for local development, automated tests and
    demonstrations.  Persistence is provided by SQLite, an in-process
    NetworkX graph, the local filesystem and an in-process job executor.
    It requires no containers and no network access.

``production``
    The deployment described in the PRD: PostgreSQL 15, Neo4j 5 (+GDS),
    MinIO object storage, Redis + Celery workers.

The profile only selects *adapters*; the domain, pipeline, analytics and API
layers are byte-for-byte identical in both profiles.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> parents[1] == backend/, parents[2] == repository root
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]

# Sentinel default for `graph_snapshot_path`.  A model validator moves it under
# `data_dir` whenever the caller has not overridden it, so that pointing the
# data directory somewhere else (tests, a demo workspace) also gives you a
# clean graph instead of silently reusing the previous one.
DEFAULT_GRAPH_SNAPSHOT = REPO_ROOT / "var" / "data" / "graph.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CRIMELINK_",
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- profile
    profile: Literal["embedded", "production"] = "embedded"
    app_name: str = "CrimeLink"
    environment: Literal["dev", "staging", "production"] = "dev"
    debug: bool = False
    log_level: str = "INFO"

    # ------------------------------------------------------------ persistence
    # Backend selection; "auto" resolves from `profile`.
    relational_backend: Literal["auto", "postgres", "sqlite"] = "auto"
    graph_backend: Literal["auto", "neo4j", "embedded"] = "auto"
    object_store_backend: Literal["auto", "minio", "local"] = "auto"
    broker_backend: Literal["auto", "celery", "inline"] = "auto"

    postgres_dsn: str = "postgresql+asyncpg://crimelink:crimelink@localhost:5432/crimelink"
    postgres_dsn_sync: str = "postgresql+psycopg2://crimelink:crimelink@localhost:5432/crimelink"
    postgres_pool_size: int = 10
    postgres_max_overflow: int = 20

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "crimelink"
    neo4j_database: str = "neo4j"
    neo4j_gds_enabled: bool = True

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "crimelink"
    minio_secret_key: str = "crimelink"
    minio_secure: bool = False
    minio_bucket_documents: str = "documents"
    minio_bucket_derived: str = "documents-derived"
    minio_bucket_audit_anchor: str = "audit-anchor"

    # Filesystem roots used by the embedded adapters
    data_dir: Path = Field(default=REPO_ROOT / "var" / "data")
    object_store_dir: Path = Field(default=REPO_ROOT / "var" / "objects")
    graph_snapshot_path: Path = Field(default=DEFAULT_GRAPH_SNAPSHOT)

    # --------------------------------------------------------------- security
    secret_key: str = "change-me-in-production-with-a-32-byte-random-value"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_hours: int = 8
    password_min_length: int = 10
    login_lockout_threshold: int = 5           # failed attempts before lockout
    login_lockout_minutes: int = 30
    rate_limit_per_minute: int = 100
    rate_limit_auth_per_minute: int = 10

    # ------------------------------------------------------------------- nlp
    # "auto" -> NIM when an API key is present, else the deterministic+heuristic
    # provider (which needs no model download and works fully offline).
    nlp_provider: Literal["auto", "nim", "indicner", "heuristic"] = "auto"
    nim_api_key: str | None = None
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_model: str = "deepseek-ai/deepseek-v4-pro-0813"
    nim_temperature: float = 0.0
    nim_max_tokens: int = 4096
    nim_timeout_s: float = 90.0
    nim_disable_thinking: bool = True
    nim_concurrency: int = 2
    nim_max_blocks_per_doc: int = 40
    nim_max_chars_per_block: int = 3000
    indicner_model: str = "ai4bharat/IndicNER"
    nlp_max_confidence: float = 0.8            # PRD 8.2: NLP output is capped

    # ------------------------------------------------------ entity resolution
    er_fuzzy_threshold: float = 0.85
    er_max_pairs_per_document: int = 200
    er_queue_sla_hours: int = 48

    # ----------------------------------------------------- pattern detection
    structuring_min_transfers: int = 4
    structuring_window_days: int = 30
    structuring_max_single_amount: float = 50_000.0
    structuring_min_total_amount: float = 1_000_000.0
    burner_max_lifespan_days: int = 21
    burner_min_fanout: int = 15
    rapid_movement_min_kmh: float = 110.0
    network_bridge_percentile: float = 95.0
    pattern_dismissal_rate_alert: float = 0.70

    # ---------------------------------------------------------------- misc
    presigned_url_ttl_seconds: int = 900       # 15 minutes (PRD 6.3)
    upload_max_bytes: int = 64 * 1024 * 1024
    # Optional path to a Devanagari TTF for the exported brief.  When unset (or
    # missing) Devanagari names are transliterated to ISO-15919 instead of
    # rendering as empty boxes.
    pdf_devanagari_font: str | None = None
    graph_max_expand_depth: int = 2            # PRD 10: hard cap
    graph_expand_node_limit: int = 300
    temporal_path_max_depth: int = 4
    export_watermark: str = "CrimeLink — CONFIDENTIAL / LAW ENFORCEMENT USE ONLY"
    audit_anchor_enabled: bool = True
    # Reserved: the lifecycle countdown that applies once a case is closed.
    # Closing a case already makes it read-only.  Automatic deletion after this
    # many days is deliberately *not* scheduled — purging evidence is the one
    # irreversible action in the system, so it stays an explicit, audited
    # administrator procedure until a district defines one.
    retention_days_after_closure: int = 90
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    @model_validator(mode="after")
    def _relocate_graph_snapshot(self) -> "Settings":
        """Keep the embedded graph beside the database unless told otherwise."""
        if self.graph_snapshot_path == DEFAULT_GRAPH_SNAPSHOT:
            object.__setattr__(self, "graph_snapshot_path", self.data_dir / "graph.json")
        return self

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    # ------------------------------------------------------------- resolution
    @property
    def effective_relational_backend(self) -> str:
        if self.relational_backend != "auto":
            return self.relational_backend
        return "postgres" if self.profile == "production" else "sqlite"

    @property
    def effective_graph_backend(self) -> str:
        if self.graph_backend != "auto":
            return self.graph_backend
        return "neo4j" if self.profile == "production" else "embedded"

    @property
    def effective_object_store_backend(self) -> str:
        if self.object_store_backend != "auto":
            return self.object_store_backend
        return "minio" if self.profile == "production" else "local"

    @property
    def effective_broker_backend(self) -> str:
        if self.broker_backend != "auto":
            return self.broker_backend
        return "celery" if self.profile == "production" else "inline"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "crimelink.db"

    @property
    def sqlite_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.sqlite_path}"

    @property
    def sqlite_url_sync(self) -> str:
        return f"sqlite:///{self.sqlite_path}"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.object_store_dir.mkdir(parents=True, exist_ok=True)
        self.graph_snapshot_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


def reload_settings() -> Settings:
    """Drop the cached settings (used by tests)."""
    get_settings.cache_clear()
    return get_settings()
