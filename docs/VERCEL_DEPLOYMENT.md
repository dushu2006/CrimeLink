# Vercel deployment

CrimeLink is configured as a Vercel Services deployment. The checked-in
`vercel.json` keeps the existing two-service architecture:

- `frontend/` is the Vite console (`npm ci`, `npm run build`, output `dist`).
- `backend/` is the FastAPI service with entrypoint `main:app`
  (`backend/main.py` re-exports the existing `app.main:app` ASGI instance for
  Vercel's runtime, which resolves a service entrypoint file at the service
  root).
- `/api/*` is sent to the backend service.
- Every other path is sent to the frontend service, whose `/index.html` rewrite
  provides the existing SPA deep-link fallback.

The frontend uses relative `/api` URLs. There is no Vercel-only API base URL and
no browser call to `localhost`.

The backend function explicitly bundles `CrimeLink_Synthetic_Corpus_v1/**`.
On serverless startup, the backend imports that checked-in corpus using the
same content-driven `app.datasets.pipeline.run_import()` used for arbitrary
uploads. It writes source bytes to the configured durable S3-compatible object
store, reads them back, verifies SHA-256, builds canonical rows and the
dataset-scoped graph, and activates only after the normal usability gate passes.
No judge upload or cloud-only seed records are required. This does not change
`python run.py`: local startup continues to use the existing V2 demo bootstrap.
An existing operator-imported active dataset is left alone; the one-time
bootstrap only fills an empty workspace or replaces the two known legacy built-in
demo IDs. Set `CRIMELINK_BUILTIN_DATASET_AUTO_IMPORT=false` to disable it.

## What is required online

Vercel supplies the HTTP compute and static frontend only. The production
profile intentionally selects the existing durable adapters; it does not turn
SQLite, the embedded graph, local object storage, or the inline executor into a
serverless persistence layer.

| Service | Required for the pre-seeded judge flows? | Required when these existing features are used | Source variables |
| --- | --- | --- | --- |
| PostgreSQL | Yes: authentication, cases, people, evidence metadata, timelines, search, audit and job state | Always | `CRIMELINK_POSTGRES_DSN`, `CRIMELINK_POSTGRES_DSN_SYNC` |
| Neo4j | Yes: graph pages, graph statistics/analytics and relationship traversal | Always for graph functionality | `CRIMELINK_NEO4J_URI`, `CRIMELINK_NEO4J_USER`, `CRIMELINK_NEO4J_PASSWORD`, `CRIMELINK_NEO4J_DATABASE` (plain `NEO4J_URI`/`NEO4J_DATABASE`/`NEO4J_USERNAME`/`NEO4J_PASSWORD` and `NEO4J_AUTH` are also accepted; a `CRIMELINK_` value wins when both are set) |
| S3-compatible object storage / MinIO | Yes: evidence and source file retrieval | Always for evidence/file functionality | `CRIMELINK_MINIO_ENDPOINT`, `CRIMELINK_MINIO_ACCESS_KEY`, `CRIMELINK_MINIO_SECRET_KEY`, `CRIMELINK_MINIO_SECURE` and the existing bucket variables |
| Redis + Celery worker/beat | No for a pre-seeded, read-focused judge deployment when `CRIMELINK_BROKER_BACKEND=inline` is explicitly selected | Required for document imports, background pipeline processing, scheduled pattern/audit jobs, distributed rate limiting, and cross-instance Redis event delivery | `CRIMELINK_BROKER_BACKEND`, `CRIMELINK_REDIS_URL`, `CRIMELINK_CELERY_BROKER_URL`, `CRIMELINK_CELERY_RESULT_BACKEND` |
| Configured AI/NLP provider | No for non-AI pages; the existing deterministic/heuristic fallback remains honest and structured | Required to exercise provider-backed extraction or genuine provider-backed AI reasoning/explanations | `CRIMELINK_NLP_PROVIDER`, `CRIMELINK_NIM_API_KEY`, `NVIDIA_API_KEY` (legacy NIM path), `CRIMELINK_AI_API_KEY` or role-specific `CRIMELINK_AI_<ROLE>_API_KEY`, and their existing base URL/model variables |

The inline broker is an existing adapter selection, not a mock or a separate
demo mode. It runs jobs in the current process and therefore does not promise
durable background execution across Vercel function instances. Use the Celery
option when those workflows are part of the online acceptance scope. The code
for Celery, Redis, WebSockets, polling, reconnect, and all existing fallbacks
is unchanged.

