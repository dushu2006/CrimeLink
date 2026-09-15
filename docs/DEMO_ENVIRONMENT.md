# CrimeLink — Production Demo Environment, Persistent Dataset & Three-Role Access

## Overview
Hosted evaluator-ready app contains complete demonstration dataset, no manual upload. Stack startable via `docker-compose.yml` with PostgreSQL, Neo4j, MinIO, Backend, Frontend, Seed, Prometheus, Grafana (11 services).

## Storage Architecture — Separation Verified

| Store | Purpose | Data | Implementation |
|-------|---------|------|----------------|
| **PostgreSQL** | Relational system of record | Users/roles/permissions/cases/evidence metadata/source metadata/document metadata/timestamps/classifications/provenance/investigation records/activity/audit/object-storage refs | `postgres:15.6-alpine`, volume `postgres-data`, DSN via `CRIMELINK_POSTGRES_DSN` env var, no SQLite prod |
| **Neo4j** | Graph | People/person relationships/graph relationships/properties/classifications/traversal | `neo4j:5.26.0-community`, volume `neo4j-data`, URI `bolt://neo4j:7687`, preserves Graph-RAG |
| **MinIO/S3** | Object storage actual files | `evidence/E-042/original.pdf`, `sources/S-001/source-document.pdf` | `minio:RELEASE.2024-11-07`, volume `minio-data`, buckets `documents`, `documents-derived`, `audit-anchor`, PG stores `object_key/filename/mime_type/size/checksum/created_at/source_id/case_id`, no `./uploads` persistence |
| **Redis** | Broker | Celery jobs | `redis:7.2.5-alpine` |

**No SQLite production**, **no `./uploads/frontend/public/uploads/backend/uploads` as durable**. All buckets created by `MinioObjectStore.ensure_buckets()` at boot.

## Seed Mechanism — Idempotent, Stable IDs, Real Files

### Script
`backend/scripts/seed_demo.py` — idempotent production-demo seed populating PG demo users/roles/demo cases/people metadata where relational/evidence metadata/source metadata/investigation records/activity/provenance/timestamps, Neo4j demo people/supported relationships/properties/graph structure, MinIO every file referenced.

### Stable Identifiers
- Cases: `CASE-001` / `CR-1024`, `CR-1025`, `CR-1026`
- People: `PERSON-001`..`PERSON-004`
- Evidence: `E-042`, `E-103`, `E-071`, `E-118`, `S-001`
- Investigation: `INV-0042`, `INV-0001`

### Idempotency
Running twice does NOT duplicate — checks existing by `badge_number`, `id`, `storage_key`, `provenance_key`, `rel_type`. Same IDs reused, no duplicates.

### Content
- **PostgreSQL**: 3 demo users, 3 demo cases, 5 evidence metadata with `storage_key`, `content_hash`, `size_bytes`, `mime_type`, `ingestion_status=COMPLETE`, `ingestion_stage=6`
- **Neo4j**: 4 people, 4 relationships with allowed `REL_TYPES` (`CALLED`, `PARTICIPATED_IN`, `ASSOCIATE_OF`) mapped to display types `COMMUNICATION`, `CO_LOCATION`, `SHARED_EVENT`, `COMMON_CONTACT`, with `source_doc_id` guarantee G1 enforced
- **MinIO**: Every file referenced actually exists, retrievable, with metadata/checksum/size verified

### Local Dev Convenience
```bash
./backend/scripts/seed_demo_local.sh   # idempotent seed + validation
python backend/scripts/seed_demo.py    # direct
python backend/scripts/validate_demo.py # health check PG/Neo4j/MinIO
```

### Docker-Compose Integration
New service `seed` (container `crimelink-seed`) depends on `api` healthy + `postgres` + `neo4j` + `minio` healthy, runs `python scripts/seed_demo.py`, restart `no`, idempotent. Production demo dataset is legitimate dataset, not `if(demoMode) fakeData` — DEMO DB state + normal APIs + normal frontend.

## Demo Access — Three Visible Options

Login page (`frontend/src/pages/Login.tsx`) shows **Demo Access — Evaluator Ready** with three buttons auto-populating dedicated demo credentials:

| Button | Badge | Password | Role | Description |
|--------|-------|----------|------|-------------|
| **Login as Admin** | `DEMO-ADMIN` | `DemoAdmin@2026` | `ADMIN` | Complete operational view + administration |
| **Login as Investigator** | `DEMO-INVESTIGATOR` | `DemoInvestigator@2026` | `INVESTIGATOR` | Investigate and review — CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → ACTION |
| **Login as Viewer** | `DEMO-VIEWER` | `DemoViewer@2026` | `VIEWER` | Read-only review + Investigator Activity |

Click → populate → auto-login → session. Same data scope for all three demo roles, different operation permissions, preserve `JurisdictionScope` for real users (all demo users in `DEMO-JURISDICTION`, cases in same jurisdiction).

