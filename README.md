# CrimeLink

**AI-Powered Criminal Network Analysis System** — an evidence-backed investigation
platform designed for Indian law-enforcement workflows (MHA / NCRB Women Safety
division problem statement, theme: Blockchain & Cybersecurity).

CrimeLink ingests the messy artefacts of a real case — FIRs in English and
Hindi, call detail records, bank statements, surveillance logs, social-media
exports, criminal histories and anonymous intel — and turns them into one
attributable graph where **every node and every edge is backed by a source
document**, where **no consequential decision is made without a human**, and
where **every action is recorded in a tamper-evident audit log**.

> Important: CrimeLink does **not** connect to live CCNS/CCTNS/official police
> databases. The architecture includes an adapter boundary (the
> `SourceAdapter` interface) for future authorised government/police feeds,
> but no such live feed is implemented or claimed. The `synthetic_corpus`
> module is a **realistic synthetic development corpus** — clearly labelled
> as such in provenance metadata and in the UI — that exercises the full
> pipeline with data of the same shape, messiness and relationship complexity
> a real feed would produce.

---

## The three guarantees

Everything else in the system is subordinate to these. They are enforced in
code, not in policy documents, and each has tests
(`backend/tests/test_guarantees.py`).

|     | Guarantee         | How it is enforced |
| --- | ----------------- | ------------------ |
| **G1** | **Everything is evidenced** | `GraphNode`/`GraphEdge` refuse to be constructed without a `source_doc_id`; `GraphInjector` is the *only* module allowed to write to the graph; an unevidenced write raises `UnevidencedGraphWriteError`. |
| **G2** | **Nothing serious happens without a human** | Fuzzy identity matches are *proposed*, never merged; detected patterns stay `NEW` until an investigator confirms/dismisses them with a written reason; the API has no `DELETE` method. |
| **G3** | **Everything is auditable and tamper-evident** | The audit log is append-only and hash-chained; documents are write-once and addressed by SHA-256; `GET /admin/audit/verify` replays the chain and reports the first modified entry; the chain head is anchored nightly to a separate object-store bucket. |

---

## Architecture at a glance

```
                    ┌─────────────────┐
   browser  ───────►│  nginx (web)    │  React console, TLS, reverse proxy
                    └────────┬────────┘
                             │ /api
                    ┌────────▼────────┐
                    │  FastAPI (api)  │  REST + WebSocket progress
                    └───┬─────┬───┬───┘
        ┌───────────────┘     │   └──────────────────┐
        ▼                     ▼                      ▼
 ┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
 │ PostgreSQL 15│   │ Celery + Redis   │   │  Neo4j 5 / embed │
 │ cases, docs, │   │ 6-stage pipeline │   │  knowledge graph │
 │ queue, audit │   │ patterns, anchor │   │                  │
 └──────────────┘   └────────┬─────────┘   └──────────────────┘
                             │
                    ┌────────▼────────┐
                    │ MinIO / local   │  documents / derived / audit anchors
                    └─────────────────┘
```

Eight containers in production (`docker-compose.yml`): `postgres`, `neo4j`,
`redis`, `minio`, `api`, `worker`, `beat`, `web`.

**Two profiles, one codebase.** `CRIMELINK_PROFILE=embedded` runs the entire
system in a single Python process on SQLite, an in-process NetworkX graph,
the local filesystem and an in-process job executor — no containers, no
network, no external services. `CRIMELINK_PROFILE=production` swaps in
PostgreSQL, Neo4j, MinIO and Celery. The domain, pipeline, analytics and API
layers are byte-for-byte identical in both; only the adapters behind the
ports change.

### The six-stage pipeline

1. **Validate** — type, size, MIME, SHA-256, duplicate detection.
2. **Extract text** — txt/csv/json natively; PDF, DOCX and XLSX through
   pypdf / python-docx / openpyxl.
3. **Deterministic extraction** (stage 1 of the hybrid) — regex and gazetteer
   rules for phones, IFSC/account numbers, vehicles, dates, amounts, IPC
   sections, districts. Confidence 0.95.
4. **NLP extraction** (stage 2) — NVIDIA NIM, or IndicNER, or a deterministic
   heuristic fallback. **Confidence is capped at 0.80**, always.