For full production parity, set `CRIMELINK_BROKER_BACKEND=celery`, provide the
three Redis/Celery URLs, and run the existing worker and beat processes on a
persistent service outside Vercel. The commands are the same as the Docker
workflow:

```text
celery -A app.adapters.broker.celery_app:celery_app worker -Q pipeline,analytics,maintenance --concurrency=2 --loglevel=INFO
celery -A app.adapters.broker.celery_app:celery_app beat --loglevel=INFO
```

Do not use `postgres`, `neo4j`, `redis`, or `minio` as production endpoint
hostnames unless they are actually resolvable from the Vercel function. Use
publicly reachable or privately networked endpoints supported by the chosen
Vercel plan and provider.

## Exact environment variables

`.env.vercel.example` is the checked-in, secret-free template. Add the values to
the backend service (project-scoped variables are also acceptable). The
following names are the exact `Settings` fields exposed by
`backend/app/config.py` with its `CRIMELINK_` prefix.

### Required backend configuration

```text
CRIMELINK_PROFILE=production
CRIMELINK_ENVIRONMENT=production
CRIMELINK_RUNTIME_CONTEXT=production
CRIMELINK_DEBUG=false
CRIMELINK_LOG_LEVEL=INFO
CRIMELINK_SECRET_KEY=<unique-32-plus-character-secret>
CRIMELINK_CORS_ORIGINS=https://<actual-vercel-domain>
CRIMELINK_TRUSTED_HOSTS=*.vercel.app,<optional-custom-domain>
CRIMELINK_DATA_DIR=/tmp/crimelink-data
CRIMELINK_OBJECT_STORE_DIR=/tmp/crimelink-objects
CRIMELINK_BUILTIN_DATASET_AUTO_IMPORT=true

CRIMELINK_POSTGRES_DSN=postgresql+asyncpg://<user>:<password>@<host>:<port>/<database>
CRIMELINK_POSTGRES_DSN_SYNC=postgresql+psycopg2://<user>:<password>@<host>:<port>/<database>

CRIMELINK_NEO4J_URI=bolt+s://<host>:<port>
CRIMELINK_NEO4J_USER=<user>
CRIMELINK_NEO4J_PASSWORD=<password>
CRIMELINK_NEO4J_DATABASE=<database>
CRIMELINK_NEO4J_GDS_ENABLED=false

CRIMELINK_MINIO_ENDPOINT=<s3-compatible-host>:<port>
CRIMELINK_MINIO_ACCESS_KEY=<access-key>
CRIMELINK_MINIO_SECRET_KEY=<secret>
CRIMELINK_MINIO_SECURE=true
CRIMELINK_MINIO_BUCKET_DOCUMENTS=documents
CRIMELINK_MINIO_BUCKET_DERIVED=documents-derived
CRIMELINK_MINIO_BUCKET_AUDIT_ANCHOR=audit-anchor
```

`CRIMELINK_DATA_DIR` and `CRIMELINK_OBJECT_STORE_DIR` are only writable
transient workspace paths. They are not a substitute for PostgreSQL or object
storage and must not be used as the durable demo data location.

Choose one broker configuration:

```text
# Full background/job parity
CRIMELINK_BROKER_BACKEND=celery
CRIMELINK_REDIS_URL=rediss://<user>:<password>@<redis-host>:<port>/0
CRIMELINK_CELERY_BROKER_URL=rediss://<user>:<password>@<redis-host>:<port>/1
CRIMELINK_CELERY_RESULT_BACKEND=rediss://<user>:<password>@<redis-host>:<port>/2
```

or, for a pre-seeded/read-focused judge deployment that does not exercise those
background workflows:

```text
CRIMELINK_BROKER_BACKEND=inline
```

With `inline`, omit the Redis/Celery variables. This is the existing in-process
adapter and retains the same API contracts; it is not a persistence guarantee
for asynchronous jobs or live events across separate serverless instances.

### Optional provider-backed AI/NLP configuration

Set real credentials only when those flows are in the judge scope. The role
names are `EXTRACTION`, `REASONING`, `EXPLANATION`, `CLASSIFICATION`, and
`EMBEDDING`.

```text
CRIMELINK_NLP_PROVIDER=auto
CRIMELINK_NIM_API_KEY=<nim-key>
CRIMELINK_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
CRIMELINK_NIM_MODEL=<nim-model>

CRIMELINK_AI_PROVIDER=nvidia
CRIMELINK_AI_API_KEY=<ai-provider-key>
CRIMELINK_AI_BASE_URL=https://integrate.api.nvidia.com/v1
CRIMELINK_AI_EXTRACTION_MODEL=<model>
CRIMELINK_AI_REASONING_MODEL=<model>
CRIMELINK_AI_EXPLANATION_MODEL=<model>
CRIMELINK_AI_CLASSIFICATION_MODEL=<model>
CRIMELINK_AI_EMBEDDING_MODEL=<model>
CRIMELINK_AI_ALLOW_RAW_PII=false
CRIMELINK_AI_PSEUDONYMIZE=true
CRIMELINK_AI_AUDIT_PROMPT_STORAGE=false
```

