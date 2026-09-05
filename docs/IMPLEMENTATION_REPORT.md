# CrimeLink — Implementation Report

Branch: `arena/01a06d5c-crimelink` · Date: 2026-09-04

This report covers the eight requested areas: graph architecture, data
storage, dataset regeneration, AI performance, the temporal graph, the auth /
WebSocket fixes, test results, and the data-storage proof. It describes the
*working tree* state; nothing has been committed or pushed.

---

## 1. Graph architecture — three views, one dataset

CrimeLink now exposes **three graph experiences over a single canonical graph
dataset** (the same nodes/edges, always evidence-backed, never re-invented per
view):

| View | Purpose | Backend | Frontend |
| --- | --- | --- | --- |
| **Person Graph** | Focused subgraph around a selected target | `GET /cases/{id}/network/{person_key}?depth=N` → `GraphService.person_centric_network` (BFS, 1–3 hops, person-involving edges preferred) | `GraphPage.tsx` person mode + target rail + depth buttons |
| **Master Graph** | Complete case/network graph | `GET /graph/cases/{id}` → `GraphService.case_graph` (whole snapshot, now with `labels` / `rel_types` filters) | `GraphPage.tsx` master mode + entity-type / relation-type chips + staging toggle |
| **Temporal Graph** | Visual, time-constrained graph (NOT a serialised path) | `GET /graph/cases/{id}/temporal` → `GraphService.temporal_graph` → `build_temporal_graph` | `GraphPage.tsx` temporal mode: from/to window, optional focus person, depth, **timeline strip** |

**Temporal path search stays a separate capability.** `POST
/graph/cases/{id}/paths` (`find_temporal_paths`) still returns chronologically
ordered paths; its serialised output is supporting evidence only, never the
primary temporal result.

All three modes share one Cytoscape renderer (`GraphPage.tsx`) and one
detail-panel/serialisation path (`_node_row` / `_edge_row`), so a node selected
in any view shows the same evidence pointer and type-specific attributes.

---

## 2. Data storage — where everything actually lives

CrimeLink has two run profiles; the domain/pipeline/analytics/API layers are
identical in both, and only the adapters behind the ports change
(`backend/app/adapters/…`, interfaces in `backend/app/ports/…`).

| Concern | Embedded (default) | Production | Where |
| --- | --- | --- | --- |
| Relational system of record | SQLite (`var/data/`) | **PostgreSQL 15** | `app/db/models.py`, `app/db/session.py` |
| Relationship graph | **NetworkX** in-process | **Neo4j 5** | `app/adapters/graph/embedded.py`, `app/adapters/graph/neo4j.py` |
| Document binaries / derived artefacts | Local FS (`var/objects/`) | **MinIO** | `app/adapters/objectstore/local.py`, `app/adapters/objectstore/minio_store.py` |
| Job broker | in-process thread pool | Celery + Redis | `app/adapters/broker/` |
| **Visualization** | **Cytoscape.js (browser only)** | same | `frontend/src/pages/GraphPage.tsx` |

Key points, verified against the code:

* **PostgreSQL** holds the authoritative transactional tables: `users`,
  `refresh_tokens`, `cases`, `case_documents`, `ingestion_jobs`,
  `document_stage_events`, `entity_resolution_queue`, `detected_patterns`,
  `pattern_config`, `jurisdiction_access_requests`, and the hash-chained
  `audit_logs` / `audit_chain_head` / `audit_anchors` (see
  `docs/ARCHITECTURE.md` §3).
* **Neo4j / NetworkX** holds only the relationship knowledge graph (nodes:
  `Person`, `Phone`, `Vehicle`, `Location`, `BankAccount`, `Organization`,
  `Event`, `Case`; evidenced edges such as `CALLED`, `TRANSFER_TO`,
  `PARTICIPATED_IN`, `LOCATED_AT`, …). The **only** module permitted to write
  it is `app/adapters/graph/injector.py`; every node and edge carries a
  `source_doc_id` (guarantee G1).
