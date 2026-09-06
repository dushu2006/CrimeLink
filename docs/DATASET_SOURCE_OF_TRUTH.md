# CrimeLink — one dataset, one truth

**Branch:** `arena/01a074ca-crimelink` (based on `main` @ `d005dcb`) · nothing committed yet
**Verified:** live, end to end, against the bundled `CrimeLink_Synthetic_Corpus_v1` and against
uploads made through the browser's own endpoints.

---

## 1. What was wrong

The application had no concept of *a dataset*. It had a corpus that someone had once ingested,
whose rows sat in `var/data/crimelink.db`, whose nodes sat in `var/data/graph.json`, and whose
documents sat in `var/objects/`. Nothing tied those three together, so nothing could take them
apart again. Uploading new data added rows next to the old ones; the graph was never re-projected;
every page read "everything in the store", which is why a browser refresh always brought the old
world back.

Three specific faults produced the symptoms in the brief:

| Symptom | Cause |
| --- | --- |
| New upload does not appear anywhere | `synthetic_external.records_from_scan()` yielded **0 records** for a scan that found 1038 files. The ingest path silently produced nothing. |
| Old data survives a refresh | No row, node or document carried a dataset identity, so nothing could be scoped to, or purged by, a dataset. |
| Graph shows the old case network | The graph was written once and never derived again. Rebuilding meant *adding*, not *replacing*. |

Everything below replaces that with a pipeline whose output is a *derivation* of one identified
dataset, so replacing the dataset replaces the application's contents.

---

## 2. The architecture

```
upload (files | folder | ZIP)
   │
   ├─ discovery.py     walk, unpack archives, hash, classify kind (table/text/document)
   ├─ readers.py       CSV · XLSX · JSON · JSONL · TXT · PDF · DOCX  → Table(columns, rows, row_numbers)
   ├─ schema_map.py    columns → canonical fields, with evidence and confidence
   ├─ normalize.py     rows → CanonicalEntity / CanonicalRelationship (+ provenance on every one)
   ├─ pipeline.py      stages, persistence, documents, cases, activation
   ├─ registry.py      dataset lifecycle + the visibility rule every read obeys
   └─ graph_build.py   canonical layer → graph nodes/edges, stamped with dataset_id
```

**One writer, one reader, one rule.**

* Everything the user sees is derived from `dataset_entities` / `dataset_relationships` for exactly
  one dataset. No page holds fixtures — there were none in the frontend to begin with, and none
  were added.
* Every read applies `registry.visibility_filter(session, Model)`:
  `dataset_id IS NULL OR dataset_id == <active dataset>`. Pre-existing untagged rows stay visible;
  a replaced dataset disappears from every query in one place, not in twenty.
* Every graph node and edge carries `dataset_id`, `canonical_id`, `entity_type`, so a rebuild is
  surgical: `purge_dataset(dataset_id)` then re-project. `purge_other_datasets(keep)` evicts
  anything the active dataset did not put there.

### Dataset lifecycle

`Dataset(id, name, version, status, created_at, activated_at, is_active)` with stages
`UPLOADED → VALIDATING → NORMALIZING → INGESTING → BUILDING_RELATIONSHIPS → BUILDING_GRAPH →
INDEXING → READY | FAILED`. Exactly one dataset is active; activation also starts a graph rebuild,
because tables and graph must never describe different worlds.

### Provenance

Every entity, relationship, document and node records
`dataset_id, source_file, source_path, source_type, source_row, source_page, source_sheet,
extraction_timestamp`. The source resolver reads the **dataset manifest** (`dataset_files`), so a
file uploaded as `data/sightings.csv` resolves exactly as well as one at
`operational/vehicle_sightings.csv`. No folder name is required anywhere.

---

## 3. The four defects found by testing (and fixed)

These were not in the brief; they were found by running the thing.

### 3.1 96 % of the graph was unreachable

After a clean import the store held 5850 nodes — and **5614 of them had `case_ids: []`**. Search,
jurisdiction scoping, case graphs and the timeline all filter on case membership, so those nodes
existed and were visible to nobody. Search for *any* term returned zero results.

