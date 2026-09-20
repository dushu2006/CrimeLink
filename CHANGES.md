# CrimeLink — investigative intelligence round (four capabilities)

**Branch:** `arena/01a0bf94-crimelink`

This round makes the Case Evidence Assistant reason *across* records instead of
only *about* one record at a time. Four capabilities were added — hybrid
semantic retrieval, narrative contradiction detection, evidence corroboration
and temporal reasoning — plus the pipeline that combines them, without adding a
service, a key or a dependency.

## A. What was missing

| Capability | Before | After |
|---|---|---|
| Retrieval | lexical + structured + graph only | hybrid: lexical → semantic supplement → graph → merge → rerank → case boundary |
| Contradictions | structural (dates, amounts, counts) | narrative: location, time, role/identity, amount, event description, relationship, status |
| Corroboration | not first-class; a repeated fact was indistinguishable from a repeated sentence | `support_count`, independent evidence types, and a status per assertion |
| Chronology | phase buckets around the incident | anchored selection: before/after/between/around/closest, with relative offsets and overlapping intervals |

## B. How it is built

One new module per capability, all standard-library-only, all deterministic
first:

* `app/ai/claims.py` — claims are the shared foundation. A claim is one
  assertion attributed to **one** stored record (`dimension`, subject, object,
  time, document, evidence type, quote). CSV/JSON evidence is read row-by-row
  through the header, so a CDR row and a ledger row produce the same kinds of
  claims a diary sentence does. Repeats inside one record collapse to one
  claim: repetition is not corroboration.
* `app/ai/contradiction.py` — groups claims by (subject, dimension, time
  bucket) and reports the pairs that disagree, each with both accounts, their
  documents and `status: UNRESOLVED`. It detects conflict; it never decides
  truth, and it cannot cite a record the case does not hold.
* `app/ai/corroboration.py` — one document is one source. An assertion is
  `SINGLE_SOURCE`, `MULTI_SOURCE_SUPPORTED`, `MULTI_TYPE_CORROBORATED`,
  `CONFLICTED` (a contested assertion is never presented as supported) or
  `UNRESOLVED`.
* `app/ai/temporal.py` — events normalised as
  `{event_id, timestamp, start_time, end_time, entity_ids, event_type,
  description, sources}`; relations BEFORE / AFTER / BETWEEN / AROUND /
  NEAREST / CHRONOLOGICAL resolved against a real anchor (the case's incident
  time when the question names the incident). An empty window widens to the
  nearest records **and says so** rather than implying nothing happened.
* `app/ai/semantic.py` — a local per-case JSON index beside the embedded graph.
  Chunks carry case_id, document_id, chunk_id, evidence type, source metadata,
  the pseudonymised text, the vector and a content hash. A changed record is
  re-embedded; a retired record is dropped from disk. With no embedding key the
  local hashing embedder is used, so the embedded profile stays runnable with
  no network.

Pipeline (both privacy modes):

    PLANNER → CASE-SCOPED RETRIEVAL → RERANK → SEMANTIC SUPPLEMENT →
    CASE BOUNDARY → INTELLIGENCE (claims → conflicts → corroboration →
    chronology) → BOUNDARY + PSEUDONYMISE → LLM → CLAIM VALIDATION →
    COMPOSER → CONTROLLED DE-ANONYMIZATION

## C. Privacy and isolation (unchanged, and re-verified)

* The vector index is a retrieval optimisation, **never** an authorization
  layer: it is built from case-scoped records, queried inside the authorised
  document set, and its hits still pass the same case validation.
* In strict mode the index stores pseudonymised text only — a name never
  reaches an embedding, and a test asserts the stored index contains
  `PERSON_01` and not the real name.
* The identity map stays inside CrimeLink. Contradiction, corroboration and
  temporal objects are scrubbed before they reach a prompt, and a test asserts
  no real name, and no map key, appears in the provider prompt.
