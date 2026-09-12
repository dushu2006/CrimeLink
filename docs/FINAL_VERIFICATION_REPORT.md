# CrimeLink — Final Verification Report

**Scope:** the Evidence-Driven Investigator mandate (bring the repository to the fully
working, dataset-agnostic investigator state: inspect → implement/fix → test →
behave → fix → re-test → report).
**Commit under review:** `2dd5b4d` + the working-tree change set described below.
**Nothing has been committed or pushed** (standing instruction).

Verification is behavioural: every claim below is backed by a test run, an HTTP
response, a database row, or a log line — never by the mere existence of a file.

---

## 1. Implementation Summary

The investigator layer is a deterministic pipeline with the model confined to
explanation:

```
Question → Scope → Investigation Memory → Entity Resolution → Deterministic
Evidence/Patterns → Relationships → Hypotheses → Contradictions → Alternatives →
Convergence → Data Gaps → Next Direction → AI Explanation
```

Work delivered in this effort (all on `arena/01a09253-crimelink`):

| Area | What changed |
| --- | --- |
| Orchestration | `app/investigator/orchestrator.py`: one transaction per question, staged timings, dataset-pinned thread, late thread creation, rejected-hypothesis carry-over, import-report wiring |
| Entity resolution | `entity_resolution.py`: ranked name/alias/hard-ID matching, merge chains, capped open proposals, unresolved → DATA_GAP; **sentence-opening question words and possessives no longer invent people** |
| Patterns | `patterns.py`: 11 deterministic detectors + explicit social-only set-asides, existing thresholds untouched, no ML |
| Relationships | `relationships.py`: 7 kinds, each with observation/interpretation/assessment, evidence, provenance, cases, time span |
| Hypotheses | `hypotheses.py`: pair selection, 7 contradiction rules, innocent alternatives, convergence from independent streams only |
| Gaps | `gaps.py`: 9 source families, presence read from the data, import-incompleteness gap (failed vs. records-without-document vs. deliberate exclusions), priority ranking before the cap |
| Memory | `memory.py` + `schemas.py`: objective, questions, facts, relationships, contradictions, rejected hypotheses, prior findings; `GET /sessions/{id}` returns them |
| Provenance | `evidence.py`: typed pointers (`document`, `source_row`, `graph_edge`, `metric`, `audit`, `note`, `dataset`) with flat roll-ups on every finding |
| AI | `app/ai/gateway.py::investigate_narrative`: explain-only contract, strict JSON, deterministic-first, keyless/unparseable/failed all degrade honestly; `prompts.py` language guard |
| API | `POST /investigate`, `GET /investigate/patterns`, `GET /investigate/sessions/{id}` — role-gated, validated, audited (hash-chained) |
| Frontend | `InvestigatorWorkspace.tsx`, `components/investigator/*`, `lib/investigator.ts`: objective, facts, relationships, patterns, hypotheses, contradictions, alternatives, convergence, gaps, next steps, provenance panel, focused graph, timeline, memory; provenance dispatched by pointer type |
| Tests | `backend/tests/test_investigator.py` (126), `backend/tests/conftest.py`, `frontend/tests/investigator-reasoning.test.mjs` (68), `frontend/smoke.mjs` |

---

## 2. Defects Found

