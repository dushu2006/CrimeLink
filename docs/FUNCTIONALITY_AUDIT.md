# CrimeLink functionality audit — root causes, fixes, verification

**Scope:** every interactive surface, every API route, and the seeded v2
dataset, checked against the *running* application rather than against
fixtures.

**Method:** read the implementation, exercise it over HTTP against a live
instance seeded with `demo-dataset-002`, then fix the cause and pin it with a
test. Nothing in this document is a prediction; every number was measured.

---

## 1. Root causes found

### 1.1 Source preview 404'd on every real evidence file

```
GET /api/v1/sources/preview?path=evidence/CR-2007/CR-2007_INTELLIGENCE_REPORT_01.pdf
→ 404 {"message": "Active dataset workspace is unavailable."}
```

while `GET /api/v1/sources/files` listed that same file as
`AVAILABLE / openable: true`.

`preview_file`, `read_file` and `raw_file` all did:

```python
root = await _dataset_root(session, dataset.id)
if root is None:
    raise NotFoundError("Active dataset workspace is unavailable.")
```

`_dataset_root()` returns `None` when `datasets.root_path` is not a directory.
The v2 corpus — and any MinIO-backed deployment — stores files in the **object
store** and never creates that directory:

```
datasets.root_path = /…/var/data/datasets/demo-dataset-002   ← does not exist
actual object      = var/objects/documents/evidence/CR-2007/CR-2007_INTELLIGENCE_REPORT_01.pdf
```

So the routes bailed out *before* reaching the object-store lookup they already
contained. A missing directory was reported as a missing file, and the message
blamed the dataset instead of naming the path.

### 1.2 A cross-dataset leak in source resolution

Resolution by path fetched object-store bytes with **no dataset check** — the
store is keyed by path alone and knows nothing about datasets. A file left
behind by a previously-active dataset stayed readable after the dataset was
switched. The leading-segments-stripped fallback had the same hole and returned
bytes with no metadata at all.

### 1.3 The Evidence panel was entirely decorative

Every field fell back to a hardcoded string — `"Communication record"`,
`"Case record"`, `"Supports relationship"`. The three provenance ticks
(`✓ Source verified`, `✓ Record available`, `✓ Traceable to original`) were
permanent literals never compared to anything. **"Open Original Record"**
called `onOpenSource?.(docIds[0])` and **"Pin Evidence"** called `onPin?.(id)` —
optional callbacks that *no caller ever supplied*, so both buttons did nothing.

### 1.4 A broken API route killed the PERSON NETWORK scope

`masterPersonNetwork()` called `GET /graph/master/person/{key}?depth=3`; the
backend serves `GET /graph/master/person/{key}/network`. Verified live:

```
/api/v1/graph/master/person/<key>?depth=3          → 404
/api/v1/graph/master/person/<key>/network?depth=3  → 200
```

The client function and the route were each individually correct, so no test
could see it.

### 1.5 Upload answered 202 and then quarantined the document

`POST /cases/{id}/documents` validates `document_type` against the whole
`DocumentType` enum (**37** members) and answers **202 Accepted**, but the
adapter registry covered only **7**. Uploading a `WITNESS_STATEMENT`:

```
KeyError: 'No adapter registered for DocumentType.WITNESS_STATEMENT'
  … retried 5 times …
pipeline.document_failed quarantined=True reason="Processing failed after 5 attempts: KeyError"
```

`supported_types()` returned every enum member, advertising support the
pipeline did not have.

### 1.6 Every graph record cited the same document

All **550** graph nodes and all **2 789** edges pointed at one hardcoded
`doc-d2-0000` (`CR-2001_FIR_01.pdf`), because the seed passed
`doc_id_for("E-0000")` as the default for every entity and every relationship.
"Open the source" for a phone in CR-2007 produced an FIR from CR-2001.

### 1.7 Chain of custody was broken for 234 of 320 documents

