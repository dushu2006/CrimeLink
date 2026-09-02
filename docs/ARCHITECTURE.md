# CrimeLink Architecture

This document is the technical map of the CrimeLink platform. It is the
companion to `README.md` (operator-oriented quick start) and the PRD.

---

## 1. Topology

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
 │ PostgreSQL   │   │ Celery + Redis   │   │  Neo4j / embed   │
 │ system of    │   │ 6-stage pipeline │   │  knowledge graph │
 │ record       │   │ patterns, anchor │   │                  │
 └──────────────┘   └────────┬─────────┘   └──────────────────┘
                             │
                    ┌────────▼────────┐
                    │ MinIO / local   │  documents / derived / anchors
                    └─────────────────┘
```

**Two profiles, one codebase.**

|                 | Embedded (default `python run.py`) | Production (`docker compose up`) |
| --------------- | ---------------------------------- | -------------------------------- |
| Relational DB   | SQLite (file in `var/data/`)       | PostgreSQL 15                    |
| Graph           | NetworkX (in-process, persisted)   | Neo4j 5 Community                |
| Object storage  | Local filesystem (`var/objects/`)  | MinIO                            |
| Job broker      | In-process thread pool             | Celery + Redis                   |
| Web             | Vite dev server                    | Nginx (built React)              |

The domain, pipeline, analytics and API layers are byte-for-byte identical
in both profiles — only the adapters behind the ports change.

---

## 2. The Three Guarantees

Everything else in CrimeLink is subordinate to these, enforced in code and
tested by `backend/tests/test_guarantees.py`.

|     | Guarantee         | Enforcement                                                                                                |
| --- | ----------------- | ---------------------------------------------------------------------------------------------------------- |
| G1  | Every fact is evidenced | `GraphEdge` refuses construction without `source_doc_id`; the Graph Injector is the *only* module that writes to the graph; unevidenced writes raise `UnevidencedGraphWriteError`. |
| G2  | Nothing serious without a human | Fuzzy identity matches land in a review queue; pattern findings are created as `NEW` and stay that way until confirmed/dismissed; there is no `DELETE` anywhere in the API. |
| G3  | Everything is auditable | Audit log is append-only and hash-chained (`row_hash = SHA256(prev_hash ‖ canonical_json(row))`); documents are write-once addressed by SHA-256; nightly anchor of chain head. |

---

## 3. PostgreSQL — System of Record

PostgreSQL (SQLite in embedded) is the authoritative transactional store. It
holds:

* `users`, `refresh_tokens` (rotating, reuse-detecting)
* `cases` (case metadata, jurisdiction, status)
* `case_documents` (original evidence metadata + content hash; UNIQUE on
  `(case_id, content_hash)` enforces de-duplication at the DB level)
* `ingestion_jobs`, `document_stage_events` (pipeline progress, WebSocket feed)
* `entity_resolution_queue` (fuzzy identity proposals, reversible)
* `detected_patterns`, `pattern_config`
* `jurisdiction_access_requests` (time-boxed cross-jurisdiction grants)
* `audit_logs`, `audit_chain_head`, `audit_anchors` (tamper-evident)

### Migrations

Schema changes go through Alembic (`backend/alembic/versions/`). Never modify
schemas only through runtime startup code.

---

## 4. Neo4j / Embedded Graph — Relationship Knowledge Graph

The graph holds entities and the evidence-backed relationships between them.

**Node labels:**

* `Person`, `Phone`, `Vehicle`, `Location`, `BankAccount`, `Organization`,
  `Event`, `Case`

**Relationship types:**

| Type                 | Meaning                                                 |
| -------------------- | ------------------------------------------------------- |
| `MENTIONED_IN`       | Entity appears in a case                                |
| `PARTICIPATED_IN`    | Person is a party to a case                             |
| `OWNS_VEHICLE`       | Person ↔ Vehicle                                        |
| `USES_PHONE`         | Person ↔ Phone                                          |
| `CALLED`             | Phone ↔ Phone (aggregated: `call_count`, `first_ts`, `last_ts`) |
| `MEMBER_OF`          | Person ↔ Organization                                   |
| `CONTROLS_ACCOUNT`   | Person ↔ BankAccount                                    |
| `TRANSFER_TO`        | BankAccount → BankAccount (per-transfer)                |
| `LOCATED_AT`         | Person/Vehicle → Location                               |
| `ACCUSED_IN`         | Person → Case                                           |
| `ASSOCIATE_OF`, `RELATIVE_OF`, `ARRESTED_WITH`, `NAMED_ACCOMPLICE_OF`, `LINKED_ON_SOCIAL` | probabilistic person-to-person edges (low confidence) |
| `POTENTIAL_ALIAS`, `SIMILARITY_REJECTED`, `MERGED_INTO` | meta-edges for entity review (the only edges permitted without a source document) |

Every node and every edge carries `source_doc_id` (or `source_doc_ids` for
aggregated edges) so it can be traced back to the document that produced it.

Centrality (betweenness, PageRank, degree) is computed **in Python** using
NetworkX on a `CaseGraphSnapshot`, so the platform does not require the Neo4j
GDS plugin and produces deterministic rankings on every backend.

---

## 5. Six-Stage Document Pipeline

Every uploaded document moves through the same stages:

1. **Validate** — type, size, MIME, SHA-256, duplicate detection.
2. **Extract text** — natively for txt/csv/json; pypdf/python-docx/openpyxl for
   binary formats.
3. **Deterministic extraction** — regex/gazetteer extraction for phones, IFSC,
   vehicle plates, dates, amounts, IPC sections, districts. Confidence 0.95.
4. **NLP extraction** — NVIDIA NIM → IndicNER → heuristic fallback.
   **Confidence capped at 0.80** — model output is a lead, not evidence.
5. **Entity resolution** — hard identifiers merge deterministically; name
   similarity ≥ 0.85 (with Devanagari transliteration) creates a *proposal*,
   never a merge.
6. **Graph write + pattern detection** — through the single-writer injector;
   then STRUCTURING, BURNER_PHONE, RAPID_MOVEMENT, NETWORK_BRIDGE detectors.

---

## 6. Source Adapter Architecture

External data enters CrimeLink through `SourceAdapter`, a narrow interface:

```
SourceAdapter
├── SyntheticCorpusAdapter          # generated synthetic development corpus (§7)
├── ExternalSyntheticCorpusAdapter  # external synthetic corpus on disk (§7a)
├── FileImportAdapter               # real user uploads (FIR, CDR, bank, surveillance)
├── DatabaseAdapter                 # reserved for authorised relational feeds
└── FutureGovernmentAdapter         # reserved for authorised CCNS/CCTNS adapters
```

CrimeLink does **not** claim CCNS/CCTNS/police database access. The
`FutureGovernmentAdapter` slot is an adapter boundary; nothing in the codebase
fabricates a live police database connection.

---

## 7. Synthetic Development Corpus

The `synthetic_corpus` module generates a realistic, configurable Indian-style
investigation corpus designed to exercise every layer of CrimeLink. It is
**not demo data** — every record carries `source_environment="synthetic"` and
the UI labels it as synthetic.

Generate explicitly from the command line:

```bash
# Default corpus (60 people, 12 cases, 75 phones, …)
.venv/bin/python -m app.synthetic_corpus.generate