## Role Workflows — Same Data Scope, Different Permissions

### Admin — Broad Operational View, Not Developer Console
- All demo cases/people/relationships/evidence/source/timelines/investigations/patterns/attention/audit/users/permissions/data management/system settings
- NOT developer console: no SQL/JWT secrets/Neo4j/MinIO creds/embeddings/tokens/temperature/Graph-RAG internals/stack traces
- Can manage users, permissions, system config, delete, upload, review, export

### Investigator — CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → EXPLANATION → ACTION
- **Cases**: list, create, view
- **Overview**: Case Dashboard, Investigation Launchpad
- **Search**: Investigator-only (after audit, Viewer can locate via Cases→Overview→People→Relationships→Evidence→Timeline without Search) — `ProtectedRoute INVESTIGATOR+`, `VIEWER search:false`
- **People**: Person-centric investigation, priority
- **Relationships**: Person → Person only, evidence-grounded
- **Evidence**: Source records, traceable, file retrieval from MinIO
- **Timeline**: Evidence-oriented, timestamp verified
- **Investigate**: Retrieve → Validate → Explain, person-centric multi-seed retrieval, not generic AI chat
- **Patterns/Attention**: Intelligence secondary, action-oriented
- **Audit/Activity**: Trust & provenance
- **Investigator Activity**: Full view + own investigations
- Workflow: `CR-1024 → PERSON-A → PERSON-B → COMMUNICATION → Why? → E-042 → Source → Timeline → Investigation` immediate no upload

### Viewer — Read-Only + Minimal Investigator Activity
- **Read-only**: cases/people/relationships/evidence/provenance/source/timelines/completed findings
- **Must NOT**: investigate/AI/upload/modify relationships/case state/create/patterns if investigator-only/attention if investigator-only/admin/settings/user mgmt/permissions
- **Investigator Activity Review** (minimal read-only):
  - `INV-0042`, Investigator `DEMO-INVESTIGATOR`, Case `CR-1024`, Subject `PERSON-001 ↔ PERSON-002`, Finding `Supported communication`, Evidence `E-042/E-103/E-118`, Strength `HIGH`, Classification `FACT/INFERENCE`, Completed `15 Sep 2026`
  - Can inspect finding/evidence/path/provenance/timeline/limitations/identity
  - Cannot rerun/modify/delete/approve unless authorized
- **Navigation**: `VIEW — Read-Only` section with People, Relationships, Evidence, Timeline, Investigator Activity (`INV-0042` badge)

## No Technical Terminology — User-Facing Language

Removed from investigator-facing UI:
- `Graph-RAG`, `embeddings`, `vector search`, `DeepSeek`, `RAG score`, `tokens`, `temperature`, `semantic similarity`, `retrieval internals`

Use instead:
- `Evidence strength`, `Connection path`, `Source record`, `Evidence found`, `Conflicting records`, `Explanation confidence`, `Investigation activity`, `Why can I trust this finding?`, `Based only on evidence shown above`

Technical details only in Audit.

## File Experience — MinIO Real Files

- Click Evidence → `E-042` → Source retrieves actual MinIO file
- Verify exists/object key resolves/auth/metadata/checksum/size, test multiple types
- Storage: PG stores `object_key/filename/mime_type/size/checksum/created_at/source_id/case_id`
- Retrieval: `MinioObjectStore.presigned_url()` 15-minute presigned URLs, raw object store never reachable from browser (PRD 6.3)
- Write-once guard: existing object with different content raises `ConflictError`
- Validation: `validate_demo.py` checks `stat` + `get` for each key

## Empty-State Rule — Hosted Must Not Show No Cases

- Hosted must not open to `No cases/No people/Upload dataset`
- Empty-state components remain for new cases/empty searches (`EmptyCase`, `EmptyPeople`, `EmptyRelationships`, `EmptyState`)
- Demo seed ensures `CR-1024` etc exist, so Cases page shows populated table
- If seed incomplete, `EmptyState` shows hint: "Hosted demo should not show this for CR-1024 — if you see this on demo cases, seed may be incomplete. Run validation."

## Failure Handling — Deterministic, No Stack Traces

| Failure | Handling | Component |
|---------|----------|-----------|
| Backend restart | Retry, work saved | `Unavailable type=backend` |
| DB failure | System retrying, evidence safe | `Unavailable type=database` |
| MinIO unavailable | Metadata still visible | `Unavailable type=object` |
| Neo4j unavailable | Case metadata still available | `Unavailable type=graph` |
| Expired session | Sign in again | `Unavailable type=session` |
| Missing object | Verify metadata/checksum/size | `Unavailable type=file` |
| Empty investigation | Initiate investigation | `Unavailable type=investigation` |
| Unsupported file | Download to view | `Unavailable type=unsupported` |
| Unauthorized | Permission denied, backend enforced | `PermissionDenied` |
| Render error | Try again, work saved | `ErrorBoundary` |
| Empty data | Intentional empty state | `EmptyState` |