The seed generated each file **twice** — once to store, once to hash. ReportLab
stamps a creation timestamp into every PDF, so the second generation produced
different bytes:

```
doc-d2-0072  recorded=21b039d40f09…  computed=ea6d685d5e0b…  match=false
```

Every PDF mismatched; the byte-deterministic CSVs were fine, which hid it.
`GET /evidence/{doc}/verify` — the endpoint that exists to catch exactly this —
reported it.

### 1.8 The People page over-fetched to count edges

It pulled the whole 575-node entity graph purely to count edges per person, and
swallowed load failures in an empty `catch {}`.

---

## 2. Fixes implemented

| Area | Fix |
| --- | --- |
| Source routes | `_dataset_root()` returning `None` is now a normal state, not an error. The object store *is* the storage layer; the workspace copy is a convenience. `_resolve_source_path()` tolerates `root=None`; every filesystem candidate goes through one `_fs()` helper that keeps the `is_relative_to` traversal guard. |
| Error semantics | `_assert_some_storage()` raises `ServiceUnavailableError` (503) only when no store is reachable at all — a distinct error from "file not found". Genuine miss: `raw`/`file` → 404 naming the file; `preview` → 200 with an explicit `NOT_FOUND` state naming the file (it is a state-reporting endpoint). Traversal / absolute / empty path → 422 `validation_failed`, checked up front by `_reject_unsafe_path()` *before* the evaluation guard so the reason is accurate. |
| Dataset isolation | A path resolves only if the active dataset owns a `DatasetFile` or `CaseDocument` row for it; the metadata travels with the bytes. The stripped-prefix fallback is dataset-scoped the same way. |
| Object store | `sources.py` and `source_viewer.py` constructed a second `LocalObjectStore` from `get_settings()`, which could read a different root than the pipeline wrote to. Both now prefer the container's store. |
| Provenance API | New `GET /api/v1/evidence/{doc_id}/provenance` returns the whole chain — case, source references, dataset file, the findings that cite the document, the people it is recorded against, and the original file's availability with the bytes **actually read** — plus computed `checks` and a `chain` array. |
| Evidence drawer | Fetches that payload. Ticks are the server-computed checks with a real `✓`/`✗` and the reason; an unresolved step renders as "Provenance unavailable". "Open Original Record" opens the stored file in the `SourceViewer` and is disabled *with a reason* when the bytes cannot be read. "Verify integrity" calls the real hash endpoint. Pin / View Graph render only when a caller supplies a handler. |
| Client route | `masterPersonNetwork()` now calls `/graph/master/person/{key}/network`. |
| Adapters | Every remaining `DocumentType` falls back to the text adapter (an arrest record and a witness statement differ in provenance classification, not parsing); CDR / FINANCIAL / SOCIAL_MEDIA / CRIMINAL_HISTORY keep their specialised parsers; the anonymous-tip adapter is unchanged. `supported_types()` reports the registry, not the enum. |
| Seed provenance | `build_graph()` resolves a real evidence document per record from that record's own case, preferring a plausible document type (CDR for a phone or call, FINANCIAL for an account or transfer, ANPR/CCTV for a vehicle, FIR/WITNESS_STATEMENT for a person), with a `crc32` pick so the seed stays reproducible across runs. |
| Seed hashing | Bytes are generated **once** per file and reused for the stored object, the recorded hash and the size. `scripts/repair_document_hashes.py` repairs records written before the fix (dry run by default, `--apply` to write). |
| Cases list | Rows carry real `person_count`, `relationship_count`, `source_count`, `finding_count` from the database and the same graph the People and Relationships pages read. |
| People page | Reads `masterPersons()` only; a load failure is surfaced instead of swallowed. |

---

## 3. Measured before / after

