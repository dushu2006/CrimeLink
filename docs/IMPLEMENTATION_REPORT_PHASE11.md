# CrimeLink — Phase 11 Implementation Report

## Synthetic corpus semantic consistency (generate.py ↔ build_external.py)

Branch: `arena/01a06d5c-crimelink` · Date: 2026-09-05

This phase makes the in-process generator
(`backend/app/synthetic_corpus/generate.py`) semantically consistent with the
reference generator (`backend/app/synthetic_corpus/build_external.py`), using
the reference as the canonical statement of intent. The two generators produce
the same *kinds* of dataset — a case population plus a coherent, unrelated
background population — even though they use different encodings (in-memory
documents vs on-disk relational CSVs). Working-tree only; **nothing has been
committed or pushed.**

---

## A. Audit — what each generator actually produces (verified from code)

### A.1 Reference: `build_external.py` (the canonical semantics)

* **Two populations.** `build_case()` creates 12 cases × 8 persons, each with
  a phone (always), a vehicle (~60%), an account (~80%) and optional
  organisation membership. `build_background()` creates 24 further persons with
  a phone (always), a vehicle (~50%), an account (~70%) and **no case**.
* **Every event row carries an explicit identity and a `case_id`** for the case
  population: `cdr_id`, `transaction_id`, `sighting_id`, `report_id`. Sightings
  also carry `vehicle_id`, `location_id`, `timestamp`, `source`
  (CCTV/PATROL/ANPR/INTELLIGENCE). Two CCTV sightings are two rows.
* **Ownership is explicit and 1:1 where claimed:** `phones.owner_person_id`,
  `vehicles.owner_person_id`, `accounts.holder_person_id`,
  `person_organizations.{person_id,organization_id}`.
* **Traceability** `Person → Account → Transaction → Account → Person` is
  guaranteed structurally: transactions name `from_account_id`/`to_account_id`
  with `amount_inr`, `transaction_type`, `timestamp`, `location_id`, `case_id`,
  and accounts name a `holder_person_id`.
* **Background rows carry no `case_id`** and are internally coherent (40 CDR +
  15 transfer rows between background people only). The source adapter
  quarantines them with reason code `subject_in_no_case` (verified in
  `app/adapters/sources/synthetic_external.py`).
* **Provenance:** `documents.csv.source_environment = "synthetic"`, sighting
  `source`, intel `source_type`; ground truth written to `ground_truth/`
  (evaluation-only, never ingested).
* **Temporal:** case registered 2024-01-01…2025-06; events within
  `[registered − 90d, registered + 365d]`; background 2024-01-01…2025-12-31.

### A.2 In-process: `generate.py` (before this phase)

* Only FIR / CDR / FINANCIAL / SURVEILLANCE documents, all attached to a case.
* **No background/unrelated population** — every person was put in a network and
  then into cases.
* **No ownership document** — there was no criminal-history record, so the
  pipeline could not emit `USES_PHONE` / `OWNS_VEHICLE` / `OWNS_ACCOUNT`
  (verified: only the `person_record` block kind emits these, and generate-mode
  never produced one).
* **Surveillance documents were silently broken:** the header
  `date,vehicle,location,observer` does not match the `SurveillanceAdapter`
  aliases (no `subject`), so every row was skipped and generate-mode produced
  **zero** sighting events.
* Phone/vehicle/account "ownership" was network-pool sampling, not per-case
  evidence; transactions sampled from *all* accounts (a background account, if
  it had existed, could have been pulled into a case transfer).
* Burner phones could fan out to un-owned/background phones; burner lifespans
  could extend into the future.

### A.3 Which generator the app/demo uses

`Settings.synthetic_data_mode` defaults to **`"external"`**
(`app/config.py`). The CLI umbrella `app/cli.py ingest-synthetic` and the admin
endpoint `POST /api/v1/admin/synthetic/ingest` both route `generate` →
`app.synthetic_corpus.generate.main` / `generate_corpus`, and `external` →
`app.synthetic_corpus.external.main` / `ingest_external_corpus`. The in-process
generator remains available via `CRIMELINK_SYNTHETIC_DATA_MODE=generate` or the
explicit adapter flag.

### A.4 Persistence layer (shared, unchanged)

Both generators feed the **same** `upload_document` service
(`app/services/documents.py`): object-store put → `CaseDocument` metadata row →
`IngestionJob` → broker dispatch → six-stage pipeline → graph injector (the only
graph writer) → graph store → API → Cytoscape.js. Ground truth is written to a
separate JSON path only (`data_dir/synthetic_ground_truth.json` for generate;
`ground_truth/` for external) and never enters operational stores.

---

## B. Canonical semantics adopted (generate.py now mirrors)