* Retrieved records are DATA. Instruction-like spans are removed outright
  before the prompt is built (not merely labelled), the prompt keeps a clear
  SYSTEM / USER / RETRIEVED-DATA separation, and a regression test drives the
  exact injection string from the brief through the pipeline.

## D. Verification

* `backend/tests/test_semantic_retrieval.py` (9), `test_narrative_contradictions.py` (14),
  `test_evidence_corroboration.py` (8), `test_temporal_reasoning.py` (11),
  `test_investigative_security.py` (10), `test_case_intelligence_regression.py` (14).
* Backend suite: **9 failed, 1052 passed** — the same nine pre-existing
  failures as the base commit (demo v2 data quality, data integrity audit and
  one runtime-context assertion); every new suite is green.
* Frontend untouched: `tsc -b` clean, 193 tests pass, production build clean.
* Live CR-2020 smoke (12 questions, deterministic path, no provider key):
  **12/12 distinct**, each answer shaped by its question — a case header for
  details, 10 people, 12 documents/11 types, "no direct documented connection"
  for the person pair, 4 accounts for the financial question, a 12-event
  chronology before the incident, the record in the ±6h window for
  communications, an honest "no conflicting accounts" comparison for
  contradictions, the one multi-record assertion with its four documents for
  corroboration, an 85-event nearest-first sequence, a 3-event window between
  22 and 29 May, and a two-paragraph briefing for the summary.
* Real finding on the demo case: **"Anjali Hussain is documented as witness"
  is recorded in four independent documents across four evidence types**
  (CASE_DIARY, CHARGE_SHEET, FIR, WITNESS_STATEMENT) — previously invisible
  because each record was read in isolation.

## E. Honest limitations

* Model-assisted narrative claim extraction exists (`validate_candidate_claims`)
  but is off by default; the deterministic extractor is what runs.
* The local hashing embedder is a lexical-semantic fallback, not a trained
  model: with no embedding key it finds paraphrases through shared vocabulary,
  not through learned meaning. The provider path is wired (`AIModelRouter.embed`)
  and is used automatically once a key is configured.
* Corroboration counts *documentary* support. It is deliberately silent about
  truth, guilt and legal effect, and the wording it renders says so.

---

# CrimeLink — third-round audit (22-section brief)

**Code commit:** `77ba473` · **Doc commit:** this file
**Branch:** `arena/01a0afee-crimelink` · **Base:** `30154e3d6fd7feb6c83cec834f8aab6c4f94e780`

The second-round report follows below, and the first-round report below that.
Rounds one and two are unchanged and still stand. This section covers the one
genuine gap the third brief exposed.

---

## A. What the third brief exposed

§3 asks for an audit of **every** analytical detector. Round two audited the two
detectors that happened to *fire on the demo corpus* — `detect_cross_case_entities`
and `detect_community_signals` — fixed them, and reported "0 non-person subjects".

That statement was true for the seeded corpus and **false in general**. Re-reading
§3 and then reading all eleven detectors' `title=` / `entities=` / `entity_keys=`
construction showed **seven more** that could put a supporting entity in the
subject position:

| Detector | Subject it built |
|---|---|
| `detect_bridge_signals` | any node ranked by betweenness — a hub phone qualifies |
| `detect_communication_anomalies` | `CALLED` endpoints, i.e. **phone ↔ phone** |
| `detect_financial_flows` | the **bank account** itself |
| `detect_cross_case_links` | any edge endpoints spanning disjoint cases |
| `detect_temporal_bursts` | any edge endpoints |
| `detect_repeated_combinations` | any co-occurring pair |
| `detect_vehicle_mismatches` | title was person-centric; `entities`/`entity_keys` still carried the vehicle |

Two were already correct: `detect_colocations` builds its subject from
`presence[person]` off `LOCATED_AT` edges, so its keys are persons by
construction; `detect_er_signals` never fired on this corpus (see §E).