# Custom size
.venv/bin/python -m app.synthetic_corpus.generate --persons 100 --cases 20 --seed 42

# Show live database counts
.venv/bin/python -m app.synthetic_corpus.generate --stats

# Clear the dev database (dev only)
.venv/bin/python -m app.synthetic_corpus.generate --clear --yes-i-am-sure
```

Corpus characteristics:

* Configurable counts (persons, cases, phones, vehicles, locations, accounts,
  organisations, documents, calls, transactions, bridges, networks).
* Indian-style names with realistic variations (e.g. `Ramesh Kumar` /
  `Ramesh K.` / `RAMESH KUMAR` / `R. Kumar`) that create natural
  entity-resolution ambiguity.
* Controlled missing/dirty fields at configurable rates.
* Multiple independent criminal networks plus **bridge** individuals who
  appear across networks — exercising betweenness centrality, PageRank,
  cross-case analysis.
* **Indirect connections** (A→phone→phone→B, vehicle→location→person,
  account→account→person), no fake `CONNECTED_TO` edges.
* Multiple cases with overlapping entities (cross-case signal).
* Chronologically coherent timestamps with bursts around incident dates,
  structuring chains, burner-phone lifespans.
* Known scenarios (burner phone, rapid transactions, bridge, mule,
  vehicle-location pattern, identity ambiguity, communication clusters,
  temporal paths, noisy relationships) encoded in
  `synthetic_ground_truth.json` for testing — never surfaced to the
  investigator UI.
* Deterministic seed for reproducible tests.

The same seed reproduces the same corpus; different seeds produce different
corpora. Changing entity counts requires no source code changes.

---

## 7a. External Synthetic Corpus (filesystem dataset)

`CRIMELINK_SYNTHETIC_DATA_MODE=external` switches synthetic ingestion from the
in-process generator to a corpus directory on disk (for example a sibling
`CrimeLink_Synthetic_Corpus_v1` checkout next to this repository, resolved via
`CRIMELINK_SYNTHETIC_DATA_ROOT`; relative paths resolve against the repo root,
absolute paths are honoured verbatim; nothing is copied into Git and nothing
is ingested at startup).

The **ExternalSyntheticCorpusAdapter** implements the same `SourceAdapter`
boundary as the generator and feeds the same pipeline
(`upload_document` → six stages → relational store → graph):

1. **Resolve + validate** the root; `operational/` and `documents/` must
   exist. Missing directories fail loudly with the offending path.
2. **Discover** files under `operational/` and `documents/` only. Any
   `ground_truth/` or `metadata/` component is explicitly excluded
   (ground truth is evaluation-only data and never enters PostgreSQL, the
   graph, the UI, or AI context). Root-level corpus docs (`README.md`,
   `SCHEMA.md`) are listed as ignored.
3. **Classify by content signature**, never by assumed filenames: CSV header
   rows are matched against the same alias tables the pipeline adapters
   parse with (CDR schema registry, financial aliases, surveillance,
   criminal history); JSON is matched by structural signature (social export,
   sighting, named record); TXT/MD/PDF/DOCX go through the text document
   adapter. Unrecognised files are reported as `unsupported` with their
   header quoted — the operator sees the gap instead of a silent skip.
4. **Ingest explicitly** — `python -m app.synthetic_corpus.external`
   (or `python -m app.cli ingest-synthetic`, or
   `POST /api/v1/admin/synthetic/ingest`). Files are grouped into synthetic
   cases by directory (`SYN-EXT/<GROUP>`, title prefixed `[SYNTHETIC]`), then
   uploaded with `source_confidence=SYNTHETIC`; the inline/celery broker runs
   the pipeline exactly as for investigator uploads.
5. **Idempotency**: case numbers are deterministic, documents dedupe on
   `UNIQUE(case_id, content_hash)`, and graph writes converge via
   provenance keys — re-running reports duplicates instead of copying data.

A run reports: records discovered / accepted / ingested / duplicates /
rejected / failed, excluded evaluation files, per-file outcomes and (with
`--wait`) the embedded pipeline's document/job statuses and graph counts.

---

## 8. AI Gateway and Data Boundary

CrimeLink never sends the entire PostgreSQL or Neo4j database to an LLM, and
it never hashes the whole graph for "privacy". Instead the architecture is:

```
Investigator Question
    ↓