| # | Defect | Class | How it surfaced |
| --- | --- | --- | --- |
| D1 | A case **number** produced a different scope than the same case's **id** | D (scope key) | Probe comparing both id and number for one case |
| D2 | A question whose names matched no record still borrowed the scope's strength | A (unearned strength) | "Zafar Qureshi and Farah Bano" returned WEAK/0.3 with no evidence |
| D3 | "No CDR records" reported while CALLED edges were in scope; source presence read from file labels | B (false source gap) | Scope with call edges; label-only presence |
| D4 | `dataset:` / `metric:` / `graph_edge:` pointers were typed as openable documents and dead-ended at `/documents/...`; relationship/hypothesis/pattern roll-ups missing | C (dead provenance) | Response walk: non-document pointers carrying `doc_id`, and empty roll-ups |
| D5 | Memory lost the objective, relationships, contradictions and rejected hypotheses after a page reload | Memory | `GET /sessions/{id}` returned `objective: ""` and no thread state |
| D6 | Import incompleteness counted **deliberately excluded** files (evaluation material) as unusable evidence | Honesty | Live gap text: "12 file(s) could not be used" where the 12 were `_ground_truth` policy exclusions while 145 real files had no document behind them |
| D7 | A question opening with an auxiliary glued it to the name: "Are Harish Varma and Suresh Pradhan connected?" → mention `Are Harish Varma`, which matched nothing | Entity resolution | Live: next step "Establish the identity of Are Harish Varma." |
| D8 | A possessive was read as a quotation: `Check Sana Iyer's phone` → phantom mention `s phone and Asha Nair`; `Sana Iyer's` could never resolve | Entity resolution | Live probe of natural phrasing |
| D9 | The acceptance suite leaked a dataset ("Permission check", ACTIVE, 5 files) into the session database, failing `assert 5 == 0` | Test hygiene | Throw-away pytest plugin recording dataset deltas per test |
| D10 | The smoke harness relied on fixed sleeps and flaked on a cold master-scope answer | Test harness | Master check rendered 0 pointers after a 15 s wait |

---

## 3. Defects Fixed

- **D1** — the resolver keys scopes on `InvestigationInputs.case_id` (the row id) and reports
  `case_number` separately. Live: `CASE_0060` by id and by number → identical
  `case_id b2deda17-…`, `113` nodes, `863` edges, `8` documents, same gaps and hypotheses.
- **D2** — when no mention resolves, the analysis is `INSUFFICIENT` / `0.1` with an explicit
  caveat ("Nothing in this question matched a record…"), never the scope's strength.
- **D3** — source presence is computed from the data in scope (labels, node kinds, edge
  signals), not from filenames. Live: 12 cases with `TRANSFER_TO` edges (11 also with
  `CALLED`) produced **zero** false "No FINANCIAL/CDR records" gaps.
- **D4** — typed pointers with `doc_id` set **iff** `kind == document`; every finding
  carries a flat roll-up. Live: 20/20 document pointers opened `200`, `0` non-document
  pointers carry a `doc_id`, and each finding's roll-up is non-empty (patterns `{metric:5}`,
  relationships `6`/`5`, hypothesis `10`).
- **D5** — memory is written on every turn and rendered by `GET /sessions/{id}` and the
  workspace; rejected hypotheses are carried forward and re-labelled "Re-tested: …" without
  being re-scored.
- **D6** — the import report distinguishes *failed* files, *records without a document*,
  and *deliberately excluded* files; only the first two make the scope incomplete. Live:
  "Out of 1038 catalogued file(s), 145 produced records without a source document …
  A further 12 file(s) were excluded by policy and are not counted here."
- **D7/D8** — sentence-opening interrogatives/auxiliaries are dropped (only at a sentence
  boundary, so `The Estate` survives), an apostrophe inside a word is no longer a quotation
  mark, and a possessive folds to the name it belongs to. Live after the fix:
  `Are Harish Varma and Suresh Pradhan connected?` → both resolved by exact name, no
  unresolved gap, next step about the hypothesis.
- **D9** — `tests/conftest.py::_no_leaked_cases` now removes datasets as well as cases by
  difference (every model carrying `dataset_id`, then the `Dataset` row). The acceptance
  test passes unmodified.
- **D10** — the smoke harness polls (`waitFor`/`answerRendered`, 60 s) instead of sleeping;
  the master check now requires the provenance panel to have actually rendered.

---

## 4. Backend Tests

```
cd backend && /tmp/clvenv/bin/python -m pytest tests/
```

| Metric | Value |
| --- | --- |
| Collected | **536** |
| Passed | **536** |
| Failed | **0** |
| Skipped | **0** |
| Errors | **0** |
| Exit code | **0** |
| Duration | 110.0 s |

