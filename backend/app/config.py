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

A third, orthogonal axis is the **runtime context** (``CRIMELINK_RUNTIME_CONTEXT``,
see :mod:`app.runtime`): ``host`` for native Python on the developer machine,
``docker`` for a container on the Compose network and ``production`` for a
deployment.  Only the ``host`` context rewrites endpoints, and it rewrites only
Compose service hostnames (``postgres`` → ``localhost:5432``) so that the same
``.env`` works both natively and inside containers.  The context never changes
which backend is used — PostgreSQL stays mandatory wherever it is configured.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, PrivateAttr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app import runtime

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
        # Several infrastructure fields carry a `validation_alias` (the bare
        # NEO4J_/S3_/REDIS_ environment names); this keeps the field name
        # usable for direct construction (tests, explicit overrides) too.
        populate_by_name=True,
    )

    # ---------------------------------------------------------------- profile
    profile: Literal["embedded", "production"] = "embedded"
    app_name: str = "CrimeLink"
    environment: Literal["dev", "staging", "production"] = "dev"
    debug: bool = False
    log_level: str = "INFO"
    api_base_url: str = "http://127.0.0.1:8000"
    trusted_hosts: str | list[str] = Field(default_factory=lambda: ["*"])
    max_request_bytes: int = 70 * 1024 * 1024

    # ------------------------------------------------------------ persistence
    # Backend selection; "auto" resolves from `profile`.
    relational_backend: Literal["auto", "postgres", "sqlite"] = "auto"
    graph_backend: Literal["auto", "neo4j", "embedded"] = "auto"
    object_store_backend: Literal["auto", "minio", "local"] = "auto"
    broker_backend: Literal["auto", "celery", "inline"] = "auto"

    # -------------------------------------------------------- runtime context
    # Where this process runs (see app/runtime.py).  `auto` detects it:
    #
    #   host        native Python on this machine (`python run.py`).  Compose
    #               service hostnames (`postgres`, `neo4j`, `minio`, `redis`)
    #               are rewritten to `infra_host` + the published host port,
    #               because those DNS names exist only on the Compose network.
    #   docker      inside a container on the Compose network — the service
    #               hostnames are authoritative and are never rewritten.
    #   production  production deployment — endpoints are used exactly as
    #               configured, with no rewriting and no fallback of any kind.
    #
    # An explicit value always wins over detection.  `run.py` detects the
    # context once and pins it into the environment of every child process
    # (bootstrap, API, console) so the whole startup path agrees.  Rewriting
    # only changes *where* a service is reached, never *which* backend is used:
    # PostgreSQL stays PostgreSQL in every context.
    runtime_context: Literal["auto", "host", "docker", "production"] = "auto"
    #: Host-accessible address used for the infrastructure services when the
    #: runtime context is `host` (Docker Desktop publishes on localhost).
    infra_host: str = runtime.DEFAULT_INFRA_HOST
    #: Host ports published by `docker-compose.infra.yml`.  The defaults are the
    #: container-internal ports declared in `docker-compose.yml`, so a default
    #: local stack needs no overrides at all.
    postgres_host_port: int = runtime.SERVICE_CONTAINER_PORTS["postgres"]
    neo4j_host_port: int = runtime.SERVICE_CONTAINER_PORTS["neo4j"]
    minio_host_port: int = runtime.SERVICE_CONTAINER_PORTS["minio"]
    redis_host_port: int = runtime.SERVICE_CONTAINER_PORTS["redis"]

    #: Filled in by `_resolve_runtime_endpoints`; never read from the environment.
    _resolved_runtime_context: str = PrivateAttr(default=runtime.CONTEXT_HOST)
    _endpoint_rewrites: list[str] = PrivateAttr(default_factory=list)

    postgres_dsn: str = Field(
        default=runtime.DEFAULT_POSTGRES_DSN,
        validation_alias=AliasChoices(
            "CRIMELINK_POSTGRES_DSN",
            "DATABASE_URL",
            "POSTGRES_URL",
            "POSTGRESQL_URL",
            "POSTGRES_DSN",
            "DATABASE_URI",
            "POSTGRES_URI",
        ),
    )
    postgres_dsn_sync: str = Field(
        default=runtime.DEFAULT_POSTGRES_DSN_SYNC,
        validation_alias=AliasChoices("CRIMELINK_POSTGRES_DSN_SYNC", "POSTGRES_DSN_SYNC"),
    )
    postgres_pool_size: int = 10
    postgres_max_overflow: int = 20
    #: Seconds an asyncpg connection attempt may take before failing. Managed
    #: PostgreSQL (Supabase etc.) occasionally stalls handshakes; without a
    #: bound a serverless cold start hangs until the platform kills it.
    postgres_connect_timeout_s: float = 10.0
    #: Per-statement timeout for the async engine (asyncpg ``command_timeout``).
    #: Generous enough for graph projection and import batches, bounded so a
    #: black-holed connection can never pin a serverless invocation forever.
    postgres_command_timeout_s: float = 120.0
    #: Recycle pooled PostgreSQL connections after this many seconds. Warm
    #: serverless instances outlive the idle-connection limit of managed
    #: proxies (Supabase/Supavisor drop idle clients); recycling plus the
    #: existing ``pool_pre_ping`` means a request never inherits a connection
    #: the server already closed. ``-1`` disables recycling.
    postgres_pool_recycle_s: int = 300
    #: On serverless platforms each function instance holds its own pool and
    #: the platform may run many instances; the defaults above (10+20 per
    #: instance) exhaust managed-PostgreSQL connection limits. When the
    #: operator has NOT set the pool variables explicitly, serverless runs use
    #: these smaller values instead. Explicit configuration always wins.
    #: Further, serverless now uses NullPool (no idle connections) to avoid
    #: holding `max_client_conn` slots on Layerbase; these numbers are the
    #: fallback when pooling is explicitly requested.
    postgres_serverless_pool_size: int = 1
    postgres_serverless_max_overflow: int = 0

    # Neo4j connection.  Each field accepts the ``CRIMELINK_``-prefixed name
    # (highest priority) **and** the bare names used by Neo4j's own tooling and
    # by Aura / managed deployments (e.g. ``NEO4J_URI``, ``NEO4J_USERNAME``,
    # ``NEO4J_PASSWORD``, ``NEO4J_DATABASE``) so a deployment that sets the
    # standard variables — as this production instance does — is honoured.
    # ``CRIMELINK_`` always wins when both are present.
    neo4j_uri: str = Field(
        default=runtime.DEFAULT_NEO4J_URI,
        validation_alias=AliasChoices(
            "CRIMELINK_NEO4J_URI", "NEO4J_URI", "NEO4J_CONNECTION_URI", "NEO4J_URL"
        ),
    )
    neo4j_user: str = Field(
        default="neo4j",
        validation_alias=AliasChoices("CRIMELINK_NEO4J_USER", "NEO4J_USER", "NEO4J_USERNAME"),
    )
    neo4j_password: str = Field(
        default="crimelink",
        # NOTE: ``NEO4J_AUTH`` (``user:password``) is deliberately NOT an
        # alias here — it is parsed into user+password by
        # ``_derive_managed_service_defaults``.
        validation_alias=AliasChoices(
            "CRIMELINK_NEO4J_PASSWORD", "NEO4J_PASSWORD", "NEO4J_PASSWORD_ENCRYPTED"
        ),
    )
    neo4j_database: str = Field(
        default="neo4j",
        validation_alias=AliasChoices("CRIMELINK_NEO4J_DATABASE", "NEO4J_DATABASE"),
    )
    neo4j_gds_enabled: bool = False  # GDS is optional; centrality is computed in Python

    redis_url: str = Field(
        default=runtime.DEFAULT_REDIS_URL,
        validation_alias=AliasChoices("CRIMELINK_REDIS_URL", "REDIS_URL", "REDIS_URI"),
    )
    celery_broker_url: str = Field(
        default=runtime.DEFAULT_CELERY_BROKER_URL,
        validation_alias=AliasChoices("CRIMELINK_CELERY_BROKER_URL", "CELERY_BROKER_URL"),
    )
    celery_result_backend: str = Field(
        default=runtime.DEFAULT_CELERY_RESULT_BACKEND,
        validation_alias=AliasChoices("CRIMELINK_CELERY_RESULT_BACKEND", "CELERY_RESULT_BACKEND"),
    )

    # MinIO / any S3-compatible object store.  Accepts the ``CRIMELINK_`` name,
    # the bare ``MINIO_*`` names, the AWS SDK style (``S3_ENDPOINT`` /
    # ``S3_ACCESS_KEY_ID`` / ``S3_SECRET_ACCESS_KEY``) and the AWS CLI style
    # (``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` / ``AWS_ENDPOINT_URL``).
    minio_endpoint: str = Field(
        default=runtime.DEFAULT_MINIO_ENDPOINT,
        validation_alias=AliasChoices(
            "CRIMELINK_MINIO_ENDPOINT",
            "S3_ENDPOINT",
            "S3_ENDPOINT_URL",
            "MINIO_ENDPOINT",
            "MINIO_SERVER",
            "AWS_ENDPOINT_URL",
        ),
    )
    minio_access_key: str = Field(
        default="crimelink",
        validation_alias=AliasChoices(
            "CRIMELINK_MINIO_ACCESS_KEY",
            "S3_ACCESS_KEY_ID",
            "S3_ACCESS_KEY",
            "MINIO_ACCESS_KEY",
            "MINIO_ACCESS_KEY_ID",
            "AWS_ACCESS_KEY_ID",
        ),
    )
    minio_secret_key: str = Field(
        default="crimelink",
        validation_alias=AliasChoices(
            "CRIMELINK_MINIO_SECRET_KEY",
            "S3_SECRET_ACCESS_KEY",
            "S3_SECRET_KEY",
            "MINIO_SECRET_KEY",
            "MINIO_SECRET_ACCESS_KEY",
            "AWS_SECRET_ACCESS_KEY",
        ),
    )
    minio_secure: bool = Field(
        default=False,
        validation_alias=AliasChoices("CRIMELINK_MINIO_SECURE", "S3_SECURE", "MINIO_SECURE"),
    )
    minio_bucket_documents: str = Field(
        default="documents",
        validation_alias=AliasChoices(
            "CRIMELINK_MINIO_BUCKET_DOCUMENTS", "S3_BUCKET_DOCUMENTS", "MINIO_BUCKET_DOCUMENTS"
        ),
    )
    minio_bucket_derived: str = Field(
        default="documents-derived",
        validation_alias=AliasChoices(
            "CRIMELINK_MINIO_BUCKET_DERIVED", "S3_BUCKET_DERIVED", "MINIO_BUCKET_DERIVED"
        ),
    )
    minio_bucket_audit_anchor: str = Field(
        default="audit-anchor",
        validation_alias=AliasChoices(
            "CRIMELINK_MINIO_BUCKET_AUDIT_ANCHOR",
            "S3_BUCKET_AUDIT_ANCHOR",
            "MINIO_BUCKET_AUDIT_ANCHOR",
        ),
    )

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
    #: The login screen offers the three documented demo accounts (Admin /
    #: Investigator / Viewer) as one-click quick sign-in so no password ever
    #: ships inside the frontend bundle. The backend endpoint authenticates
    #: the fixed demo badges through the normal token machinery and audits the
    #: sign-in. Operators who do not want the hosted demo accounts exposed on
    #: their deployment set ``CRIMELINK_DEMO_QUICK_LOGIN_ENABLED=false``.
    demo_quick_login_enabled: bool = True
    cors_origins: str | list[str] = Field(default_factory=lambda: ["*"])

    # ------------------------------------------------------------------- nlp
    # "auto" -> NIM when an API key is present, else the deterministic+heuristic
    # provider (which needs no model download and works fully offline).
    nlp_provider: Literal["auto", "nim", "indicner", "heuristic"] = "auto"
    nim_api_key: str | None = None
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nim_model: str = "nvidia/nemotron-3-super-120b-a12b"
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
    #
    # Availability is decided ONLY from these resolved settings — role key
    # first, then this global key.  ``run.py`` maps the legacy
    # ``NVIDIA_API_KEY`` onto ``CRIMELINK_AI_API_KEY`` explicitly, so a bare
    # legacy variable can never flip a role to "available" behind an
    # operator's back.
    ai_provider: str = "nvidia"
    ai_api_key: str | None = None
    ai_base_url: str = "https://integrate.api.nvidia.com/v1"
    ai_temperature: float = 0.1
    ai_max_tokens: int = 2048
    ai_timeout_s: float = 180.0
    ai_allow_raw_pii: bool = False            # safety: always false unless explicit
    ai_pseudonymize: bool = True              # apply reversible pseudonymization
    ai_audit_prompt_storage: bool = False     # whether to persist full prompts in audit

    ai_extraction_model: str = "nvidia/nemotron-3-super-120b-a12b"
    ai_extraction_provider: str = "default"   # "default" uses ai_provider/ai_api_key
    ai_extraction_api_key: str | None = None
    ai_extraction_base_url: str | None = None

    ai_reasoning_model: str = "nvidia/nemotron-3-super-120b-a12b"
    ai_reasoning_provider: str = "default"
    ai_reasoning_api_key: str | None = None
    ai_reasoning_base_url: str | None = None
    ai_reasoning_target_count: int = 5

    ai_explanation_model: str = "meta/llama-3.2-11b-vision-instruct"
    ai_explanation_provider: str = "default"
    ai_explanation_api_key: str | None = None
    ai_explanation_base_url: str | None = None

    ai_classification_model: str = "meta/llama-3.2-11b-vision-instruct"
    ai_classification_provider: str = "default"
    ai_classification_api_key: str | None = None
    ai_classification_base_url: str | None = None

    ai_embedding_model: str = "nvidia/llama-3.2-nv-embedqa-1b-v2"
    ai_embedding_provider: str = "default"
    ai_embedding_api_key: str | None = None
    ai_embedding_base_url: str | None = None

    # --- context budget -----------------------------------------------------
    # How much of a case subgraph may be sent to a model in one request.  These
    # bound the *prompt*, not the graph: retrieval walks the case and then
    # keeps the highest-value slice that fits.  They were referenced by the
    # gateway long before they existed here, which made every Case AI request
    # fail with an AttributeError before it ever reached a provider.
    ai_max_context_nodes: int = 300
    ai_max_context_edges: int = 600
    #: Total character budget for document text included in AI context.
    #: Previously every document (up to 3000 chars each) was sent unconditionally,
    #: so a 50-doc case produced 150k chars of document text alone.
    #: This caps the *total* document characters, not per-document.
    ai_max_context_doc_chars: int = 30000
    #: Default hop depth used when retrieving context around a target entity.
    ai_retrieval_depth: int = 2
    #: Retries for a transient provider failure (timeouts, 5xx, rate limits).
    ai_max_retries: int = 2

    # --- interactive path budget (§2.1 / §2.2) ------------------------------
    #: The interactive /ai/cases/{id}/ask and /ask/stream endpoints need a
    #: shorter latency budget than any background/batch AI usage.  These
    #: override the global ai_timeout_s / ai_max_retries / context budget
    #: for that path only.
    ai_interactive_timeout_s: float = 45.0
    ai_interactive_max_retries: int = 1
    ai_interactive_max_context_nodes: int = 100
    ai_interactive_max_context_edges: int = 200
    #: Interactive override for total document char budget — keeps the
    #: investigator-facing path comfortably within model timeout.
    ai_interactive_max_context_doc_chars: int = 15000

    # --- hybrid semantic retrieval ------------------------------------------
    # Semantic retrieval *supplements* deterministic retrieval (exact terms,
    # structured records, graph).  It is local-first: the index is a JSON file
    # beside the embedded graph, and when no embedding provider is configured
    # the deterministic local embedder is used, so the embedded profile needs
    # no service, no key and no network.
    ai_semantic_enabled: bool = True
    ai_semantic_use_provider_embeddings: bool = False
    ai_semantic_top_k: int = 8
    ai_semantic_min_score: float = 0.05
    #: Upper bound on embedded chunks per case (a bound on work, not on recall
    #: of the deterministic layers, which are unaffected).
    ai_semantic_max_chunks: int = 600
    #: How many semantic-only documents may join an already-ranked context.
    ai_semantic_max_extra_documents: int = 4
    #: Where per-case vector indexes live.  ``None`` → ``data_dir``/ai_index.
    ai_index_dir: Path | None = None
    #: Characters per embedded chunk and the overlap between adjacent chunks.
    ai_semantic_chunk_chars: int = 700
    ai_semantic_chunk_overlap: int = 120
    #: Characters of each record kept for indexing and narrative analysis.
    #: Chunking exists so a long record is *not* lost to a prompt-sized
    #: excerpt: the index and the claim extractor read the whole record, while
    #: the prompt still receives only what its own character budget allows.
    ai_semantic_index_doc_chars: int = 12000

    # --- narrative intelligence ---------------------------------------------
    # Claims/contradictions/corroboration are extracted from at most this many
    # case records per question, so a very large case file stays bounded.
    ai_intelligence_max_documents: int = 40
    #: Optional model-assisted narrative claim extraction.  Off by default: the
    #: deterministic extractor always runs, and model-proposed claims are only
    #: admitted after quoting a stored record verbatim.  Off keeps the
    #: interactive path to a single model call.
    ai_narrative_model_extraction: bool = False

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
    # Default: backend/CrimeLink_Synthetic_Corpus_v1 (optional evaluation
    # fixture committed to this repository; never read at startup).
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
    def _resolve_runtime_endpoints(self) -> "Settings":
        """Make the infrastructure endpoints reachable from wherever this runs.

        Detection and rewriting live in :mod:`app.runtime`, so the launcher, the
        API, the workers, Alembic and the seed scripts all resolve endpoints
        identically instead of each growing their own idea of where PostgreSQL
        lives.

        Nothing here can downgrade a backend: a ``postgres`` DSN stays a
        ``postgres`` DSN, a ``minio`` object store stays MinIO.  Only the
        host/port of an endpoint may change, and only in the ``host`` context
        where the Compose DNS name cannot possibly resolve.  Container and
        production contexts are returned untouched.
        """
        context = runtime.resolve_runtime_context(
            explicit=self.runtime_context,
            profile=self.profile,
            environment=self.environment,
        )
        rewrites: list[str] = []
        if context == runtime.CONTEXT_HOST:
            for field, service in runtime.ENDPOINT_FIELDS:
                configured = getattr(self, field)
                resolution = runtime.resolve_service_endpoint(
                    configured,
                    service,
                    context=context,
                    field=field,
                    infra_host=self.infra_host,
                    host_port=getattr(self, runtime.HOST_PORT_FIELDS[service], None),
                )
                if resolution.rewritten and resolution.value != configured:
                    object.__setattr__(self, field, resolution.value)
                    rewrites.append(
                        f"{field}: {resolution.configured_host} -> "
                        f"{resolution.host}:{resolution.port}"
                    )
        self._resolved_runtime_context = context
        self._endpoint_rewrites = rewrites
        return self

    @model_validator(mode="after")
    def _derive_managed_service_defaults(self) -> "Settings":
        """Derive credential gaps from the connection string itself.

        Two common deployment patterns set only part of the configuration:

        * **Neo4j Aura** — the URI is ``<scheme>://<instance-id>.databases.
          neo4j.io`` and the database *and* user are that same instance id.
          A deployment that sets only ``NEO4J_URI`` (plus credentials) would
          otherwise target a database literally named ``neo4j`` which Aura
          never has, surfacing as ``22N51 GraphDatabaseError: The provided
          reference does not identify any graph database``.  Deriving both
          from the URI makes the connection string the single source of truth
          and makes the old failure structurally impossible: the default
          database ``neo4j`` can never be selected for an Aura host.
        * **S3/MinIO** — an endpoint that already carries an ``https://``
          scheme is, by definition, TLS.

        Explicit configuration always wins: a value the operator set in the
        environment (any of the accepted names) is never overridden here.
        ``NEO4J_AUTH`` (the standard ``user:password`` form) fills both the
        user and the password when neither was set.
        """
        explicit = set(self.model_fields_set)

        # NEO4J_AUTH="user:password" is the canonical Neo4j environment form.
        auth = os.environ.get("NEO4J_AUTH", "")
        auth_user, sep, auth_password = auth.partition(":")
        if (
            sep
            and auth_user
            and auth_password
            and "neo4j_user" not in explicit
            and "neo4j_password" not in explicit
        ):
            self.neo4j_user = auth_user
            self.neo4j_password = auth_password
            explicit |= {"neo4j_user", "neo4j_password"}

        host = urlparse(self.neo4j_uri).hostname or ""
        aura = re.fullmatch(r"([0-9a-f]{8})\.databases\.neo4j\.io", host)
        if aura:
            instance_id = aura.group(1)
            if "neo4j_user" not in explicit and self.neo4j_user == "neo4j":
                self.neo4j_user = instance_id
            if "neo4j_database" not in explicit and self.neo4j_database == "neo4j":
                self.neo4j_database = instance_id

        # The MinIO client takes a bare ``host[:port]`` — the scheme is
        # conveyed by the ``secure`` flag, and a URL with a scheme raises
        # ``"path in endpoint is not allowed"``.  AWS-style environment
        # variables (``S3_ENDPOINT`` / ``AWS_ENDPOINT_URL``) carry a full
        # URL, so normalise it: ``https://s3.x.com`` → endpoint ``s3.x.com``
        # with TLS on.  A scheme-less endpoint keeps working as before
        # (``MINIO_SECURE`` decides).
        if "://" in self.minio_endpoint:
            parts = urlparse(self.minio_endpoint)
            if parts.scheme in {"http", "https"} and parts.hostname:
                self.minio_endpoint = self.minio_endpoint.split("://", 1)[1].rstrip("/")
                if parts.scheme == "https":
                    self.minio_secure = True
        return self

    @model_validator(mode="after")
    def _validate_production_security(self) -> "Settings":
        """Fail closed when an operator selects a production profile.

        Development keeps the embedded zero-configuration experience, but a
        production process must never boot with the repository placeholders,
        wildcard CORS, or debug logging enabled.
        """
        production = self.profile == "production" or self.environment == "production"
        if production:
            if self.debug:
                raise ValueError("CRIMELINK_DEBUG must be false in production")
            if self.secret_key.startswith(("change-me", "GENERATE", "<")) or len(self.secret_key) < 32:
                raise ValueError("CRIMELINK_SECRET_KEY must be a generated 32+ character secret in production")
            if "*" in self.cors_origins:
                raise ValueError("CRIMELINK_CORS_ORIGINS must not contain '*' in production")
            if self.neo4j_password in {"crimelink", "neo4j"}:
                raise ValueError("CRIMELINK_NEO4J_PASSWORD must be changed in production")
            if self.minio_secret_key == "crimelink":
                raise ValueError("CRIMELINK_MINIO_SECRET_KEY must be changed in production")
            if "crimelink:crimelink@" in self.postgres_dsn or "GENERATE_" in self.postgres_dsn:
                raise ValueError("CRIMELINK_POSTGRES_DSN must contain a real production credential")
        return self

    @model_validator(mode="after")
    def _relocate_graph_snapshot(self) -> "Settings":
        """Keep the embedded graph beside the database unless told otherwise."""
        if self.graph_snapshot_path == DEFAULT_GRAPH_SNAPSHOT:
            object.__setattr__(self, "graph_snapshot_path", self.data_dir / "graph.json")
        if self.ai_index_dir is None:
            object.__setattr__(self, "ai_index_dir", self.data_dir / "ai_index")
        return self

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def _split_origins(cls, v: Any) -> Any:
        if v is None or v == "":
            return ["*"]
        if isinstance(v, list):
            return [str(part).strip() for part in v if str(part).strip()]
        if isinstance(v, str):
            text = v.strip()
            if not text:
                return ["*"]
            if text.startswith("["):
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, list):
                        return [str(part).strip() for part in parsed if str(part).strip()]
                    if isinstance(parsed, str):
                        return [parsed.strip()] if parsed.strip() else ["*"]
                except json.JSONDecodeError:
                    pass
            return [part.strip() for part in text.split(",") if part.strip()] or ["*"]
        return [str(v).strip()] if str(v).strip() else ["*"]

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
    # The four adapter selections delegate to app.runtime so `run.py` (which
    # cannot import pydantic before the virtualenv exists) resolves them with
    # exactly the same logic.
    @property
    def effective_relational_backend(self) -> str:
        return runtime.resolve_backend(
            self.relational_backend,
            profile=self.profile,
            production_backend="postgres",
            embedded_backend="sqlite",
        )

    @property
    def effective_graph_backend(self) -> str:
        return runtime.resolve_backend(
            self.graph_backend,
            profile=self.profile,
            production_backend="neo4j",
            embedded_backend="embedded",
        )

    @property
    def effective_object_store_backend(self) -> str:
        return runtime.resolve_backend(
            self.object_store_backend,
            profile=self.profile,
            production_backend="minio",
            embedded_backend="local",
        )

    @property
    def effective_broker_backend(self) -> str:
        # On Vercel / serverless the default production broker (celery+redis)
        # is not available unless the operator explicitly provides Redis URLs.
        # The judge-focused deployment is documented to use inline. If the
        # operator did not set broker_backend explicitly, default to inline on
        # serverless to avoid trying redis://redis:6379/0 and spamming
        # `rate_limit.redis_unavailable` warnings. Explicit `celery` still wins.
        if runtime.running_on_serverless():
            if self.broker_backend == "auto" and "broker_backend" not in self.model_fields_set:
                return "inline"
        return runtime.resolve_backend(
            self.broker_backend,
            profile=self.profile,
            production_backend="celery",
            embedded_backend="inline",
        )

    # ------------------------------------------------------- runtime context
    @property
    def resolved_runtime_context(self) -> str:
        """`host`, `docker` or `production` — resolved once at construction."""
        return self._resolved_runtime_context

    @property
    def is_host_runtime(self) -> bool:
        """True when running natively on the machine that started the process."""
        return self._resolved_runtime_context == runtime.CONTEXT_HOST

    @property
    def is_production_deployment(self) -> bool:
        """True for the production profile/environment (fail-closed paths)."""
        return self.profile == "production" or self.environment == "production"

    @property
    def endpoint_rewrites(self) -> list[str]:
        """Compose hostnames that were rewritten for host-native execution."""
        return list(self._endpoint_rewrites)

    @property
    def resolved_endpoints(self) -> dict[str, runtime.EndpointResolution]:
        """Every infrastructure endpoint resolved for this runtime context."""
        return runtime.resolve_infrastructure(
            context=self._resolved_runtime_context,
            infra_host=self.infra_host,
            values={field: getattr(self, field) for field, _ in runtime.ENDPOINT_FIELDS},
        )

    @property
    def postgres_endpoint(self) -> tuple[str, int]:
        """Host/port of the resolved PostgreSQL DSN (used by bootstrap checks)."""
        host, port = runtime.split_endpoint(
            self.postgres_dsn_sync, runtime.SERVICE_CONTAINER_PORTS["postgres"]
        )
        return host, int(port or runtime.SERVICE_CONTAINER_PORTS["postgres"])

    @property
    def neo4j_endpoint(self) -> tuple[str, int]:
        host, port = runtime.split_endpoint(
            self.neo4j_uri, runtime.SERVICE_CONTAINER_PORTS["neo4j"]
        )
        return host, int(port or runtime.SERVICE_CONTAINER_PORTS["neo4j"])

    @property
    def minio_endpoint_address(self) -> tuple[str, int]:
        host, port = runtime.split_endpoint(
            self.minio_endpoint, runtime.SERVICE_CONTAINER_PORTS["minio"]
        )
        return host, int(port or runtime.SERVICE_CONTAINER_PORTS["minio"])

    @property
    def redis_endpoint(self) -> tuple[str, int]:
        host, port = runtime.split_endpoint(
            self.redis_url, runtime.SERVICE_CONTAINER_PORTS["redis"]
        )
        return host, int(port or runtime.SERVICE_CONTAINER_PORTS["redis"])

    @property
    def required_infrastructure(self) -> list[str]:
        """Compose services the currently selected adapters depend on."""
        return runtime.required_services(
            {
                "relational": self.effective_relational_backend,
                "graph": self.effective_graph_backend,
                "object_store": self.effective_object_store_backend,
                "broker": self.effective_broker_backend,
            }
        )

    def endpoint_report(self) -> dict[str, Any]:
        """Non-secret description of the runtime context and every endpoint."""
        return {
            "runtime_context": self._resolved_runtime_context,
            "runtime_context_description": runtime.CONTEXT_DESCRIPTIONS.get(
                self._resolved_runtime_context, ""
            ),
            "profile": self.profile,
            "environment": self.environment,
            "infra_host": self.infra_host,
            "backends": {
                "relational": self.effective_relational_backend,
                "graph": self.effective_graph_backend,
                "object_store": self.effective_object_store_backend,
                "broker": self.effective_broker_backend,
            },
            "endpoints": {
                field: resolution.as_dict() for field, resolution in self.resolved_endpoints.items()
            },
            "rewrites": self.endpoint_rewrites,
        }

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
        ``backend/CrimeLink_Synthetic_Corpus_v1`` is the local synthetic
        corpus regardless of the operator's cwd.  The corpus is fully
        synthetic and tracked in the repository.  The directory is *not*
        required to exist — the external-corpus adapter reports a clear
        error when it is missing.
        """
        root = Path(self.synthetic_data_root).expanduser()
        if not root.is_absolute():
            root = REPO_ROOT / root
        return root.resolve()

    # ---- AI role resolution (provider "default" falls back to global) ------
    def role_config(self, role: str) -> dict[str, Any]:
        """Return base_url / api_key / model for a given AI model role.

        Availability is decided **only** from resolved settings: the role key
        (``CRIMELINK_AI_<ROLE>_API_KEY``) or the global key
        (``CRIMELINK_AI_API_KEY``, which ``run.py`` populates from the legacy
        ``NVIDIA_API_KEY`` when present).  A role with no key is reported
        unavailable, and the gateway returns a structured "unavailable"
        result — no provider invocation is attempted for it.
        """
        prefix = f"ai_{role}"
        model = getattr(self, f"{prefix}_model", "")
        provider = getattr(self, f"{prefix}_provider", "default")
        api_key = getattr(self, f"{prefix}_api_key", None) or self.ai_api_key
        base_url = getattr(self, f"{prefix}_base_url", None) or self.ai_base_url
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

    #: Every AI role the platform declares.  Used by the health check so the
    #: Administration screen can report each one independently instead of
    #: implying a single global "AI is on/off" switch.
    AI_ROLES: ClassVar[tuple[str, ...]] = (
        "extraction",
        "reasoning",
        "explanation",
        "classification",
        "embedding",
    )

    def ai_role_report(self, role: str) -> dict[str, Any]:
        """Non-secret description of how a role is configured.

        Deliberately returns ``key_present``/``key_fingerprint`` rather than
        the key: this feeds an API response, and an API response must never be
        able to carry credential material.  The fingerprint is a truncated
        hash, enough to tell two keys apart in a support conversation and
        useless to an attacker.
        """
        config = self.role_config(role)
        key = config.get("api_key") or ""
        fingerprint = (
            hashlib.sha256(key.encode("utf-8")).hexdigest()[:8] if key else None
        )
        return {
            "role": role,
            "provider": config["provider"],
            "model": config["model"],
            "base_url": config["base_url"],
            "key_present": bool(key),
            "key_fingerprint": fingerprint,
            "key_source": self._ai_key_source(role),
            "temperature": config["temperature"],
            "max_tokens": config["max_tokens"],
            "timeout_s": config["timeout"],
            "available": bool(key),
        }

    def _ai_key_source(self, role: str) -> str | None:
        """Which environment variable supplied this role's key."""
        if getattr(self, f"ai_{role}_api_key", None):
            return f"CRIMELINK_AI_{role.upper()}_API_KEY"
        if self.ai_api_key:
            return "CRIMELINK_AI_API_KEY"
        return None

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