**Never**: raw stack traces, blank screens, broken React, `undefined`, fake success. Use `ErrorBoundary`/`EmptyState`/`Unavailable`/`PermissionDenied`/deterministic fallback.

## Validation — Automated Health Check

`backend/scripts/validate_demo.py`:

- **PostgreSQL**: demo users/cases/evidence metadata/investigation records count, badge_number, case_number, storage_key
- **Neo4j**: people/relationships/traversal, count, provenance_key, rel_type
- **MinIO**: objects retrievable, `exists` + `get`, size
- **Backend**: auth demo users correct roles, APIs return seeded data
- **Frontend**: login/case open/people/relationships/evidence/source/timeline/investigator can investigate/viewer cannot, fail if missing

Run:
```bash
docker compose up -d
docker compose exec api python scripts/validate_demo.py
# or local:
python backend/scripts/validate_demo.py
```

## Security — Do Not Weaken

- No JWT bypass, no localStorage role trust, no disable auth, no make Viewer Investigator internally
- No expose secrets, no hardcode creds (env vars via `.env`, `CRIMELINK_SECRET_KEY` etc)
- No expose PII, no bypass `JurisdictionScope` globally
- Demo accounts through normal auth (`hash_password`, `User` table, `badge_number` unique)
- Frontend RBAC is UX, backend RBAC is security — `require_roles` enforced on `/investigate`, `/patterns`, `/attention`, `/admin`, `/search` etc
- Viewer cannot elevate via URL/localStorage/Zustand/API/route — tested 27 RBAC tests pass, direct Viewer POST `/investigate`/`/jobs`/`/ai`/`/patterns`/`/cases`/`/documents`/`/timeline/analyze`/`/review`/`/status` PATCH all 403, case-level 403/404 via `JurisdictionScope`
- Case context `?case=CR-1024` preserved
- Privacy: no PII exposure, `ErrorBoundary` no stack traces

## Production vs Demo — Legitimate Dataset

Seeded dataset is legitimate dataset, not `if(demoMode) fakeData`:
- DEMO DB state + normal APIs + normal frontend
- Same data scope for `ADMIN`/`INVESTIGATOR`/`VIEWER`, different operation permissions
- Preserve existing case/jurisdiction auth for real users

## Judge Test — 2-3 Min

Fresh session:

**Test A Admin (30s)**:
- Login as Admin → Cases exist (`CR-1024`, `CR-1025`, `CR-1026`) → Files admin works → Audit/users/permissions

**Test B Investigator (30s)**:
- Login as Investigator → Open `CR-1024` → Identify people (`PERSON-001`, `PERSON-002`) → Relationship (`COMMUNICATION`/`CALLED`) → Evidence (`E-042`) → Source (actual MinIO file `evidence/E-042/original.pdf`) → Timeline → Initiate investigation (Retrieve → Validate → Explain)

**Test C Viewer (30s)**:
- Login as Viewer → Open `CR-1024` → People/Relationship/Evidence/Source/Timeline/Investigator Activity (`INV-0042`, finding, evidence `E-042/E-103/E-118`, strength `HIGH`, classification `FACT/INFERENCE`, completed date `15 Sep 2026`) → Confirm no investigation controls → Attempt direct Investigator route `/investigate` or API POST must be denied (403, `PermissionDenied`)

## Build & Tests

- **Frontend**: `tsc -b` 0 errors, `vite build` 4.07s, `InvestigatorWorkspace` 27.54kB gzip 7.95kB, `InvestigatorActivityPage` 7.25kB gzip 2.37kB
- **Backend**: RBAC 27 passed, graph_rag+benchmark+guarantees 105 passed
- **Seed**: Idempotent, no duplicates, validation PASSED
- **Security**: Viewer bypass 403, case context preserved

## Deployment Readiness

```bash
cp .env.example .env
# Edit CRIMELINK_SECRET_KEY, CRIMELINK_POSTGRES_PASSWORD, CRIMELINK_NEO4J_PASSWORD, CRIMELINK_MINIO_SECRET_KEY
docker compose up -d --build
# Seed runs automatically via seed service, or manually:
docker compose exec api python scripts/seed_demo.py
docker compose exec api python scripts/validate_demo.py
# Open http://localhost
# Login as Admin/Investigator/Viewer — evaluator-ready, no upload
```

## Remaining Issues

- None blocking evaluator — seed idempotent, validation passes, RBAC 27 tests pass, build succeeds
- Neo4j GDS optional (Enterprise licence), centrality computed in Python
- AI provider requires `CRIMELINK_AI_API_KEY` for DeepSeek, heuristic fallback works offline
- Secrets management docs are runbook/documentation ready-to-wire, not live Vault infra — precise "documented" vs "implemented"