`tests/test_investigator.py` alone: **126 tests**. The suite uses a temporary database
(`tests/conftest.py::TEST_DB_URL`) and **no test calls a live model or the network** —
the AI contract is exercised through a stub router.

New deterministic coverage added for this mandate: negative entity (`unresolved` never
borrows strength), unearned confidence, gap-source truth (label-only presence, call edges
present ⇒ no CDR gap, import incompleteness), provenance typing and the non-document
negative control, case number vs. ID, cross-dataset isolation + thread pinning, decoys
(single co-location, benign repetition, social-only, rejected identity), contradictions
(all seven rules), criminal-status safety, memory continuation, keyless/invalid/failed
AI output, authorization and 4xx API behaviour, and the two mention-quality defects.

---

## 5. Frontend Tests

| Check | Result |
| --- | --- |
| `npm test` (`node --test`) | **68 tests / 68 passed / 0 failed** |
| `npx tsc -b --noEmit` | clean (exit 0) |
| `npm run build` | OK — `dist/assets/index-DuhBTBO4.js`, `index-CgaQSzep.css` |

`tests/investigator-reasoning.test.mjs` runs against the real `src/lib/investigator.ts`
(no DOM, no network) and pins: facts vs. interpretations, suspiciousness never rendering as
criminality, strength phrasing, provenance dispatch by pointer type, label legend,
next-step link targets, and memory rendering.

---

## 6. Live Smoke

Environment: freshly rebuilt (empty data dir → bootstrap → corpus import → active dataset
`3ab4b423-3f4c-4328-9baa-e81d390a9b0f`, 61 cases, 4 237 nodes / 35 183 edges, 1 038 files
= 881 ingested + 145 normalized + 12 policy-skipped), API on `:8000`, console on `:5173`.

**Headless UI smoke (`frontend/smoke.mjs`, jsdom against the live API) — 10/10 PASS,
`console errors: none`:** cases screen mounts · investigator workspace mounts with its
objective banner · asking a question renders a reasoned answer · the answer states an
honest strength instead of a bare percentage · the answer renders every provenance pointer
it carries · no provenance pointer links to a non-document reference · openable records and
non-openable references render differently · the master-scope answer renders · non-document
references render as references, never as document links · a document the answer links to
opens from the API.

**HTTP behaviour (`/tmp/verify_live.py` → `/tmp/verify_live.json`):**

| Probe | Result |
| --- | --- |
| Case number vs. id | identical scope (113 nodes / 863 edges / 8 documents), identical gaps and hypotheses |
| Master pair question | `WEAK` / 0.4, 1 hypothesis (H1 WEAK, 1 independent source, 4 supporting / 1 contradicting, 10 pointers), 5 711 ms total (scope 3 892 ms, patterns 3 207 ms) |
| Unresolved names | `INSUFFICIENT` / 0.1, 0 hypotheses, caveats naming the two unmatched mentions |
| Keyless AI | `model.available = false`, reason `no_api_key_for_role_reasoning`, caveat: "No language model is configured: this answer is the deterministic analysis only." |
| Provenance | 20 document pointers, all `200`; 0 non-document pointers typed as openable; roll-ups non-empty |
| Decoys | 10 set-asides kept in the response with reasons (e.g. "Single co-location: one shared presence is coincidence until repeated.") |
| Labels | hypothesis `HYPOTHESIS`, patterns `COINCIDENCE`/`CORROBORATED_LEAD`, gaps `DATA_GAP` |
| Follow-up | thread continuation ("No entity was named in this question; the analysis continued with Harish Varma, Suresh Pradhan from this investigation's own thread."), memory carries objective, 2 questions, relationships, contradiction |
| Session endpoint | `200` with objective/questions/contradictions; unknown id → `404` |
| Authorization | viewer `403`, anonymous `401`, unknown case `404`, short question `422`, `max_patterns=1000` `422` |
| Audit | `INVESTIGATE` + `AI_QUERY` rows with 64-char row/prev hashes; `GET /admin/audit/verify` → `{"valid": true, "checked": 238}`, `first_tampered_id: null` |
| Criminal-status safety | no guilt vocabulary in any response; `criminal_status` null everywhere (nothing in the corpus asserts one) |
| Isolation | activating a second dataset switched the universe (old case → `404`, old thread → `422`, case list = active dataset only, graph evicted to the active projection) — see Known Limitations for the replacement semantics |
| Gap truth | 12 live case-scope answers cross-checked against their own graph edges: **0** false source gaps |

