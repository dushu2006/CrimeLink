# CrimeLink — Runtime Data-Flow Verification Report

Branch: `arena/01a06d5c-crimelink` · Date: 2026-09-05

This report answers the question *"is CrimeLink actually database/Neo4j-backed at
runtime, rather than only containing the integration code?"* It was produced by
starting the real stack and driving data through it end-to-end. **Nothing was
committed or pushed.** No functionality was modified.

---

## 0. Environment constraints (what "real stack" means here)

The sandbox has **no Docker** and a strict network allowlist. Verified reachable
hosts: `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org`, `github.com`
(and `api.github.com`). Verified **blocked**: `deb.debian.org` (apt), `dist.neo4j.org`,
`dl.min.io`, `raw.githubusercontent.com`, `repo.maven.apache.org`, and every JDK
CDN (Adoptium/BellSoft/Azul/Oracle).

I therefore stood up the real services from the sources that *were* reachable:

| Service | How started | Real? |
| --- | --- | --- |
| PostgreSQL **16.2** | PyPI wheel `pgserver` bundles genuine PostgreSQL 16.2 binaries (`initdb`/`postgres`/`psql`/`pg_ctl`); `initdb` + `postgres` on `127.0.0.1:5432` | **Yes — a real PostgreSQL server over TCP** |
| Redis **7.2.5** | Built from source (`github.com/redis/redis`, tag 7.2.5) with `make`; `redis-server` on `127.0.0.1:6379` | **Yes — a real Redis server** |
| Celery **5.4.0** | `celery … worker -Q pipeline,analytics,maintenance` against the Redis broker | **Yes — a real Celery worker** |
| Backend API | `uvicorn app.main:create_app --factory` on `0.0.0.0:8000` | **Yes — the real FastAPI app** |
| Frontend | `npm install` + `npm run build` (tsc + vite) | **Yes — real production build** |
| Neo4j | **NOT STARTED** — distribution host blocked, no Java, no pip/npm bundle | Unavailable |
| MinIO | **NOT STARTED** — `dl.min.io` blocked, no pip/npm bundle | Unavailable |

Because Neo4j and MinIO could not run, the backend used its own declared
backends for those two concerns: **`graph_backend=embedded`** (NetworkX with
write-through JSON persistence) and **`object_store_backend=local`** (filesystem).
These are first-class adapters in the codebase implementing the same `GraphStore` /
object-store contracts as the Neo4j/MinIO adapters — they are **not mocks and not
replaced for the sake of the test**. The relational store and the broker were the
real production services (PostgreSQL and Redis/Celery).

---

## 1. Fresh dataset generated (Section 2)

`python -m app.synthetic_corpus.generate --yes-i-am-sure` produced and ingested a
fresh corpus through the real upload path (`upload_document` → object store →
`case_documents`/`ingestion_jobs` rows → Celery dispatch). The Celery worker
processed all documents through the six-stage pipeline.

| Metric | Value |
| --- | --- |
| Cases | **12** |
| Persons (case population) | **40** |
| Persons (background / unrelated) | **20** |
| Phones | 95 |
| Accounts | 50 |
| Vehicles | 38 |
| Locations | 25 |
| Organizations | 12 |
| Events (sightings) | **76** |
| Documents (ingested) | **60** (0 skipped) |
| Relationships (final graph) | **1,616 edges / 369 nodes** |

Ground truth written to `/home/user/runtime/data/synthetic_ground_truth.json`
(evaluation-only, not ingested). Seed `20260902` (deterministic).

---

## 2. PostgreSQL verification (Section 3)

Direct `psql` queries against the running server. **17 tables** exist:
`users, refresh_tokens, cases, case_documents, ingestion_jobs, document_stage_events,
quarantined_records, entity_resolution_queue, investigation_stage_runs,
investigation_findings, detected_patterns, pattern_config, source_references,
audit_logs, audit_chain_head, audit_anchors, jurisdiction_access_requests`.

Representative row counts (live, via `psql`):

| Table | Count |
| --- | --- |
| cases | 12 |
| case_documents | 60 (all `COMPLETE`) |
| ingestion_jobs | 60 |
| entity_resolution_queue | 54 (PENDING, similarity 1.0) |
| audit_logs | 21 (GRAPH_EXPAND 11 · LOGIN 8 · LOGIN_FAILED 1 · AI_QUERY 1) |
| users | 2 (SYN-0000 ADMIN, INV-NAG-01 INVESTIGATOR) |
| detected_patterns | 0 |
| investigation_findings | 0 |

