# Production readiness fix — technical report

Branch: `arena/01a107e5-crimelink` (from `main` @ `0a49617`, PR #53 already merged).
Scope: the three reported production problems — corpus/graph quality, evidence
provenance truthfulness, and import state surviving navigation/refresh — plus
the tests and deployment notes needed to verify them.

No secrets appear in this document. Values shown as `<...>` are placeholders.

---

## 1. Problem 1 — the hosted importer produced a worse graph than `python run.py`

Reported symptoms: the bundled corpus collapsed into one case; phones and
accounts floated away from their owners; `CP_01`-style identifiers appeared as
disconnected or falsely-typed organisations; relationships were thin compared
to a local run.

## 2. Problem 1 — root causes

1. **Row identifiers stole foreign keys.** In relation sheets such as
   `case_members.csv` (`case_member_id,case_id,person_id,role`) the row's own
   id matched the "…id" token heuristics and won the `CASE.id` /
   `PERSON.id` slots, leaving the real `case_id` unmapped. Every membership row
   then minted a phantom case named after the row.
2. **Document rows were modelled as graph actors.** Index/manifest tables were
   ingested as entities and produced `HAS_DOCUMENT`/`DOCUMENT` records, which
   both duplicated case information and generated duplicate edge keys
   (`IntegrityError` on `dataset_relationships.edge_key`).
3. **Sightings stated by plate were dropped.** `_sightings()` resolved only
   `VEHICLE.id`, but CCTV logs map the `vehicle` column to
   `VEHICLE.registration`, so every CCTV row that named a plate produced no
   relationship at all.
4. **Bare references outranked the dataset's own identifiers.** When a ledger
   row mentioned `100000000001` before the register was read, the merged
   entity kept the value-derived id (`~hash`/plate) instead of the register's
   `AC0001`/`V001`, and `CP_01`-style opaque codes could be minted as
   organisations or left as disconnected nodes.
5. **Absence markers became people.** A CCTV sheet whose subject column reads
   `DATA_GAP` (an interrupted recording) was imported as a `PERSON`.
6. **Case membership was attributed by the wrong evidence.** Records that a
   case's own file named (a plate in its CCTV log, an account in its bank
   statement) were not attributed to that case when the record itself was
   described in a register elsewhere; the only membership that did happen came
   from one hop over `SEEN_AT`, which made every case's shared camera point
   drag every vehicle ever filmed there into that case (measured: C101
   absorbed 21 vehicles).

## 3. Problem 1 — fixes

| Fix | Where |
| --- | --- |
| Entity-key/row-id aware column mapping (a relation row id never claims the FK; `owner_person_id`/`holder_person_id` belong to `PERSON.id`; a person register stays a person table; `document_id,case_id,file_path` classifies as an index) | `app/datasets/schema_map.py` |
| Document indexes stay provenance metadata; no DOCUMENT entities, no `HAS_DOCUMENT`; mention rows deduplicated per `(entity, case)` with aggregated filenames/doc ids | `app/datasets/pipeline.py` (`_is_document_index_table`, `_ingest_documents`) |
| Sighting resolution falls back to `VEHICLE.registration` | `app/datasets/normalize.py` (`_sightings`) |
| The dataset's own identifier wins a merge over a value-derived id (`described` preference in the union-find); unknown opaque codes stay unknown with a warning, and resolve to the defined record when the dataset defines one; a name-only mention folds into the identified person of that name | `app/datasets/normalize.py` (`_link`, `_claim`, `_register`, `_register_person`, `_name_remap`, `_reference_entity`) |
| Missing-value markers (`DATA_GAP`, `UNKNOWN`, `N/A`, `MISSING_*`, …) never become entities; each is reported once, spelled as the file spells it | `app/datasets/normalize.py` (`is_missing_value`, `_note_missing`) |
| Entities a case file's rows name join that case (relationship endpoints attributed per source file); `SEEN_AT` is removed from the membership-propagation set so co-observation at a shared place cannot contaminate a case | `app/datasets/pipeline.py` (`endpoints_by_file`), `app/datasets/graph_build.py` (`CASE_MEMBERSHIP_RELS`) |
| Single-real-case datasets absorb unclaimed records; every other dataset keeps the container case, so nothing is left outside every case | `app/datasets/graph_build.py` |

