# CrimeLink Investigation Workflow — Implementation Report

**Date:** 2026-09-04 · **Branch:** `arena/01a06bcb-crimelink` · **Task:** SIH 2026 PS SIH26189 — investigator-driven investigation system (person-centric graph, explicit gated workflow, evidence-backed findings, honest AI).

This report separates what is **implemented and verified by tests that actually ran** from what is **implemented but requires an external service**, and from what is **not implemented**. Nothing in the "verified" section is claimed without a command that was executed in this workspace.

---

## 1. IMPLEMENTED AND VERIFIED

### 1.1 Explicit 8-stage investigation workflow (PRD 21)

- `backend/app/services/investigation.py` — stage state machine (`STAGES`, `workflow_state`, `run_stage`). A stage runs only when every prerequisite is `COMPLETED`; otherwise `StageBlocked` → HTTP 409 with the blocking stage named. Failures are recorded (`status=FAILED`, real error text, attempt count) and **never** surface as "analysis complete".
- `backend/app/api/v1/investigation.py` — 7 REST endpoints (state, run-stage, findings, review, persons, network, person findings), wired into `app/api/router.py`, JWT + roles + jurisdiction scope as everywhere else.
- `backend/db` — `InvestigationStageRun` + `InvestigationFinding` tables; migration `backend/alembic/versions/20260904_e3b4c5d6f007_investigation_tables.py` (head). `alembic upgrade head` runs the full chain a5dcb473 → e3b4c5d6f007 cleanly and creates both tables.
- **Stage handlers re-run the real pipeline components** — no parallel fake path:
  - `process_data` → `orchestrator.process_document` per pending document (real S1–S6).
  - `extract_entities` → real `extract_deterministic` + real NLP provider, with a per-type inventory in **canonical wire labels** (`PERSON`, `PHONE`, `BANK_ACCOUNT`, `VEHICLE`, `LOCATION`, `ORGANIZATION`, `EVENT`).
  - `resolve_entities` → real `EntityResolver` (deterministic-first, confidence-aware; low-confidence matches become POTENTIAL_ALIAS review-queue proposals — human review preserved).
  - `build_relationships` → re-runs deterministic relation extraction (idempotent by provenance keys).
  - `build_graph` → verifies what the graph store ACTUALLY holds (`graph_backend`, nodes/edges persisted, per-label counts, graph version); **fails honestly** (HTTP 503 `service_unavailable`) if the graph database is unreachable, and HTTP 422 if the case graph has no persons yet.
  - `network_analysis` → real NetworkX centrality/communities/patterns (`orchestrator.run_nightly_patterns` machinery).
  - `ai_analysis` → calls the real AI gateway; **without a key it says exactly that** (`no_api_key_for_role_reasoning`) and completes with the deterministic analysis only.
  - `generate_findings` → the new deterministic findings engine (below), deduplicated by (type, entities), persisted, reviewable.

### 1.2 Deterministic, evidence-backed findings

- `backend/app/analytics/findings.py` — new engine:
  - `FINANCIAL_LINK` — requires an OWNS_ACCOUNT edge **and** repeated TRANSFER_TO edges (MEDIUM ≥ 2 transfers, HIGH ≥ 3), each transfer carrying amount/timestamp/channel/reference from the financial records. Neutral language ("pattern requiring review; not by itself evidence of an offence").
  - `FREQUENT_CONTACT` — ≥ 4 calls (HIGH ≥ 8) via real CALLED edges.
  - `HIGH_CENTRALITY` — top-5 betweenness persons, expressed as a rank with method.
  - Every finding stores `confidence` + `confidence_band` + `reason` + `method` + `entity_keys` + structured `evidence` (relationship pointers with `source_doc_ids`, analysis metrics). No fabricated suspicious behaviour; review = CONFIRMED/DISMISSED with note, actor recorded, audit-appended.

### 1.3 Person—OWNS→Account financial chain (the fixed data gap)