Role-specific `CRIMELINK_AI_<ROLE>_API_KEY` and
`CRIMELINK_AI_<ROLE>_BASE_URL` values can override the shared AI values. The
legacy `NVIDIA_API_KEY` is retained for the NIM adapter; use
`CRIMELINK_AI_API_KEY` for the AI Gateway. Never commit any credential or
replace an unavailable provider response with hardcoded data.

Optional application overrides in the template are also source-backed:
`CRIMELINK_ACCESS_TOKEN_TTL_MINUTES`, `CRIMELINK_REFRESH_TOKEN_TTL_HOURS`,
`CRIMELINK_RATE_LIMIT_PER_MINUTE`, `CRIMELINK_RATE_LIMIT_AUTH_PER_MINUTE`,
`CRIMELINK_UPLOAD_MAX_BYTES`, `CRIMELINK_MAX_REQUEST_BYTES`,
`CRIMELINK_AI_TIMEOUT_S`, `CRIMELINK_AI_INTERACTIVE_TIMEOUT_S`, and
`CRIMELINK_AUDIT_ANCHOR_ENABLED`.

The frontend does not need a `VITE_API_URL` or any other API URL variable:
`frontend/src/api/client.ts` uses relative paths and `frontend/vite.config.ts`
only adds the local development proxy.

## Deployment steps

1. Provision PostgreSQL, Neo4j, and durable S3-compatible object storage.
   Ensure the configured documents, documents-derived, and audit-anchor buckets
   exist (or allow the application to create them) and the service identity can
   list, read, write, and delete dataset-owned objects. Provision Redis and a
   persistent Celery worker/beat service as well if imports, background jobs,
   scheduled jobs, or cross-instance live events are in scope.
2. Add the production PostgreSQL, Neo4j, and S3-compatible object-store
   settings. The bundled repository corpus is imported automatically on the
   first serverless startup through the generic importer. Do not run the local
   `bootstrap_demo_dataset()` command against production: `python run.py` keeps
   its separate, non-destructive local V2 seeding behavior. The serverless
   importer verifies the replacement before activation and preserves an
   operator-imported active dataset.
3. Import the repository into Vercel and select **Services**. Keep the project
   root unset; the checked-in root `vercel.json` supplies the frontend and
   backend service roots.
4. Add the required backend variables above to the Vercel project or backend
   service. Replace every placeholder, use the final Vercel/custom domain in
   `CRIMELINK_CORS_ORIGINS`, and keep `CRIMELINK_TRUSTED_HOSTS` aligned with
   that host. Do not add secrets to git.
5. Keep the backend function runtime/plan compatible with the checked-in
   `maxDuration: 300` setting. Deploy with the normal Vercel production deploy
   flow (`vercel --prod` or the dashboard).
6. After deployment, check `/api/v1/health/live`,
   `/api/v1/health/ready`, `/api/openapi.json`, `/api/docs`, `/`, and at least
   one client-side deep link such as `/cases/<case-id>/graph`. Then test the
   seeded login and the judge flows relevant to the selected broker/AI scope.

The FastAPI lifespan performs the schema upgrade, initializes configured
adapters, and (when enabled) imports the bundled corpus through the standard
pipeline. S3-compatible storage is mandatory for imported source/evidence bytes;
production reads do not fall back to the function bundle when an object is
missing. Storage errors or hash mismatches prevent activation and leave the
previous active dataset intact. Neo4j/MinIO are never silently replaced by local
adapters in production.

## Validation status for this change

Regression coverage exercises the checked-in corpus through the real generic
import pipeline, including its cases/entities/relationships, object-store
round-trip SHA-256 verification, dataset-scoped graph identity, evidence/source
resolution, and replacement failure safety. Local startup code in `run.py` and
`backend/app/db/bootstrap.py` is not redirected to the serverless importer.

A Vercel deployment and hosted PostgreSQL, Neo4j, Redis/Celery, S3-compatible
storage, and provider-backed AI/NLP were not available for this change; those
live cloud integrations are not claimed as tested. See the PR test report for
the exact commands and outcomes from the local regression suite.