**Why round two missed it:** verifying against one dataset proves only that the
detectors active in *that* dataset are clean. A corpus with no dated transfers
simply never reaches the financial-flow detector, so its bug stays invisible.

## B. Root cause

The detectors are written against **edges**, because that is what the graph
provides. Their natural subject is whatever the edge endpoints happen to be.
Person-centric scope is a *presentation and analysis* requirement layered on top,
and nothing enforced it: each detector was individually trusted to have made a
person the subject. Seven had not.

## C. The fix

Rather than rewrite nine detectors — which would have meant re-deriving each one's
evidence, strength, case list, contradictions and innocent alternatives — the fix
is a **subject-resolution layer** in `detect_all_patterns`
(`backend/app/investigator/patterns.py`, inserted immediately above it):

- `_person_subjects` — the people already in a finding's `entity_keys`.
- `_people_via_support` — for a non-person subject, the people attached to it
  through a first-class relationship or a bridged record
  (Person → Phone → call → Phone ← Person).
- `_reframe_for_person_subject` — rewrites `title`, `entities` and `entity_keys`
  onto those people, **keeping the detector's own evidence, strength, cases,
  contradictions and innocent alternatives intact**. The mechanism (the phone,
  the account) is retained in the evidence text, and the reframing is disclosed
  as a provenance pointer rather than applied silently.
- `_dedupe_person_patterns` — collapsing subjects exposes latent duplication:
  detectors fire per graph edge, so 13 parallel call records between one phone
  pair become 13 identical person-pair findings. Person-scoped results are merged
  by `(kind, entity_keys)` — union of cases, union of evidence, stronger strength
  wins — and annotated **once, from the final counts**.
- `REFRAME_MAX_PEOPLE = 6` — a hub connecting 40 people is not a person-pair
  finding; it is capped and labelled.

Gated on `person_centric = ctx.subject.upper() != "ENTITY"`. `DetectorContext.subject`
defaults to `"PERSON"`, so all four call sites (`network_analysis.py:536`,
`orchestrator.py:994/1588/1665`) are person-centric unless they explicitly ask
for the evidence layer.

A finding that attaches to **no** person in scope is not dropped silently and not
surfaced with a phone in front: it is set aside with a written reason
(`"Evidence-layer finding: it describes supporting entities that attach to no
person in this scope…"`), which the API returns under `exclusion_reason`.

**ENTITY scope is untouched.** The evidence layer must keep naming phones and
accounts — that is what makes it the evidence layer.

### A defect found and fixed while verifying

The first version of `_dedupe_person_patterns` rebuilt the title on every merge,
so the suffix accumulated: `Priya Kumar ↔ Dinesh Malhotra (6 supporting records)
(9 supporting records) … (39 supporting records)`. The base title is now kept and
the suffix emitted once, at the end, from the final count.

## D. Verification

### D.1 Real seeded master graph (25 cases, 527 in-scope nodes, 2 550 edges)

`detect_all_patterns(..., max_patterns=100000)` — the cap raised so every
detector's output is visible rather than truncated to 25:

| Scope | Live | Set aside | **Findings with no person subject** |
|---|---|---|---|
| `PERSON` | **748** | 10 | **0** |
| `ENTITY` | 706 | 10 | 526 — unchanged, correct for the evidence layer |

Live kinds in PERSON scope: `CROSS_CASE_PERSON_LINK` 522, `CROSS_CASE_LINK` 96,
`CROSS_CASE_ENTITY` 73, `REPEATED_COMBINATION` 46, `COMMUNITY_SIGNAL` 8,
`COLOCATION` 2, `NETWORK_BRIDGE` 1.
Set-aside reasons: 10 × `COLOCATION` — "Single co-location: one shared presence
is coincidence".

Sample reframed titles (real data):
`REPEATED_COMBINATION` → `Ajay Kapoor — 2 records collapsed from the same person`;
`CROSS_CASE_LINK` → `Ajay Kapoor ↔ Varun Thakur (+4 more) — 2 records collapsed…`;
`COLOCATION` → `Harish Chatterjee ↔ Sachin Iyer`.

