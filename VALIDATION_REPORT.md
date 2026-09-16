# CrimeLink Demo Environment Validation Report
**Date:** 2026-09-16
**Dataset:** DEMO-DATASET-002 (v2)

## Infrastructure Status

| Service | Backend | Status |
|---------|---------|--------|
| Relational DB | PostgreSQL 16 (127.0.0.1:5432) | ✅ OK (9 Alembic migrations applied) |
| Graph | EmbeddedGraphStore (NetworkX, persistent JSON) | ✅ OK (575 nodes, 2803 edges) |
| Object Store | LocalObjectStore (write-once + HMAC signed URLs) | ✅ OK (360 files) |
| Broker | Celery + Redis 7.4.2 | ✅ OK (1 worker alive) |

**Health endpoint returns `status: "ready"`** with all four backends reporting OK.

## Demo Credentials
- **Admin:** `DEMO-ADMIN` / `DemoAdmin@2026` (full access)
- **Investigator:** `DEMO-INVESTIGATOR` / `DemoInvestigator@2026`
- **Viewer (read-only):** `DEMO-VIEWER` / `DemoViewer@2026`

## Dataset Contents (DEMO-DATASET-002)

| Entity | Count |
|--------|-------|
| Cases (CR-2001 … CR-2025) | 25 |
| Persons | 120 |
| Phones | 90 |
| Vehicles | 40 |
| Locations/Addresses | 50 |
| Organizations | 15 |
| Bank Accounts | 35 |
| Timeline Events | 200 |
| Call Data Records (communications) | 400 |
| Financial Transactions | 250 |
| Evidence Documents (PDF+CSV, real files) | 320 |
| Confidential Source Documents | 40 |
| Investigation Findings | 30 |
| Detected Patterns | 15 |
| Investigation Tasks | 30 |
| Graph Edges (relationships) | 2,803 |

Cross-case connections: **300** case-to-case edges in master case network (shared persons, phones, accounts, vehicles, and direct cross-case relationships).

## Cases Covered (themes)
Armed robbery, auto theft, narcotics trafficking, financial fraud, extortion, kidnapping, arms smuggling, cyber fraud, land forgery, counterfeit currency, human trafficking, contract killing, gambling, smuggling, insurance fraud, gang activity, ATM skimming, bootlegging, wildlife trafficking, corruption, hit-and-run, money laundering, certificate forgery, chain snatching, and conspiracy.

## Endpoint Validation (all 200 OK)
- `/api/v1/health`
- `/api/v1/auth/login` (JWT with refresh + single-flight refresh)
- `/api/v1/cases` (jurisdiction-scoped listing; 25 cases for METRO-CENTRAL admin)
- `/api/v1/cases/{id}` (case detail)
- `/api/v1/cases/{id}/dashboard` (aggregated stats, high-priority items, recent activity, gaps)
- `/api/v1/cases/{id}/documents` (with signed HMAC presigned URLs)
- `/api/v1/cases/{id}/persons`
- `/api/v1/cases/{id}/timeline` (temporal event feed)
- `/api/v1/cases/{id}/investigation`
- `/api/v1/cases/{id}/findings`
- `/api/v1/graph/cases/{id}` (case canvas, Cytoscape-ready elements)
- `/api/v1/graph/cases/{id}/temporal` (time-windowed graph)
- `/api/v1/graph/cases/{id}/influencers` (centrality rankings)
- `/api/v1/graph/master` (cross-case graph, 527 nodes / 2,530 edges)
- `/api/v1/graph/master/case-network` (300 cross-case edges with strength and evidence)
- `/api/v1/graph/master/persons` (person selector)
- `/api/v1/graph/master/person/{key}/network` (cross-case person network)
- `/api/v1/graph/master/centrality` (betweenness, pagerank, degree, communities)
- `/api/v1/search/global` (cases, entities, documents, patterns, locations — fixed enum cast bug)
- `/api/v1/attention` (attention center)
- `/api/v1/patterns` (detected patterns)
- `/api/v1/investigate/patterns` (investigator patterns)
- `/api/v1/datasets` (dataset registry)
- `/api/v1/objects/{bucket}/{key}?exp=&sig=` (HMAC-signed object access; 200 OK for valid, 422/403 for invalid/expired)
- Object download via presigned URL verified: real PDF bytes returned (2,646 bytes, `%PDF` header)

## RBAC Enforcement (verified)
- Viewer can GET cases/search/graph: **200 OK** ✅
- Viewer attempting POST /cases: **403 permission_denied** ✅
- Admin can perform all actions ✅

## Security & Architecture Guarantees Preserved
- **No SQLite fallback**: PostgreSQL is the only relational backend in use.
- **No in-memory graph**: EmbeddedGraphStore persists to `var/data/graph.json` with process-level file lock and write-once semantics.
- **No mock object store**: LocalObjectStore enforces write-once, SHA-256 verification on read, HMAC-signed URLs with constant-time verification, path-traversal protection.
- **Alembic migrations**: schema at revision `9c0d1e2f003` (head); no `create_all()` shortcut used for schema.
- **Chain-of-custody**: every CaseDocument has an EvidenceCustodyEvent row with SHA-256 hash.
- **RBAC server-side enforced**: viewer is read-only even when bypassing UI.
- **Presigned URL signature verification**: attempts without valid `sig`/`exp` are rejected (422).
- **Soft-delete only**: no DELETE methods anywhere; `is_deleted` flag.
- **Audit logs append-only**: UPDATE/DELETE on audit tables REVOKEd from the application user.

## Frontend
- Production build: **succeeds** (`npm run build`, 4.6 seconds, 117 modules, 29 chunks)
- Vite dev server: running on port 5173 with `/api` proxy to :8000
- FastAPI serves built frontend from `frontend/dist/` for production

## Idempotency
Re-running `seed_demo_v2.py` twice produces no duplicate rows:
- Demo users are upserted safely
- Demo cases/documents/evidence are deleted-then-recreated for the demo dataset ID
- Object store put() is idempotent for identical bytes (returns success; raises ConflictError on differing content)
- Graph purge_dataset() + rebuild is deterministic

## Known Minor Items
- PostgreSQL pg_trgm/btree_gin extensions unavailable in pgserver-bundled PostgreSQL (warning only; trigram indexes degrade to ordinary filtering — does not break functionality).
- Celery worker started manually (1 worker, concurrency=1); production would use systemd/Docker.
- 360 object-store files (320 evidence + 40 sources) match the 320 case_documents count exactly.

## Files Modified/Created
- `backend/scripts/seed_demo_v2.py` — new comprehensive seed
- `backend/app/db/bootstrap.py` — updated to DEMO-DATASET-002 anchors
- `backend/app/services/global_search.py` — fixed DocumentType enum cast bug in ILIKE
- `backend/app/services/case_dashboard.py` — fixed stale attribute references (question→title, created_at→detected_at, etc.)
- `backend/app/services/documents.py` — evidence_url now populated for local object store (not just MinIO)
- `.env` — profile=embedded with real PostgreSQL/Redis backends