---

## 7. Acceptance Matrix

| # | Requirement | Status | Behavioural evidence |
| --- | --- | --- | --- |
| 1 | Dataset-agnostic integrity (no hardcoded IDs, no corpus dependence) | ✅ VERIFIED | `grep` of `app/` finds no dataset-specific identifiers; every test dataset is built in-process; live isolation probe imported a hand-made 3-CSV folder |
| 2 | No bundled/default corpus *required* | ✅ VERIFIED | App boots and serves with an empty data dir; arbitrary folder import builds a full graph; the shipped corpus is a test fixture only (see Known Limitations) |
| 3 | Only the active dataset visible/queryable; activation switches the universe | ✅ VERIFIED | Live: old case `404`, old thread `422`, case/graph/search scoped to the active dataset; `tests/test_dataset_replacement.py`, `test_single_active_dataset_acceptance.py` |
| 4 | Criminal status only from source records | ✅ VERIFIED | `test_criminal_status_is_echoed_never_derived`, `test_convicted_status_echoes_from_data_only`; live: status null where no record asserts it |
| 5 | Centrality/connectivity never confer criminality | ✅ VERIFIED | Centrality text is explicitly qualified ("High centrality means the person sits on many connection paths; it does not imply wrongdoing"); `test_centrality`/UI legend checks |
| 6 | Vehicle ownership ≠ usage | ✅ VERIFIED | `vehicle_mismatch` fires only on recorded use without title (`test_vehicle_mismatch_fires_on_use_without_title`, `test_vehicle_without_a_holder_is_not_a_mismatch`) |
| 7 | Missing = missing, never fabricated | ✅ VERIFIED | 9 source families reported when absent; import-incompleteness gap; social/FIR/CCTV/surveillance gaps live |
| 8 | Orchestrator modules present and cohesive | ✅ VERIFIED | `orchestrator, entity_resolution, patterns, relationships, hypotheses, gaps, next_steps, memory, labels, schemas, prompts, evidence` |
| 9 | Canonical entity resolution (ranked, merge-aware, uncertain = proposal) | ✅ VERIFIED | Exact 1.0 / alias 0.9 / partial 0.75 / open proposal capped 0.6, merge chains followed; **two mention defects fixed** |
| 10 | 7 relationship kinds with observation/interpretation/assessment/evidence/provenance/cases/time | ✅ VERIFIED | `tests/test_direct/repeated/temporal/cross_case/suspicious/indirect/coincidental…`; live roll-ups 6 and 5 pointers |
| 11 | 11 deterministic pattern detectors, thresholds preserved, no ML | ✅ VERIFIED | `_DETECTORS` = 11 functions + explicit social-only set-asides; thresholds unchanged; no ML dependency added |
| 12 | Each pattern reports type/entities/cases/time/explanation/evidence/provenance/strength/label/gaps | ✅ VERIFIED | Schema + live payloads; 0 patterns without provenance |
| 13 | Suspicious ≠ criminal everywhere (API, UI, AI language) | ✅ VERIFIED | Guilt-term scans on live responses (`[]`), `neutralize_language` tests, UI wording checks |
| 14 | Hypotheses: contradiction search, innocent alternatives, convergence, uncertainty, vocabulary, strength ≠ guilt probability | ✅ VERIFIED | 7 contradiction rules tested; alternatives never empty; labels `FACT/CORROBORATED_LEAD/LEAD/HYPOTHESIS/COINCIDENCE/DATA_GAP`; live H1 WEAK with 4 supporting / 1 contradicting |
| 15 | Convergence counts independent documents/source types only | ✅ VERIFIED | `test_independent_sources_count_records_not_metrics`; live single-source contradiction on H1 |
| 16 | Defect class A (unearned strength) reproduced and fixed | ✅ VERIFIED | `test_unresolved_question_never_borrows_the_scope_strength`; live INSUFFICIENT/0.1 |
| 17 | Defect class B (false source gap) reproduced and fixed | ✅ VERIFIED | `test_a_scope_with_call_edges_never_reports_missing_cdr`, label-only presence test; live 12-case cross-check, 0 offenders |
| 18 | Defect class C (dead provenance) reproduced and fixed | ✅ VERIFIED | `test_a_live_answer_never_types_a_non_document_as_openable`, pointer-constructor tests; live 20/20 openable, 0 mistyped |
| 19 | Defect class D (case number vs. id) reproduced and fixed | ✅ VERIFIED | `test_case_number_scope_reads_the_same_case_as_its_id`; live identical scopes |
| 20 | Typed provenance + flat roll-ups + negative tests | ✅ VERIFIED | `document/source_row/graph_edge/metric/audit/note/dataset`; roll-up tests; non-document negative control |
| 21 | Cross-case reasoning (master network, bridges, communities, membership via real relationships, never cross datasets) | ✅ VERIFIED | `CROSS_CASE_ENTITY`/bridge/community detectors; live master scan 35 patterns; cross-dataset thread refused `422` |
| 22 | Social media as weak evidence + data gap | ✅ VERIFIED | Social-only pairs set aside with a reason; missing `SOCIAL_MEDIA` reported as a gap |
| 23 | Persistent memory (incl. rejected hypotheses, contradictions, cross-dataset rejection) | ✅ VERIFIED | Memory tests; live session endpoint and follow-up; rejected hypothesis re-labelled, never re-scored |
| 24 | AI Gateway reuse: strict JSON, deterministic-first, keyless degradation, invalid-output safety, neutral language, no invented evidence | ✅ VERIFIED | New stub-router tests (`ModelSection` field set pinned; a narrative naming `DOC-9999` never reaches provenance; accusations neutralized) + live keyless caveat |
| 25 | API `POST /investigate`, `GET /patterns`, `GET /sessions/{id}` with auth/validation/4xx/isolation/keyless | ✅ VERIFIED | Live `403/401/404/422` matrix; audit rows; dataset-pinned sessions |
| 26 | Audit of investigation activity | ✅ VERIFIED | `INVESTIGATE` + `AI_QUERY` rows; `/admin/audit/verify` → `valid: true` (238 rows), `first_tampered_id: null`, genesis hash anchored |
| 27 | Frontend workspace incl. provenance dispatch by type | ✅ VERIFIED | 10/10 headless smoke; 68 unit tests; provenance chips carry the case id (wiring guard with a negative control) |
| 28 | Graph analytics as signals, not guilt | ✅ VERIFIED | Centrality language, "signals only" copy, focused graph scoped to the question |
| 29 | Decoys / negative controls never silently dropped | ✅ VERIFIED | Single co-location, benign-only repetition, social-only, rejected identity, insufficient evidence — all present with reasons (`include_excluded`) |
| 30 | Honest data gaps (FIR/CDR/financial/CCTV/surveillance/social/documents/incomplete records) | ✅ VERIFIED | 9 families + import incompleteness; `what_would_help` on every gap |
| 31 | Blockchain-cybersecurity integrity theme preserved meaningfully | ✅ VERIFIED | Hash-chained audit log + `/audit/verify`; content hashes on provenance pointers; tamper-evidence tests |
| 32 | Neo4j projection where present | ⚪ N/A (live) | No Neo4j instance in this environment; `tests/test_neo4j_correctness.py` and `test_graph_persistence.py` cover the projection |
| 33 | Deterministic tests added for the mandated cases | ✅ VERIFIED | 536 backend tests (126 investigator), 68 frontend tests, all offline |
| 34 | Live HTTP smoke over a fresh environment | ✅ VERIFIED | Bootstrap → import → active dataset → investigation → follow-up → patterns → retrieval → provenance → audit → unknown case/entity → criminal-status safety → isolation → frontend, no console errors |
| 35 | Don't stop after the first pass | ✅ VERIFIED | Three verification passes this session; the last two rounds each found and fixed new defects (D6–D8) |