The corpus explains why: its operational layer is a **registry** (450 persons, 480 accounts, 650
phones, 230 vehicles…), and only `case_entities.csv` (250 rows) links anything to a case. Most
records genuinely belong to no investigation.

`graph_build._case_links` now assigns membership in three passes, in decreasing order of evidence:

1. **Direct** — rows that state it (`PERSON_00042 → CASE_0007`).
2. **Belonging** — one hop over `CASE_MEMBERSHIP_RELS` (owns/uses/resides/member-of/evidence…).
   A suspect's phone joins the suspect's case. Transactional edges (`CALLED`, `TRANSFER_TO`,
   `MESSAGED`, `TRAVELLED_TO`) are deliberately **excluded**: they are what an investigation
   *discovers*, and treating them as membership put 919 nodes in the median case and 21 000 edges
   on a case canvas.
3. **Container** — every dataset gets a container case ("… (unassigned records)") that holds the
   registry. Those records are searchable and jurisdiction-scoped without pretending they are
   part of a case.

Result: **0 orphans**, median case 65 nodes, a case canvas of ~113 nodes / ~860 edges.

### 3.2 A broken header row was manufacturing people

`documents/financial_summaries/FIN_*.xlsx` carries **six header names above eight columns of
data**, shifted: the column headed `person_id` holds dates, `amount` holds `PERSON_00450`,
`account_id` holds `TXN_005403`. The mapper believed the headers, so the People page filled with
**643 "people" named `2022-01-29`** and the Financial page with **762 accounts named `TXN_…`** —
entities no row in the dataset ever asserted.

`schema_map` now treats a header as a *claim* and the values as *evidence*:

* `column_agreement(canonical, values)` scores whether the values support the field (dates for
  date fields, parsable amounts — `"27.87 lakh"` included — for money, non-dates for identifiers).
  Below 50 % the header loses and the column is mapped from its data.
* `SchemaLexicon` learns the dataset's **own key vocabulary** from its self-consistent tables
  (`ACCT_ → ACCOUNT.id`, `TXN_ → TRANSACTION.id`, …) and may outvote a header that claims a
  different entity's primary key. Role columns (`from_person`, `owner_id`, `observed_driver`) are
  never flattened — they say more than the prefix does. The lexicon is per dataset and never shared.
* Because import order is alphabetical, not pedagogical, `pipeline` runs **two passes**: tables
  whose headers are contradicted, or which key onto prefixes not yet explained, are re-mapped after
  every table has been read.
* Every such decision is recorded per file and shown in Administration (§4).

Result: the entity counts now reconcile **exactly** with the source registry —
450 persons + 60 officers = 510, 480 accounts, 650 phones + 110 devices + 180 emails = 940,
230 vehicles, 881 documents + 260 evidence = 1141.

### 3.3 Placeholder records posing as organisations

`transactions.csv` has a `counterparty` column that sometimes holds a person key. The extractor
that met it first created an **organisation named `PERSON_00379`** — 188 of them.
`Normalizer.reconcile_identifiers()` now runs after every table is read, when the whole id space is
known, and folds a placeholder (an entity whose name is nothing but its own key) into the
differently-typed record that carries the same key and a real name, rewiring its relationships.
188 fake organisations became references to the real people.

### 3.4 Headerless JSON was silently dropped

`documents/cdr/*.json` stores rows as arrays of arrays under a `rows` key. The JSON reader only
understood arrays of objects, so 25 files × 150 call records were read as two metadata cells and
discarded. `readers._table_from_matrix` now gives headerless matrices positional columns, and
`_PAIRED_FIELDS` recognises that two columns of the same key type in one row are a directed pair
(caller/callee, from/to account) — so those files now classify as CDR and merge into the existing
call edges instead of vanishing.

---

## 4. What the operator sees

* **Administration → Dataset**: import (files, folder, ZIP), live stage progress, the dataset list
  with entity/relationship/case counts, Activate, Build/Rebuild graph.
* **Administration → Schema mapping review** (new): every table the importer was unsure about, with
  the reason in words — *"Column 'person_id' is headed as PERSON.id but its values do not match
  (0% agreement); mapped from the data instead"* — plus unmapped columns, per-file **Accept**, and
  **Accept all**. Sign-off is recorded (`mapping_accepted_at/by`).