### D.2 Synthetic graph engineered to fire the seven leaking detectors

`backend/tests/test_person_centric_analytics.py::leaking_detector_graph` — 4
persons, 2 phones, 1 account, 1 vehicle, 13 `CALLED` records, 6 dated
`TRANSFER_TO`, an `ASSOCIATE_OF`/`OWNS_VEHICLE` mismatch.

| Scope | Live | No-person-subject findings |
|---|---|---|
| `PERSON` before | 23 | **17** |
| `PERSON` after | **11** | **0** |
| `ENTITY` after | 23 | 17 — unchanged |

| Kind | Before | After |
|---|---|---|
| `COMMUNICATION_ANOMALY` | `+919000000000 ↔ +919000000137: 37 calls` | `Priya Kumar ↔ Dinesh Malhotra` |
| `FINANCIAL_FLOW` | `ACC-0000: 6 transfers in 7 days` | `Amit Sharma` |
| `VEHICLE_USE_OWNERSHIP_MISMATCH` | carried `RJ-14-CX-1234` in `entity_keys` | `Priya Kumar ↔ Amit Sharma` |

### D.3 The regression tests are genuine

Six tests were added. Patching the gate at `patterns.py:1641` to
`person_centric = False` and re-running makes **three of them fail**, reproducing
the leak verbatim:

```
AssertionError: COMMUNICATION_ANOMALY named no person as its subject:
                '+919000000000 ↔ +919000000137: 37 calls'
AssertionError: duplicated person-subject findings: [... ('CROSS_CASE_LINK', ('NA','NB')) ×13 ...]
AssertionError: the reframing must be disclosed in provenance, not applied silently
```

Restoring the fix returns 21/21.

> **A methodological error worth recording.** My first attempt at this check
> patched the first textual occurrence of
> `person_centric = ctx.subject.upper() != "ENTITY"` — which is line **614**,
> inside `detect_cross_case_entities`, not the real gate at **1641**. It appeared
> to confirm the tests, because four *round-two* tests failed. The six new tests
> passed either way, which is the signature of a test that verifies nothing.
> Re-running against line 1641 produced the failures above. There are three
> occurrences of that line (614, 1077, 1641); only 1641 gates the layer.

### D.4 Live API, seeded dataset `demo-dataset-002`

`GET /api/v1/investigate/patterns` (master) → 200, 110 patterns / 100 live,
**0 naming a supporting entity as a subject**, all 10 set-aside entries carrying
a written reason. `GET /api/v1/graph/master/relationships?limit=3000` → 200,
`view=PERSON_NETWORK`, node labels `{PERSON}` only, 1 661 edges with **0
dangling** and **1 661 distinct person pairs** (no duplicates). **13/13 live
checks pass.**

Criminal stars, checked against the authoritative persisted source
(`graph.json`, 120 PERSON nodes): `criminal_status` histogram
`{CONFIRMED: 3, None: 117}`; the three are Priya Kumar, Vikram Verma, Amit
Sharma. The API returns `counts.confirmed_criminals = 3` and exactly 3
`is_criminal` nodes, all with `criminal_status == "CONFIRMED"`, none unstarred,
and no SUSPECT/WITNESS/VICTIM/ASSOCIATE/INFORMANT role starred on role alone.
(Priya Kumar's `role` is `SUSPECT` — she is starred because of persisted
`CONFIRMED` status, not because of her role.)

An earlier version of this live check read `confirmed_criminals` from the top
level of the response, got `None`, and passed vacuously via `cc is None or …`.
The field lives under `counts`; the check now asserts it is present first.

## E. Test and build results