**This is evidence of records actually existing in the running PostgreSQL
database**, not merely SQLAlchemy being configured: the API logged
`db.async_engine_ready url=postgresql+asyncpg://…/crimelink`, every document row
and job row was committed there, and a restart of PostgreSQL (see §5) preserved
them.

---

## 3. Graph store verification (Section 4 — Neo4j unavailable)

Neo4j could not be started (see §0), so **the Neo4j runtime column is FALSE**.
What *was* verified at runtime is the graph store actually in use — the embedded
NetworkX store, which persists write-through to `graph.json` and reloads from it:

- Snapshot: **369 nodes / 1,616 edges**.
- Labels: `Case=12 Phone=37 Vehicle=8 Person=175 Location=49 BankAccount=12 Event=76`.
- Relationships: `MENTIONED_IN=847 CALLED=198 TRANSFER_TO=166 OWNS_VEHICLE=77
  USES_PHONE=80 POTENTIAL_ALIAS=54 OWNS_ACCOUNT=42 PARTICIPATED_IN=76 LOCATED_AT=76`.
- **Event uniqueness**: 76 distinct `Event` nodes for 76 sightings; each carries
  `event_type` (ANPR/CCTV/PATROL/INTELLIGENCE), its own `timestamp`,
  `source_doc_ids`, `case_ids`. **No global PATROL/CCTV collapse.**
- **Traceability path exists** (verified with real node/edge IDs):
  `Person "Ankit Siddiqui" → OWNS_ACCOUNT → Account 896492062876 → TRANSFER_TO →
  Account 754671568224 → OWNS_ACCOUNT ← Person "Mamta Bose"`, and
  `Ankit Siddiqui → USES_PHONE → +919365376623`.
- **Transaction metadata** on `TRANSFER_TO`: `amount=199480.99`,
  `ts=2026-07-07T18:30:00+00:00`, `channel=UPI`, `reference=TXN_00039`,
  `text_span`, `extractor=deterministic`, `source_doc_id`.
- `USES_PHONE` carries `first_seen`/`last_seen`; `OWNS_ACCOUNT` carries `bank_code`.
- **Negative class isolated**: 0 background persons, 0 background phones, 0
  background accounts appear in the graph (verified against ground-truth IDs).

In production the same `GraphStore` contract is served by `neo4j.py`; the
pipeline/API layers are identical either way, but the **Neo4j server itself was
not runtime-verified here** and must be verified in the deployment environment.

---

## 4. Persistence (Section 5)

- **API restart**: stopped and restarted uvicorn. Startup logged
  `graph.snapshot_loaded edges=1616 nodes=369` — the graph was **reloaded from the
  persisted JSON snapshot on disk**, not rebuilt. A graph endpoint returned the
  same 75/400 result.
- **PostgreSQL restart**: `pg_ctl -D … -m fast stop` then `start`. After restart,
  `cases=12, case_documents=60, entity_resolution_queue=54` (and the audit rows
  present at that point) — all data persisted on disk. The API reconnected via
  `pool_pre_ping` and served identical graph data afterwards.

→ **PERSISTED DATA, not temporary in-memory state**, for both stores.

---

## 5. API → graph store (Section 6)

All endpoints were called against the live API with a real JWT
(`INV-NAG-01`, jurisdiction `RJ-NAG`):

- `GET /api/v1/graph/cases/{id}` → **75 nodes / 400 edges** for case
  `d83ad27d…` (`FIR/2026/0011/PS-NAG`).
- `GET /api/v1/graph/cases/{id}/temporal` → 60 nodes / 379 edges + `time_range`
  + `events` timeline.
- `GET /api/v1/graph/stats` → `{"backend": "embedded", "nodes": 369, "edges": 1616, …}`
  (matches the persisted snapshot exactly — the API is reading the graph store,
  not a hardcoded demo).
- `GET /api/v1/graph/entity-types` → the 7 canonical labels.

The API's `GraphService` reads `container.graph_store.snapshot(case_id)` — the
same store the pipeline writes. This is a **graph-store read path, not a
hardcoded graph**.

---

## 6. Three graph modes (Section 8)