5. **Entity resolution** — hard identifiers merge deterministically; name
   similarity ≥ 0.85 (with Devanagari transliteration, so `सुरेश मेहता` matches
   `Suresh Mehta`) creates a *proposal*, never a merge.
6. **Graph write + pattern detection** — through the single-writer injector,
   then the deterministic pattern detectors.

A document that fails to parse is quarantined with the reason recorded.

### Analytics

Centrality (betweenness, PageRank, degree) is computed **deterministically in
Python** with confidence-weighted edges, so it needs no GDS licence and produces
the same ranking on every run. Louvain communities and four pattern detectors
(`STRUCTURING`, `BURNER_PHONE`, `RAPID_MOVEMENT`, `NETWORK_BRIDGE`) complete the
picture.

**A score never travels alone.** Every influence response carries the subgraph
that produced it: the summary, weighted edges, community, method description
and document IDs behind it.

### AI Gateway / multi-model routing

AI access goes exclusively through the **AI Gateway** — a controlled data
boundary that retrieves only the relevant subgraph, minimizes the context,
replaces real identifiers with reversible pseudonyms (e.g. `PERSON_023`,
`PHONE_041`) before data leaves the trusted backend, routes the task to the
configured model for the role, validates structured Pydantic output, checks
for evidence references, and enforces neutral-language and no-direct-write
policies before surfacing results to an authorized investigator.

Model roles are independently configurable:

* `EXTRACTION`    — NER, relation extraction, document understanding
* `REASONING`     — multi-hop investigation, hypothesis generation, temporal
* `EXPLANATION`   — investigator-friendly summaries, "why this matters"
* `CLASSIFICATION`— triage, prioritization, pattern classification
* `EMBEDDING`     — semantic search, similar-case/FIR retrieval

Every AI request is audited. The system works fully offline (heuristic NLP,
no AI reasoning) when no API keys are present.

---

## Quick start (no containers required)

**Prerequisites**

* Python 3.11 or 3.12 (newer versions work but NumPy wheels may lag)
* Node.js 18+ and npm

> Windows: you do **not** need Visual Studio Build Tools. CrimeLink pins NumPy
> to a range (`numpy>=1.26,<2.3`) so pip always picks a prebuilt wheel. If pip
> still tries to build from source, run
> `python -m pip install --upgrade pip setuptools wheel` first.

**Run it**

```bash
git clone <repo> && cd CrimeLink
python run.py
```

The first run creates a virtual environment in `.venv/`, installs backend and
frontend dependencies, starts the API on `http://127.0.0.1:8000` and the
investigator console on `http://127.0.0.1:5173`, then opens a browser. The
database starts **empty** — create the first **administrator** in the
console (badge number, name, station, jurisdiction, password), then:

1. **Administration → Users** — add investigators and viewers.
2. **Cases → Register Case** — open a real FIR/case file.
3. Upload documents (FIR, CDR, bank statement, surveillance, social export,
   criminal history, intel). Processing is automatic.
4. Open the Network Graph and Review Queue to see entities, relationships,
   centrality scores, detected patterns and entity-resolution proposals.

### Optional: generate a synthetic development corpus

CrimeLink does **not** insert demo data on startup. To exercise the whole
platform end to end without real case files, generate the labelled-synthetic
development corpus:

```bash
# Default corpus (60 people, 12 cases, 75 phones, …)
.venv/bin/python -m app.synthetic_corpus.generate            # macOS/Linux
.venv\Scripts\python -m app.synthetic_corpus.generate         # Windows

# Scale up to 100+ people
.venv/bin/python -m app.synthetic_corpus.generate --persons 100 --cases 20

# Deterministic seed (reproducible)
.venv/bin/python -m app.synthetic_corpus.generate --seed 42

# Live database counts
.venv/bin/python -m app.synthetic_corpus.generate --stats

# Reset dev database
.venv/bin/python -m app.synthetic_corpus.generate --clear --regenerate --yes-i-am-sure
```

Every synthetic record carries `source_environment="synthetic"` so it can
never be confused with operational data. The UI labels synthetic data
accordingly.

### Optional: ingest the local synthetic corpus from disk

The development dataset lives at **`backend/CrimeLink_Synthetic_Corpus_v1/`**
(gitignored; its absence from GitHub does not mean it is absent locally):