| Metric | Before | After |
| --- | --- | --- |
| `preview` on a real evidence file | 404 "workspace unavailable" | 200 `AVAILABLE`, correct renderer |
| Distinct documents cited by graph nodes | **1** | **121** (max 12 share one) |
| Distinct documents cited by graph edges | **1** | **236** (max 50 share one) |
| Distinct documents cited across the person network | **1** | **167** |
| Documents whose hash matched storage | 86 / 320 | **320 / 320** |
| Evidence files that open | 320 / 320 | 320 / 320 |
| `PERSON NETWORK` scope | 404 | 200 |
| `WITNESS_STATEMENT` upload | quarantined after 5 failures | `COMPLETE` |
| Frontend `api()` paths resolving to a real route | 1 broken (`/graph/master/person/{key}`) | **all 81 resolve** |

---

## 4. Verification

### Live end-to-end harness

`scripts/verify_end_to_end.py` walks the required chain over HTTP against a
running instance and the seeded v2 database:

```
CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → SOURCE DOCUMENT
     → ORIGINAL FILE → PROVENANCE
```

**43 checks, 43 passed, 0 failed.** Highlights from the actual run:

```
[1] active dataset consistent    cases=25  people=110  relationships=1667 (109 people)
                                 entity graph 528 nodes — a different, deeper graph
[2] stars                        Priya Kumar, Vikram Verma, Amit Sharma — all CONFIRMED
                                 is_criminal == bool(criminal_status) for all 109
[3] top relationship             Priya Kumar ↔ Dinesh Malhotra | Communication |
                                 12 records | cross_case=True | kinds
                                 {COMMUNICATION, DIRECT_RECORD, TRANSACTION} | 9 docs
[4] six cases walked             CR-2025/2024/2023/2022/2020/2021 — every evidence
                                 file opened, every provenance chain resolved,
                                 all four checks green
[5] preview/download/errors      200 pdf 2639B · 404 names the file · traversal 422
[6] person detail + network      Amit Ansari rels=224 · /person/{key}/network 200
[7] hash integrity               20/20 verified
[8] provenance spread            167 distinct documents; most-cited holds 91 of 2664
```

It paces itself at ~0.65 s per request because the API rate-limits at 100
requests/minute per principal and this walk legitimately exceeds that.

### Test suites

| Suite | Result |
| --- | --- |
| backend `pytest` | **855 collected, 1 failure** — `test_runtime_context.py::test_unreachable_postgres_message_is_actionable`, reproduced on a clean tree, unrelated |
| frontend `npm test` | **125 passed, 0 failed** |
| frontend `tsc -b` | clean |
| frontend `npm run build` | clean |

Tests added this pass:

| File | Checks |
| --- | --- |
| `test_source_object_store_resolution.py` | 15 — preview serves an object-store-only file; missing file is a 404 naming it; listing and preview agree; resolution by doc id and dataset-file id; traversal and absolute paths refused; server paths never leaked; provenance chain traversable; switching datasets closes the old files |
| `test_demo_v2_data_quality.py` | 10 — every case has real, distinct, case-specific files; no shared files; real content naming its own case; provenance distributed and case-correct; hash matches storage; criminal status explicit and rare |
| `test_document_adapter_coverage.py` | 11 — every `DocumentType` resolves; `supported_types()` matches the registry; the types that broke upload are covered; specialised adapters survive; MIME allowlist is explicit |
| `test_frontend_api_contract.py` | 12 — every console `api()` path resolves to a real route; the person-network route pin; investigator-critical routes exist |
| `frontend/tests/evidence-source-integration.test.mjs` | 11 — drawer fetches real provenance; ticks computed with a failure state; "Provenance unavailable"; no invented placeholders; Open Original Record opens the stored file and disables with a reason; integrity endpoint; no decorative button; viewer hits preview/raw; pages read the API |

---

## 5. Remaining genuine limitations

1. **`test_unreachable_postgres_message_is_actionable` fails.** It asserts the
   unreachable-Postgres message contains "does not fall back to SQLite"; the
   message no longer says that. Pre-existing — reproduced on a clean checkout
   with every change stashed. Not touched here.