**A. Person Graph** — `GET /cases/{id}/network/{person_key}?depth=1..3` for
"Ankit Siddiqui":
- depth 1 → 3 nodes (PERSON + PHONE + BANK_ACCOUNT), 2 edges (USES_PHONE, OWNS_ACCOUNT).
- depth 2 → 28 nodes, 56 edges (adds CALLED 21, TRANSFER_TO 32).
- depth 3 → 52 nodes, 321 edges (6 persons, 1 vehicle, OWNS_VEHICLE).
- BFS `layers` field present (`{1:2, 2:25, 3:24}`).

**B. Master Graph** — `GET /graph/cases/{id}` with filters:
- `labels=PERSON,PHONE` → 48 nodes, only those labels.
- `rel_types=TRANSFER_TO` → 166 edges, only that type.

**C. Temporal Graph** — `GET /graph/cases/{id}/temporal`:
- date-range filtering works (`from_ts`/`to_ts` respected; `time_range.first/last`
  reflect actual event times).
- focus person (`target`) works; `depth` works.
- `events` timeline carries unique per-occurrence `Event` names
  (`Anpr · 20, Nagar, Agra Agra · 2026-06-17 10:50` …), timestamps, event types.
- It returns **graph-ready nodes/edges + events**, *not* a serialised textual path
  — the serialised path capability is the separate `POST /graph/cases/{id}/paths`
  (`find_temporal_paths`), which correctly returned 0 paths for an ownership chain
  because undated edges cannot join a chronological chain.

---

## 7. Cytoscape data flow (Section 7)

Verified from code + a successful production build:

```
backend graph service (GraphStore: Neo4j in prod / NetworkX embedded here)
   ↓   GET /api/v1/graph/cases/{id}, /graph/cases/{id}/temporal,
   ↓   /cases/{id}/network/{person_key}, /graph/nodes/{key}/expand
FastAPI REST
   ↓   JSON nodes/edges (provenance_key, label, rel_type, source_doc_ids, …)
React (`src/api/client.ts` fetch wrapper + proactive token refresh)
   ↓   GraphPage.tsx maps responses → cytoscape ElementDefinition[]
Cytoscape.js (+ fcose layout) renders — visualization only
```

- `src/api/client.ts`: `caseGraph` → `/graph/cases/{id}` (with `labels`/`rel_types`),
  `personNetwork` → `/cases/{id}/network/{key}?depth=1|2|3`,
  `temporalGraph` → `/graph/cases/{id}/temporal` (with `target`/`from_ts`/`to_ts`/`depth`).
- `src/pages/GraphPage.tsx` builds `ElementDefinition[]` from `nodes`/`edges` and
  calls `cytoscape({ container, elements })`.
- The production build succeeded (`tsc -b && vite build`, 76 modules).

**Cytoscape receives its nodes/edges/labels/relationship-types/timestamps from the
backend graph API — it is the visualization layer, not the source of truth.** The
only thing not exercised here is actual pixel rendering in a browser (no browser
automation is available in this sandbox); the data flow is proven at the code and
live-API-response level.

---

## 8. One end-to-end investigation (Section 9)

Target: **Ankit Siddiqui** (ACCOMPLICE in `FIR/2026/0011/PS-NAG`).

1. **Source document**: `FIR-2026-0011-PS-NAG-PERSONS.csv` (CRIMINAL_HISTORY) in the
   object store contains the literal row
   `Ankit Siddiqui,,ACCOMPLICE,FIR/2026/0011/PS-NAG,29/06/2026,+919365376623,,896492062876,CNRB066688,"124, Sector, Visakhapatnam"`.
2. **Extraction** (deterministic adapter) → `Person "Ankit Siddiqui"`,
   `USES_PHONE → +919365376623`, `OWNS_ACCOUNT → 896492062876`.
3. **Entity resolution** → 4 PENDING queue rows for the case (`similarity_score=1.0`),
   conservative (proposals await human review; `POTENTIAL_ALIAS` edges link variants).
4. **PostgreSQL** → `case_documents` row (COMPLETE), `cases` row, ER queue rows.
5. **Graph store** → Person node + USES_PHONE/OWNS_ACCOUNT/TRANSFER_TO edges, all with
   `source_doc_id`/`text_span` provenance.
6. **Graph API** → master graph, person network (1/2/3 hop), temporal graph all
   return the same target and relationships.

The same target and its relationships appear consistently across every stage.