- `accounts.csv.holder_person_id` now flows end-to-end:
  - `backend/app/adapters/sources/synthetic_external.py` — account + bank_code joined into person-history rows.
  - `backend/app/pipeline/adapters/criminal_history.py` — `account`/`bank_code` field mapping + `normalize_account` + emission.
  - `backend/app/pipeline/extraction/deterministic.py` — person records emit a BANK_ACCOUNT entity and an **OWNS_ACCOUNT** edge (never fabricated: only what the source states; weaker third-party allegations remain `CONTROLS_ACCOUNT` by design).
  - `backend/app/domain/enums.py` — `OWNS_ACCOUNT` added to the relationship vocabulary; `canonical_label()` map so every surface (API, console) agrees on SCREAMING_CASE labels.
- **Verified live through REST** on the real 36-case synthetic corpus: `GET /cases/{id}/network/{person_key}?depth=2` returns the traversable chain `Person —OWNS_ACCOUNT→ Account —TRANSFER_TO(₹1,06,726.32, ref TX004081)→ Account ←OWNS_ACCOUNT— Person` (227 transfers, 104 bank accounts in one 2-hop neighbourhood).

### 1.4 Person-centric graph API

- `backend/app/services/graph_service.py` — `person_targets` (persons of a case ranked by connections) and `person_centric_network` (BFS depth 1–3, person-first edge priority, target row, per-layer/per-label/per-rel counts, honest `truncated` flag). The whole-case dump is no longer the investigation path.

### 1.5 Person-centric console (frontend)

- `frontend/src/pages/GraphPage.tsx` — rewritten: person target rail → typed neighbourhood (1/2/3-hop buttons, gated by which layers exist) → target-dominant rendering (PERSON largest via `LABEL_SIZE`, per-type colors, orange target ring, zoom-safe truncated labels) → per-type detail panels (person role/phone/account/plate; account + bank; vehicle plate/make; edge panels with transfer amount/timestamp/channel/reference and call counts/durations) → evidence pointers per node/edge → findings about the selected person → "set as investigation target" for any other person. The graph store actually in use (`embedded`/`neo4j`) is shown, not guessed.
- **Pre-existing graph analytics preserved inside the new layout** (no silent feature removal): the per-node influence explanation (`GET /graph/nodes/{key}/influence` — betweenness, PageRank, degree, case rank, plain-language summary, evidence documents) loads with the target and on any node tap, hidden honestly when unavailable; and temporal path search (`POST /graph/cases/{id}/paths`) is a collapsible panel over the canvas with From/To entity selects. Both verified live and in the jsdom render check.
- `frontend/src/pages/InvestigationPage.tsx` — new workspace: 8 stage rows with real status badges, locked-until-runnable Run buttons (409 message surfaced verbatim), the stage's real counters as subtitle, attempt count + duration, findings list with confidence bands, evidence entries rendered by their real kind (relationship / analysis), Confirm/Dismiss review, and a link to the review queue (POTENTIAL_ALIAS candidates → human review).
- `frontend/src/lib/investigation.ts` — the presentation logic as pure functions, unit-tested against the real backend payload shapes.
- `frontend/src/api/client.ts` — typed investigation endpoints (+ `ApiError` code/status preserved).
- Routing: `/cases/:caseId/investigation` (+ link from CaseDetail).

### 1.6 Honest failure surfaces

- `app/errors.py` — new `ServiceUnavailableError` (503) used when the graph store is down during `build_graph`.
- `_stage_process_data` now FAILS when zero documents could be processed (e.g. the IngestionJob bug this task found and fixed: `requested_by` column, not `user_id`) — the failure detail carried the backend's own message, which is how the bug was diagnosed.
- Frontend never shows a completed badge for a failed run; errors render with the backend text.

### 1.7 Tests and validation that were executed here