Measured on the bundled corpus (194 files, 160 documents, real pipeline):

```
entities     227   ACCOUNT 42  ADDRESS 42  PERSON 42  PHONE 42  VEHICLE 30
                   LOCATION 11  CASE 10  ORGANIZATION 8      (corpus goldens exactly)
relationships 365  TRANSFER_TO 66  CALLED 50  SEEN_AT 47  OWNS_ACCOUNT 42
                   RESIDES_AT 42  USES_PHONE 42  INVOLVED_IN 41
                   OWNS_VEHICLE 30  MEMBER_OF 5
ownership     USES_PHONE 42/42   OWNS_ACCOUNT 42/42   OWNS_VEHICLE 30/30
graph         227 nodes; 0 nodes outside every case
case members  ALL 60 · C101 34 · C102 29 · C103 22 · C104 29 · C105 17
              C106 23 · C107 23 · C108 17 · C109 17 · C110 23
              (C101 owns 6 vehicles, not the 21 it absorbed before the fix)
```

Changes isolated to their own measurements: `SEEN_AT` 20 → 47 (the 27 CCTV
sightings that were silently dropped now exist); `PERSON` 43 → 42 (the
`DATA_GAP` marker is no longer a person, and is reported as a warning
instead); `MENTIONED_IN` 100 → 160 (case files now claim the records they
name); vehicle/phone/account ids are the dataset's own (`V001`, `PH0001`,
`AC0001`) rather than derived hashes.

## 4. Problem 2 — root cause

Production pointed `CRIMELINK_MINIO_ENDPOINT` at the Compose service name
(`minio:9000`), which only resolves inside the Docker network. Every byte read
failed with `Failed to resolve 'minio'`, and the evidence panel rendered that
as `record_available=false`, `integrity_hash_matches=false` — i.e. an outage
was displayed as an allegation that the record was missing or corrupt.

## 5. Problem 2 — fixes and the truth semantics now published

* `Settings.object_store_endpoint_problem` names the variable that is wrong
  when the production endpoint is unset or a Compose-only hostname
  (`app/config.py`).
* `Container.object_store` refuses to build a client that can only fail: for
  the `minio` backend it raises `DependencyUnavailableError` with that message
  (HTTP 503 `dependency_unavailable`), every time, uncached
  (`app/container.py`).
* Provenance now distinguishes three facts instead of two
  (`app/services/documents.py`):

| `file.storage_status` | when | `file.available` | `record_available.ok` | hash |
| --- | --- | --- | --- | --- |
| `no_key` | no storage key recorded | `false` | `false` | `null` |
| `available` | bytes were really read | `true` | `true` | compared |
| `missing` | storage answered "no such object" | `false` | `false` | `null` |
| `unavailable` | storage unreachable / not configured | `null` | `null` (`?`) | `null` |
| `error` | any other read failure | `null` | `null` (`?`) | `null` |

* The investigator UI renders the third state as `?` with the warning palette
  and names it in the detail list, so "could not be checked" is never shown as
  "failed" (`frontend/src/components/investigator/EvidenceDrawer.tsx`,
  `styles.css`).
