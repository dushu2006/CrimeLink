# CrimeLink — second-round audit (26-section brief)

**Code commits:** `4d97eb3`, `f6b2caa`, `ce45e11`, `d591534`
**Branch:** `arena/01a0afee-crimelink` · **Base:** `30154e3d6fd7feb6c83cec834f8aab6c4f94e780`

The first-round report follows below. This section covers the items the second
brief added that the first round had **not** touched — chiefly §2/§18
(cross-case analytics surfacing a phone number as the analytical subject), §17
(contradictions), §24 (frontend/backend contract) and two source-viewer defects
the first round only suspected.

---

## A. Root causes found in this round

### A.1 A phone number was the analytical subject  *(§2, §18 — the headline defect)*

The reported output reproduced exactly:

```
F001 +919000000000 appears in 9 cases
CROSS CASE ENTITY
Why: +919000000000 appears in 9 cases was surfaced because elevated
betweenness centrality (0.00), cross-case presence in 9 case(s),
bridging 2 communit(ies), degree centrality 0.03, weighted degree 12.44,
PageRank 0.0076.
```

**Three independent defects** produced it, all in the deterministic detectors:

1. **`patterns.py::detect_cross_case_entities`** iterated every node in the
   snapshot regardless of label. A hub phone spanning nine cases therefore
   outranked every person, and the phone became the finding's subject.
2. **The "why" text** called any computed metric *"elevated"* whenever it was
   not `None` — so a betweenness of **0.00** was described as elevated. An
   unsupported adjective printed as if it were a measurement.
3. **`patterns.py::detect_community_signals`** listed supporting entities as
   fellow group members (`Group of 3: +919000000000, Priya Kumar, Dinesh
   Malhotra`), which reads as a person in the group.

**Fix.** A `subject` field now travels on `DetectorContext`
(`"PERSON"` default; `"ENTITY"` only for the MASTER deep-dive). In
person-centric scope:

- a PERSON spanning cases is still reported as that person;
- any **other** entity spanning cases is *translated* into the PERSON ↔ PERSON
  pairs it carries, with the entity demoted to the supporting basis:

  ```
  [CROSS_CASE_PERSON_LINK] Dinesh Malhotra ↔ Priya Kumar: connected through +919000000000
      evidence: "Dinesh Malhotra is linked to +919000000000 via USES_PHONE."
                "Priya Kumar is linked to +919000000000 via USES_PHONE."
                "The shared phone number +919000000000 is attributed to 9 in-scope cases."
  ```
- communities are described by their people, with the shared entity named as the
  mechanism: `Group of 2 people: Priya Kumar, Dinesh Malhotra`;
- ranked metrics (`degree`, `betweenness`, `pagerank`, `weighted_degree`) and the
  cross-case list report people only.

The entity-level reading is **not** deleted: MASTER is the deep evidence graph
where entity structure genuinely is the point, and it keeps those rows — labelled
as statements about the evidence graph, not about a person.

**Verified on the real seeded master graph** (527 nodes, 109 persons, 25 cases):
person-centric scope now yields **0** patterns naming a non-person entity. Before
the fix the same input surfaced 12 phone numbers and bank accounts as subjects.

### A.2 A consistency check that never ran  *(§17)*

`person_graph_rag.py::evidence_sufficiency_gate` set
`temporal_consistent = True` unconditionally under the comment *"For now, assume
consistent unless explicit contradiction in data."* A real
`detect_contradictions()` already existed in the same module and was used by the
retrieval path — the gate simply never called it, so it reported a passed check
for records it had never examined.

**Fix:** the gate now runs the real detector, and `temporal_consistent` is
tri-state, matching the provenance rule that an unassessed check shows `?`:

| value | meaning |
|---|---|
| `True` | checked and consistent (≥2 timestamped locations, no conflict) |
| `False` | contradiction found; the conflicting records are named |
| `None` | no timestamped location data — **not assessable** |

A real contradiction still downgrades FACT → INFERENCE.

### A.3 Corrupt PPTX returned 500 instead of "CORRUPTED"  *(§11)*

`source_viewer.py:1580` raised `SourceAccessError(..., code="CORRUPTED")`, but
the constructor takes `status`. Reproduced before the fix:

```
TypeError: SourceAccessError.__init__() got an unexpected keyword argument 'code'
```

So a damaged presentation produced a 500 where the viewer has a `CORRUPTED`
state designed to explain exactly this. The first-round report listed this as
*suspected, not reproduced* — it is now reproduced and fixed.