| Check | Command | Result |
|---|---|---|
| Backend suite | `pytest tests/` | **933 passed, 1 failed, 1 skipped** in 218.95 s |
| Person-centric analytics | `pytest tests/test_person_centric_analytics.py` | **21/21 passed** (15 from round 2 + 6 new) |
| Frontend suite | `npm test` | **193/193 passed** |
| TypeScript build | `tsc -b` | clean |
| Production build | `vite build` | **✓ built in 3.98 s** |

Backend was 927 passed before this round; +6 is exactly the six new tests.
The single failure is the pre-existing
`test_runtime_context.py::test_unreachable_postgres_message_is_actionable`,
deliberately untouched in all three rounds.

**One round-two test had to be corrected, not deleted.**
`test_community_signal_names_people_not_the_shared_entity` asserted the literal
word `"people"` in the title. The reframing layer now names the actual people
(`Amit Sharma ↔ Vikram Verma`), which satisfies the test's *intent* more strongly
but no longer contains that word. The assertion now checks that a real person's
name from `entity_keys` appears and that no supporting entity does. The test's
purpose is preserved and strengthened; it was not weakened to hide a failure.

## F. Files changed

| File | Change |
|---|---|
| `backend/app/investigator/patterns.py` | +199 lines: the subject-resolution layer and the dedup pass |
| `backend/tests/test_person_centric_analytics.py` | +190 lines: six regression tests, `_edge` now forwards extra properties, one assertion corrected |

No frontend change was needed: the frontend renders whatever subjects the API
returns, and the API now returns people.

## G. Remaining genuine limitations

1. **Four detectors cannot fire on the demo corpus, for data reasons rather than
   code defects** — and so are proven only by the synthetic test, not by live
   data:
   - `detect_communication_anomalies` — the busiest phone pair carries **2** calls;
     the threshold is `MIN_CALLS_ANOMALY = 10`.
   - `detect_financial_flows` and `detect_temporal_bursts` — **0 of 2 786** edges
     carry a timestamp-ish key, and both need dated records. The seeder does not
     write edge timestamps.
   - `detect_vehicle_mismatches` — no person is associated with a vehicle owned by
     a different person in the seeded data.
2. `detect_er_signals` produced 0 findings, so its raw subject type is **not
   runtime-verified**; it is covered by the reframing layer like every other
   detector.
3. The 25-pattern default cap (`max_patterns`) means the live endpoint shows only
   the highest-ranked kinds. On this corpus that is cross-case detection; raising
   the cap reveals the other five. This is ranking, not suppression, but it does
   mean the UI under-represents detector diversity on a dense corpus.
4. **Embedded profile only.** No Postgres, Neo4j, MinIO, Redis or Docker in this
   sandbox, so the production adapters remain unexercised.
5. The pre-existing backend failure above is still failing.
6. Every demo relationship still classifies as FACT — the rule is genuinely
   derived and its inputs are real, but the seeded corpus is uniformly
   high-confidence, so the INFERENCE/HYPOTHESIS/UNKNOWN branches are not
   exercised by demo data.
7. 40 of 360 documents still lack a `SourceReference` (they pass
   `traceable_to_original` on a registered `DatasetFile` alone, and the detail
   string says so).
8. **The 575-vs-527 node gap is now traced, and it is a real seeder defect.**
   Diffing `graph.json` against `multi_case_snapshot` gives exactly 48 excluded
   nodes: **25 `CASE`** nodes (excluded by design — the master view is
   entity-level) plus **23 nodes whose `case_ids` is empty**: 11 `PERSON`,
   9 `ORGANIZATION`, 3 `VEHICLE`. The snapshot is built by case membership, so a
   node with no `case_ids` can never enter scope. Those 11 people are therefore
   invisible to every case-scoped and master-scope analysis, to every detector,
   and to the People Network — while still counting towards the seed's reported
   575. Fixing this means correcting the seeder to attach case membership to
   every person it writes, which is a data-generation change outside the scope of
   this brief; it is recorded here rather than silently absorbed.