```
CrimeLink/
└── backend/
    └── CrimeLink_Synthetic_Corpus_v1/
        ├── operational/     # structured CSV — ingested
        ├── documents/       # investigation documents — ingested
        ├── metadata/        # dataset documentation — never imported
        ├── ground_truth/    # evaluation answers — NEVER ingested
        ├── README.md
        └── SCHEMA.md
```

Configure CrimeLink to use it (these are the development defaults):

```bash
# .env
CRIMELINK_SYNTHETIC_DATA_MODE=external
CRIMELINK_SYNTHETIC_DATA_ROOT=backend/CrimeLink_Synthetic_Corpus_v1
```

Ingestion is **explicit** — application startup never imports the dataset.
In the console: **Administration → Dataset → Validate Dataset / Import Dataset**.
Equivalent CLI:

```bash
# validate / classify only (writes nothing)
.venv/bin/python -m app.synthetic_corpus.external --dry-run

# ingest through the standard six-stage pipeline
.venv/bin/python -m app.synthetic_corpus.external

# equivalent mode-aware umbrella command
.venv/bin/python -m app.cli ingest-synthetic
```

Create the first administrator with jurisdiction **`SYN-DEV`** so imported
cases appear on the Cases page.

Records flow through the exact same path as investigator uploads:
`SourceAdapter → upload_document → six-stage pipeline → PostgreSQL → graph →
entity resolution → pattern detection → UI → AI Data Gateway`. Only
`operational/` and `documents/` are read. `cases.csv` becomes Case records
(using the dataset case numbers); event tables are sliced by `case_id` (or
via `case_members` when `case_id` is empty). Unrecognised files are reported
by name instead of being skipped silently. Re-running is safe: cases match on
case number and documents on `UNIQUE(case_id, content_hash)`, so a second run
reports duplicates rather than copying them. `ground_truth/` is isolation-checked
and excluded — it may only be consumed by a separate evaluation harness, never
by the operational pipeline, AI context or UI.

### Manual start (without run.py)

```bash
python3.11 -m venv .venv
.venv/bin/pip install ./backend
.venv/bin/python -m uvicorn app.main:create_app --factory \
    --app-dir backend --host 0.0.0.0 --port 8000

cd frontend && npm install && npm run dev
```

### Production (containers)

```bash
cp .env.example .env        # set CRIMELINK_SECRET_KEY; add API keys if you have them
docker compose up -d --build
```

Then open the console and create the first administrator.

---

## API keys you may (optionally) want

CrimeLink works fully offline without any API key. You only need keys if you
want AI-powered extraction/reasoning.

| Key                       | Purpose                                              | Where to get it                  | Required? |
| ------------------------- | ---------------------------------------------------- | -------------------------------- | --------- |
| `NVIDIA_API_KEY`          | Enables NVIDIA NIM for NLP extraction + AI Gateway roles when per-role keys are not set | https://build.nvidia.com (free personal key) | **Optional** |
| `CRIMELINK_AI_EXTRACTION_API_KEY` / `_BASE_URL` / `_MODEL` | Overrides the extraction model                      | Any OpenAI-compatible endpoint   | Optional |
| `CRIMELINK_AI_REASONING_API_KEY` / `_BASE_URL` / `_MODEL`   | Multi-hop reasoning model                           | Any OpenAI-compatible endpoint   | Optional |
| `CRIMELINK_AI_EXPLANATION_API_KEY` / `_BASE_URL` / `_MODEL` | Investigator-friendly explanations                  | Any OpenAI-compatible endpoint   | Optional |
| `CRIMELINK_AI_CLASSIFICATION_API_KEY` / `_BASE_URL` / `_MODEL` | Lightweight classification/triage                | Any OpenAI-compatible endpoint   | Optional |
| `CRIMELINK_AI_EMBEDDING_API_KEY` / `_BASE_URL` / `_MODEL`   | Semantic search / embeddings                        | Any OpenAI-compatible endpoint   | Optional |

You do **not** need a CCNS/CCTNS/police-database credential, Google/Maps key,
Firebase, AWS, or any other paid SaaS to run the platform.

---

## API surface

Interactive docs are at `/api/docs` when the API is running. There is no
`DELETE` and no `PUT` anywhere — by design.