2. **`test_investigator.py::test_cross_dataset_isolation_and_thread_pinning` is
   flaky.** `UNIQUE constraint failed: datasets.is_active` from the shared
   session-scoped test database — 2 failures in 6 runs on a clean tree. Also
   pre-existing.
3. **A deployment seeded before these fixes has stale graph provenance and stale
   hashes.** `scripts/repair_document_hashes.py --apply` fixes the hashes
   without touching files or relational rows. Refreshing graph provenance
   requires re-running the seed's `build_graph` (graph nodes/edges only) or a
   full reseed. The instance used for the verification above had both applied.
4. **No OCR.** A scanned PDF with no text layer fails with "OCR is required"
   and is quarantined. That is the documented, deliberate behaviour of
   `TextDocumentAdapter`, not a regression.
5. **Rate limiting applies to scripted audits.** 100 requests/minute per
   principal; the harness paces itself.
6. **`VMxx … reportAllChanges … startTime`** — the repository contains no
   `reportAllChanges`, `web-vitals`, `PerformanceObserver` or `startTime`
   anywhere in `frontend/`. It is browser/DevTools instrumentation, and no
   workaround was added to CrimeLink for it.

## 6. Data-quality and orphan audit (§17)

`backend/scripts/audit_data_quality.py` is a read-only, additive audit over the
active dataset. It checks seventeen categories across the whole
CASE → EVIDENCE → SOURCE DOCUMENT → STORED FILE → PROVENANCE chain and exits
non-zero if any is non-empty. Its classification logic is a pure function
(`classify()`), unit-tested against deliberately broken inputs in
`tests/test_data_integrity_audit.py`, so a clean run means the checks can
actually notice a problem.

Final result on the live instance:

```
cases=25 documents=360 dataset_files=360 references=320 findings=30 nodes=575 edges=2800
total orphan / integrity problems: 0
```

### Two defects it found

**1. Evidence and source documents shared one id namespace.**
`doc_id_for` stripped both the `E-` and the `S-` prefix, so `E-0000` and
`S-0000` both produced `doc-d2-0000`. Two consequences:

- `persist()` skipped the source insert as a duplicate, so the 40 seeded
  source PDFs sat in the object store and in `dataset_files` with **no
  `CaseDocument` row of their own** — 40 of 360 stored files were not
  first-class documents.
- `dataset_files.doc_id` for those 40 rows pointed at an **evidence**
  document (`doc-d2-0000`, the CR-2001 FIR) instead of the source file that
  owned the row. That is a mis-linked provenance row: following it opens the
  wrong document.

Fix: evidence keeps `doc-d2-NNNN`, sources get `doc-s2-NNNN`
(`SOURCE_DOC_PREFIX`). `scripts/repair_source_documents.py --apply` registers
the 40 missing rows and re-points the 40 `dataset_files` rows at their own
document. It is additive — nothing existing is modified or removed. Verified
live: `doc-s2-0000` and `doc-s2-0039` both resolve through
`/evidence/{id}/provenance` with all four checks green and the hash matching
storage.

**2. Deleting a document does not retire the graph entities it produced.**
Three CSVs uploaded during the audit were removed from the database, which
left one graph node and one edge citing a document that no longer resolved,
plus three stray objects in storage. All were cleaned up. The underlying gap
is still open: `services/documents.py::discard_quarantined` soft-deletes the
row but has no graph-store API for retiring the derived entities, and
`snapshot()` skips `is_active=False` nodes. See limitation 7.

### Correction to an earlier claim

An interim note said the id collision meant "67 nodes and 356 edges cite the
wrong file." That was wrong. Those numbers came from flagging any cited id
whose numeric suffix was below 40, which selects the first 40 *evidence*
documents — `doc-d2-0000…0039` — and those resolve correctly. `source_doc_for`
only ever returns `doc_id_for(evidence_id)`, so no graph record ever cited a
source id. The collision was real, but its live effect was confined to the 40
source `CaseDocument` rows and the 40 `dataset_files.doc_id` values described
above.