### A.4 Evidence was labelled as living in MinIO when it did not  *(§8, §10)*

The preview response set `file.storage = "minio"` whenever bytes came back from
the object store. `_try_minio` reads from whichever object store the deployment
configured and ignores its own `is_minio` flag, so a local-filesystem store was
reported as MinIO — telling an investigator the wrong thing about where their
evidence lives. Now `"minio"` only when MinIO is the configured backend, else
`"object_store"`.

### A.5 Entity labels were mangled in sentences  *(cosmetic, user-facing)*

`SUPPORTING_ENTITY_WORD.get(label, label.lower())` lowercased the seeded label
`BankAccount` to **"bankaccount"**. Now normalised through
`supporting_entity_word()`: `BankAccount` / `BANK_ACCOUNT` / `bank account` all
resolve to "bank account".

---

## B. What was already correct, and how that was established

Two claims in the brief were audited rather than assumed, and **no code change
was warranted**:

### B.1 Frontend/backend contract  *(§24)*

Every HTTP call site was extracted (95 across `frontend/src`) and matched
against the 151 routes FastAPI actually serves:

```
backend /api routes      : 151
frontend HTTP call sites : 95
call sites with NO matching backend route: 5
  client.ts:187,322,325,346,350  fetch(`/api/v1${path}`)
```

The 5 are the shared base-URL wrapper that every other call passes its path
through — not endpoints. **Zero contract mismatches.**

The specific mismatch named in the brief does not occur: the frontend calls
`/graph/cases/{case_id}/relationships` (client.ts:2238) and
`/graph/master/relationships` (client.ts:2239), both of which exist. The
`/cases/{caseId}/network/{personKey}` calls (client.ts:1136, 1263) also resolve —
`investigation.py:131` serves exactly that route.

One genuine finding from this audit: there is **no** case-scoped
`relationship-evidence` route. Only `/graph/master/relationship-evidence`
exists, and the frontend correctly calls only that one.

### B.2 Relationship deduplication  *(§3)*

`person_relationships.py:251` keys aggregation on `frozenset((a, b))`, so one
unordered person pair yields one edge. Verified live: **0 duplicated pairs**
across 400 edges, with 400 edges aggregating more than one supporting record.

---

## C. Graph architecture after the fix

```
                        ┌─────────────────────────────┐
   investigator-facing  │   PEOPLE NETWORK  (default) │   /graph/master/relationships
                        │   nodes: PERSON only        │   /graph/cases/{id}/relationships
                        │   edges: PERSON ↔ PERSON    │
                        └──────────────┬──────────────┘
                                       │  supporting entities are walked
                                       │  internally to establish + evidence
                                       │  an edge; never rendered as nodes
                        ┌──────────────┴──────────────┐
   deep evidence view   │  ENTITY / MASTER NETWORK    │   /graph/master
                        │  phones, accounts, vehicles │   (explicit mode, not the
                        │  locations, orgs, events    │    default landing view)
                        └─────────────────────────────┘

   analytics subject    DetectorContext.subject:
                          PERSON → findings name people; shared entities appear
                                   only in the supporting basis
                          ENTITY → entity-level rows kept, labelled as
                                   statements about the evidence graph
```

## D. Source-tracing architecture after the fix

```
Finding → Evidence (CaseDocument)
        → SourceReference (320 rows) or DatasetFile row
        → storage_key / relative_path
        → object store  (local here, MinIO in production)
        → bytes read, hash re-computed
        → provenance checks computed, never asserted:
            source_verified        source_confidence == VERIFIED and COMPLETE
            record_available       the bytes were actually read
            traceable_to_original  a source reference or dataset file points at it
            hash_matches           recorded hash re-computed from the stored bytes
        → preview with a machine-readable code on every branch
```

Every failure carries a distinct code: `active_dataset_unavailable`,
`source_record_not_found`, `source_file_not_found`, `source_bytes_missing`,
`storage_unavailable` (HTTP 503), `unsupported_file_type`,
`preview_generation_failed`, `permission_denied`, `backend_unavailable`.

---

## E. Test results

### Backend — `pytest tests/`

**927 passed, 1 failed, 1 skipped** (929 collected).

| new / updated file | tests |
|---|---|
| `tests/test_person_centric_analytics.py` (new) | 15 |
| `tests/test_provenance_checks_can_fail.py` (new) | 7 |
| `tests/graph_rag/test_contradictory_deep.py` (+4) | 61 total |
| `tests/test_network_analysis.py` (updated) | 6 |

