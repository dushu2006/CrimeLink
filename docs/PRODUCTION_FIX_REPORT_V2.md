# Production fix report — case/graph quality, evidence traceability, import state

Branch: `arena/01a10c6c-crimelink` (from `main` @ `0227e52`, PR #54 already merged).

Scope: the three reported production problems.

1. The hosted importer collapsed the bundled corpus into one case, and a second
   ten-case dataset produced ten cases with a poor graph (floating phones and
   accounts, `CP_01`/`CP_001` as disconnected or mis-typed nodes).
2. Evidence provenance read "Record available ?", "Integrity hash matches ?",
   "Original record unavailable" in production.
3. Administration → Import Dataset reset on navigation and on browser refresh.

Every statement below about behaviour was produced by running something in this
repository; where a claim could not be verified here it is marked **unchecked**.
No secrets appear in this document; `<…>` marks a placeholder.

---

## 1. Root cause — the corpus collapsed into one case

`_extract_content_cases` in `backend/app/datasets/pipeline.py` decided, **per
file**, whether a document named a case or an FIR. A FIR document that did not
also restate its case number was read as "this file states no case", so it
contributed nothing to the case register; a file that named a case made every
other file's FIR numbers look redundant. The decision was order-dependent: the
same folder produced a different case count depending on which file the walker
reached first, and in the hosted run the ordering landed on the degenerate
branch — every record folded into a single container case.

Fix: two passes. Pass one reads **every** file and collects
`(case identifiers, FIR numbers)` plus a dataset-level `any_case_identifier`
flag. Pass two then applies one rule for the whole dataset:

* if *any* file states a case identifier → those are the cases, and FIR numbers
  are filing references attached to them;
* only if *no* file anywhere states a case identifier → one case per FIR.

Measured (`/tmp` probe, same fixtures, before → after):
`content-cases-only` **1 → 7** canonical cases; `corpus-folder`,
`corpus-flat-upload`, `ten-case-relational` unchanged at 10.

## 2. Root cause — phones, accounts and vehicles not linked to their owners

Three separate defects in `backend/app/datasets/normalize.py`:

1. **Row identifiers captured foreign keys.** In relation sheets such as
   `case_members.csv` (`case_member_id,case_id,person_id,role`) the row's own
   id matched the "…id" heuristics and won the `CASE.id` / `PERSON.id` slots, so
   the real `case_id`/`person_id` were never mapped and no ownership edge could
   be built from the row.
2. **A foreign scope could claim a local reference.** `("PERSON","P001")` inside
   a phone row is that row's own owner reference; the normaliser also registered
   it as a claimable identifier, so an unrelated file could resolve `P001` to
   the phone instead of to the person.
3. **Sightings stated by plate were dropped.** `_sightings()` resolved only
   `VEHICLE.id`, but CCTV logs map `vehicle` to `VEHICLE.registration`, so every
   CCTV row naming a plate produced no relationship at all.

Measured on `ten-case-relational`: `TRANSFER_TO` **0 → 9**, orphan entities
**→ 0**, and `OWNS_ACCOUNT 20 / USES_PHONE 20 / OWNS_VEHICLE 10` now present.

## 3. Root cause — floating `CP_*` entities, sometimes typed ORGANIZATION

Opaque codes such as `CP_01` / `CP_001` were treated as new identities when they
were first seen in a file that had no register row for them, and the type fell
back to ORGANIZATION because nothing in the row named an entity type. Two rules
now govern this:

* an identifier is only ever an identity when the dataset itself says so. A
  foreign-key column, a purely numeric column, and a column whose qualifier
  names a different entity type can never mint an identity;
* people are canonicalised **across files** in the index pass, so a code that
  appears in one file and is registered in another resolves to the same node
  instead of a second, disconnected one.

A duplicate hard identifier now yields exactly one graph node (asserted in
`test_case_extraction_and_graph_quality.py`).

## 4. Root cause — evidence objects unreadable in production

`CRIMELINK_MINIO_ENDPOINT` was set to the Compose service hostname `minio`. That
name resolves only inside the Compose network; on Vercel it does not resolve at
all, so `provenance_payload` could never open the object and reported the record
as unavailable.

This is a **configuration** defect, not a code defect, and it is fixed by
configuration (items 16–18). The existing object-storage abstraction is used
unchanged: no new client, no new environment variable, no filesystem fallback.

## 5. Root cause — integrity verification reported as unresolved

`backend/app/services/documents.py::provenance_payload` had two problems:

1. When the store could not be read, the response could not distinguish
   "the bytes disagree with the recorded hash" from "no bytes were read". Both
   rendered as a failed/unresolved tick.
2. `DatasetFile` was looked up by `doc_id` alone, so a manifest row from an
   unrelated dataset sharing a `doc_id` could become this document's provenance.

Both are fixed. `record_available` and `hash_matches` are now tri-state
(`ok` ∈ `true | false | null`) with an explicit `state` of
`unproven | match | mismatch`, and the lookup is scoped to
`(doc_id, dataset_id)`. `hash_matches` is `null` — not `false` — when no SHA-256
was ever recorded, because there is nothing to compare against; reporting a
match there would be a fabricated verification and reporting a mismatch would
accuse a record imported before hashes were stored.

The check keys are exactly `source_verified`, `record_available`,
`traceable_to_original`, `hash_matches`. There is no `integrity_hash_matches`
key: the UI label "Integrity hash matches ?" is the rendering of
`hash_matches.ok === null`.

`source_confidence = SYNTHETIC` was **not** changed. Source classification and
technical storage verification are different facts, and a SYNTHETIC document
keeps `source_verified.ok is False` while `record_available` and `hash_matches`
genuinely pass.

## 6. Root cause — import state reset on navigation and refresh

The console's view of an import lived only in the React component. `GET
/datasets/jobs/current` answered only "is anything running?" and returned
`{"job": null}` the moment a job finished, and the hydration effect discarded
terminal jobs outright (`if (... current.job.terminal) return;`). So navigating
away unmounted the state, coming back found nothing to hydrate, and a refresh
found nothing either. An operator who returned after a completed import saw an
idle panel, read it as "nothing happened", and started the same import again.

The backend is now authoritative and the console reads it:

* `dataset_jobs.latest_job()` — the running job if there is one, otherwise the
  most recent one — read from the database only;
* `GET /api/v1/datasets/jobs/current` returns `{"job": <row>, "running": bool}`;
* the console hydrates on mount, renders terminal jobs instead of dropping
  them, and uses `running` (not the presence of a job) to decide whether to
  watch or to leave the upload form usable.

No new job system was built; the existing `dataset_jobs` row is the source.

## 7. Files changed

```
 backend/app/api/v1/datasets.py                     |  17 +-
 backend/app/api/v1/jobs.py                         |  53 ++-
 backend/app/datasets/normalize.py                  | 277 +++++++++++++--
 backend/app/datasets/pipeline.py                   | 272 +++++++++++++-
 backend/app/datasets/registry.py                   |   3 +-
 backend/app/services/dataset_jobs.py               | 105 +++++-
 backend/app/services/documents.py                  |  63 +++-
 backend/tests/test_dataset_job_hydration.py        |  21 +-
 backend/tests/test_dataset_jobs_ws.py              |  85 ++++-
 backend/tests/test_object_store_configuration.py   |   5 +-
 backend/tests/test_provenance_checks_can_fail.py   |   7 +-
 frontend/src/api/client.ts                         |  12 +-
 frontend/src/components/DatasetConsole.tsx         |  43 ++-
 frontend/tests/evidence-source-integration.test.mjs|  19 +-
 14 files changed, 915 insertions(+), 67 deletions(-)
```

New files:

```
 backend/tests/test_case_extraction_and_graph_quality.py   (16 tests)
 backend/tests/test_evidence_object_verification.py        (11 tests)
 backend/tests/test_import_state_persistence.py            (13 tests)
 frontend/tests/dataset-import-state-hydration.test.mjs    ( 8 tests)
 docs/PRODUCTION_FIX_REPORT_V2.md                          (this document)
```

## 8. Migrations

**None.** No schema changed. `DatasetJob` already carried every field the
console needs (id, dataset id, kind, status, stage, progress, message,
timestamps, error, result); the work was to populate and read it truthfully.
`registry.job_row` gained one *derived* output key, `requested_by`, and the
service layer adds `dataset_name` by reading the existing `datasets` row — both
are response-shape changes, not columns.

## 9. API changes

* `GET /api/v1/datasets/jobs/current` — **behaviour changed.** Previously
  returned the running job or `{"job": null}`. Now returns
  `{"job": <row> | null, "running": bool}`, where a finished job is still
  returned with `running: false`. Admin-only (403 for investigator/viewer).
* `GET /api/v1/datasets/jobs/{job_id}` — response gained `requested_by` and
  `dataset_name`. Both endpoints return the same payload for the same job
  (asserted).
* `WS /api/v1/jobs/ws/job/{job_id}` — same frames, but the stream can no longer
  wait forever. After `_JOB_STREAM_IDLE_S` (2.0 s) without an event the handler
  re-reads the job row and closes with 1000 if the job has finished.
* `GET /api/v1/evidence/{doc_id}/provenance` — `record_available.ok` and
  `hash_matches.ok` are tri-state; both gained `state`
  (`unproven|match|mismatch`); `file` gained `expected_size_bytes`,
  `size_matches`, `expected_hash`.
* `ImportReport.as_dict()` gained `failed_stage`.

No endpoint was removed and no authorization was weakened.

## 10. Object-storage changes

**No code change to the storage abstraction.** The adapter, the bucket
resolution, the dataset-scoped key layout and the read-back path are untouched.
What changed is the honesty of the verification result built on top of it
(item 5), and the guarantee that a verification failure cannot activate a
replacement dataset (item 21).

Objects remain private. Nothing was made public to make a check pass, and no
ephemeral filesystem is used for durable storage.

## 11. Graph and normalization changes

* Two-pass content-case extraction (item 1) — dataset-scoped, order-independent.
* People canonicalised across files in the index pass; identity is only ever
  minted from the dataset's own hard identifiers (items 2–3).
* Foreign-key columns, purely numeric columns, and columns whose qualifier names
  a different entity type can no longer claim an identity.
* Vehicle sightings resolve by registration as well as by id.
* Duplicate hard identifier → one graph node.
* Dataset-scoped graph identity preserved; provenance preserved on both entities
  and relationships (asserted: provenance survives to the projected graph).
* `run_import` now records the stage it actually reached. A new explicit
  `VERIFYING` stage runs before `_verify_source_objects`, and the failure path
  emits `FAILED` at the percentage reached rather than at 100. `ImportFailed`
  carries `.stage` and `.progress_pct`.

Lifecycle unchanged:
STAGE → DISCOVER → PARSE → NORMALIZE → PERSIST → BUILD RELATIONSHIPS →
BUILD GRAPH → INDEX → VERIFY USABLE → ACTIVATE → RETIRE OLD → CLEANUP.

## 12. Import-state changes

* `dataset_jobs.latest_job(*, kinds=None, dataset_id=None)` — new. Running job,
  else most recent, from the database only.
* `dataset_jobs._row(session, job)` — renders one job row and resolves
  `dataset_name` **through the caller's session**. Taking a second session per
  read was tried first and is documented in item 15 as a real regression.
* `dataset_jobs.current_job()` — a `RUNNING` row whose process is gone is
  reported as `FAILED` / `error="interrupted_by_restart"` rather than
  resurrected, so an interrupted import is visible instead of blocking the next
  one forever.
* `registry.job_row` — `terminal` now includes `CANCELLED`; `requested_by`
  added.
* Frontend `DatasetConsole.tsx` — hydration effect renders terminal jobs;
  `STAGES` includes `VERIFYING`; the watch is torn down on unmount
  (`useEffect(() => () => unwatchRef.current?.(), [])`); a new upload is refused
  only while `job && !job.terminal`.
* `localStorage`/`sessionStorage` are **not** used for progress, stage, status,
  completion, failure, activation or readiness (asserted by the frontend test).
* The console never cancels a job it leaves (asserted: no cancel path exists).

## 13. Tests added

**Backend — 41 new tests, all passing (40 in three new files + 1 added to an
existing file).**

`backend/tests/test_case_extraction_and_graph_quality.py` — 16:
multi-case register; content-only cases; FIR-as-alias; no-case datasets;
dataset-level documents mint nothing; cross-file person identity; separate-file
ownership; shared phone; `CP_*` resolution; foreign-key/numeric columns never
become identity; duplicate hard identifier → one graph node; transfer-vs-
membership case scoping; provenance survives to the graph.

`backend/tests/test_evidence_object_verification.py` — 11:
full chain resolution with a real byte read; dataset-scoped keys; a
cross-dataset manifest row is not used; hash mismatch reported; missing object
reported; unreachable store → `unavailable` / `available is None` while
`traceable_to_original` stays `ok=True`; no recorded hash → `unproven`; size
mismatch; SYNTHETIC stays SYNTHETIC while storage verifies; no invented
person/finding citations; a real finding citation is discovered.

`backend/tests/test_import_state_persistence.py` — 13:
persisted job row; persisted stage and progress; `/jobs/current`;
completed-after-refresh; failed-stays-failed naming the real stage; a failed
import does not take over activation; the job survives a client disconnect; a
client that cannot reach the API does not fail the job; an orphaned `RUNNING`
row is reported, not resurrected; no duplicate import (409 + existing job id);
authorization (403 for investigator and viewer); hydration payload equals the
polling payload; activation semantics unchanged.

`backend/tests/test_dataset_jobs_ws.py::test_a_lost_terminal_frame_still_ends_the_stream`
— 1: the socket delivers the terminal state even when every published frame is
dropped.

**Frontend — 9 new tests.**
`frontend/tests/dataset-import-state-hydration.test.mjs` — 8: hydration reads
the persisted endpoint; no progress is reconstructed from a timer, `Date.now()`
or browser storage; hydration runs on mount and cannot setState after unmount;
a finished job is shown, not discarded; history does not block the next import;
the watch is torn down on unmount and no cancel path exists; the stage list
includes `VERIFYING`; the failed stage is marked where it failed.
`frontend/tests/evidence-source-integration.test.mjs` — 1 added: an unproven
check is reported, not silently dropped.

**Frontend tests are source-contract tests**, matching the existing convention
in this repository (`tests/*.test.mjs` read the real `.tsx`/`.ts` and assert on
the wiring). They verify the wiring is present and correct; they do not mount
the component in a DOM.

## 14. Full test results

Backend — `pytest tests/ -q --tb=no`, full suite:

```
collected 1375   (baseline at 0227e52: 1334, i.e. +41)
13 failed, 1361 passed, 1 skipped
```

The 40 new tests in the three new files, run alone: **40 passed**.

Frontend — `npm test`:

```
# tests 248
# pass 248
# fail 0
```

Frontend — `npm run build`: **succeeded**, `✓ built in 3.62s`, no type errors.

End-to-end pipeline probe (5 real import scenarios through `run_import`), all
`READY`, histograms unchanged from the post-fix baseline:

| scenario | cases | entities | relationships |
|---|---|---|---|
| corpus-folder | 10 (+1 container) | ACCOUNT 42, PERSON 42, PHONE 42, ADDRESS 42, VEHICLE 30, LOCATION 11, CASE 10, ORGANIZATION 8 | MENTIONED_IN 160, TRANSFER_TO 66, CALLED 50, SEEN_AT 47, OWNS_ACCOUNT 42, RESIDES_AT 42, USES_PHONE 42, INVOLVED_IN 41, OWNS_VEHICLE 30, MEMBER_OF 5 |
| corpus-flat-upload | 10 (+1) | identical | identical |
| folder-per-case | 1 | PERSON 2, PHONE 1 | USES_PHONE 1 |
| ten-case-relational | 10 (+1) | ACCOUNT 20, PERSON 20, PHONE 20, CASE 10, VEHICLE 10 | OWNS_ACCOUNT 20, INVOLVED_IN 20, USES_PHONE 20, OWNS_VEHICLE 10, MENTIONED_IN 10, TRANSFER_TO 9 — orphans 0 |
| content-cases-only | 7 (+1) | PERSON 7, CASE 7 | MENTIONED_IN 7 |

## 15. Baseline vs new failures

**Zero new failures.** The failure list was captured at `0227e52` in a clean
worktree and again after all changes; `diff` of the two sorted lists is empty.

The same 13 pre-existing backend failures, all unrelated to this work
(demo-v2 fixtures, AI streaming/triage, runtime-context messaging, two
data-integrity audits):

```
tests/test_ai_interactive_budget.py::TestInteractiveSettings::test_interactive_settings_exist
tests/test_ai_stream_and_triage.py::test_mid_stream_failure_reports_partial_without_retry
tests/test_ai_stream_and_triage.py::test_pre_token_stream_failure_degrades_to_plain_chat
tests/test_data_integrity_audit.py::test_evidence_and_source_documents_do_not_share_an_id
tests/test_data_integrity_audit.py::test_no_orphan_entity_outside_every_case_snapshot
tests/test_dataset_jobs_ws.py::test_dataset_listing_and_activation
tests/test_demo_v2_data_quality.py::test_criminal_status_is_explicit_and_rare
tests/test_demo_v2_data_quality.py::test_every_case_has_real_case_specific_evidence_files
tests/test_demo_v2_data_quality.py::test_evidence_files_are_generated_with_real_content
tests/test_demo_v2_data_quality.py::test_no_evidence_record_shares_a_file_with_another
tests/test_demo_v2_relationship_network.py::test_v2_graph_yields_a_real_person_to_person_network
tests/test_demo_v2_relationship_network.py::test_v2_star_lands_only_on_explicitly_confirmed_criminals
tests/test_runtime_context.py::test_unreachable_postgres_message_is_actionable
```

Frontend baseline at `0227e52`: **239 tests, 238 passed, 1 failed** —
`evidence-source-integration.test.mjs::provenance ticks are computed, never
hardcoded literals`. That test pinned the expression
`const cls = check.ok ? "verified" : "failed"`, but `EvidenceDrawer.tsx`
**already** carried the tri-state form
`check.ok === null ? "unknown" : check.ok ? "verified" : "failed"` at the base
commit (verified: `git show 0227e52:…EvidenceDrawer.tsx`, and
`git status` reports the file unmodified by this branch). The test was stale
against its own source. It was updated to assert the tri-state, which is the
stronger and truthful contract. Frontend now: 248 tests, 0 failures.

### One regression introduced and fixed during this work

Adding `dataset_name` to the job payload was first implemented as a second
`async_session()` per job read. That made
`tests/test_dataset_jobs_ws.py` hang intermittently — measured **3 timeouts out
of 6 runs**, against **0 out of 3** in a clean base worktree. A `faulthandler`
dump of a hung run placed it precisely:

```
File "…/starlette/testclient.py", line 211 in receive_text
File "…/tests/test_dataset_jobs_ws.py", line 368 in test_failure_is_delivered_over_the_socket_too
```

Fixed by resolving the dataset name through the caller's own session
(`dataset_jobs._row`), so one read takes one connection. After the fix: **0
timeouts out of 8 runs**.

Investigating that hang exposed a **pre-existing** race in the job WebSocket
handler, which was then fixed as well: the handler read the job row, sent the
snapshot, and only *then* registered its event-bus subscription, so a job that
finished inside that window published its terminal frame to nobody and the
socket waited forever. The handler now re-reads the job row when no event
arrives. The new test
`test_a_lost_terminal_frame_still_ends_the_stream` was checked as a real guard:
with `_JOB_STREAM_IDLE_S` raised to 600 s it **fails**; at 2.0 s it **passes**.

## 16. Required Vercel environment variables

Object storage (item 10 — existing names, no new variables). Any one alias per
setting is enough; `CRIMELINK_*` is the canonical name:

| Setting | Canonical | Also accepted |
|---|---|---|
| Endpoint | `CRIMELINK_MINIO_ENDPOINT` | `S3_ENDPOINT`, `S3_ENDPOINT_URL`, `MINIO_ENDPOINT`, `MINIO_SERVER`, `AWS_ENDPOINT_URL` |
| Access key | `CRIMELINK_MINIO_ACCESS_KEY` | `S3_ACCESS_KEY_ID`, `S3_ACCESS_KEY`, `MINIO_ACCESS_KEY`, `MINIO_ACCESS_KEY_ID`, `AWS_ACCESS_KEY_ID` |
| Secret key | `CRIMELINK_MINIO_SECRET_KEY` | `S3_SECRET_ACCESS_KEY`, `S3_SECRET_KEY`, `MINIO_SECRET_KEY`, `MINIO_SECRET_ACCESS_KEY`, `AWS_SECRET_ACCESS_KEY` |
| TLS | `CRIMELINK_MINIO_SECURE` | `S3_SECURE`, `MINIO_SECURE` |
| Documents bucket | `CRIMELINK_MINIO_BUCKET_DOCUMENTS` | `S3_BUCKET_DOCUMENTS`, `MINIO_BUCKET_DOCUMENTS` |
| Derived bucket | `CRIMELINK_MINIO_BUCKET_DERIVED` | `S3_BUCKET_DERIVED`, `MINIO_BUCKET_DERIVED` |
| Audit-anchor bucket | `CRIMELINK_MINIO_BUCKET_AUDIT_ANCHOR` | `S3_BUCKET_AUDIT_ANCHOR`, `MINIO_BUCKET_AUDIT_ANCHOR` |

A URL form is accepted: a value containing `://` is parsed, and an `https://`
scheme sets `minio_secure` automatically.

**Never** set the endpoint to `minio`, `minio:9000`, `localhost:9000` or
`127.0.0.1:9000` on Vercel.

## 17. Neo4j configuration

Existing names, unchanged:

| Setting | Canonical | Also accepted |
|---|---|---|
| URI | `CRIMELINK_NEO4J_URI` | `NEO4J_URI`, `NEO4J_CONNECTION_URI`, `NEO4J_URL` |
| User | `CRIMELINK_NEO4J_USER` | `NEO4J_USER`, `NEO4J_USERNAME` |
| Password | `CRIMELINK_NEO4J_PASSWORD` | `NEO4J_PASSWORD`, `NEO4J_PASSWORD_ENCRYPTED` |
| Database | `CRIMELINK_NEO4J_DATABASE` | `NEO4J_DATABASE` |

Production must set `CRIMELINK_NEO4J_URI` explicitly
(`neo4j+s://<host>:<port>` for a managed cluster, `bolt+s://…` where TLS is
terminated at a proxy). The default is a local Docker URI and must not be
relied on. Graph construction is **not** disabled in production: readiness
depends on it.

## 18. Production object-storage configuration

**LOCAL (Docker Compose)** — the Compose service name is correct here because
the API container is on the same network:

```
CRIMELINK_MINIO_ENDPOINT=minio:9000
CRIMELINK_MINIO_SECURE=false
CRIMELINK_MINIO_ACCESS_KEY=<compose value>
CRIMELINK_MINIO_SECRET_KEY=<compose value>
```

**VERCEL / PRODUCTION** — a real, publicly routable S3-compatible endpoint.
Example for a managed provider:

```
CRIMELINK_MINIO_ENDPOINT=https://<region>.<provider-domain>
CRIMELINK_MINIO_SECURE=true
CRIMELINK_MINIO_ACCESS_KEY=<access key>
CRIMELINK_MINIO_SECRET_KEY=<secret key>
CRIMELINK_MINIO_BUCKET_DOCUMENTS=<documents bucket>
```

Requirements:

* the endpoint must resolve and be reachable **from the Vercel runtime**, not
  from a developer machine or a Compose network;
* buckets must exist and the credentials must have read/write on the
  dataset-scoped prefixes. Nothing is made public — verification reads back
  through the authenticated client, exactly as locally;
* the Vercel ephemeral filesystem is never used for durable evidence;
* keys stay dataset-scoped, so a dataset can be retired without touching
  another's objects.

## 19. Deployment steps

1. Create/confirm the three buckets in the production object store. Do not
   enable public access.
2. Set the item-16 object-storage variables and the item-17 Neo4j variables in
   the Vercel project. Verify `CRIMELINK_MINIO_ENDPOINT` is a routable URL.
3. Deploy the backend. No migration step is required (item 8).
4. Deploy the frontend (`npm run build` output).
5. Confirm `GET /api/v1/health` and that the runtime context resolved to
   `production` rather than `host`/`docker`.
6. Import one **small** dataset first and validate it (item 20) before importing
   anything large.
7. Do not reset production PostgreSQL, do not delete Neo4j data globally, and do
   not delete the active dataset. A failed import leaves the current active
   dataset in place (item 21), so there is no window in which the system has no
   active dataset.

## 20. Post-deployment validation

1. **Object store reachability** — open any evidence record's provenance panel.
   `record_available` must be `✓` with detail "Bytes read from object storage."
   If it reads `?` with detail naming the endpoint, the endpoint is still wrong;
   this is the exact signal that identified the original fault.
2. **Integrity** — `hash_matches` must be `✓` for records imported with a
   recorded hash. `?` with "no SHA-256 was recorded" is truthful for older
   records and is not a failure.
3. **Original file** — "Open Original Record" must render real bytes.
4. **Case count** — a corpus import must produce one case per real case, not
   one container case. Compare against the source folder.
5. **Graph quality** — phones linked by `USES_PHONE`, accounts by
   `OWNS_ACCOUNT`, vehicles by `OWNS_VEHICLE`; no `CP_*` node disconnected; no
   `CP_*` node typed ORGANIZATION.
6. **Provenance** — pick an entity and a relationship at random and confirm both
   carry source provenance back to a real file and row.
7. **Import state** — start an import, navigate to Cases, come back: the panel
   must show the same job, stage and percentage. Refresh mid-import: same. A
   completed import must still be shown after a refresh, and a failed one must
   name the stage it failed at.
8. **No duplicate import** — while an import runs, a second attempt must return
   HTTP 409 naming the existing job, not start a second one.
9. **Failure path** — deliberately point the endpoint at an unreachable host and
   import: the job must end `FAILED` at the stage reached with
   `progress_pct < 100`, and the previously active dataset must still be active.
   Restore the endpoint afterwards.

## 21. Confirmation — the active dataset is protected on failure

Confirmed by test, not by inspection. With the object store made unreachable
during `VALIDATING`:

* the job ends `status="FAILED"`, `stage="VALIDATING"`,
  `result={"failed_stage": "VALIDATING"}`, `progress_pct < 100`;
* `error` contains `storage unreachable`;
* the candidate dataset is absent from `GET /api/v1/datasets`;
* the previously active dataset is **still** the active one;
* exactly one dataset has `is_active` at any time.

Before this fix the failure path reported `FAILED` at **100%**, which claimed
the run had reached READY. It now reports the percentage actually reached and
names the stage.

## 22. Confirmation — local `python run.py` still works

Partially verified in this sandbox, and the gap is stated plainly:

* `python run.py --help` runs and prints the documented launcher usage.
* `backend/tests/test_run_launcher.py` — **33 passed**. These cover interpreter
  selection, runtime-context detection, endpoint resolution through
  `app.runtime`, the non-destructive guarantees and the pre-flight TCP checks.
* The five end-to-end import scenarios in item 14 all reach `READY` through the
  real `run_import` path, which is the same pipeline the launcher bootstraps.
* `backend/tests/test_db_bootstrap.py` passes.

**Unchecked:** a full `python run.py` boot. Docker is not available in this
sandbox (`docker info` fails), so the launcher's `--start-infra` path and the
Compose MinIO/Postgres/Neo4j bootstrap could not be exercised end to end here.
No launcher, bootstrap or Compose file was modified by this branch, and the
local Docker configuration in item 18 is unchanged, so no regression is
expected — but the end-to-end boot should be run once on a Docker-capable
machine as the first step of validation.

---

## Summary of what was deliberately *not* done

* No mock, demo or fabricated data, cases, people, relationships, evidence,
  findings, provenance, hashes or object availability.
* No hardcoded case IDs, `CP_01`/`CP_001` behaviour, phone numbers, bank
  accounts or per-dataset relationships. No special importer for the bundled
  corpus; the canonical pipeline is the only path.
* No direct PostgreSQL inserts of imported records; no bypass of normalization,
  projection, provenance, read-back or SHA-256 verification.
* `source_confidence = SYNTHETIC` left as SYNTHETIC.
* No invented PERSON→DOCUMENT or finding→evidence citations. Where a source
  contains none, the truthful "Provenance unavailable" state stands.
* Nothing made public, no authorization weakened, no ephemeral filesystem used
  for durable storage.
* No unnecessary migrations. No existing test removed. No existing failure
  hidden — the 13 are listed in full above.
