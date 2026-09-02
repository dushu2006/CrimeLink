# CrimeLink

**AI-Powered Criminal Network Analysis System** — an evidence-backed investigation
platform for Indian law enforcement, built for the Ministry of Home Affairs / NCRB
Women Safety Division (Problem Statement ID 26189, Theme: Blockchain &
Cybersecurity).

CrimeLink ingests the messy artefacts of a real case — FIRs in English and Hindi,
call detail records, bank statements, surveillance logs, social-media exports,
criminal histories and anonymous intel — and turns them into one attributable
graph where **every node and every edge is backed by a source document**, where
**no consequential decision is made without a human**, and where **every action
is recorded in a tamper-evident audit log**.

---

## The three guarantees

Everything else in the system is subordinate to these. They are enforced in code,
not in policy documents, and each has tests (`backend/tests/test_guarantees.py`).

| | Guarantee | How it is enforced |
|---|---|---|
| **G1** | **Everything is evidenced** | `GraphNode`/`GraphEdge` refuse to be constructed without a `source_doc_id`. `app/adapters/graph/injector.py` is the *only* module allowed to write to the graph, and every write helper takes a document id. An unevidenced write raises `UnevidencedGraphWriteError` — it is never logged and dropped. |
| **G2** | **Nothing serious happens without a human** | Fuzzy identity matches are *proposed*, never merged: they land in a review queue with a 48-hour SLA. Detected patterns are created as `NEW` findings and stay that way until an investigator confirms or dismisses them with a written reason. The API has no `DELETE` method anywhere. |
| **G3** | **Everything is auditable and tamper-evident** | The audit log is append-only and hash-chained (`row_hash = SHA256(prev_hash ‖ canonical_json(row))`). Documents are stored write-once and addressed by SHA-256. `GET /admin/audit/verify` replays the whole chain and reports the first modified entry. Every night the chain head is anchored to a separate object-store bucket. |

---

## Architecture at a glance

```
                    ┌─────────────────┐
   browser  ───────►│  nginx (web)    │  React console, TLS, reverse proxy
                    └────────┬────────┘
                             │ /api
                    ┌────────▼────────┐
                    │  FastAPI (api)  │  REST + WebSocket progress channel
                    └───┬─────┬───┬───┘
        ┌───────────────┘     │   └──────────────────┐
        ▼                     ▼                      ▼
 ┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
 │ PostgreSQL 15│   │ Celery + Redis   │   │  Neo4j 5 / embed │
 │ cases, docs, │   │ 6-stage pipeline │   │  the graph       │
 │ queue, audit │   │ patterns, anchor │   │                  │
 └──────────────┘   └────────┬─────────┘   └──────────────────┘
                             │
                    ┌────────▼────────┐
                    │ MinIO (3 buckets)│ documents, derived text, audit anchors
                    └─────────────────┘
```

Eight containers in production (`docker-compose.yml`): `postgres`, `neo4j`,
`redis`, `minio`, `api`, `worker`, `beat`, `web`.

**Two profiles, one codebase.** `CRIMELINK_PROFILE=embedded` runs the entire
system in a single Python process on SQLite, an in-process NetworkX graph, the
local filesystem and an in-process job executor — no containers, no network, no
external services. `CRIMELINK_PROFILE=production` swaps in PostgreSQL, Neo4j,
MinIO and Celery. The domain, pipeline, analytics and API layers are
byte-for-byte identical in both; only the adapters behind the ports change.

### The six-stage pipeline

Every uploaded document moves through the same stages, each of which records a
timestamped event row so an investigator can see exactly where a file is:

1. **Validate** — type, size, MIME, SHA-256, duplicate detection.
2. **Extract text** — txt/csv/json natively; PDF, DOCX and XLSX through
   pypdf / python-docx / openpyxl.
3. **Deterministic extraction** (stage 1 of the hybrid) — regex and gazetteer
   rules for phones, IFSC/account numbers, vehicles, dates, amounts, IPC
   sections, districts. Confidence 0.95: a regex either matched or it did not.
4. **NLP extraction** (stage 2) — NVIDIA NIM, or IndicNER, or a deterministic
   heuristic fallback. **Confidence is capped at 0.80**, always: model output is
   a lead, not evidence.
5. **Entity resolution** — hard identifiers merge deterministically; name
   similarity ≥ 0.85 (with Devanagari transliteration, so `सुरेश मेहता` matches
   `Suresh Mehta`) creates a *proposal*, never a merge.
6. **Graph write + pattern detection** — through the injector, then the four
   deterministic pattern detectors run.

A document that fails to parse is never silently accepted: it is quarantined
with the reason recorded, and shows in the quarantine list until an
administrator releases or discards it.

### Analytics

Centrality (betweenness, PageRank, degree) is computed **deterministically in
Python** with confidence-weighted edges, so it needs no GDS licence and produces
the same ranking on every run. Louvain communities, four pattern detectors
(`STRUCTURING`, `BURNER_PHONE`, `RAPID_MOVEMENT`, `NETWORK_BRIDGE`) and
chronologically-coherent temporal pathfinding complete the picture.

**A score never travels alone.** Every influence response carries the subgraph
that produced it: the summary, the weighted edges, the community, the method
description and the document ids behind it.

---

## Quick start (no containers required)

```bash
git clone <repo> && cd CrimeLink
python3.11 -m venv .venv && .venv/bin/pip install ./backend
```

Seed a complete synthetic case (7 documents, Hindi and English):