`test_network_analysis.py::test_entity_types_are_preserved_in_metrics` needed
updating: it asserted a `BANK_ACCOUNT` row appears in `mode="case"` metrics,
which is exactly what the person-centric rule now forbids. It asserts type
preservation in MASTER (where entity rows are legitimate) **and** that CASE
surfaces no non-person row.

### Frontend

| command | result |
|---|---|
| `npm test` | **193 passed, 0 failed** |
| `npm run build` (`tsc -b && vite build`) | **✓ built in 5.00s**, no errors |

---

## F. Live API verification

### 26 acceptance checks — **26/26 passed**

```
[PASS] 12a. case-d2-024 dashboard -> 200
[PASS] 12b. all 25 case dashboards -> 200
[PASS]  3a. People Network nodes are PERSON only -- {'PERSON'}
[PASS]  3b. node_types declares PERSON only -- ['PERSON']
[PASS]  3c. view is PERSON_NETWORK
[PASS]  3d. every edge joins two PERSON nodes -- 0 dangling
[PASS]  3e. one unordered person pair = one relationship -- 0 duplicated pairs
[PASS]  3f. multi-record pairs aggregate into one edge -- 400 edges
[PASS]  4a. confirmed criminal count > 0 -- 3
[PASS]  4b. star count == reported count -- 3 vs 3
[PASS]  4c. every starred node carries a criminal_status
            Priya Kumar, Vikram Verma, Amit Sharma
[PASS]  4d. nodes show real names, not uuids
[PASS]  7a. relationship evidence returns real supporting records -- 12
[PASS] 16a. strength is distributed, not blanket STRONG -- {'STRONG': 126, 'MODERATE': 274}
[PASS] 15a. classification inputs are real and varied -- 41 distinct
            strength/confidence pairs, confidence 0.825-0.98
[PASS]  7b. cross-case flag is real, not constant -- 358/400 cross-case
[PASS]  2a. network-analysis accepts the request -- 200
[PASS] 10a. CR-2007 preview works -- AVAILABLE/available
[PASS] 10b. missing file -> source_file_not_found
[PASS] 10c. reason does not say workspace unavailable
[PASS] 10d. path traversal rejected -- 422
[PASS] 19a. evidence -> source -> file works for 40 documents
[PASS] 14a. provenance hashes distinct per document -- 79 distinct
[PASS] 13a. exactly one active dataset
[PASS] 13b. the active dataset is demo-dataset-002
[PASS]  8a. dashboard carries a real last_activity_at
```

### End-to-end demo flow (§20) — **18/18 steps pass**

```
[OK]  1. Login                     role=INVESTIGATOR badge=DEMO-INVESTIGATOR
[OK]  2. Cases list                25 cases
[OK]  3. Open a case               CR-2025
[OK]  4. Case dashboard            entities=41 relationships=204 evidence=79
[OK]  5. View involved people      7 people
[OK]  6. Open People Network       104 nodes, 400 edges
[OK]  7. PERSON<->PERSON only      labels={'PERSON'}, 0 dangling edges
[OK]  8. Confirmed criminals ★     3: Priya Kumar, Vikram Verma, Amit Sharma
[OK]  9. Select a relationship     Manoj Khan <-> Amit Ansari (COMMUNICATION)
[OK] 10. Why they are connected    types=[COMMUNICATION, FINANCIAL_LINK,
                                   KNOWN_ASSOCIATION] supporting=12 STRONG cross_case=True
[OK] 11. Supporting evidence       12 records
[OK] 12. Open the source record    SOURCE-0024.pdf -> sources/CR-2025/SOURCE-0024.pdf
[OK] 13. Preview the actual file   status=AVAILABLE kind=pdf
[OK] 14. Trace provenance          all four checks OK, hash re-computed from bytes
[OK] 15. Timeline/activity         8 entries
[OK] 16. Analytical findings       200
[OK] 17. Cross-case relationships  358/400 edges span more than one case
[OK] 18. Return to workspace       cases=25 me=DEMO-INVESTIGATOR
```

---

## G. Remaining limitations — stated plainly

1. **Environment.** Still no Postgres, Neo4j, MinIO, Redis or Docker in this
   sandbox (re-verified: all client binaries absent, nothing listening on
   5432/7687/9000/6379). Everything ran on the `embedded` profile — sqlite,
   local object store, embedded graph, inline broker. The production adapter
   paths are **not** exercised.

2. **One pre-existing backend failure.**
   `tests/test_runtime_context.py::test_unreachable_postgres_message_is_actionable`
   asserts the phrase `'does not fall back to SQLite'` in a message that never
   contained it. Present at `30154e3` before any edit; left alone rather than
   editing someone else's assertion to match code I did not write.

