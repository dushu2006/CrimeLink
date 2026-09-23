# Vercel deployment

CrimeLink is configured as a Vercel Services deployment. The repository root
`vercel.json` builds the existing Vite console from `frontend/` and the existing
FastAPI application from `backend/`, then routes `/api/*` to FastAPI and every
other path to the console.

## Deployment steps

1. Import the repository as one Vercel project and select the **Services**
   framework/preset. Services are defined by the checked-in `vercel.json`; do
   not set a separate project root in the Vercel dashboard.
2. Keep Fluid Compute enabled for the backend service so the existing
   WebSocket endpoints can operate within Vercel's documented limits.
3. Add the production variables from `.env.vercel.example` to the backend
   service (project-scoped variables are also valid). Add no secrets to git.
4. Point the variables at the existing external infrastructure. Vercel does
   not provide PostgreSQL, Neo4j, Redis/Celery workers, or S3-compatible object
   storage as part of this repository deployment.
5. Deploy. The FastAPI lifespan runs the existing idempotent Alembic upgrade;
   it does not reset, drop, or reseed an existing database.

The Vercel service entrypoint is `app.main:app`, the same FastAPI instance used
by local uvicorn and the Docker deployment. No replacement API or adapter is
used.

## External services and persistence

The production profile intentionally selects the existing adapters:

- PostgreSQL is the relational system of record. Supply both async and sync
  DSNs for the same database.
- Neo4j remains the graph backend.
- MinIO/S3-compatible storage remains the object store for evidence and derived
  artifacts. Use an externally reachable endpoint; do not use the Compose name
  `minio`.
- Redis remains the existing Celery broker/result backend and rate-limit store.
  The Celery worker and beat processes are persistent services outside Vercel.
  Vercel functions do not pretend to be a permanent worker.
- The database, graph, and object data must be provisioned in the external
  services before production use. Vercel's local filesystem is ephemeral: set
  `CRIMELINK_DATA_DIR` and `CRIMELINK_OBJECT_STORE_DIR` to writable `/tmp`
  paths for transient upload/index workspace only. Durable evidence and
  application records must remain in the configured external services. The
  checked-in corpus and demo scripts are preserved for local and
  external-service workflows; deployment does not run a destructive seed
  automatically.

The application retains its WebSocket endpoints and frontend reconnect/polling
fallback. Vercel WebSockets run on Fluid Compute and may reconnect; durable job
state is already stored in the database. Use an external Redis-compatible
coordination layer for deployments that require cross-instance live events.

## Environment variables

See `.env.vercel.example` for names and non-secret placeholders. The existing
root `.env.example` remains the local embedded/Compose development template.

Required production categories:

- core: `CRIMELINK_PROFILE`, `CRIMELINK_ENVIRONMENT`,
  `CRIMELINK_RUNTIME_CONTEXT`, `CRIMELINK_SECRET_KEY`,
  `CRIMELINK_CORS_ORIGINS`, `CRIMELINK_TRUSTED_HOSTS`,
  `CRIMELINK_DATA_DIR`, `CRIMELINK_OBJECT_STORE_DIR`
- database: `CRIMELINK_POSTGRES_DSN`, `CRIMELINK_POSTGRES_DSN_SYNC`
- Neo4j: `CRIMELINK_NEO4J_URI`, `CRIMELINK_NEO4J_USER`,
  `CRIMELINK_NEO4J_PASSWORD`, `CRIMELINK_NEO4J_DATABASE`
- Redis/Celery: `CRIMELINK_REDIS_URL`, `CRIMELINK_CELERY_BROKER_URL`,
  `CRIMELINK_CELERY_RESULT_BACKEND`
- object storage: `CRIMELINK_MINIO_ENDPOINT`,
  `CRIMELINK_MINIO_ACCESS_KEY`, `CRIMELINK_MINIO_SECRET_KEY`,
  `CRIMELINK_MINIO_SECURE`
- NLP/AI: optional `CRIMELINK_NIM_*`, `NVIDIA_API_KEY`, and role/global
  `CRIMELINK_AI_*` variables, according to the existing provider configuration

Never use `postgres`, `neo4j`, `redis`, or `minio` as production endpoint
hostnames unless those names are genuinely resolvable from Vercel. Those names
are only local Compose defaults.