* **AI health** panel: provider, model, connectivity, last test, request ids on failures.
* Progress is reported, never simulated: the console watches the job over a WebSocket and says so
  when it has fallen back to polling.

---

## 5. Replacement, proven

Uploading one CSV as a new dataset (through the same multipart endpoint the browser uses):

```
BEFORE  active: CrimeLink Synthetic Corpus | cases: 61 | graph: 4237 nodes
        search 'Harish Varma': ['Harish Varma']
AFTER   active: Kerala District Register   | cases: 1  | graph: 7 nodes
        old bookmarked case URL -> 404 "This case belongs to a dataset that is no longer
                                        active. Activate that dataset in Administration…"
        search 'Meera': ['Meera Nair']
        search 'Harish Varma' (replaced dataset): no results
        datasets: [('Kerala District Register', READY, active), ('CrimeLink Synthetic Corpus', READY, inactive)]
```

Re-activating the corpus rebuilt the graph (job SUCCEEDED, 4176 nodes, 35 183 edges, 61 cases) and
restored all 61 cases. A browser refresh cannot resurrect anything: there is no client cache of
dataset content, and the server filters by the active dataset.

---

## 6. Jobs and the WebSocket

`POST /api/v1/datasets/import` → `{job_id}` immediately. The job is watched at
`WS /api/v1/jobs/ws/job/{job_id}?token=…` (first frame is a full `job_snapshot`; close codes 4401
unauthenticated / 4404 unknown job / 1000 terminal), with `GET /api/v1/datasets/jobs/{job_id}`
polling as an automatic fallback — same payload either way. `vite.config.ts` proxies with
`ws: true`; `frontend/nginx.conf` gained the `Upgrade` / `Connection: upgrade` block it was missing
in production.

Verified through the dev proxy — the exact path a browser takes:

```
rebuild job: 7f3e8bed-… · WebSocket upgraded through the Vite proxy
  [  4%] PURGING  Removing the previous projection of this dataset
  [100%] COMPLETED
frames: 47 · status: SUCCEEDED · {"nodes_written": 4176, "edges_written": 35183, "cases": 61}
```

---

## 7. Commands

```bash
# environment (once)
python3 -m venv .venv && .venv/bin/pip install -e "backend[dev]"
cd frontend && npm install && cd ..

# everything at once (FastAPI :8000 + Vite :5173)
python run.py

# or separately
cd backend && CRIMELINK_PROFILE=embedded ../.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
cd frontend && npm run dev -- --host 0.0.0.0

# first admin (fresh database)
curl -X POST localhost:8000/api/v1/auth/setup -H 'content-type: application/json' \
  -d '{"badge_number":"AP-ADMIN-01","full_name":"Admin","password":"Crimelink!Demo2026",
       "station_id":"HQ","jurisdiction_id":"SYN-DEV"}'

TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"badge_number":"AP-ADMIN-01","password":"Crimelink!Demo2026"}' | jq -r .access_token)

# ingest a dataset already on the server's disk
curl -X POST localhost:8000/api/v1/datasets/import/path -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"path":"backend/CrimeLink_Synthetic_Corpus_v1","name":"CrimeLink Synthetic Corpus","activate":true}'

# upload instead (files / folder / ZIP — same endpoint the console uses)
curl -X POST localhost:8000/api/v1/datasets/import -H "Authorization: Bearer $TOKEN" \
  -F files=@people.csv -F paths=people.csv -F name="My dataset"

# watch it
curl -s localhost:8000/api/v1/datasets/jobs/$JOB -H "Authorization: Bearer $TOKEN"

# activate / rebuild the graph and indexes
curl -X POST localhost:8000/api/v1/datasets/$DATASET/activate      -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/api/v1/datasets/$DATASET/graph/rebuild -H "Authorization: Bearer $TOKEN"

# schema mappings that want a human
curl -s "localhost:8000/api/v1/datasets/$DATASET/mappings?needs_review=true" -H "Authorization: Bearer $TOKEN"
curl -X POST localhost:8000/api/v1/datasets/$DATASET/mappings/accept -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"file_ids":[]}'

# tests
cd backend && ../.venv/bin/python -m pytest -q
cd frontend && npx tsc --noEmit && npm run build
```