---

## 9. AI data flow (Section 10)

`POST /api/v1/ai/cases/{id}/ask` → `AI Gateway.ask` → `_retrieve_subgraph`
(bounded BFS from `container.graph_store`, depth-limited) → minimisation →
pseudonymisation → role availability check.

With no API key configured, the live call returned HTTP 200 with
`available=false`, `fallback_reason=no_api_key_for_role_reasoning`, `model=null`,
`pseudonymized=true`, and an honest structured finding ("…no API key is configured
for the reasoning model…"). The query was audited (`AI_QUERY` appears in
`audit_logs`).

→ **AI CODE PATH VERIFIED** (retrieval, context sourcing, pseudonymization,
auditing, honest fallback). **LIVE MODEL EXECUTION NOT VERIFIED** (no API key —
no model was called, and none was faked).

---

## 10. Auth / WebSocket (Section 11)

Verified live against the real backend:

- **Login**: `POST /auth/login` → access + refresh tokens, `expires_in=900`
  (15-minute access token).
- **Refresh rotation**: `POST /auth/refresh` issues a *new* access token and a
  *different* refresh token; reusing the old refresh token returns
  `authentication_failed` — "…has already been used; the session has been revoked…".
- **Rate limit**: 10/min on auth; 429 `rate_limited` observed after a burst.
- **WebSocket** `GET /jobs/ws/{case_id}?token=…`:
  - missing token → close code **4401**;
  - garbage token → **4401**;
  - valid token, out-of-scope case → **4403** (uniform, no case-existence leak);
  - valid token, own case → stays open and streams.
- **Polling fallback**: `GET /jobs/{job_id}` and `GET /cases/{id}/jobs` exist
  alongside the WebSocket channel.

---

## 11. Issues found during verification

Three are **pre-existing** (not introduced by the Phase-11 corpus work); the
fourth is an environment limitation surfaced by the code:

1. **[CRITICAL] WebSocket authorization poisons the asyncpg pool across event
   loops.** `app/api/v1/jobs.py::_authorize_websocket` runs its DB queries on a
   dedicated auth event loop (`_get_auth_loop`) using the process-wide
   `async_session` (shared asyncpg pool). asyncpg connections are single-loop, so
   connections created on the auth loop are later handed to the main loop, raising
   `RuntimeError: … Future … attached to a different loop` and returning HTTP 500
   on subsequent REST calls. **Reproduced deterministically**: 30 WS authorizations
   → the next 5 `/auth/login` calls returned 500 (53 occurrences in the log), then
   429 from the rate limiter. This will affect any deployment with live WebSocket
   traffic.
2. **[MEDIUM] Embedded graph store is not multi-process-safe.** `app/adapters/graph/embedded.py::_flush`
   uses a fixed `graph.json.tmp` name and a process-local `threading.RLock`. With a
   Celery worker at `--concurrency=2`, two processes race the `.tmp→graph.json`
   rename (`FileNotFoundError`, observed and retried) and each process only holds
   its own in-memory subgraph, so the persisted snapshot loses nodes. This
   `embedded + celery` combination is not a documented production profile
   (production uses Neo4j), but there is no guard against configuring it. Verified
   clean at `--concurrency=1`.
3. **[MEDIUM] `psycopg2` is required but not declared.** `postgres_dsn_sync` is
   `postgresql+psycopg2://…`, used by Alembic (`env.py`), the Celery worker's sync
   sessions (`orchestrator._session`, failure-path audit writes) and metrics — but
   `pyproject.toml` does not list `psycopg2`/`psycopg2-binary`, and the Dockerfile
   runs only `pip install .`. In the production image these paths would raise
   `ModuleNotFoundError` at first use. (Installed manually for this verification.)
4. **[LOW] `_bootstrap_postgres` transaction fragility.** The hardening statements
   run in one `engine.begin()` transaction; a single failing `CREATE EXTENSION`
   (here: `pg_trgm` missing from the minimal `pgserver` bundle — a sandbox
   artifact, not a code bug) aborts the transaction, so the four
   `REVOKE UPDATE/DELETE ON audit_logs/audit_chain_head` statements are silently
   skipped. The append-only audit hardening should be isolated per-statement so one
   unavailable extension cannot silently drop the security grant.

---

## 12. Final verdict table