9. Sandbox resets recurred again this round (`/tmp` scripts, `.venv-cl` and
   `node_modules` all vanished). Recovered with
   `git fetch origin arena/01a0afee-crimelink && git reset --mixed FETCH_HEAD`
   plus a rebuild.

---

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

---

# CrimeLink — Case Evidence Assistant redesign (question-driven RAG)

**Branch:** `arena/01a0bf94-crimelink` · **Base:** `384c810d3fca1e4224b2dd6759c224e17e2242ef`

The Case Evidence Assistant no longer answers every question with the same
template. It now understands the question first, retrieves the evidence that
question needs, and composes an answer whose shape follows the question.

All previous case-grounding guarantees are preserved: canonical case-id
resolution, strict case-scoped retrieval, cross-case contamination prevention,
graph-edge case provenance, citation validation, authoritative deterministic
metrics, empty-retrieval honesty, history isolation, cache isolation, permission
isolation, and "no retrieval fallback broadening".

## A. What the current architecture actually was

| Layer | Before |
|---|---|
| RAG storage | **No vector store.** Retrieval is deterministic: embedded graph snapshot (`graph.json`) + SQL `CaseDocument` rows, ranked by keyword/exact-term scoring. |
| Embeddings | `AIModelRouter.embed()` exists and is wired to a provider role, but nothing indexes or searches vectors. Retrieval was **lexical + graph + structured DB**, not semantic. |
| Provider abstraction | Present and good — per-role OpenAI-compatible endpoints, local or cloud, with honest unavailability reporting. |
| Entity privacy | `PseudonymMap` with dataset-stable pseudo-ids, persisted per dataset, plus prompt-minimization. |
| **Response shape** | **The defect.** One fixed JSON contract (`direct_answer`, `establish`, `does_not_establish`, `why_this_matters`, `limitations`, …) was demanded for every question, and `enrich_finding_contract` re-injected generic versions of those sections whenever the model omitted them. The UI then rendered all of them unconditionally. |
| Deterministic fallback | Existed, but keyed on coarse keyword branches and produced the same "Case Intelligence Briefing" skeleton. |

So the "RAG" was real and deterministic; the problem was that the **answer
generator** ignored the question.

## B. What changed

    USER QUESTION
      → QUERY PLANNER            (new: app/ai/query_planner.py)
      → CASE-SCOPED RETRIEVAL    (existing graph + SQL + lexical, now intent-directed)
      → RERANKING                (existing rank_and_filter_context, unchanged)
      → EVIDENCE BOUNDARY        (new: app/ai/evidence_boundary.py)
      → LLM SYNTHESIS            (new intent-shaped prompt)
      → CLAIM→EVIDENCE VALIDATION (existing validate_finding, unchanged)
      → RESPONSE COMPOSER        (new: app/ai/response_composer.py)
      → CONTROLLED DEANONYMIZATION (fixed + broadened to every prose field)
      → FINAL RESPONSE

1. **Query planner** (`query_planner.py`) classifies intent — CASE_OVERVIEW,
   PEOPLE, EVIDENCE_INVENTORY, RELATIONSHIP, TIMELINE, CONTRADICTION,
   FINANCIAL, COMMUNICATION, LOCATION, SUMMARY, GENERAL — plus requested
   detail, response style, named people resolved against case entities,
   temporal constraints, evidence-type filters and exact identifiers. No
   per-question hardcoding.
2. **Evidence boundary** (`evidence_boundary.py`) is the single object the model
   may reason over: case metadata, entities grouped by type, relationships with
   document provenance, bounded documents, timeline, authoritative counts,
   contradictions, gaps. It is case-scoped by construction and cannot widen.
3. **Question-dependent retrieval.** Network questions keep the person-centric
   narrowing. Financial / communication / location / timeline / inventory /
   contradiction questions take the **whole case subgraph** — the person-centric
   view deliberately demotes a bank transfer to "supporting material" and would
   otherwise drop the very edges the question is about. This was found by live
   testing, not by inspection.