* **Cytoscape.js is visualization only.** It receives JSON from the API and
  draws it. It is never the source of truth — a graph appearing on screen is
  not evidence of persistence. Persistence is proven by the graph store
  (Neo4j/NetworkX) and the relational store (PostgreSQL/SQLite).
* **Ground truth** (`backend/CrimeLink_Synthetic_Corpus_v1/ground_truth/*.json`)
  is evaluation-only and never enters the relational DB or the graph.

### Data-storage proof (concrete)

* Graph write gate: `app/adapters/graph/injector.py` — docstring "THE ONLY
  MODULE PERMITTED TO WRITE TO THE GRAPH"; `tests/test_guarantees.py` statically
  scans the repo for Cypher write keywords outside it and fails the build.
* Graph backends implement one contract: `app/ports/stores.py`
  (`GraphStore`), implemented by `embedded.py` (NetworkX) and `neo4j.py`.
* Relational engine selection: `app/db/session.py` (`effective_relational_backend`
  → `postgres_dsn` or `sqlite_url`), with `init_db()` creating tables and
  retrofitting SQLite columns.
* Object store selection: `app/adapters/objectstore/` (`local.py` vs
  `minio_store.py`).
* The console reports the active graph backend honestly
  (`GET /cases/{id}/investigation` → `graph_backend`, shown as a badge in the
  UI) — `neo4j` in production, `embedded`/`networkx` locally.

---

## 3. Dataset — regenerated, coherent, no garbage negatives

### Problem

The previous corpus left ~72% of CDR, ~78% of transactions, ~68% of vehicle
sightings and ~37% of intelligence rows with an empty `case_id`. Those dangling
rows had no coherent story, so the graph filled with meaningless "negative"
noise, and sighting events collapsed to bare `PATROL` / `CCTV` / `ANPR` /
`INTELLIGENCE` node labels.

### Fix

New deterministic generator: `backend/app/synthetic_corpus/build_external.py`
(stdlib only, seeded `20260902`), run with:

```
cd backend && python3 app/synthetic_corpus/build_external.py
```

It builds **two explicit populations**:

* **Case population** — 12 cases; each case has 8 members (roles, phones, and
  per-person vehicles/accounts), 6 locations, 1–2 organisations, and *every*
  event row (CDR, transfer, sighting, intel) carries an explicit `case_id` and
  references only that case's own entities.
* **Background population** — 24 persons with phones (and some vehicles and
  accounts) who are **not members of any case**. Their calls and transfers are
  internally coherent (they phone and pay *each other*) but carry no
  `case_id`: the coherent **"unrelated" negative class**. The source adapter
  quarantines them with the honest reason `subject_in_no_case`.

### Event identity

Each sighting row is a distinct event instance. The extraction pipeline
(`app/pipeline/extraction/deterministic.py`) now keys the event by
`SIGHTING::{person}::{timestamp}` and labels it
`{SOURCE} · {location} · {time}` with `event_type` set from the source column —
so the master graph shows `PATROL · Warehouse 12 · 2025-04-27 01:51`-style
nodes instead of a single global `PATROL` node.

### Resulting counts (verified on disk)

| File | Total | case-assigned | background (no `case_id`) |
| --- | --- | --- | --- |
| `cases.csv` | 12 | — | — |
| `persons.csv` | 120 | 96 (members) | 24 (background) |
| `cdr.csv` | 394 | 355 | 39 |
| `transactions.csv` | 110 | 96 | 14 |
| `vehicle_sightings.csv` | 120 | 120 | 0 |
| `intelligence_reports.csv` | 60 | 60 | 0 |
| `documents/*.txt` | 24 | 24 | 0 |

Referential integrity was verified by script: 0 foreign-key violations across
`case_members`, `phones`, `vehicles`, `accounts`, CDR/transaction endpoints,
sightings, intel, and `person_organizations`; 0 missing document files. All
names are generated synthetic combinations — no real personal data.

`SCHEMA.md` and `README.md` inside the corpus were updated to describe the
two-population model and event identity.

---

## 4. AI performance — smaller context, parallel analysis, caching

The AI reasoning stage (`stage 7`) was the slow path. Three changes reduce it
without changing its honesty:

1. **Smaller context.** New settings in `app/config.py`:
   `ai_max_context_nodes = 120`, `ai_max_context_edges = 400`. The gateway
   (`app/ai/gateway.py`) now retrieves a *bounded BFS neighbourhood* of the
   target person (`_neighbourhood_keys`) instead of the whole case, drops
   `Case` nodes, caps node/edge lists at the budget, and re-caps at
   serialisation time. A busy demo case can no longer blow up the prompt.
2. **Parallel, independent analysis.** `_stage_ai_analysis`
   (`app/services/investigation.py`) analyses the top
   `ai_reasoning_target_count = 3` persons with `asyncio.gather(...)` instead
   of sequentially. The three reasoning calls are independent, so they run
   concurrently over the workflow loop.
3. **Reusable caching.** Centrality was already cached keyed by
   `(case_id, graph_version)` and is reused by the AI stage and the influence
   endpoint (`GraphService._centrality`). No new cache is needed for the
   graph snapshot because the version key already invalidates on every write.

The UI was already non-blocking on AI: deterministic stages and findings show
immediately, and the AI stage reports honestly when no key is configured
(`AI unavailable (no_api_key_for_role_reasoning). Deterministic analysis
results remain available.`).

---

## 5. Temporal graph — visual, with controls and a timeline

Backend:

* `app/analytics/temporal.py` — `build_temporal_graph(snapshot, target_key,
  from_ts, to_ts, depth, limit)` returns graph-ready nodes/edges, the
  effective `time_range`, the dated `events` inside the window, and an
  `empty_reason` when nothing matches. Undated attribute edges
  (`OWNS_ACCOUNT`, `USES_PHONE`, `OWNS_VEHICLE`, `MEMBER_OF`, …) are kept only
  as structural glue between entities already active in the window.
* `app/services/graph_service.py` — `temporal_graph()` serialises that result
  with the same `_node_row` / `_edge_row` helpers as every other endpoint.
* `app/api/v1/graph.py` — `GET /graph/cases/{id}/temporal` with
  `target`, `from_ts`, `to_ts`, `depth` (1–4), `limit`; audited as
  `GRAPH_EXPAND` with `kind = "temporal_graph"`.

Frontend (`GraphPage.tsx` temporal mode):

* Date-range controls (`from` / `to`), optional focus person, depth selector,
  and a **Build temporal graph** button.
* A **timeline strip** renders every dated event in the window as a marker;
  clicking a marker selects the event node on the canvas.