```bash
export PYTHONPATH=backend
export CRIMELINK_PROFILE=embedded
export CRIMELINK_DATA_DIR=$PWD/var/data
export CRIMELINK_OBJECT_STORE_DIR=$PWD/var/objects

.venv/bin/python backend/scripts/seed_demo.py
```

Run the API and the console:

```bash
.venv/bin/python -m uvicorn app.main:create_app --factory \
    --app-dir backend --host 0.0.0.0 --port 8000          # API on :8000
cd frontend && npm install && npm run dev                 # console on :5173
```

Sign in with **`INV-0001` / `CrimeLink@Inv1`** (also `ADM-0001`, `VIW-0001`,
`INV-0002` — see `backend/scripts/seed_demo.py`).

Or bring the whole production topology up:

```bash
cp .env.example .env        # set CRIMELINK_SECRET_KEY (and NVIDIA_API_KEY if you have one)
docker compose up -d --build
docker compose run --rm api python scripts/seed_demo.py
```

---

## Using the NVIDIA NIM API key

NLP extraction is the only part of CrimeLink that calls a model. It goes through
the OpenAI-compatible NIM endpoint:

```bash
export NVIDIA_API_KEY=nvapi-…
export CRIMELINK_NLP_PROVIDER=auto      # auto → NIM when the key is present
export CRIMELINK_NIM_MODEL=deepseek-ai/deepseek-v4-pro-0813
```

`auto` resolves NIM → IndicNER → deterministic heuristic. With no key set, the
system runs fully offline on the heuristic provider and still produces a
complete graph; the only difference is that person/organisation mentions come
from gazetteers rather than a model. Model output is always clamped to
`CRIMELINK_NLP_MAX_CONFIDENCE` (0.8) before it can enter the graph.

---

## API surface

53 paths, three methods only: 39 `GET`, 17 `POST`, 1 `PATCH`. There is no
`DELETE` and no `PUT` anywhere — by design. Interactive docs at `/api/docs` when
the API is running.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/login`, `/auth/refresh`, `/auth/logout`, `GET /auth/me` |
| Cases | `GET/POST /cases`, `GET/PATCH /cases/{id}`, `/cases/{id}/timeline`, `/cases/{id}/export` |
| Documents | `POST /cases/{id}/documents`, `GET /cases/{id}/documents`, `GET /evidence/{doc_id}`, `GET /evidence/{doc_id}/verify` |
| Jobs | `GET /jobs/{id}`, `GET /cases/{id}/jobs`, `WS /jobs/ws/{case_id}` |
| Graph | `GET /graph/cases/{id}`, `/centrality`, `/influencers`, `/staging`, `POST /cases/{id}/paths`, `POST /cases/{id}/staging/promote`, `GET /graph/nodes/{key}`, `/expand`, `/influence` |
| Review | `GET /resolution`, `POST /resolution/{id}/merge|reject|unmerge`, `GET /patterns`, `POST /patterns/{id}/review` |
| Access | `POST /access/request`, `POST /access/approve/{id}`, `GET /access/requests` |
| Admin | `GET /admin/overview`, `/admin/audit/search`, `/admin/audit/verify`, `/admin/audit/anomalies`, `/admin/quarantine`, `/admin/users`, `/admin/thresholds` |

---

## Roles and jurisdiction

Three roles only: **VIEWER**, **INVESTIGATOR**, **ADMIN**. Jurisdiction scoping
sits underneath roles and is enforced in the query layer
(`JurisdictionScope.case_filter()`), never by hiding a button: a request for a
case outside your jurisdiction returns the same `404` as one that does not
exist, so the API does not confirm that another district's case is real.

---

## Testing

```bash
cd backend
../.venv/bin/python -m pytest tests/ -q
# 80 passed
```

| File | Covers |
|---|---|
| `tests/test_domain.py` | Provenance keys, hash chain, transliteration and fuzzy matching, model guards |
| `tests/test_pipeline.py` | All seven source adapters, extraction, entity resolution, patterns |
| `tests/test_api_contract.py` | Auth/refresh/lockout, RBAC, jurisdiction scoping, uploads, full HTTP surfaces |
| `tests/test_guarantees.py` | G1, G2 and G3 end to end, including tamper detection and write-once storage |
| `tests/test_analytics.py` | Centrality ranking, mandatory explanations, temporal paths, pattern detection |

`docs/VERIFICATION.md` maps every item of the PRD's §18 verification checklist
to the test and code path that satisfies it.

---

## Repository layout

```
backend/
  app/
    config.py            settings, the two deployment profiles
    domain/              enums, provenance, normalisation, graph models
    adapters/            graph (neo4j/embedded), nlp (nim/indicner/heuristic),
                         objectstore (minio/local), broker (celery/inline)
    pipeline/            extraction, seven source adapters, entity resolution,
                         orchestrator, celery tasks
    analytics/           centrality, patterns, explanations, temporal paths
    api/v1/              the REST surface
    security/            JWT rotation, RBAC, jurisdiction, rate limiting, @audited
    services/            the application layer the routes call
  alembic/               schema migrations
  scripts/               seed_demo.py, generate_bulk_samples.py
  tests/
frontend/                React 18 + Cytoscape + Zustand console
samples/                 the synthetic case corpus (7 documents)
infra/                   TLS mount point for nginx
```

## Licence and handling

Confidential — law-enforcement use only. Exported PDF briefs carry a
`CONFIDENTIAL / LAW ENFORCEMENT USE ONLY` watermark on every page.