3. **Every demo relationship classifies as FACT.** The *rule* is derived
   (`lib/classification.ts`: FACT only when strength is STRONG/MODERATE **and**
   confidence ≥ 0.6, degrading to INFERENCE / HYPOTHESIS / UNKNOWN), and the
   inputs are real — all 2548 graph edges carry a `confidence` property with 8
   distinct values from 0.8 to 0.98, so the `1.0` default is never exercised.
   But the seeded v2 corpus is uniformly high-confidence, so the output is
   uniformly FACT and the INFERENCE/HYPOTHESIS/UNKNOWN branches are **not
   exercised by the demo data**. If the presentation needs to show a non-FACT
   relationship, the corpus needs lower-confidence records — that is a data
   change, and I did not make one silently.

4. **40 of 360 documents have no `SourceReference` row.** They pass
   `traceable_to_original` on the strength of a registered `DatasetFile`, which
   is a real source-file record, and the detail string says so explicitly
   (`"0 source reference(s), dataset file registered"`). Not a fake green, but
   a weaker link than the other 320.

5. **Unexplained node-count gap.** The seeder reports 575 graph nodes;
   `/graph/master?limit=600` returns 527 and the relationship endpoint 104
   persons in one case scope. The 48-node difference is not investigated.

6. **Sandbox reset mid-session.** The workspace lost `.venv-cl`, the data
   directory and the harness scripts, and git history reverted to the branch
   point while the working tree survived. History was recovered from the remote
   (`git fetch` + `git reset --mixed FETCH_HEAD`); the environment was rebuilt
   and the dataset re-seeded. All results above are from **after** the rebuild.

---

## H. Files changed this round (9)

**Backend — source (5)**
`app/investigator/patterns.py` · `app/investigator/network_analysis.py` ·
`app/ai/person_graph_rag.py` · `app/services/source_viewer.py` ·
`app/api/v1/sources.py`

**Backend — tests (4)**
`tests/test_person_centric_analytics.py` (new, 15) ·
`tests/test_provenance_checks_can_fail.py` (new, 7) ·
`tests/graph_rag/test_contradictory_deep.py` (+4) ·
`tests/test_network_analysis.py` (updated)

`9 files changed, 1179 insertions(+), 72 deletions(-)`

---

## I. What was not done

- Did not make the People Network show a non-FACT relationship by weakening the
  classification rule; the corpus is what it is (limitation 3).
- Did not delete the entity-level analytics; they were re-scoped and labelled.
- Did not add `startTime`/LCP instrumentation (§22 — re-verified: zero hits for
  `reportAllChanges|web-vitals|getLCP|getCLS|onLCP` in `frontend/src`,
  `index.html`, `vite.config.ts`, `package.json`).
- Did not relax the single-active-dataset index or add a second active dataset.
- Did not invent contradictions to prove the detector works; the negative paths
  are driven by real broken documents in tests.
- Did not "fix" the two live checks that initially failed by loosening them —
  both were harness bugs (a stale `TOKEN` binding, and a call to a
  case-scoped `relationship-evidence` route that does not exist).

---
---

# First round — 20-workstream fix report

**Code commit:** `c81e0b6` (this report is committed immediately after it)
**Branch:** `arena/01a0afee-crimelink` · **Base:** `30154e3d6fd7feb6c83cec834f8aab6c4f94e780`

---

## 1. Environment caveat (read first)

The production log you supplied runs the `production` profile: Postgres `:5432`,
Neo4j `:7687`, MinIO `:9000`, Redis `:6379`, Celery. **None of those services,
and no Docker, exist in this sandbox** (`command -v psql docker` → nothing). I
therefore reproduced and verified everything against the repo's own `embedded`
profile:

| adapter | production (yours) | this sandbox |
|---|---|---|
| relational | postgres | sqlite `/home/user/clvar/data/crimelink.db` |
| object store | minio | local filesystem |
| graph | neo4j | embedded |
| broker | celery | inline |

Schema revision is `ad1e2f3a004` in both. The defects fixed here are in
application code and adapter-independent SQL, so they reproduce under either
profile — but the Postgres/Neo4j/MinIO paths themselves are **not** exercised
here and I am flagging that rather than implying otherwise.

Sandbox recipe (repeatable): `source /tmp/clenv.sh`, `upgrade_database`,
`scripts/seed_demo_v2.py`, uvicorn on `:8000`. Seeded: 25 cases, 120 people, 320
evidence docs, 40 sources, 2682 relationships, graph 575 nodes / 2776 edges.