* An empty window is reported honestly ("No dated relationships in this
  window") instead of drawing an empty canvas.
* The separate **Temporal path search** panel remains available; its JSON
  output is supporting evidence only.

---

## 6. Auth fix — 401 and the WebSocket, without weakening security

### Diagnosis

* `/api/v1/cases/{case_id}/persons` requires a valid access token
  (`get_principal` → `decode_access_token` → active user). Access tokens live
  **15 minutes** (`app/security/tokens.py`). The 401 appears when the token
  expires and the console has not renewed it yet.
* The jobs WebSocket authenticates with the access token as a query parameter.
  The server **accepts the socket first and then closes with `4401` (token
  rejected) or `4403` (out of scope)** — this is deliberate
  (`app/api/v1/jobs.py`): closing before accept would surface an opaque 1006.
  A stale token therefore produced exactly the "closing before connection was
  established" console noise the user saw.

### Fixes (auth model unchanged)

`frontend/src/api/client.ts`:

* **Proactive renewal.** `ensureFreshToken()` decodes the access token's `exp`
  and, within a 60 s margin, performs one shared refresh *before* the request —
  so a call rarely has to 401 at all. `apiInner` calls it before every fetch.
* **Single-flight refresh preserved.** The 401 → refresh → replay path and the
  single shared `refreshSession()` promise (which prevents the refresh-token
  reuse/family-revocation race) are untouched.
* **WebSocket supervision.** `jobSocket` reconnects on `4401` with the freshly
  rotated token (bounded, `WS_MAX_AUTH_RETRIES = 2`), stops immediately on
  `4403`, and backs off on transient closes (capped at 15 s, max 5 retries).
  If the channel genuinely cannot be established (e.g. a proxy dropping
  upgrade requests), it now **degrades honestly to polling** and reports
  `onStatus({ state: "polling", reason })`; `CaseDetail.tsx` shows a bilingual
  banner ("Live updates unavailable — … refreshed by polling").

No authentication rule was relaxed: the same `get_principal` / `get_scope` /
`require_roles` chain and the same 4401/4403 codes apply. The changes only make
the client renew/reconnect correctly and tell the truth when it cannot.

### Related root cause found and fixed

`frontend/src/lib/investigation.ts` (imported by `GraphPage.tsx` and
`InvestigationPage.tsx`) was **missing from a fresh checkout** because the
repository's root `.gitignore` line `lib/` (a Python-package rule) matched
`frontend/src/lib/`. Fixed with a scoped exception (`!frontend/src/lib/`), and
the module was rebuilt so it resolves on any checkout. Its 9 unit tests pass.

---

## 7. Test results

Environment (installed this session): Python 3.11 venv at `backend/.venv`
(`pip install -e ".[dev]"`), Node 22 with `npm install`.

* **Backend:** `213 passed` (full `pytest`; embedded profile — SQLite +
  NetworkX + local FS + in-process broker). Includes 4 new tests in
  `tests/test_analytics.py` for the Master Graph label/rel-type filters and
  the Temporal Graph endpoint (visual shape, empty-window honesty).
* **Frontend:** `19 passed` (`node --experimental-strip-types --test`):
  9 in `tests/investigation-format.test.mjs` (honest presentation helpers) and
  10 in `tests/auth-refresh.test.mjs` (single-flight refresh, session expiry,
  download replay, WebSocket 4401/4403/backoff/cap, signed-out no-connect).
* **Frontend typecheck:** clean (`npm run typecheck`).
* **Frontend production build:** clean (`npm run build`, 76 modules).

---

## 8. Files changed

Backend:

* `backend/app/analytics/temporal.py` — `build_temporal_graph`,
  `_bounded_neighbourhood`, `UNDATED_ATTRIBUTE_REL_TYPES`.
* `backend/app/services/graph_service.py` — `temporal_graph()`,
  `case_graph()` gains `labels` / `rel_types` filters.
* `backend/app/api/v1/graph.py` — `GET /graph/cases/{id}/temporal`; filter
  query params on the case-graph endpoint.
* `backend/app/ai/gateway.py` — `target_key`-scoped retrieval
  (`_neighbourhood_keys`), node/edge context caps.
* `backend/app/services/investigation.py` — parallel AI analysis
  (`asyncio.gather`), `ai_reasoning_target_count`.
* `backend/app/config.py` — `ai_max_context_nodes`, `ai_max_context_edges`,
  `ai_reasoning_target_count`.
* `backend/app/pipeline/extraction/deterministic.py` — unique sighting-event
  identity/labels; indentation fix on `LOCATED_AT` / `OWNS_VEHICLE` origin.
* `backend/app/synthetic_corpus/build_external.py` — **new** external corpus
  generator.
* `backend/CrimeLink_Synthetic_Corpus_v1/*` — regenerated corpus (v2),
  `SCHEMA.md` + `README.md` updated.

Frontend:

* `frontend/src/api/client.ts` — `ensureFreshToken`, proactive renewal in
  `apiInner`, supervised `jobSocket` (polling fallback + `onStatus`),
  `caseGraph` / `temporalGraph` helpers and wire types.
* `frontend/src/pages/GraphPage.tsx` — Person / Master / Temporal modes,
  master filters, temporal controls, timeline strip, shared Cytoscape renderer.
* `frontend/src/lib/investigation.ts` — presentation helpers (rebuilt; was
  missing from git due to the `.gitignore` bug).
* `frontend/src/pages/CaseDetail.tsx` — WebSocket polling-status banner.
* `frontend/src/i18n.ts` + `frontend/src/styles.css` — new labels and styles.
* `.gitignore` — scoped exception for `frontend/src/lib/`.
