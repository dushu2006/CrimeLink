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
    api_base_url: str = "http://127.0.0.1:8000"

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
    neo4j_gds_enabled: bool = False  # GDS is optional; centrality is computed in Python

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
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

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

    # -------------------------------------------------------- AI Gateway / multi-model routing
    #
    # Each model role can point at a different provider/model.  The same
    # OpenAI-compatible HTTP client is used; different base_url / api_key / model
    # values can be configured per role.  When a role's key is empty the system
    # either falls back to heuristic processing (extraction/classification) or
    # returns a structured "insufficient evidence" result (reasoning/explanation).
    ai_provider: str = "nvidia"
    ai_api_key: str | None = None
    ai_base_url: str = "https://integrate.api.nvidia.com/v1"
    ai_temperature: float = 0.1
    ai_max_tokens: int = 2048
    ai_timeout_s: float = 90.0
    ai_allow_raw_pii: bool = False            # safety: always false unless explicit
    ai_pseudonymize: bool = True              # apply reversible pseudonymization
    ai_audit_prompt_storage: bool = False     # whether to persist full prompts in audit

    ai_extraction_model: str = "deepseek-ai/deepseek-v4-pro-0813"
    ai_extraction_provider: str = "default"   # "default" uses ai_provider/ai_api_key
    ai_extraction_api_key: str | None = None
    ai_extraction_base_url: str | None = None

    ai_reasoning_model: str = "deepseek-ai/deepseek-r1"
    ai_reasoning_provider: str = "default"
    ai_reasoning_api_key: str | None = None
    ai_reasoning_base_url: str | None = None

    ai_explanation_model: str = "meta/llama-3.1-8b-instruct"
    ai_explanation_provider: str = "default"
    ai_explanation_api_key: str | None = None
    ai_explanation_base_url: str | None = None

    ai_classification_model: str = "meta/llama-3.1-8b-instruct"
    ai_classification_provider: str = "default"
    ai_classification_api_key: str | None = None
    ai_classification_base_url: str | None = None

    ai_embedding_model: str = "nvidia/llama-3.2-nv-embedqa-1b-v2"
    ai_embedding_provider: str = "default"
    ai_embedding_api_key: str | None = None
    ai_embedding_base_url: str | None = None

    # ------------------------------------------------------ synthetic corpus
    synthetic_corpus_enabled: bool = False
    synthetic_corpus_seed: int = 20260902
    synthetic_corpus_version: int = 1
    synthetic_person_count: int = 60
    synthetic_case_count: int = 12
    synthetic_phone_count: int = 75
    synthetic_vehicle_count: int = 30
    synthetic_location_count: int = 25
    synthetic_account_count: int = 35
    synthetic_organization_count: int = 12
    synthetic_document_count: int = 60
    synthetic_call_count: int = 350
    synthetic_transaction_count: int = 180
    synthetic_bridge_count: int = 4
    synthetic_network_count: int = 3
    synthetic_missing_field_rate: float = 0.12
    synthetic_duplicate_rate: float = 0.08
    synthetic_name_variation_rate: float = 0.15
    synthetic_source_environment: str = "synthetic"  # provenance tag

    # ---------------------------------------------- external synthetic corpus
    # Where synthetic development data comes from:
    #   "generate" -> the deterministic in-process generator;
    #   "external" -> a corpus directory read from the filesystem.
    # Development default is the local dataset at
    # ``backend/CrimeLink_Synthetic_Corpus_v1``.  The in-process generator
    # remains available via ``CRIMELINK_SYNTHETIC_DATA_MODE=generate``.
    # Nothing is ingested at startup either way; ingestion is always an
    # explicit operator action (UI, CLI, or POST /api/v1/admin/synthetic/ingest).
    synthetic_data_mode: Literal["generate", "external"] = "external"
    # Root of the external corpus. Absolute paths are honoured verbatim;
    # relative paths resolve against the CrimeLink repository root.
    # Default: backend/CrimeLink_Synthetic_Corpus_v1 (gitignored local dataset).
    # Only `operational/` and `documents/` under this root are ingestion
    # sources; `ground_truth/` and `metadata/` are never operational input.
    synthetic_data_root: Path = Field(
        default=BACKEND_ROOT / "CrimeLink_Synthetic_Corpus_v1"
    )

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
    pdf_devanagari_font: str | None = None
    graph_max_expand_depth: int = 2            # PRD 10: hard cap
    graph_expand_node_limit: int = 300
    temporal_path_max_depth: int = 4
    export_watermark: str = "CrimeLink — CONFIDENTIAL / LAW ENFORCEMENT USE ONLY"
    audit_anchor_enabled: bool = True
    retention_days_after_closure: int = 90
    http_port: int = 8000  # used by docker compose

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

    @field_validator(
        "synthetic_missing_field_rate",
        "synthetic_duplicate_rate",
        "synthetic_name_variation_rate",
        mode="before",
    )
    @classmethod
    def _clamp_unit(cls, v: Any) -> float:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))

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

    @property
    def resolved_synthetic_data_root(self) -> Path:
        """Absolute path of the external synthetic corpus root.

        Absolute ``synthetic_data_root`` values are honoured verbatim;
        relative values resolve against the repository root so that
        ``backend/CrimeLink_Synthetic_Corpus_v1`` is the local gitignored
        dataset regardless of the operator's cwd.  The directory is *not*
        required to exist — the external-corpus adapter reports a clear
        error when it is missing.
        """
        root = Path(self.synthetic_data_root).expanduser()
        if not root.is_absolute():
            root = REPO_ROOT / root
        return root.resolve()

    # ---- AI role resolution (provider "default" falls back to global) ------
    def role_config(self, role: str) -> dict[str, Any]:
        """Return base_url / api_key / model for a given AI model role."""
        prefix = f"ai_{role}"
        model = getattr(self, f"{prefix}_model", "")
        provider = getattr(self, f"{prefix}_provider", "default")
        api_key = getattr(self, f"{prefix}_api_key", None) or self.ai_api_key
        base_url = getattr(self, f"{prefix}_base_url", None) or self.ai_base_url
        if not api_key:
            # fall back to NVIDIA_API_KEY for convenience
            import os
            api_key = os.environ.get("NVIDIA_API_KEY")
        return {
            "role": role,
            "provider": provider if provider != "default" else self.ai_provider,
            "model": model,
            "api_key": api_key,
            "base_url": base_url,
            "temperature": self.ai_temperature,
            "max_tokens": self.ai_max_tokens,
            "timeout": self.ai_timeout_s,
        }

    def ai_role_available(self, role: str) -> bool:
        return bool(self.role_config(role).get("api_key"))

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