| Check | Command | Result |
| --- | --- | --- |
| Backend suite (incl. 11 new workflow e2e tests, WS-auth, AI-unavailable) | `.venv/bin/python -m pytest tests/ -q` | **209 passed, exit 0** (baseline 198 + 11) |
| Frontend type check | `npm run typecheck` | clean |
| Frontend unit tests | `npm test` | **19/19** (10 auth-refresh + 9 investigation-format) |
| Frontend production build | `npm run build` | ✓ |
| jsdom smoke against real API | `node smoke.mjs` | PASS, no console errors (authed + anonymous) |
| Route render check | jsdom, production bundle vs live API | workspace: 8 stages + findings render; graph: rail/target/depth/detail panels render |
| Live REST e2e | corpus ingest → 8 × POST run → persons → network d1/d2 → findings → review | all 8 stages COMPLETED on two real corpus cases; chain traversable; review persisted |
| Influence + temporal paths (restored) | `GET /graph/nodes/{key}/influence`, `POST /graph/cases/{id}/paths` against the live API | rank 1/800 with summary + 6 evidence docs; 1 chronologically coherent path with steps |
| jsdom route render check | production bundle vs live API | graph: rail, target, influence rank, path panel with populated selects; workspace: stages + review-queue link; zero console errors |
| Migration chain | `alembic upgrade head` (fresh SQLite) | a5dcb473 → e3b4c5d6f007 ✓ |
| Secret scan | `git diff` pattern scan | clean |

New backend tests (`backend/tests/test_investigation_workflow.py`) cover: gating order, stage 1 real processing, stage 2 inventory, stage 3 resolution, stage 4 ownership materialization, stage 5 persistence, stage 6 network analysis, stage 7 honest no-key AI, stage 8 findings + dedupe + review, findings-before-network-analysis 409, and failure-blocks-dependents with recovery.

---

## 1.8 Acceptance-evidence matrix (spec §26 areas)

| Requirement area | Evidence |
| --- | --- |
| Person is primary; selecting a person = target | `/persons` → `/network/{key}` flow; GraphPage target rail + re-target button; workflow e2e `test_stage_five_verifies_the_persisted_graph` |
| Target → connected entities → 2nd/3rd degree | depth 1–3 BFS `person_centric_network`; live d1 70 nodes → d2 116 nodes; 3-hop button gated by layer existence |
| No whole-dataset dumps in the investigation flow | GraphPage loads only the person network; whole-case view remains only on the legacy `/graph/cases/{id}` API, not the console flow |
| Semantically named relationships (OWNS/USES/CALLED/TRANSFERRED…) | extraction emits OWNS_ACCOUNT/OWNS_VEHICLE/USES_PHONE/CALLED/TRANSFER_TO (+ amounts); live chain printed with refs; never fabricated (only source-stated ownership) |
| Person→Account→Transfer→Account→Person traverses | verified live: `Ganesh Raju —OWNS→ ad77… —TRANSFER(₹1,06,726, TX004081)→ 0030… ←OWNS— person` |
| Normalization keeps canonical + original; confidence-aware resolution; low-confidence → human review | deterministic-first `EntityResolver`; POTENTIAL_ALIAS queue preserved (review_queue_pending counts in stage 3 output; Review page linked from workspace) |
| Explicit workflow; real buttons; next locked until prior COMPLETED | 409 `stage_blocked` verified over REST and in tests; workspace locks buttons via `blocked_by` |
| Honest failures; never "Analysis Complete" on failure | FAILED status + verbatim error in state; stage 1 now FAILS when 0 documents processed; build_graph 503 when store down (ServiceUnavailableError) |
| AI honesty (deterministic where appropriate; says when unavailable) | stage 7 `ai_available:false, reason:no_api_key_for_role_reasoning`; `test_ai_unavailable.py`; no mock AI anywhere |
| Findings evidence-linked, neutral, confidence band + reason, reviewable | findings engine spec §1.2; e2e review CONFIRMED persisted; neutral wording in narratives |
| Neo4j real when configured; availability reported | store port + `effective_graph_backend`; workspace/graph page show the actual backend; §2 below for the live-server caveat |
| PostgreSQL split / persistence of users, cases, files, jobs, entities, provenance, findings, audit | models + migration chain; embedded SQLite runs the same ORM code (§2 for PostgreSQL itself) |
| Preserve auth/security (JWT, WS case-authz, refresh, jurisdiction scoping) | untouched; `test_ws_auth.py` (10) green inside the 209; endpoints reuse `get_scope`/`require_roles` |
| No fake demo data in flow | demo corpus passes through the real adapter + six-stage pipeline (verified over REST) |
| Tests across data/graph/AI/workflow-gating/frontend | 209 backend + 19 frontend; table above |
| Run suites + typecheck + build + smoke | all executed, results in §1.7 |