---

## 8. Known Limitations

1. **No live model key in this environment.** The narrative path is therefore exercised
   deterministically (stub router: valid, accusatory, non-JSON, failed, keyless) and the
   live API reports `model.available: false` with an honest caveat. Nothing about the
   deterministic answer depends on it.
2. **The shipped corpus is a fixture, not a dependency.** `backend/CrimeLink_Synthetic_Corpus_v1`
   (1 038 files, git-tracked, its `.gitignore` line commented out) backs the synthetic
   adapter tests, and `app/config.py:234` offers it as the CLI's default path. The
   application never imports it implicitly: it boots, imports arbitrary folders, and runs a
   full investigation without it (verified live with a hand-made dataset).
3. **Dataset replacement is destructive by design.** Importing/activating a dataset purges
   the previously active dataset's imported rows (`tests/test_dataset_replacement.py`).
   That is what "one active universe" means here, and it is why the isolation probe left the
   corpus dataset empty until it was re-imported. Operators should expect a replacement, not
   a switch-back.
4. **Corpus coverage:** the shipped data has no FIR, CCTV, surveillance, social-media or
   criminal-history feed. Those appear as data gaps (correctly); the corresponding evidence
   handling is covered by fixtures, not by live data.
5. **Performance:** a cold master-scope question over 61 cases / 35 k edges takes ~6–9 s
   (scope + pattern scan); case-scope answers are ~0.75 s warm. No caching added —
   the numbers are deterministic and reported in `timing_ms`.