**AI provider.** No key is present in this workspace, so the gateway reports
`configured: false` and the UI says so instead of failing mysteriously. To verify against NVIDIA:

```bash
CRIMELINK_AI_API_KEY=nvapi-… python run.py
curl -X POST localhost:8000/api/v1/ai/health/test -H "Authorization: Bearer $TOKEN"
```

Keys are read from the environment only (`CRIMELINK_AI_API_KEY`, or per role
`CRIMELINK_AI_<ROLE>_API_KEY`); none appear in frontend code.

---

## 8. Test results

```
backend:  ../.venv/bin/python -m pytest -q   →  332 passed, 0 failed   (EXIT=0)
frontend: npx tsc --noEmit                    →  clean
          npm run build                       →  built in 3.5s
```

New suites: `test_schema_inference.py` (9), `test_dataset_upload_formats.py` (formats A–F),
`test_dataset_replacement.py` (8), `test_dataset_jobs_ws.py`, `test_ai_case_ask.py`,
`test_ai_provider_roundtrip.py`, `test_person_graph_depth.py`.

Live checks against the running stack (through the Vite proxy, not the API directly):
import 1038 files → 4176 nodes / 35 183 edges / 61 cases in ~50 s · 0 orphan nodes ·
replacement · re-activation · WebSocket job stream · Cases, Case detail, Documents, Timeline,
Case graph, Entities by type, Search, Admin summary, Datasets, AI health.

---

## 9. Changed files

**New — the dataset layer**

```
backend/app/datasets/{__init__,discovery,readers,schema_map,normalize,registry,graph_build,pipeline}.py
backend/app/services/dataset_jobs.py
backend/app/api/v1/datasets.py
frontend/src/components/DatasetConsole.tsx
backend/tests/{test_schema_inference,test_dataset_upload_formats,test_dataset_replacement,
               test_dataset_jobs_ws,test_ai_case_ask,test_ai_provider_roundtrip,
               test_person_graph_depth}.py
```

**Modified**

```
backend/app/db/models.py                 Dataset, DatasetFile, DatasetEntity, DatasetRelationship,
                                         DatasetJob; dataset_id on Case/CaseDocument/SourceReference;
                                         mapping_notes / mapping_accepted_at / mapping_accepted_by
backend/app/services/cases.py            active-dataset visibility on every case read
backend/app/api/v1/database.py           the same filter on the admin inspector
backend/app/synthetic_corpus/generate.py admin summary counts the active dataset
backend/app/adapters/graph/embedded.py   purge_dataset, purge_other_datasets, search_text
backend/app/adapters/graph/neo4j.py      the same, in Cypher
backend/app/ports/stores.py              purge_dataset on the port
backend/app/services/graph_service.py    hop depth, scope checks
backend/app/api/v1/jobs.py               WS /jobs/ws/job/{job_id}
backend/app/api/v1/ai.py, ai/{gateway,router,schemas}.py, config.py, main.py
                                         AI contract, request ids, env-only keys
backend/app/api/v1/investigation.py, api/router.py
frontend/src/api/client.ts               dataset API, watchDatasetJob (WS → poll), mappings
frontend/src/pages/{Admin,CaseDetail,GraphPage}.tsx, styles.css, nginx.conf
```

---

## 10. Known limits, stated plainly

* `FIN_*.xlsx` recovers its identifiers, dates and accounts, but two of its columns
  (`counterparty` holding a category, `description` holding the amount) cannot be resolved with
  confidence from an eight-column row with six wrong headers. Those files are flagged for review
  rather than guessed at — inventing financial edges would be worse than omitting them.
* The container case is intentionally large (2702 records for this corpus). Its canvas truncates at
  2000 nodes and says so; it is a registry, not an investigation.
* Neo4j and Postgres code paths are written and unit-tested, but this workspace runs the embedded
  profile (SQLite + NetworkX); they have not been exercised against live servers here.
* No AI key is configured in this workspace, so the AI answer path is verified against a local
  OpenAI-compatible stub, not NVIDIA.