---

## 2. Root cause of every error

### 2.1 `GET /api/v1/cases/case-d2-024/dashboard` → 500  *(workstream 1)*

**Root cause — `backend/app/services/case_dashboard.py:173`, pre-existing:**

```python
pat_timestamp_attr = "detected_at" if hasattr(patterns[0], "detected_at") and patterns else "created_at"
```

`hasattr(patterns[0], ...)` **subscripts the list before the emptiness guard**,
so `and patterns` can never protect it. Any case with zero `DetectedPattern`
rows raised `IndexError: list index out of range` and the route returned 500.

Reproduced before the fix: **15 of 25 dashboards returned 200, 10 returned 500**,
all with the identical traceback at that line — matching the three trace ids in
your log. `case-d2-024` is one of the 10.

The dead branch is also meaningless: `detected_at` is a non-nullable column on
`DetectedPattern`, so there is nothing to probe.

**Fix:** removed the probe entirely; added a typed sort key
`_pattern_sort_key(pattern) -> (pattern.detected_at or datetime.min, pattern.id or "")`,
which additionally stops a `None` timestamp being compared against a string id.

**No hardcoding:** the fix is a general emptiness/ordering correction. Verified
across **all 25 seeded cases → 200**, not just `case-d2-024`.

### 2.2 `GET /api/v1/sources/preview?path=evidence/CR-2007/...pdf` → 404  *(workstream 2)*

**This does not reproduce on the current tree, and I did not "fix" it blindly.**
With the v2 dataset seeded and active, the exact path resolves:

```
GET /api/v1/sources/preview?path=evidence/CR-2007/CR-2007_INTELLIGENCE_REPORT_01.pdf
→ 200  status=AVAILABLE  code=available  openable=True
```

and a full sweep of **360 documents → 360 available, 0 preview failures**.

I traced each candidate cause you listed against the code and data:

| candidate | verdict |
|---|---|
| path resolution | OK — `sources.py::_resolve_source_path` recovers the key from `document.source_metadata["relative_path"]`; `/explore/documents` returns `relative_path: null` and the drawer must not trust that field |
| active-dataset workspace | OK — `/datasets/active` → `demo-dataset-002` ACTIVE/READY |
| MinIO key vs local store | **real defect found** — see 2.3 |
| encoding | OK — `encodeURIComponent` + `safe=""` round-trips |
| dataset-ID mismatch | not present |
| missing seeded file | not present — every seeded `EvidenceDocument` has bytes |
| route mismatch | not present |

The 404 in your log is consistent with the dataset **not** being seeded/active at
that moment; it is not a code path defect I could reproduce. What *was* a real
defect is the next item.

### 2.3 Storage outage misreported as "not found"  *(workstreams 2, 13)*

**Root cause — `backend/app/services/source_viewer.py:273` and `:287`:** two
production branches caught a MinIO/S3 failure and raised
`SourceAccessError(..., status=STATUS_NOT_FOUND)`. A storage outage therefore
told the investigator *"that evidence does not exist"* — the worst possible
message in an evidence system.

**Fix:** both now raise `STATUS_STORAGE_UNAVAILABLE` / `code=storage_unavailable`;
`sources.py::_try_minio` raises `StorageUnavailableError` rather than swallowing
it; `/sources/file` maps the outage to **503**.

### 2.4 The generic "Active dataset workspace is unavailable."  *(workstream 13)*

Replaced by nine data-driven codes. Live-verified:

| condition | HTTP | `status` | `code` |
|---|---|---|---|
| file present | 200 | `AVAILABLE` | `available` |
| path resolves to nothing | 200 | `NOT_FOUND` | `source_file_not_found` |
| source row absent | 200 | `NOT_FOUND` | `source_record_not_found` |
| no active dataset | 200 | `NOT_FOUND` | `active_dataset_unavailable` |
| object store down | **503** | `STORAGE_UNAVAILABLE` | `storage_unavailable` |
| path traversal | **422** | — | `validation_failed` |

Also `source_bytes_missing`, `unsupported_file_type`, `preview_generation_failed`.
The reason string now names the path — e.g. `"Source file not found in the active
dataset: evidence/CR-2007/NOPE.pdf"` — and never says "workspace is unavailable".
`grep -rn "Active dataset workspace is unavailable" backend/ frontend/src/` →
**3 hits, all historical comments in docstrings, zero user-facing strings.**