6. **Frontend verification is headless.** The jsdom smoke uses a canvas shim, so graph
   rendering is verified structurally (mount, selection, labels), not pixel-wise.
7. **Jurisdiction scoping is operational:** accounts must carry the jurisdiction of the
   active dataset (`SYN-DEV` for the corpus) or they see no cases.

---

## 9. Pre-existing Issues (not introduced, not fixed)

1. `backend/CrimeLink_Synthetic_Corpus_v1/` remains committed (1 038 files) with the
   `.gitignore` exclusion commented out — a heavy fixture; left as-is because removing or
   `git rm`-ing it would break the synthetic-adapter tests and is a separate decision.
2. `app/config.py:223–234` still defaults the CLI's synthetic-corpus path to that folder.
3. A tracked **0-byte** `crimelink.db` sat at the repository root (the real database lives in
   `var/data/`, which is ignored). It has been deleted in this change set.
4. Master-scope questions that name no entity return scope-level patterns; the response says
   so in a caveat rather than pretending it answered about the names.

---

## 10. Final Verdict

**Every mandated item is VERIFIED except the live-Neo4j item, which is N/A in this
environment and covered by unit tests.** The investigator pipeline behaves as specified on a
freshly rebuilt dataset: honest strength, typed and openable provenance, contradiction-aware
hypotheses, explicit decoys and data gaps, dataset isolation, hash-chained audit, and an AI
layer that explains only what was computed (or states plainly that it is unavailable).

Backend 536/536 (exit 0) · frontend 68/68 plus clean `tsc` · smoke 10/10 with no console
errors · live matrix green.

**Ready to freeze and commit? Yes — with two review notes:**

- commit the working-tree change set as a whole (27 modified files + 5 untracked paths,
  including the two new documents and the new investigator frontend module), and
- decide separately what to do about the committed synthetic corpus and its CLI default
  (item 1/2 above) and about the deleted 0-byte `crimelink.db`.

Nothing has been committed or pushed; the tree is left exactly as verified.