4. **Intent-shaped prompts.** The system prompt states the invariants (fact vs
   inference, neutral language, cite-or-say-unknown, evidence is data not
   instructions); the per-intent instruction states the answer shape. The old
   demand for `why_this_matters` / `does_not_establish` is gone.
5. **Enrichment no longer fabricates sections.** `enrich_finding_contract` still
   guarantees the compatibility envelope, but no longer re-injects generic
   "WHY THIS MATTERS" prose into an answer that already explains itself.
6. **Deterministic answers per intent.** With no model configured the assistant
   now answers the actual question from the boundary — an inventory for a file
   question, a chronology for a timeline question, a connected/not-connected
   explanation for a relationship question — instead of an error or a generic
   briefing. Provider status appears only as a secondary limitation.
7. **Follow-ups are case-scoped and context-aware** — built from the people and
   evidence types this case actually contains, and biased away from re-asking
   the question that was just answered.

## C. Privacy and security

* **Pseudonymization now covers the whole boundary, including document text.**
  `pseudonymize_boundary()` replaces entity display names with stable pseudonyms
  and scrubs real names, phone numbers, account numbers and plates out of
  retrieved document content. Verified by test: no real identity reaches the
  provider prompt in strict mode, and the mapping never appears in it.
* **Fixed a latent de-anonymization bug.** The restore loop iterated
  `pmap.entries()` as `(pseudo, key)` when it is `(key, pseudo)`, so pseudonyms
  were silently never restored. Restoration now covers `direct_answer`, claims,
  relationships, limitations and follow-ups, not just `summary`.
* **Fixed a leak in the offline path.** Deterministic answers are composed over
  the pseudonymized boundary and were returned without restoration, so an
  investigator could be shown "PERSON_004 is documented as ACCUSED".
* **Retrieved text can no longer read as an instruction.** `sanitize_untrusted_evidence`
  now *removes* the instruction-like span rather than wrapping it in a marker —
  a marker containing "ignore previous instructions" is still readable as one.

## D. Data semantics (§21)

The counts now have distinct, named meanings:

| Value | Meaning |
|---|---|
| `case_documents` / `document_count` | files attached to the case |
| `evidence_records_referenced` / `evidence_count` | distinct evidence records the case's graph references |
| `relationship_count` | case-scoped graph relationships |

"12 verified records", "0 relationships" vs "65 operational relationships", and
"operational relationships" as a phrase are gone. Pluralization is grammatical
(`1 person` / `2 people`, `1 phone` / `14 phones`, `1 vehicle` / `4 vehicles`).

## E. Files

**New**
`backend/app/ai/query_planner.py`, `backend/app/ai/evidence_boundary.py`,
`backend/app/ai/response_composer.py`,
`backend/tests/test_case_assistant_architecture.py`,
`backend/scripts/smoke_case_assistant.py`,
`backend/scripts/live_smoke_assistant.py`.

**Modified**
`backend/app/ai/gateway.py` (planner wiring, intent retrieval, boundary + prompt,
deterministic fallback, de-anonymization), `backend/app/ai/case_context.py`
(semantics + frontend-compatible summary), `backend/app/ai/evidence_contract.py`
(no fabricated sections), `backend/app/ai/safety.py` (injection removal),
`frontend/src/components/investigator/CaseRagChat.tsx` (natural answer first,
sections only when informative), and five backend test modules updated for the
new deterministic-answer contract.

## F. Remaining limitations

* **No vector/semantic retrieval.** Retrieval remains lexical + graph +
  structured. Adding embeddings would help "thematic" questions; it is not
  required for the questions in the brief, and no vector dependency was added.
* **Contradiction detection is structural** (dates, amounts, counts). Narrative
  conflicts between witness statements are *not* detected deterministically; the
  answer says so rather than implying none exist.
* **Corroboration and gap-reasoning are not yet first-class** beyond what the
  boundary already exposes.
* **`AIModelRouter.embed()` is still unused** by retrieval.