---

## 2. IMPLEMENTED BUT REQUIRES EXTERNAL SERVICE

These paths are implemented against real clients and were exercised here only through the embedded adapters (identical domain code, different adapter):

- **Neo4j graph backend** — `neo4j.py` (real driver, MERGE + constraints) selected by `CRIMELINK_GRAPH_BACKEND=neo4j` + `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD`. All graph code goes through the store port, so the workflow, person-centric network and findings run unchanged. Not verified against a live Neo4j in this workspace (no server available); the embedded NetworkX store verified the same code paths.
- **PostgreSQL** — all persistence is SQLAlchemy; SQLite verified, PostgreSQL needs `CRIMELINK_DATABASE_URL`. The new migration is Alembic-standard and applies (verified on SQLite).
- **AI analysis with a real key** — stage 7 calls the gateway when `CRIMELINK_AI_REASONING_API_KEY` is set; without it the stage reports the honest fallback (verified both ways in tests: `test_ai_unavailable.py`, workflow stage-7 test).
- **OCR** — intentionally NOT implemented. A scanned/image-only PDF fails with an explicit "OCR required"-class error rather than pretending to read it. Adding OCR remains future work (task spec allowed honest absence).
- **MinIO/S3 object store, Celery broker** — embedded local-store/in-process-broker verified; adapters swap by env.

## 3. NOT IMPLEMENTED

- OCR for scanned documents (explicit honest failure instead).
- Geo/spatial analytics, multimedia analysis, and any AI-generated narrative beyond the gateway's role (no mock output anywhere).
- Cross-case entity pivot UI (the data model supports it; no screen yet).

## 4. Files changed (this task)

**Backend:** `app/services/investigation.py` (new), `app/api/v1/investigation.py` (new), `app/analytics/findings.py` (new), `alembic/versions/20260904_e3b4c5d6f007_investigation_tables.py` (new), `app/services/graph_service.py`, `app/db/models.py`, `app/domain/enums.py`, `app/errors.py`, `app/pipeline/extraction/deterministic.py`, `app/pipeline/adapters/criminal_history.py`, `app/adapters/sources/synthetic_external.py`, `app/api/router.py`, `tests/test_investigation_workflow.py` (new).
**Frontend:** `src/pages/InvestigationPage.tsx` (new), `src/lib/investigation.ts` (new), `src/pages/GraphPage.tsx` (person-centric rewrite), `src/api/client.ts`, `src/i18n.ts`, `src/App.tsx`, `src/pages/CaseDetail.tsx`, `src/styles.css`, `tests/investigation-format.test.mjs` (new).

## 5. How to run

```bash
# backend tests (embedded profile: SQLite + NetworkX, no external services)
cd backend && .venv/bin/python -m pytest tests/ -q

# frontend
cd frontend && npm ci && npm run typecheck && npm test && npm run build

# live demo (embedded)
cd backend && CRIMELINK_PROFILE=embedded CRIMELINK_DATA_DIR=/tmp/d/data \
  CRIMELINK_OBJECT_STORE_DIR=/tmp/d/objects .venv/bin/uvicorn app.main:app --port 8000
# then: admin/synthetic/ingest (adapter=external, root=backend/CrimeLink_Synthetic_Corpus_v1)
#       and drive /cases/{id}/investigation/... — or use the console at /cases/{id}/investigation
```

## 6. Known limitations

- Corpus-scale processing is slow by design on the inline broker (~15–30 s/document through the full six-stage pipeline); stages process up to 40 documents per run and report honest remainders.
- The workflow run endpoints are synchronous REST calls; for very large cases a job/progress surface would improve UX (the state endpoint already exposes RUNNING/FAILED/attempt data).
- `ai_analysis` with a key is implemented but unverified against a paid provider in this workspace (no key available; the no-key path is what was verified).
- Person-merge (POTENTIAL_ALIAS → confirm) flows through the pre-existing review queue UI; the workspace now links to it, but the alias queue is not embedded in the workspace page itself.