* Stored evidence still uses dataset-scoped keys with read-back size + SHA-256
  verification before activation, and production never falls back to bundled
  files (PR #53 direction preserved).

## 6. Problem 3 — root cause

The running import existed only in the tab that started it. A refresh lost the
progress, the console could start a duplicate import over the top of the first,
and a job whose process died sat at `RUNNING` forever, blocking every later
import.

## 7. Problem 3 — fixes

* `GET /api/v1/datasets/jobs/current` returns the live import job (or `null`)
  from the database — the authoritative source (`app/services/dataset_jobs.py`,
  `app/api/v1/datasets.py`).
* Both import endpoints refuse a second concurrent import with `409` carrying
  the running job's id.
* A `RUNNING`/`QUEUED` job left behind by a restart is reported as `FAILED`
  with `interrupted_by_restart`, so the queue clears instead of blocking.
* The console hydrates from that endpoint on mount, resumes polling/websocket
  watching, and short-circuits duplicate `upload()`/`activate()` actions
  (`frontend/src/components/DatasetConsole.tsx`, `api/client.ts`).
* Import failures are only `FAILED` for a real backend failure; the message no
  longer repeats the exception type.

## 8. Files changed

Backend: `app/config.py`, `app/container.py`, `app/api/v1/datasets.py`,
`app/services/dataset_jobs.py`, `app/services/documents.py`,
`app/datasets/{schema_map,normalize,pipeline,graph_build}.py`.
Frontend: `src/api/client.ts`, `src/components/DatasetConsole.tsx`,
`src/components/investigator/EvidenceDrawer.tsx`, `src/styles.css`.
Tests: new `tests/test_dataset_identity_and_case_scope.py`,
`tests/test_dataset_job_hydration.py`,
`tests/test_object_store_configuration.py`; extended
`tests/test_provenance_checks_can_fail.py`.

## 9. Migrations

**None.** All fixes are code/configuration changes over the existing schema.
No table, column or index was added, removed or renamed, and no data was
rewritten; existing datasets keep their rows.

## 10. API changes

| Endpoint | Change |
| --- | --- |
| `GET /api/v1/datasets/jobs/current` | **new** (ADMIN): `{"job": <job row> \| null}` |
| `POST /api/v1/datasets/import`, `…/import-path` | now `409 ConflictError` while an import runs (response names the running job) |
| Document provenance payload | adds `file.storage_status`; `checks.record_available` adds `state` and its `ok` becomes tri-state (`true`/`false`/`null`) |

No endpoint was removed or renamed; all changes are additive.

## 11. Storage / configuration changes

* Production now validates the object-store endpoint at the point of use and
  fails with an actionable 503 instead of reporting missing evidence.
* No new required variables were introduced. `.env.vercel.example` already
  documents the durable endpoint aliases (`CRIMELINK_MINIO_*`, `S3_*`,
  `AWS_*`), `CRIMELINK_MINIO_SECURE=true`, and the three buckets.
* Host-native execution is untouched: `python run.py` rewrites a Compose
  hostname to `localhost` before the check runs, so the guard never fires
  locally (covered by a test).

## 12. Tests added

35 new tests (plus one updated contract assertion):

* `tests/test_dataset_identity_and_case_scope.py` (18) — column mapping that
  keeps FKs with their entities; account/phone identity folding; undefined
  `CP_01` stays unknown while a defined `CP_01` resolves; absence markers are
  not entities (and are reported); a case file's statement rows join the case;
  no node outside every case; documents are evidence, not graph actors; a
  shared camera point does not merge two cases.
* `tests/test_dataset_job_hydration.py` (7) — `/jobs/current` hydration,
  `null` once finished, 409 on a duplicate import carrying the running job id,
  zombie `RUNNING` → `interrupted_by_restart`, hydration == polling payload,
  non-import jobs do not block imports.
* `tests/test_object_store_configuration.py` (9) — the production endpoint
  check (Compose hostname / unset / durable / host context), the container's
  refusal (raises twice, not cached), local backend unaffected, readiness
  degrades without failing the request, and an import whose bytes cannot be
  stored ends `FAILED` and never becomes the active dataset.
* `tests/test_provenance_checks_can_fail.py` — the per-check contract now
  allows the documented `state` key, and an unreachable store renders
  `record_available.ok = null` / `state = unavailable` with `hash_matches`
  unproven while the other checks keep their computed values.

## 13. Full test results

```
cd backend && .venv/bin/python -m pytest -q -p no:cacheprovider
1266 tests collected
13 failed, 0 errors   (1253 passed)
npx tsc -b (frontend) → exit 0
```

Focused suites (`test_dataset_identity_and_case_scope`,
`test_dataset_job_hydration`, `test_object_store_configuration`,
`test_provenance_checks_can_fail`, `test_schema_inference`,
`test_structure_agnostic`, `test_synthetic_external`,
`test_dataset_replacement_lifecycle`, `test_minio_error_semantics`,
`test_builtin_corpus_import`, `test_domain`) all pass.

## 14. Baseline vs new failures

| | Baseline (post-PR-#53) | Now |
| --- | --- | --- |
| Failures | 14 | **13** |
| Composition | 13 pre-existing + 1 stale scratch file | the same 13 pre-existing only |

The 13 are environment/legacy failures unrelated to this work and unchanged:
`test_ai_interactive_budget` (1), `test_ai_stream_and_triage` (2),
`test_data_integrity_audit` (2), `test_dataset_jobs_ws` (1),
`test_demo_v2_data_quality` (4), `test_demo_v2_relationship_network` (2),
`test_runtime_context` (1). No new failure was introduced; the temporary
scratch probes used during development were deleted.

## 15. Required Vercel environment variables

```
CRIMELINK_PROFILE=production
CRIMELINK_ENVIRONMENT=production
CRIMELINK_RUNTIME_CONTEXT=production
CRIMELINK_DEBUG=false
CRIMELINK_SECRET_KEY=<unique 32+ character secret>
CRIMELINK_CORS_ORIGINS=https://<your-vercel-domain>
CRIMELINK_TRUSTED_HOSTS=*.vercel.app,<your-custom-domain>
CRIMELINK_DATA_DIR=/tmp/crimelink-data            # transient workspace only
CRIMELINK_BUILTIN_DATASET_AUTO_IMPORT=true
CRIMELINK_POSTGRES_DSN=postgresql+asyncpg://<user>:<password>@<host>:<port>/<database>
CRIMELINK_POSTGRES_DSN_SYNC=postgresql+psycopg2://<user>:<password>@<host>:<port>/<database>
CRIMELINK_NEO4J_URI / _USER / _PASSWORD / _DATABASE
CRIMELINK_NEO4J_GDS_ENABLED=false
CRIMELINK_BROKER_BACKEND=celery                     # or inline for a pre-seeded demo
CRIMELINK_REDIS_URL / CRIMELINK_CELERY_BROKER_URL / CRIMELINK_CELERY_RESULT_BACKEND
CRIMELINK_MINIO_ENDPOINT / _ACCESS_KEY / _SECRET_KEY / _SECURE=true
CRIMELINK_MINIO_BUCKET_DOCUMENTS / _DERIVED / _AUDIT_ANCHOR
```

No secret is committed; the template is `.env.vercel.example`.

## 16. Neo4j configuration

* `CRIMELINK_NEO4J_URI=bolt+s://<host>:<port>` (Aura) or `bolt://<host>:7687`
  (self-managed). With Aura, the user and database default to the instance id
  in the URI when they are not set explicitly.
* `CRIMELINK_NEO4J_PASSWORD` must not be the Compose placeholder; production
  refuses to boot with it.
* `CRIMELINK_NEO4J_GDS_ENABLED=false` unless the Graph Data Science plugin is
  installed; graph construction never silently disables itself, and an
  unreachable graph degrades case metadata instead of failing the API
  (documented failure table preserved).

## 17. Object-storage configuration

* `CRIMELINK_MINIO_ENDPOINT` must be a **durable, deployment-reachable**
  S3-compatible endpoint (`<host>:<port>`, scheme optional; `https` URLs are
  normalised). `minio`, `postgres`, `redis`, `neo4j` — Docker Compose service
  names — are rejected in production with a message naming the variable.
* `CRIMELINK_MINIO_SECURE=true`; keys and the three buckets as above. AWS
  style aliases (`S3_*`, `AWS_*`) are accepted.
* Written keys are dataset-scoped; PR #53's read-back size + SHA-256
  verification and the ban on production filesystem fallback are unchanged.

## 18. Deployment steps

1. Set the variables above in the Vercel project (or the backend service).
2. Deploy this branch; the process applies Alembic migrations at boot
   (no manual migration is required for this change).
3. Import the corpus through the normal console/API import path — never by
   inserting rows into PostgreSQL or Neo4j.
4. Let the import finish and check the job row/console progress; do not
   navigate away mid-import (and if you must, the job is recovered on return).

## 19. Post-deployment validation

* `GET /api/v1/health/ready` → `database`, `graph`, `object_store` all `ok`.
* `GET /api/v1/datasets` → the imported dataset `ACTIVE` with the corpus
  counts (persons 42, phones 42, accounts 42, vehicles 30, cases 10).
* Open a case → the graph shows people, phones, accounts and vehicles with
  their owners; no floating phone/account; no `CP_*` organisation invented.
* Open an evidence drawer → all four checks computed; each tick names a real
  reason; hash re-computed from real bytes.
* Refresh the console during an import → the same job continues, with the same
  progress; starting a second import returns 409.
* If object storage is misconfigured/unreachable → the endpoint answers 503
  naming `CRIMELINK_MINIO_ENDPOINT`, and the panel shows `?` (unproven), not a
  failed/missing record.

## 20. Local `python run.py` unchanged — and known limits

* `object_store_endpoint_problem` returns `None` outside production, and in the
  host context the endpoint validator rewrites `minio:9000` to
  `localhost:9000` first, so local runs keep working exactly as before
  (covered by `test_a_host_machine_rewrites_the_compose_name_before_the_check`).
* Local imports, the embedded graph and the local object-store directory are
  untouched; all changes are additive and production-gated.
* **Not validated live:** no real Vercel/Postgres/Neo4j/S3 deployment was
  exercised from this sandbox (no cloud credentials); the deployment steps
  above remain to be run against the real environment.
* Register rows that no case claims (e.g. locations `L002`–`L007`, `L009`,
  `L010`, organisations `ORG006`–`ORG008`) remain in the dataset's container
  case — searchable and jurisdiction-scoped, never silently dropped. Observed
  places such as `Camera Point 1` are kept as evidenced locations.

---

## Appendix A — live validation performed in this workspace

The checks below were run against the fixed code over real HTTP (not the test
client) and, where noted, in a production-profile process. Cloud credentials
are not available in this workspace, so no live S3/Postgres/Neo4j/Aura
endpoint was contacted; the Vercel preview deployment for the commit built
successfully but is protected by Vercel SSO, so it could not be queried from
here.

**A1 — hosted import path, real HTTP (`embedded` profile, local object store).**

```
POST /api/v1/datasets/import/path  →  job QUEUED → SUCCEEDED, status READY
files discovered 194 · usable 180 · tables 62 · text 122 · document 10
GET /api/v1/datasets/stats →
  cases 10 · documents 164 · entities 227 · relationships 525
  ACCOUNT 42  ADDRESS 42  CASE 10  LOCATION 11  ORGANIZATION 8
  PERSON 42  PHONE 42  VEHICLE 30
  CALLED 50  INVOLVED_IN 41  MEMBER_OF 5  MENTIONED_IN 160
  OWNS_ACCOUNT 42  OWNS_VEHICLE 30  RESIDES_AT 42  SEEN_AT 47
  TRANSFER_TO 66  USES_PHONE 42
```

**A2 — evidence provenance with real bytes.** `GET /api/v1/evidence/{doc}/provenance`

```
file.storage_status = available     file.available = true
file.size_bytes     = 300           file.hash_matches = true
checks.record_available  ok=true   state=available
checks.traceable_to_original ok=true
checks.hash_matches      ok=true
checks.source_verified   ok=false   (the corpus is SYNTHETIC — source
                                     classification, by design separate
                                     from storage verification)
chain: CASE ✓  EVIDENCE ✓  SOURCE_RECORD ✓  ORIGINAL_FILE ✓  FINDING —
```

`GET /api/v1/evidence/{doc}/verify` recomputed the same SHA-256
(`b492e566…`, match: true) from the stored bytes.

**A3 — production-profile boot with an unusable object-store endpoint.**
Process started with `VERCEL=1`, `profile/environment/runtime_context=production`
and `CRIMELINK_MINIO_ENDPOINT=minio:9000`:

```
GET /api/v1/version     → profile production, object_store_backend minio
GET /api/v1/health/ready → HTTP 200, status "degraded",
                           checks.object_store = {status: error,
                                                  error: DependencyUnavailableError}
GET /api/v1/datasets (no credentials) → HTTP 401 authentication_failed
```

The instance boots, health reports the misconfiguration as a health fact
instead of crashing, and authorization is unchanged.