AI Request Orchestrator
    ↓
Query Planner / Retrieval (Postgres + Neo4j)
    ↓
Relevant Evidence Selection
    ↓
Graph Subgraph Construction
    ↓
Data Minimization
    ↓
Reversible Pseudonymization   (PERSON_023, PHONE_041, VEHICLE_009, …)
    ↓
Model-specific context
    ↓
LLM
    ↓
Structured result (Pydantic-validated)
    ↓
Backend validation (evidence refs present; no forbidden labels; no
    irreversible-action requests)
    ↓
De-pseudonymization for authorized UI only
    ↓
Investigator
```

### Reversible pseudonymization

Pseudo-IDs are deterministic within an investigation context. The mapping
table lives inside the trusted backend only; the model never sees it. Hashing
is explicitly NOT used as the only mechanism because it is one-way and would
prevent authorized de-pseudonymization.

### Model roles

Five independently configurable roles (each can point at a different
provider/model via environment variables):

| Role             | Used for                                                                    |
| ---------------- | --------------------------------------------------------------------------- |
| `EXTRACTION`     | NER, relation extraction, document understanding                            |
| `REASONING`      | Multi-hop investigation, hypothesis generation, temporal reasoning          |
| `EXPLANATION`    | Investigator-friendly summaries, "why this matters", evidence-grounded text |
| `CLASSIFICATION` | Pattern classification, prioritization, triage, relevance scoring           |
| `EMBEDDING`      | Semantic search, similar-case/FIR retrieval, near-duplicate detection       |

All role configuration comes from env vars (`AI_EXTRACTION_MODEL`,
`AI_REASONING_API_KEY`, …). No model name or key is hard-coded. When a role's
key is absent the gateway returns a structured "insufficient evidence"
response and the system keeps working in offline/heuristic mode.

### Structured output contract

Every AI response is validated against the `FindingResult` Pydantic model
(finding_type, summary, confidence, evidence_level, entities, relationships,
evidence_refs, reasoning_steps, uncertainties, recommended_review,
suggested_next_actions). Malformed output is rejected and surfaced to the
investigator as "model output failed validation; human review required".

### Evidence grounding and fact/inference distinction

Every finding must carry `evidence_refs`. If the model cannot cite evidence,
the result is marked `UNKNOWN` / unsupported rather than presented as fact.
The output distinguishes:

* **FACT** — directly supported by provided evidence
* **INFERENCE** — analytically derived
* **HYPOTHESIS** — possible explanation requiring investigation
* **UNKNOWN** — insufficient evidence

### Neutral language policy

AI output is filtered for forbidden labels (`criminal`, `guilty`, `terrorist`,
`gang member`, `mastermind`, `kingpin`). The system uses: *person of interest*,
*associated entity*, *analytically significant entity*, *potential connection*,
*pattern requiring review*, *evidence-supported association*. All serious
findings require human review (`recommended_review=true` by default).

### Audit

Every AI request is audited with requesting user, case, model role/ID,
timestamp, entity/evidence IDs, output hash, latency, and (if configured)
token counts. Full prompts are only persisted if `CRIMELINK_AI_AUDIT_PROMPT_STORAGE=true`.

### AI cannot write to the database directly

Models produce findings; they cannot merge identities, delete evidence,
confirm a criminal pattern, or modify authoritative data. Human investigators
remain responsible for consequential decisions.

---

## 9. Human-in-the-Loop

* Fuzzy entity matches are **proposed**, never merged. Investigators approve,
  reject, or unmerge from the Review Queue. Merges are reversible: the
  pre-merge edge list is stored on the deactivated node.
* Detected patterns are created as `NEW` and must be confirmed/dismissed with
  a written reason.
* Anonymous-tip content stays in a staging subgraph until an investigator
  promotes it (`promote_staging`).
* Cross-jurisdiction access is always time-boxed and requires approval.
* There is no `DELETE` anywhere in the API; documents are soft-deleted.

---

## 10. Security Model

The data flow is strictly:

```
Raw source data
  → Trusted backend
  → Controlled graph representation
  → Relevant subgraph
  → Minimized/pseudonymized AI context
  → AI
  → Validated result
  → Authorized de-pseudonymization
  → Investigator