| Component | Code Verified | Runtime Verified | Evidence |
|-----------|---------------|------------------|----------|
| Dataset generator | TRUE | TRUE | `generate_corpus` ran live: 60 docs ingested, counts verified, ground truth written |
| PostgreSQL | TRUE | TRUE | Real PG 16.2; 17 tables; `psql` counts (cases 12, docs 60 COMPLETE, ER 54, audit 21); survives `pg_ctl` restart |
| Neo4j | TRUE | **FALSE** | Driver/adapter code present; server could not be started (dist.neo4j.org blocked, no Java). Graph ran on embedded NetworkX (369/1616, persisted) |
| MinIO | TRUE | **FALSE** | `MinioObjectStore` code present; server could not be started (`dl.min.io` blocked). Object store ran on local FS (120 files) |
| Redis/Celery | TRUE | TRUE | Real Redis 7.2.5 + Celery 5.4.0 worker processed all 60 docs (extraction→ER→graph injection logged) |
| Graph API | TRUE | TRUE | `/graph/cases/{id}` 75/400; `/stats` matches persisted snapshot (369/1616) |
| Person Graph | TRUE | TRUE | 1/2/3-hop BFS for "Ankit Siddiqui": 3/28/52 nodes, USES_PHONE+OWNS_ACCOUNT+OWNS_VEHICLE |
| Master Graph | TRUE | TRUE | Complete case network + `labels`/`rel_types` filters verified |
| Temporal Graph | TRUE | TRUE | date-range filter, focus person, depth, timeline events, unique Event instances (no PATROL/CCTV collapse) |
| Cytoscape data flow | TRUE | **FALSE (rendering)** | API→React→`ElementDefinition[]`→cytoscape verified in code; production build passed; in-browser rendering not automatable here |
| AI pipeline | TRUE | **FALSE (live model)** | Gateway retrieval/fallback verified live (`available=false`, audited); no API key → no model call (none faked) |
| Authentication | TRUE | TRUE | login (900s), refresh rotation + reuse-revocation, 429 rate limit, lockout path |
| WebSocket | TRUE | TRUE | 4401 (missing/garbage), 4403 (out-of-scope), open (own case) — **but see Issue #1 (cross-loop asyncpg crash)** |

---

## 13. Explicit answers

**1. Is it safe to commit now?**
The Phase-11 synthetic-corpus changes themselves are safe: they are verified
end-to-end (fresh corpus → real Celery/Redis pipeline → real PostgreSQL + graph
store → API), the full backend suite passes, and nothing was weakened. However,
the working tree as a whole contains **Issue #1 (CRITICAL)** — a reproducible
WebSocket-triggered REST 500 crash — which is **pre-existing** and will ship if
you commit without addressing it. Committing the corpus work does not *cause* it,
but I cannot call the whole tree "safe to ship" while it is present.

**2. What remains unverified?**
- **Neo4j at runtime** (server could not be started — download host blocked, no Java).
- **MinIO at runtime** (server could not be started — download host blocked).
- **Live AI model execution** (no API key available; the graceful-fallback path was verified).
- **In-browser Cytoscape rendering** (no browser automation; the data flow and build were verified).
- **PostgreSQL append-only hardening** (the `REVOKE` grant) could not be applied in
  this environment because the minimal bundled PostgreSQL lacks `pg_trgm`/`btree_gin`.

**3. Are code changes required before committing?**
For the Phase-11 deliverable itself: **no** — it is complete and verified. But the
verification surfaced real, pre-existing defects that should be fixed before any
production use (and ideally before this branch is treated as done):
1. **Issue #1 (CRITICAL)** — give the WebSocket auth loop its own DB engine/pool
   (or run authorization on the socket loop with a per-connection session) instead
   of sharing the process-wide asyncpg pool across loops.
2. **Issue #2 (MEDIUM)** — either guard against `embedded + celery --concurrency>1`
   or make `EmbeddedGraphStore._flush` use a unique temp name + file lock.
3. **Issue #3 (MEDIUM)** — add `psycopg2-binary` (or `psycopg`) to `pyproject.toml`.
4. **Issue #4 (LOW)** — isolate `_bootstrap_postgres` statements so one failed
   `CREATE EXTENSION` cannot abort the audit-table `REVOKE`s.

I made **no code changes** this session (per "do not modify functionality unless a
real issue is found", these are reported for your decision rather than silently
rewritten). Nothing was committed or pushed.