1. **Case population vs background population.** Background persons are
   structurally valid (name, address, always a phone, sometimes a vehicle and
   an account) but belong to **no** case, appear in **no** case document, and
   therefore never enter the operational graph. They are recorded with the
   canonical flag **`subject_in_no_case`** (the same field the external adapter
   uses as its quarantine reason), not a second incompatible representation and
   never `Test Person 1` placeholders.
2. **Case-scoped attribution.** Case → persons/phones/vehicles/accounts/
   locations are the only entities referenced by that case's documents.
3. **Event identity.** Every sighting is its own entity
   (`SIGHTING::{person}::{timestamp}` in the deterministic extractor) with a
   distinct `(subject, timestamp)` and its own `id`/`source`; no global
   PATROL/CCTV collapse.
4. **Meaningful ownership.** `USES_PHONE` / `OWNS_VEHICLE` / `OWNS_ACCOUNT`
   edges are emitted only from an explicit criminal-history record that names
   the phone/vehicle/account the corpus actually assigns — never inferred.
5. **Transaction traceability.** `Person → Account → Transaction → Account →
   Person` is structural (account holders + `channel` + amount + timestamp).
6. **Temporal coherence.** All event timestamps are strictly in the past and
   within the case window.
7. **Provenance preserved.** Documents remain
   `source_confidence=UNVERIFIED` with `[SYNTHETIC]` case titles; generated
   *evidence* (records) stays distinct from *inferred* relationships (the
   extractor's `origin`/`source_doc_id` pointers).

---

## C. Changes to `generate.py`

* `CorpusOptions.background_person_count` (default 0 in the dataclass so direct
  construction keeps historical behaviour; production default 20 from
  `Settings.synthetic_background_person_count`).
* `SynthPerson.subject_in_no_case`; `SynthTransaction.channel` (was `remarks`);
  new `SynthSighting` (id / person / vehicle / location / ts / source / case).
* `SyntheticCorpus` gains `sightings`, `background_calls`,
  `background_transactions`, `_now` (frozen at `build()` start), and a
  `validate()` method delegating to `app.synthetic_corpus.validation`.
* Build order adds: `_mark_background_population()` →
  `_assign_background_assets()` → `_sightings()` → `_background_activity()`.
* `_networks_and_bridges` excludes background persons; burners are drawn only
  from *owned* phones; burner deactivation is clamped to the past.
* `_assign_entities_to_cases` excludes background persons.
* `_transactions` uses only accounts with a case-person holder; channel from
  `TRANSACTION_CHANNELS`.
* `_documents()` adds a **CRIMINAL_HISTORY** document per case (ownership
  evidence, real CSV quoting for comma-containing addresses), fixes the
  FINANCIAL `remarks`→`channel` column, and rewrites SURVEILLANCE to render from
  first-class `SynthSighting` events (`subject,observed_at,location,vehicle,
  remarks`, real CSV quoting).
* `_build_ground_truth` records `background_person_ids`,
  `background_population` (each with `subject_in_no_case: true`),
  `background_calls`, `background_transactions`, and the extra counts.
* `generate_corpus()` validates the built corpus and raises on any problem (the
  admin endpoint already converts `RuntimeError` → `ValidationFailedError`).

---

## D. Changes to `build_external.py`

* `Builder(seed, root=None)` — the output directory is now overridable
  (defaults to `CORPUS_ROOT`). This lets tests write and validate the reference
  generator's real on-disk output without touching the repository-local corpus.
  No generation semantics changed.

---

## E. New module `app/synthetic_corpus/validation.py`

One shared validator for both paths, enforcing the same invariants:

* `validate_generated(corpus) -> list[str]` (in-memory model)
* `validate_external(root) -> list[str]` (on-disk CSV tables)

Checks: referential integrity, unique entity/event ids, event identity
(distinct `(subject, timestamp)` per sighting), background isolation (no
`subject_in_no_case` person in a case, a case asset list, a case call/transfer/
sighting), meaningful ownership (referenced assets always name a real owner),
transaction traceability (both ends hold real holders, positive amounts),
valid timestamps (parseable, ≥ 2020, ≤ now + 2 days). Validators are read-only
and return problem strings.

---

## F. Configuration

`Settings.synthetic_background_person_count: int = 20`
(`app/config.py`) — the coherent negative-class size, mapped into
`CorpusOptions.from_settings`.

---

## G. Document-level semantics (what the pipeline now sees)

* **CRIMINAL_HISTORY** (new): `name,aliases,role,case_ref,case_date,phone,
  plate,account,bank_code,address` → `person_record` blocks →
  `USES_PHONE` / `OWNS_VEHICLE` / `OWNS_ACCOUNT` edges (verified by test).
* **SURVEILLANCE** (fixed): `subject,observed_at,location,vehicle,remarks` →
  `sighting` blocks → `Event` entities with unique keys + `LOCATED_AT` edges
  (verified by test; previously produced **no** blocks).
* **FINANCIAL** (fixed): `channel` column now populates the transfer channel.

---

## H. Test results

| Run | Result |
| --- | --- |
| `tests/test_synthetic.py` before | 15 passed |
| `tests/test_synthetic_external.py` before | 34 passed |
| After changes, both files | 24 + 40 = 64 passed |
| **Full backend suite after** | **228 passed in 426.94s** (was 213) |

The 15 new tests cover: background population structure + isolation,
background assets exclusivity, background absence from case documents/graph,
case-scoped integrity, event identity uniqueness, person→phone/account
evidence semantics, transaction traceability, temporal validity, document-level
ownership + sighting edges, the shared validator on both generators, and
validator negative cases (background-in-case, missing references/owners,
duplicate event ids, impossible timestamps).

No existing test was weakened; the suite count only grew.

---

## I. Verified runtime data flow

```
generate mode:
  python -m app.synthetic_corpus.generate  (or cli ingest-synthetic --mode generate,
   or POST /admin/synthetic/ingest {adapter:generate})
    → generate_corpus() → SyntheticCorpus.build() [+ validate()]
    → init_db() → _ensure_system_user() → _ensure_cases()   [Case rows]
    → _ingest_documents() → upload_document()
        → object store put → CaseDocument row → IngestionJob → broker dispatch
    → six-stage pipeline → graph injector (only graph writer) → graph store
    → REST API → Cytoscape.js
    ground truth → data_dir/synthetic_ground_truth.json ONLY

external mode:
  python -m app.synthetic_corpus.external (or cli ingest-synthetic --mode external)
    → ingest_external_corpus() → ExternalSyntheticCorpusAdapter.scan()/iter_records()
    → SourceRecord(s) (source_environment=synthetic, SourceConfidence.SYNTHETIC)
    → upload_document() → …same shared pipeline… → API → Cytoscape.js
    background rows (no case_id) → quarantine reason subject_in_no_case
```

Arrows above were traced in code this phase. The generate-mode document
adapter chain (CRIMINAL_HISTORY → `CriminalHistoryAdapter`, SURVEILLANCE →
`SurveillanceAdapter`, FINANCIAL → `FinancialAdapter`, CDR → `CDRAdapter`) was
exercised in `test_documents_emit_ownership_and_sighting_edges`.

---

## J. Graph compatibility (data layer, verified)

* **Person Graph (1/2/3 hops), Master Graph (filters), Temporal Graph** share
  one canonical graph dataset; the generated data now supplies the evidence the
  three views render: `USES_PHONE` / `OWNS_VEHICLE` / `OWNS_ACCOUNT` / `CALLED`
  / `TRANSFER_TO` / `PARTICIPATED_IN` / `LOCATED_AT` edges and distinct `Event`
  nodes.
* **No PATROL/CCTV global-node regression:** the deterministic extractor builds
  event keys `SIGHTING::{person}::{timestamp}` and labels
  `{Source} · {location} · {ts}`; generate-mode now feeds real
  subject/timestamp/source rows (it previously fed none), and
  `test_documents_emit_ownership_and_sighting_edges` asserts
  `len(event_keys) == len(corpus.sightings)` — every observation is a distinct
  event.
* **Negative class has no graph presence:** background identifiers appear in
  *zero* generated documents, and documents are the sole ingestion input, so no
  background person/phone/account can reach the graph (asserted by test).

---

## K. Honest limitations (not verifiable here)

* **PostgreSQL / Neo4j / MinIO / Celery+Redis** were not running; verification
  is against the embedded profile (SQLite / NetworkX / local FS / in-process
  broker) and against code paths. No live deployment claim is made.
* **Browser (Cytoscape.js) rendering** was not exercised in this sandbox;
  graph compatibility is verified at the data/extraction layer, plus the
  Phase 10 frontend work already in place.
* The two paths tag provenance slightly differently (pre-existing): external
  records carry `SourceConfidence.SYNTHETIC`, generate-mode uses
  `SourceConfidence.UNVERIFIED` (both resolve to the same 0.75 base extraction
  confidence; both are unambiguously synthetic via `[SYNTHETIC]` case titles and
  the synthetic-only ingestion gate). Left unchanged to avoid scope creep.
* Seed/streams shifted for `generate.py` (extra population/activity steps);
  same-seed determinism is preserved and tested (`test_deterministic_seed_…`).

## L. Constraints honoured

* Nothing committed or pushed. Phase 10 changes untouched.
* Only synthetic, fictional data (Indian gazetteers; no real PII).
* Existing tests run before (49 in the two synthetic files) and after (64);
  none weakened or deleted.
* Reused the shared validation module; no competing validator introduced.
* Background people use the canonical `subject_in_no_case` field.