### 2.5 Criminal stars at zero  *(workstream 4)*

**Not a rendering bug and not fabricated.** `/graph/master/relationships?limit=75`
returns `counts.confirmed_criminals = 2`, and at `limit=400` → **3**: Priya Kumar,
Amit Sharma, Vikram Verma. Your screenshot's "54 people · 75 relationships ·
0 confirmed criminals" reproduces at `limit=75` where only 2 of the 3 criminals
fall inside the window — i.e. the counter and the stars were both *correct* and
the window was small.

`displayLabels.ts::isConfirmedCriminal` keys off the persisted
`criminal_status` against `{confirmed, convicted, accused, chargesheeted,
criminal}` only. `SUSPECT`, `WITNESS`, `VICTIM`, `ASSOCIATE`, `INFORMANT`,
`PERSON_OF_INTEREST` are **not** in that set, and neither is any centrality or
degree measure. The star, red fill (`#DC2626`), amber ring (`#F59E0B`) and label
prefix `★` all read the same flag. The legend count is the API's own count, not a
locally recomputed one, so the two cannot drift.

### 2.6 Flaky `UNIQUE constraint failed: datasets.is_active`  *(workstream 16)*

Found while establishing a stable baseline — **not caused by my changes.** I
verified this by stashing all edits and running the pristine tree: **4 failures
in 6 runs at `30154e3`.**

**Root cause:** `registry.set_only_active` marked the outgoing dataset
`is_active=False` and the incoming one `True` in the same unit of work.
SQLAlchemy batched both dirty rows into one `executemany`
(`parameters = [(1, 'new-id'), (0, 'old-id')]`) whose row order is undefined.
When the `1` row applied first, the partial unique index rejected it. The same
hand-rolled flip existed at `tests/test_investigator.py:1867`.

**Fix:** deactivation is flushed **before** activation, in both
`set_only_active`, `set_only_active_sync` and the test. The index is untouched —
`select(...).where(is_active=True).limit(1)` was never introduced. Result: **8/8
consecutive runs pass**, and `test_dataset_activation_order.py` (3 new tests)
pins the ordering.

### 2.7 Chrome `VM57:2 ... reading 'startTime'` at `et.reportAllChanges`  *(workstream 17)*

**Not CrimeLink's code.**

```
grep -rn "reportAllChanges|web-vitals|getLCP|getCLS|onLCP|PerformanceObserver|startTime" \
     frontend/src frontend/index.html frontend/vite.config.ts frontend/package.json
→ zero hits
```

`index.html` is 12 lines with no injected script; `reportAllChanges` is the
`web-vitals` option name, which is not a dependency. This is browser-extension or
devtools instrumentation. **No `startTime` code was added**, per your instruction.

---

## 3. Implementation summary by workstream

| # | What changed | Files |
|---|---|---|
| 1 | Dashboard IndexError removed; typed sort key; `last_activity_at` (4 unbounded `max()` queries — no capped slices) and `stats.findings` added | `backend/app/services/case_dashboard.py` |
| 2, 13 | Nine distinct failure codes; outage ≠ not-found; `code` on every preview branch; `/file` → 503 | `source_viewer.py`, `api/v1/sources.py`, `errors.py` |
| 3 | Verified, not re-generated: 360/360 seeded docs have real bytes; hashes are distinct per document | *(no change needed)* |
| 4 | Star derived from `criminal_status` only; count read from the API | `displayLabels.ts` (unchanged, now covered by tests), `PersonRelationshipNetwork.tsx` |
| 5, 6 | People Network renders PERSON→PERSON only; phones/accounts/vehicles/locations/orgs/events appear solely as edge evidence with counts | `PersonRelationshipNetwork.tsx` |
| 7, 8 | New pure geometry module: `ringRadiusFor`, `boundedRingGeometry`, `concentricRingRadii`, `fitZoomFor`, `zoomExtentFor`, `labelZoomThreshold`, clamped pan, fit/reset | `lib/graphViewport.ts` (**new**, 523 lines) |
| 7, 9 | Shared canvas hook + zoom/fit/reset control row; node budget and zoom-dependent labels; full graph stays an explicit mode | `lib/useGraphCanvas.ts` (**new**), `components/common/GraphViewControls.tsx` (**new**) |
| 10 | The two views call different endpoints (`/graph/master` vs `/graph/master/relationships`) with different contracts | `MasterCaseNetwork.tsx`, `PersonRelationshipNetwork.tsx` |
| 11, 12 | Edge panel shows the persisted record; provenance ticks read `payload.checks` (server-computed, `ok: boolean \| null`) | `PersonRelationshipNetwork.tsx`, `EvidenceDrawer.tsx` (unchanged, now covered) |
| 14 | Bounded, visibility-gated live refresh + stale-data notice | `lib/useLiveRefresh.ts` (**new**), `components/common/StaleDataNotice.tsx` (**new**), `Cases`, `CaseWorkspace`, `EvidencePage`, `TimelinePage`, `PeoplePage` |
| 15 | Traversal verified case → dashboard → people → relationships → evidence → source → preview for 8 cases, 0 dead ends | *(verified)* |
| 16 | Activation ordering fixed; unique index preserved; no data deleted | `datasets/registry.py`, `tests/test_investigator.py` |
| 18 | Six silent catches replaced with surfaced errors + retry | `SourceViewer.tsx`, `InvestigatorWorkspace.tsx`, `CaseDetail.tsx` |