```

It is never:

```
Raw police database → LLM           (forbidden)
Entire Neo4j → LLM                  (forbidden)
LLM → direct database write         (forbidden)
```

---

## 11. Administrator Database Inspection

Authorized administrators can inspect the live state of the system through
read-only endpoints (RBAC + jurisdiction scoped; no secrets):

| Endpoint                              | Provides                                               |
| ------------------------------------- | ------------------------------------------------------ |
| `GET /api/v1/admin/database/summary`  | Aggregate counts + infrastructure status               |
| `GET /api/v1/admin/database/health`   | Postgres / graph / redis / object-store / broker / NLP / AI role status |
| `GET /api/v1/admin/database/cases`    | Paginated case listing                                 |
| `GET /api/v1/admin/database/documents`| Paginated document listing                             |
| `GET /api/v1/admin/database/entities` | Paginated graph entities (type-filterable)             |
| `GET /api/v1/admin/database/relationships` | Paginated graph relationships (type-filterable)    |
| `GET /api/v1/admin/database/postgres` | PostgreSQL table counts                                |
| `GET /api/v1/admin/database/neo4j`    | Graph store stats                                      |

The Administration UI exposes these through the Overview, Database, Cases,
Documents, Entities, Relationships, Health, AI Activity and Audit Log tabs.

---

## 12. Windows/Dependency Notes

CrimeLink supports Python 3.11 and 3.12. The `numpy` and `networkx` pins are
deliberately loose (`numpy>=1.26,<2.3`, `networkx>=3.2,<4.0`) so that pip always
picks a prebuilt wheel on Windows, macOS and Linux. **You do not need Visual
Studio Build Tools** to install CrimeLink on Windows. If you see NumPy trying
to build from source, upgrade pip first:

```
python -m pip install --upgrade pip setuptools wheel
python run.py
```

---

## 13. Commands Cheat Sheet

```bash
# Start the embedded profile (recommended for first use)
python run.py

# Reinstall dependencies
python run.py --reinstall

# Generate a realistic synthetic development corpus
.venv/Scripts/python -m app.synthetic_corpus.generate            # Windows
.venv/bin/python -m app.synthetic_corpus.generate                # macOS/Linux

# Scale the corpus up to 100+ people
.venv/bin/python -m app.synthetic_corpus.generate --persons 100 --cases 20

# Show live counts
.venv/bin/python -m app.synthetic_corpus.generate --stats

# Reset the dev database (dev only)
.venv/bin/python -m app.synthetic_corpus.generate --clear --regenerate --yes-i-am-sure

# Run backend tests
cd backend && ../.venv/bin/python -m pytest tests/ -q

# Production (containers)
cp .env.example .env        # set CRIMELINK_SECRET_KEY and AI keys
docker compose up -d --build
```