| Area        | Endpoints |
| ----------- | --------- |
| Auth        | `GET/POST /auth/setup` (first admin only), `POST /auth/login`, `/auth/refresh`, `/auth/logout`, `GET /auth/me` |
| Cases       | `GET/POST /cases`, `GET/PATCH /cases/{id}`, `/cases/{id}/timeline`, `/cases/{id}/export` |
| Documents   | `POST /cases/{id}/documents`, `GET /cases/{id}/documents`, `GET /evidence/{doc_id}`, `GET /evidence/{doc_id}/verify` |
| Jobs        | `GET /jobs/{id}`, `GET /cases/{id}/jobs`, `WS /jobs/ws/{case_id}` |
| Graph       | `GET /graph/cases/{id}`, `/centrality`, `/influencers`, `/staging`, `POST /cases/{id}/paths`, `POST /cases/{id}/staging/promote`, `GET /graph/nodes/{key}`, `/expand`, `/influence` |
| Review      | `GET /resolution`, `POST /resolution/{id}/merge|reject|unmerge`, `GET /patterns`, `POST /patterns/{id}/review` |
| AI          | `POST /ai/cases/{id}/ask` |
| Access      | `POST /access/request`, `POST /access/approve/{id}`, `GET /access/requests` |
| Admin       | `GET /admin/overview`, `/admin/audit/search`, `/admin/audit/verify`, `/admin/audit/anomalies`, `/admin/quarantine`, `/admin/users`, `/admin/thresholds`, `/admin/database/summary`, `/admin/database/health`, `/admin/database/cases`, `/admin/database/documents`, `/admin/database/entities`, `/admin/database/relationships`, `/admin/database/postgres`, `/admin/database/neo4j` |

---

## Roles and jurisdiction

Three roles only: **VIEWER**, **INVESTIGATOR**, **ADMIN**. Jurisdiction scoping
sits underneath roles and is enforced in the query layer
(`JurisdictionScope.case_filter()`), never by hiding a button: a request for
a case outside your jurisdiction returns the same `404` as one that doesn't
exist, so the API does not confirm that another district's case is real.

---

## Testing

```bash
cd backend
../.venv/bin/python -m pytest tests/ -q
```

| File                        | Covers |
| --------------------------- | ------ |
| `tests/test_domain.py`      | Provenance keys, hash chain, transliteration and fuzzy matching, model guards |
| `tests/test_pipeline.py`    | All source adapters, extraction, entity resolution, patterns |
| `tests/test_api_contract.py`| Auth/refresh/lockout, RBAC, jurisdiction scoping, uploads, full HTTP surfaces |
| `tests/test_guarantees.py`  | G1, G2 and G3 end-to-end, including tamper detection and write-once storage |
| `tests/test_analytics.py`   | Centrality ranking, mandatory explanations, temporal paths, pattern detection |
| `tests/test_synthetic.py`   | Synthetic corpus determinism, entity counts, name variation, cross-case linking |

`docs/VERIFICATION.md` maps every item of the verification checklist to the
test and code path that satisfies it.

---

## Repository layout

```
backend/
  app/
    config.py                 # settings, both deployment profiles
    domain/                   # enums, provenance, normalisation, graph models
    ai/                       # AI gateway, pseudonymization, model router, schemas
    adapters/
      graph/                  # neo4j, embedded (NetworkX), injector
      nlp/                    # nim, indicner, heuristic, factory
      objectstore/            # minio, local
      broker/                 # celery, inline
      sources/                # SourceAdapter protocol + registry
    pipeline/                 # extraction, source adapters, entity resolution,
                              # orchestrator, celery tasks
    synthetic_corpus/         # configurable dev corpus + external corpus ingest (CLI)
    analytics/                # centrality, patterns, explanations, temporal paths
    api/v1/                   # REST surface (auth, cases, documents, graph,
                              # resolution, patterns, access, admin, ai, database)
    security/                 # JWT rotation, RBAC, jurisdiction, rate limiting, @audited
    services/                 # application layer the routes call
  alembic/                    # schema migrations
  tests/
frontend/                     # React 18 + Cytoscape + Zustand console
infra/                        # TLS mount point for nginx
run.py                        # local installer + launcher (embedded profile)
docker-compose.yml            # production topology
.env.example                  # all environment variables, grouped by section
```

## Licence and handling

Confidential — law-enforcement use only. Exported PDF briefs carry a
`CONFIDENTIAL / LAW ENFORCEMENT USE ONLY` watermark on every page.