**Design decision worth flagging:** a `circle`/`concentric` ring is **not**
compressed to fit the container. It lays out at the spacing-correct radius
(`ringRadiusFor(100)=1432.6`, `(500)=7162.0`) and `fitToView` with
`minZoom ≤ fitZoom` brings it on screen. Readability is controlled by a node
budget and zoom-dependent labels, never by shrinking the geometry.

**Bug found by the new tests, not by reading:** `zoomExtentFor` clamped its floor
with `Math.max(0.01, …)`, which exceeded the fit zoom for a 400 000×300 000 bbox —
the full graph was genuinely unreachable. The floor is a NaN guard, never a cap.

---

## 4. Tests executed

### Backend — `pytest tests/ -q`

**901 passed, 1 failed, 1 skipped.** The single failure is pre-existing (see §6).

New/updated files:

| file | tests | result |
|---|---|---|
| `tests/test_case_dashboard.py` (new) | 6 | 6 passed |
| `tests/test_source_preview_error_states.py` (new) | 5 | 5 passed |
| `tests/test_dataset_activation_order.py` (new) | 3 | 3 passed |
| `tests/test_investigator.py` (ordering fix) | — | 8/8 consecutive runs pass |
| combined source/dashboard/demo sweep | 54 | 54 passed |

Note: `TestClient(app)` defaults to `raise_server_exceptions=True`, so the
dashboard 500 tests use `raise_server_exceptions=False` to reach the real
handler — otherwise they assert nothing.

### Frontend

| command | result |
|---|---|
| `npm test` | **193 passed, 0 failed** (was 154) |
| `npm run typecheck` (`tsc -b`) | clean |
| `npm run build` (`tsc -b && vite build`) | **✓ built in 4.32s**, no errors |

New files: `tests/graph-viewport.test.mjs` (21 tests),
`tests/criminal-stars-and-live-refresh.test.mjs` (18 tests).
`tests/person-relationship-network.test.mjs` was updated because it matched
removed implementation details (`cyRef.current?.destroy()`, a literal star-label
template); it now asserts behaviour.

---

## 5. Live API verification

`/tmp/clverify/accept20.py` against uvicorn on `:8000`, `DEMO-ADMIN` session —
**17/17 passed**:

```
[PASS]  1. case-d2-024 dashboard -> 200
[PASS]  2. all 25 dashboards -> 200
[PASS]  3. CR-2007 preview works — code=available
[PASS]  4. original record opens — evidence/CR-2007/CR-2007_INTELLIGENCE_REPORT_01.pdf
[PASS]  5. People Network is PERSON-only — {'PERSON'}
[PASS]  6. criminal count > 0 — 3
[PASS]  7. stars == count and every star has a status — 3 == 3
[PASS]  8. relationship evidence returns supporting records — 12 records
[PASS]  9. provenance checks are computed, not constant — all four present
[PASS] 13. distinct failure codes — missing=source_file_not_found traversal=422
[PASS] 14. real last_activity_at for staleness — 2026-09-17T15:20:02.542310
[PASS] 15. no dead-end across 8 cases (case→graph→evidence→source→preview)
[PASS] 16. exactly one active dataset — [('demo-dataset-002', True)]
[PASS] 17. provenance computed per document — 120 docs, 120 distinct hashes
[PASS] 18. strengths derived, not blanket STRONG — {'STRONG': 127, 'MODERATE': 273}
[PASS] 19. test suites green
[PASS] 20. this report written
```

Checks 10–12 (viewport geometry, fit-after-layout, zoom recovery, node
separation, large-graph usability) are interaction behaviour and are asserted in
`frontend/tests/graph-viewport.test.mjs` rather than over HTTP:
`ringRadiusFor(100)=1432.6` · `boundedRingGeometry(100, 800×500)={radius:195,
achievedSpacing:12.25}` · `concentricRingRadii(500, 800×500)` → 13 rings
`[15…195]` · `fitZoomFor(1000×1000, 900×520, 32)=0.456` ·
`fitZoomFor(4000×3000, 800×500)=0.1453` · `labelZoomThreshold` 10:0, 50:0.41,
200:0.57, 600:1.02.

`case-d2-024` detail: entities 41 (Person 7, Phone 10, Vehicle 3, Location 8,
BankAccount 5, Event 8), relationships 207, evidence 80, documents 13,
patterns 0 — **the zero that used to crash the route.**

---

## 6. Remaining pre-existing failure

**`tests/test_runtime_context.py::test_unreachable_postgres_message_is_actionable`**

```
AssertionError: assert 'does not fall back to SQLite' in
'PostgreSQL is not running (localhost:5432).\n\nStart the CrimeLink
 infrastructure services and retry: ...'
```

The test asserts a phrase the message does not contain. Present at `30154e3`
before any edit — it was the only failure in my first baseline run — and left
alone deliberately: it is an assertion about wording, unrelated to these
workstreams, and "fixing" it would mean editing a test to match code I did not
write. **It is the sole remaining backend failure.**

Also noted, unfixed, out of scope but worth your attention:

- `source_viewer._preview_pptx_from_bytes` may raise
  `SourceAccessError(..., code="CORRUPTED")` while `__init__` takes `status` —
  would `TypeError` on a corrupt PPTX. **Suspected, not reproduced.**
- `seed_demo_v2.py` reports 575 graph nodes but `/graph/master?limit=600` returns
  527. Unexplained gap of 48; **not investigated.**
- `test_investigator.py::test_cross_dataset_isolation_and_thread_pinning` fails
  under `-x` but passes in a full run (order sensitivity in fixtures).

---

## 7. Files changed (29)

**Backend — modified (5)**
`app/services/case_dashboard.py` · `app/services/source_viewer.py` ·
`app/api/v1/sources.py` · `app/errors.py` · `app/datasets/registry.py`

**Backend — tests (4)**
`tests/test_case_dashboard.py` (new) · `tests/test_source_preview_error_states.py` (new) ·
`tests/test_dataset_activation_order.py` (new) · `tests/test_investigator.py`

**Frontend — new (5)**
`src/lib/graphViewport.ts` · `src/lib/useGraphCanvas.ts` ·
`src/lib/useLiveRefresh.ts` · `src/components/common/GraphViewControls.tsx` ·
`src/components/common/StaleDataNotice.tsx`

**Frontend — modified (12)**
`src/api/client.ts` · `src/components/SourceViewer.tsx` ·
`src/components/investigator/MasterCaseNetwork.tsx` ·
`src/components/investigator/PersonRelationshipNetwork.tsx` ·
`src/components/investigator/CaseHeader.tsx` · `src/pages/CaseDetail.tsx` ·
`src/pages/CaseWorkspace.tsx` · `src/pages/Cases.tsx` ·
`src/pages/EvidencePage.tsx` · `src/pages/InvestigatorWorkspace.tsx` ·
`src/pages/PeoplePage.tsx` · `src/pages/TimelinePage.tsx`

**Frontend — tests (3)**
`tests/graph-viewport.test.mjs` (new) ·
`tests/criminal-stars-and-live-refresh.test.mjs` (new) ·
`tests/person-relationship-network.test.mjs`

Diffstat: **29 files changed, 2970 insertions(+), 239 deletions(-)**

---

## 8. What I did *not* do

- Did not force stars into the UI, infer criminal status from centrality, or
  promote SUSPECT/WITNESS/VICTIM/ASSOCIATE/INFORMANT/PERSON_OF_INTEREST.
- Did not mark relationships FACT/STRONG wholesale — the live data shows
  127 STRONG / 273 MODERATE.
- Did not add `startTime`/LCP instrumentation.
- Did not relax the single-active-dataset index, add a second active dataset, or
  delete dataset data.
- Did not add `select(...).where(is_active=True).limit(1)`.
- Did not add aggressive polling — refresh is event-driven plus
  visibility-gated, throttled to ≥15 s, and never fires in a hidden tab.
- Did not shrink nodes or hide them to make the graph look tidy.
- Did not re-seed or regenerate evidence files; the 360 already on disk were
  verified instead.
