# Investigator Reasoning Layer — §35 Verification and Final Report

**Date:** 2026-09-12 · **Branch:** `arena/01a09253-crimelink` · **Base commit:** `2dd5b4d`
**Working tree only — nothing committed or pushed.**

This report verifies the investigator reasoning layer against the task's §35 checklist.
Every verdict below is backed by a command that was run or a live response that was
inspected; no item is marked ✅ because a file, route or schema exists. Where the
evidence is partial, the item says so.

Verification environment: embedded profile (SQLite + NetworkX + local object store),
API served by `uvicorn app.main:create_app --factory`, dataset **Synthetic Corpus v1**
imported from `backend/CrimeLink_Synthetic_Corpus_v1` — **1038 discovered files, 881
ingested, 145 normalized, 12 skipped, 61 cases, 4237 graph nodes, 35183 edges**. No language
model is configured in this environment, which is deliberate: every deterministic path is
exercised keylessly and the model sections report themselves unavailable.

> The sandbox was reset mid-session (`/tmp`, the SQLite database and the graph snapshot are
> outside the repository and are not snapshotted). Everything below was **re-run after the
> reset**: the corpus was re-imported from the repository's own copy, the users were
> re-created through `POST /api/v1/auth/setup` and `POST /api/v1/admin/users`, and every
> count and quote in this report comes from that rebuilt environment. Nothing was carried
> over from the pre-reset run.

---

## Part A — §35 Checklist

| # | Item | Verdict |
|---|------|---------|
| 35.1 | Multi-source analysis; absent source = DATA_GAP, never fabricated | ✅ (⚠️ importer coverage) |
| 35.2 | Entity extraction and canonical-ID resolution | ✅ (2 defects fixed) |
| 35.3 | Relationship discovery (direct / indirect / temporal / repeated / cross-case / suspicious / coincidental) | ✅ |
| 35.4 | Structured suspicious-pattern detection (12 detector families) | ✅ |
| 35.5 | Observation / interpretation / assessment separation | ✅ |
| 35.6 | Convergence and evidence-strength factors — no unearned scores | ✅ (2 defects fixed) |
| 35.7 | Supporting *and* contradictory evidence, with innocent alternatives | ✅ |
| 35.8 | Data-gap identification that matches the data | ✅ (defect fixed) |
| 35.9 | Next investigative direction | ✅ |
| 35.10 | Investigation memory / state across follow-ups | ✅ (defect fixed) |
| 35.11 | Deterministic metrics; LLM explains only | ✅ |
| 35.12 | Investigation-aware retrieval (scope, entity, time, source) | ✅ (scope-key defect fixed) |
| 35.13 | Social-media intelligence without fabrication | ✅ |
| 35.14 | Provenance path Finding → Relationship/Event → Evidence → Source Doc | ✅ (3 defects fixed) |
| 35.15 | Focused evidence graph and timeline integration | ✅ |
| 35.16 | Investigator UI (§34 acceptance criteria) | ✅ (headless behavioral check) |
| 35.17 | Security, tamper-evident audit, dataset isolation | ✅ |

### 35.1 Multi-source analysis — ✅ (⚠️ on importer coverage)

One graph receives every ingested family: FIR and charge-sheet documents (304 case
documents typed FIR), financial statements/transactions (452 FINANCIAL), intel notes (95),
surveillance (30), plus the operational tables (call records, transfers, sightings,
vehicles, phones, accounts, employment, travel). Live: **4237 nodes / 35183 edges** over 61
cases.

Absent sources are reported, never filled in. On the live corpus the investigator answers
with `No CRIMINAL_HISTORY records in the in-scope cases.` and `No SOCIAL_MEDIA records in
the in-scope cases.`, and both claims were checked against the data:
`nodes with criminal_status = 0` in the graph and no `criminal_status` attribute anywhere
in `dataset_entities`; no social edge type is present (`POSTED`, `TAGGED`, `FOLLOWS`,
`ASSOCIATED_ONLINE`, `INTERACTED_WITH`, `LINKED_ON_SOCIAL` — none). The gaps are true
statements about the data, not boilerplate.

⚠️ The corpus does ship 15 `documents/social/SOCIAL_*.json` captures and 12 files the
importer skipped (xlsx/financial summaries, signature-less JSON); those files were
classified `UNKNOWN` and produced no records, so the gap is honest but the *reason* is not
yet attributed in the gap text. See Remaining gaps #3.

### 35.2 Entity extraction and resolution — ✅

Mentions are extracted deterministically (quoted spans and capitalised multi-word runs),
then resolved against the in-scope snapshot by hard identifier (confidence 1.0), exact
name, alias, token overlap, and merge-chain canonicalisation; open `POTENTIAL_ALIAS`
proposals are surfaced but capped at 0.6 and never silently merged. Live: the question
*"What connects Harish Varma and Suresh Pradhan?"* resolves both to canonical ids
`ds:d33c7678-…:PERSON:…` at confidence 1.0; *"Zafar Qureshi and Farah Bano"* resolves
neither and both become unresolved entities plus `unresolved-entity` data gaps.

A verification pass over natural phrasing found and fixed two extraction defects that
made the *most* natural wording the least useful one. A question that opens with an
auxiliary glued it onto the first name ("Are Harish Varma and Suresh Pradhan
connected?" → the mention `Are Harish Varma`, which matched nothing and produced a
next step to "establish the identity" of a question word), and a possessive produced a
phantom person (`Sana Iyer's` never matched `Sana Iyer`). Sentence-opening
interrogatives/auxiliaries are now dropped (only at a sentence boundary, so a name such
as `The Estate` is untouched), apostrophes inside words are no longer read as quotation
marks, and a possessive folds to the name it belongs to. Live, after the fix:
`Are Harish Varma and Suresh Pradhan connected?` resolves both by exact name with no
unresolved-entity gap, and `Show me Sana Iyer's phone records` reports the honest
mention `Sana Iyer`. Covered by `test_a_sentence_opening_question_word_is_not_glued_onto_a_name`,
`test_a_possessive_mention_is_the_same_person`, and
`test_a_question_opening_with_a_name_still_resolves_it`.

### 35.3 Relationship discovery — ✅

All seven readings are implemented and pinned by tests (`test_direct_relationship_is_fact`,
`test_repeated_relationship_needs_two_docs`, `test_temporal_relationship_uses_chronology`,
`test_indirect_relationship_reports_its_path`, `test_cross_case_relationship_spans_disjoint_cases`,
`test_suspicious_relationship_needs_calls_and_transfers`,
`test_coincidental_relationship_flags_weak_links`), plus negatives: meta edges never form
relationships, sub-threshold call volumes do not fire, and output is capped. Live: the pair
question returns a `direct` reading and a `temporal` reading whose path carries 4 hops.

### 35.4 Suspicious-pattern detection — ✅

Twelve detector families (cross-case entity, cross-case link, communication anomaly,
financial flow, vehicle use-vs-ownership, co-location, temporal burst, network bridge,
community signal, repeated combination, entity-resolution signal, social-only) each carry
strength factors, `contradictions_considered`, innocent alternatives and provenance; decoys
are emitted as `excluded` with the reviewer's reason rather than hidden
(`test_dismissed_combinations_are_excluded_with_reasons`,
`test_single_colocation_is_set_aside_openly`, `test_social_only_links_are_set_aside`,
`test_vehicle_without_a_holder_is_not_a_mismatch`).

Live, master scope returned STRONG `CROSS_CASE_ENTITY` patterns with counted evidence
("152 Temple Street, Anna Nagar, Vadodara appears in 9 cases"), a `COMMUNITY_SIGNAL` with
degree/weighted-degree/betweenness/PageRank/community inputs, and — importantly — the
single shared-location case was set aside as `INSUFFICIENT` rather than escalated.

### 35.5 Observation / interpretation / assessment separation — ✅

`ObservationBlock` accompanies every relationship and hypothesis; the answer's
`AssessmentSection` keeps `observation`, `interpretation`, `assessment` and `caveats`
distinct. Live text: observation *"1 of 1 mention(s) resolved; 0 relationship(s), 5 live
pattern(s), 0 hypotheses; scope case over 1 case(s), 148 nodes, 2088 edges, 15 documents."*,
interpretation *"Convergence: No hypotheses were formed: nothing to converge."*, assessment
*"Overall strength INSUFFICIENT (confidence 10%) over the records the question's entities
resolved to."*

### 35.6 Convergence and strength — ✅ (fixed this session)

Two defects were found and fixed by behavioral probing:

1. **Unearned headline strength.** A question naming a person who does not exist in the
   data reported *"Overall strength STRONG (confidence 90%)"* because the strength was the
   best of the scope's ambient patterns. `_overall()` now scores the *answer*: patterns only
   count when they intersect the resolved entities, and a question whose mentions all failed
   to resolve is `INSUFFICIENT` with an explicit caveat. Live re-probe: 0 entities resolved,
   `INSUFFICIENT 0.1`, 0 hypotheses, and the caveat *"Nothing in this question matched a
   record…"*. Regression test:
   `test_unresolved_question_never_borrows_the_scope_strength`.
2. **Independent-source counting.** Source counts are computed from record pointers only
   (`document`, `source_row`, `dataset`), one per record set, never from metrics.
   Regression test: `test_independent_sources_count_records_not_metrics`.

Strength factors record the inputs (independent sources, corroborating records,
contradiction level, entity certainty, directness) rather than a bare number; convergence
requires two independent streams with no major contradiction. Live: the named pair returns
`WEAK 0.4` on 1 independent record with 4 supporting / 1 contradicting items and the gap
*"H1-association rests on 1 independent record(s)."* — the honest reading, not a high score.

### 35.7 Contradictory evidence and innocent alternatives — ✅

Every hypothesis runs all seven contradiction rules (single source, dismissed conflict,
benign-only relationship, open identity proposal, low-confidence-only, no shared records,
thin support). Live: the pair hypothesis carries both supporting and contradicting evidence
and an `analysis` block; `contradictions_considered` appears on patterns.
`alternative_explanations` is never empty — grounded alternatives come from the detectors
and hypotheses (routine contact, legitimate money movement, shared routines,
incomplete case attribution) with `INSUFFICIENT_BASELINE` retained when nothing else
survives (`test_alternative_explanations_are_never_empty`).

### 35.8 Data gaps that match the data — ✅ (fixed this session)

Gaps are categorised (`unresolved-entity`, `missing-source`, `single-source`,
`provisional-identity`, `missing-link`), bounded, and phrased as *what is absent* plus *what
would help* — never as speculation about what the missing evidence would say.

A defect was found by inspection: source presence was judged from document-type labels
alone, so a scope holding **16442 `CALLED` edges** still announced *"No CDR records in the
in-scope cases"*. `present_sources()` (`app/investigator/gaps.py`) now unions document
labels with graph-evidenced sources (`CALLED/CONTACTED/SMS/…` → CDR,
`TRANSFER_TO/OWNS_ACCOUNT/…` → FINANCIAL, `SEEN_AT/DROVE/…` → SURVEILLANCE,
`POSTED/TAGGED/FOLLOWS/…` → SOCIAL_MEDIA, authoritative node attributes → CRIMINAL_HISTORY),
wired at `orchestrator.py`. Regression test:
`test_present_sources_are_read_from_the_data_not_the_file_labels`. Live after the fix: the
CDR claim is gone; the two remaining source gaps were independently verified as true.

### 35.9 Next investigative direction — ✅

`build_next_steps` is identity-first and deliberately non-coercive (no arrest, detention or
seizure verbs — pinned by `test_next_steps_are_never_coercive` and a vocabulary scan over
whole responses). Live output: *"Corroborate or rule out H1-association: … (4 supporting
item(s), 1 contradicting)."*, *"Obtain CRIMINAL_HISTORY records."*, *"Find an independent
record for the single-source reading."* — each with a priority and links.

### 35.10 Investigation memory and follow-ups — ✅ (improved this session)

Threads are dataset-pinned (continuing against a different dataset is refused with 422 —
`test_cross_dataset_isolation_and_thread_pinning`), bounded, and exposed through
`GET /api/v1/investigate/sessions/{id}`. Live: turn 2 returned `questions_asked = 2`, the
prior questions, and the sticky objective.

A defect was found live: a follow-up that names nobody — *"Do they share any vehicle?"* —
resolved no entities and still scored the scope's own patterns `STRONG 0.9`. Follow-ups now
continue with the entities the thread established (`MAX_CARRIED = 4`), marked
`matched_by: "thread-continuation"`, with the caveat *"No entity was named in this question;
the analysis continued with Harish Varma, Suresh Pradhan from this investigation's own
thread."* Regression test:
`test_followup_that_names_nobody_continues_the_thread_entities`. Live: the same follow-up
now returns `WEAK 0.4` about the right two people.

### 35.11 Deterministic metrics, LLM explains only — ✅

Every structural field — entities, relationships, patterns, hypotheses, gaps, next steps,
timeline, focused graph, provenance — is computed by Python (NetworkX + the existing
analytics engines). The language model may author only `InvestigatorNarrative`. With no key
configured the live responses carry `model.available = false`,
`reason = "no_api_key_for_role_reasoning"`, and a deterministic answer; a model reply that
is not valid JSON degrades to `confidence 0.0`, `evidence_level UNKNOWN`,
`recommended_review = true`, so unverified prose can never enter as a finding.

### 35.12 Investigation-aware retrieval — ✅

Scope is either `master` or a case, echoed with human identity (`Case CASE_0056`), and
retrieval is constrained by dataset (active dataset only), case (case graph / case
documents), entities (question mentions → canonical ids → focused subgraph) and time
(timeline ordered, temporal paths require dated edges). The focused graph is capped at 60
nodes / 200 edges and the timeline at 80 entries.

A defect was found here by asking the same question two ways. `require_case` accepts a human
case number as well as a case id, but the investigator keyed its scope by the caller's raw
reference: asked with `case_id="CASE_0060"` it read documents and graph nodes *under that
string*, found none, and answered `INSUFFICIENT` with a page of missing-source gaps about
records the case actually holds. The scope is now keyed by the resolved row's id. Live proof
that the two entry points agree:

| Scope argument | `case_id` echoed | case number | nodes | edges | documents | gaps |
|---|---|---|---|---|---|---|
| `b31fb579-…` (id) | `b31fb579-…` | CASE_0060 | 113 | 863 | 8 | identical |
| `CASE_0060` (number) | `b31fb579-…` | CASE_0060 | 113 | 863 | 8 | identical |

Regression test: `test_case_number_scope_reads_the_same_case_as_its_id`.

### 35.13 Social-media intelligence without fabrication — ✅

Social data is treated as a source family with its own detector and gap guidance: a
social-only adjacency is set aside as a coincidence (`test_social_only_links_are_set_aside`)
and online adjacency is never read as a real-world association. Because this corpus carries
no social records, the investigator reports the gap instead of inventing posts or accounts.

### 35.14 Provenance path — ✅ (two defects fixed)

Every claim carries openable pointers, and the pointers say what they are. Live master-scope
answer: **20/20 document pointers opened** (`GET /api/v1/documents/{id}` → 200), with
`graph_edge`, `dataset` and `metric` references kept out of the document namespace; the
verification walks the whole response and asserts that **no non-document pointer carries a
`doc_id`** (result: zero).

Three defects were found by opening the pointers rather than trusting them:

1. **Path findings had no provenance.** Indirect and temporal relationship evidence was a
   sentence with an empty pointer list ("5 chronologically valid path(s) connect the pair"
   with `path.edges = []`). `find_temporal_paths` now returns the edge keys behind each hop
   and `discover_relationships` attaches the real edges plus the document behind each one.
   Regression tests: `test_indirect_relationship_opens_every_hop`,
   `test_temporal_relationship_carries_its_edges`.
2. **Dataset-level rows masqueraded as documents.** Operational-table records are stamped
   `source_doc_id = dataset:<id>`; rendered as a `document` pointer, the console offered a
   link that 404s (reproduced live: 5 × `GET /api/v1/documents/dataset%3A…` → 404). They now
   emit a `dataset` pointer (`kind: "dataset"`, no `doc_id`). Regression test:
   `test_dataset_level_records_point_at_the_dataset_not_a_document`.
3. **A finding exposed only its own nested evidence.** Relationships, patterns and
   hypotheses returned the pointer list only inside each evidence item, and the console's
   own provenance panel rendered *every* pointer as a `/documents/...` link — so a graph
   edge or a computed metric became a link that dead-ends. Findings now carry a derived
   flat `provenance` list (`evidence.roll_up_provenance`, deduped, capped at 20) and the
   workspace renders each pointer by kind. Live: relationships 9 `graph_edge` + 2 `dataset`
   pointers, the hypothesis 9 + 1, patterns 5 `metric` pointers; the master-scope answer
   renders **20 rows / 0 dead links / 0 reference chips linked as documents**.
   Regression tests: `test_roll_up_provenance_is_derived_and_deduped`,
   `test_findings_carry_openable_sources_derived_from_their_evidence`,
   `the answer renders every provenance pointer it carries` (smoke).

Driving the production bundle against the API with the fix disabled (a deliberate negative
control: `source_pointer` re-routed as before the fix) made the smoke checks fail exactly
where they should — *"non-document references render as references, never as document
links"* and *"a document the answer links to opens from the API"* — and the same checks pass
with the fix in place, with the API log showing the 404 it produces
(`GET /api/v1/documents/dataset%3A… → 404` vs `GET /api/v1/documents/0020f0dd-… → 200`).

⚠️ A `dataset` pointer names the dataset rather than a single row: operational-table records
still lack row-level pointers (`operational/transactions.csv · row 10`), even though the
importer stamps exactly that origin on the edges. See Remaining gaps #2.

### 35.15 Focused evidence graph and timeline — ✅

The focused graph is seeded from the resolved entities plus one hop and capped (60 nodes /
200 edges); the timeline is built from the same snapshot with typed events, participants and
source document ids (live: 32 entries for `CASE_0056`, each carrying `source_doc_id`).
Both are returned in one deterministic response, so the console never renders a graph that
disagrees with the evidence list.

### 35.16 Investigator UI — ✅ (behavioral check)

The console runs the **production bundle** in jsdom against the **live API**
(`frontend/smoke.mjs`), which is a behavioral test rather than a file check:

```
PASS  cases screen mounts
PASS  investigator workspace mounts with its objective banner
PASS  asking a question renders a reasoned answer
PASS  the answer states an honest strength instead of a bare percentage
PASS  the answer renders every provenance pointer it carries
PASS  no provenance pointer links to a non-document reference
PASS  openable records and non-openable references render differently
PASS  the master-scope answer renders
PASS  non-document references render as references, never as document links
PASS  a document the answer links to opens from the API
console errors: none
```

The check logs in as an investigator, picks a case the API says carries provenance (asked,
not hard-coded), opens `/cases/:id/investigate`, types a real question, submits the real
form, waits for the answer (the pipeline is slower cold than warm, so it polls rather than
sleeping a fixed time), then repeats on the master scope and fetches back the first document
the answer links to. It also asserts that a reference chip is **not** a document link. jsdom
has no canvas, so the harness installs a no-op 2d context — the focused evidence graph
otherwise throws inside the renderer and the error boundary replaces the page (that failure
mode was hit and diagnosed during this session, not assumed).

Supporting evidence: `npm run typecheck` clean, `npm run build` EXIT=0, `npm test` → 59
passed (investigation formatting, investigator helpers including the provenance roll-up,
auth refresh).

The workspace implements the §34 surface: objective banner and question box, findings,
patterns with strength factors and set-asides, supporting / contradicting / context
evidence with per-item pointers, alternative explanations, convergence and assessment, data
gaps, next direction, focused evidence graph, timeline, provenance path and case-vs-master
scope switching.

⚠️ No pixel-level or manual browser review was performed in this environment; the check is
functional (mount, submit, render) and does not cover visual polish or responsive layout.

### 35.17 Security, auditability, dataset isolation — ✅

- **Roles and scope:** investigator/admin only (viewer forbidden, anonymous rejected —
  `test_viewer_is_forbidden_and_anonymous_is_rejected`); answers run inside the caller's
  jurisdiction and dataset.
- **Tamper-evident audit:** every investigate call writes a hash-chained `AuditLog` row, and
  a narrative `AI_QUERY` row. `test_investigate_audit_link_is_hash_valid` recomputes
  `chain_hash(prev_row_hash, canonical_json(payload))` and confirms the previous row's hash,
  so a rewritten row is detectable. `GET /documents/evidence/{doc_id}/verify` re-hashes the
  stored document bytes.
- **Dataset isolation:** answers are pinned to the active dataset; continuing a thread
  against another dataset is refused (`422`); swapping the active dataset changes the
  canonical ids rather than merging them (`test_cross_dataset_isolation_and_thread_pinning`).
- **Suspicious ≠ criminal:** the response never contains guilt vocabulary
  (`GUILT_TERMS`/`COERCIVE_TERMS` scans) and criminal status is only ever echoed from an
  authoritative record — live, both named persons resolve to PERSON nodes with
  `criminal_status = null`, and nothing derived it.

---

## Part B — §35.18 Final Report

### Architecture

The investigator reasoning layer sits **above** the existing AI gateway and reuses the
platform instead of duplicating it: one `InvestigatorOrchestrator` per request composes
scope resolution → dataset-pinned thread → entity extraction/resolution → deterministic
pattern detection → relationship discovery → hypothesis construction with mandated
contradiction search → convergence and strength → gap analysis → next steps → optional
narrative (gateway only) → memory update. The response is a single strict
`InvestigatorResponse`; the model's only authorship is `InvestigatorNarrative`. No new
service, queue, store or model runtime was added — the layer is pure Python over the graph
snapshot, the existing analytics engines, the existing audit service and the existing AI
gateway. Runtime on the 4237-node corpus, measured in this environment: a case-scoped
question returns in **0.16 s**; a master-scope question over 61 cases takes **7.5–8.8 s**,
of which ~4 s is scope assembly and ~4 s is running the twelve detectors over the whole
network. That master cost is the honest price of asking a network-wide question, and it is
the reason the console polls for the answer instead of assuming one.

### SIH alignment

The problem statement asks for an assistant that reasons over evidence rather than chatting.
The layer answers with counted, provenance-backed structure: every claim is either a counted
fact, a labelled inference, a hypothesis with both sides, or an explicit gap — never prose
alone. The blockchain theme is met with tamper-evident provenance and auditability (hash
chain over audit rows, hashed documents, verifiable pointers) rather than by putting case
data on a chain.

### Suspicious pattern detection

Twelve deterministic detectors, each returning strength factors, contradictions considered,
innocent alternatives, evidence pointers and an explicit `excluded` flag with the reviewer's
reason for set-asides. Patterns are first-class objects, not strings, and the strength
vocabulary is bounded (INSUFFICIENT / WEAK / MODERATE / STRONG) with the counted inputs
recorded beside it.

### Investigator reasoning

The answer separates what was observed from what it might mean from what is concluded
(35.5), searches for both supporting and contradicting evidence (35.7), keeps innocent
readings on the table, ranks readings by convergence rather than volume, states the next
direction, and remembers the thread — including, after this session, the entities a
follow-up continues with.

### Contradiction analysis

Seven contradiction rules run on every hypothesis. Detectable contradictions include
single-source answers, evidence that a reviewer already set aside, benign-only
relationships, open identity proposals, low-confidence-only links, no shared records and
thin support. Findings never silently win: the contradicting items are returned to the
console with the same prominence as the supporting ones.

### Evidence provenance

Every structural claim carries pointers — document, source row, graph edge, metric, dataset
and audit — both on the evidence items and rolled up per finding, and the pointers were
opened during verification (20/20 documents 200, graph-edge refs present in the stored
graph, audit rows re-hash). Three real defects were found by doing that, and all three are
fixed with regression tests; a deliberate negative control showed the smoke checks fail when
the fix is removed.

### Data-gap handling

Gaps name what is absent and what would help close it, are bounded, and are verified against
the data before being claimed: after the fix, the live corpus reports only gaps that the
data actually supports. Absence is never filled with invented records.

### Graph analytics

Centrality, weighted degree, betweenness, PageRank, communities, temporal path search and
co-location come from the existing NetworkX engines, are surfaced as first-class patterns
with their inputs recorded, and are explicitly distinguished from criminal status, which
only authoritative records can set.

### Frontend UI/UX

The investigator workspace is a new console surface backed by the real endpoints; the
production bundle was exercised headlessly against the live API and passes ten functional
checks with no console errors, including that every pointer renders as what it is and that a
linked document actually opens. Supporting helpers are unit-tested; the build and typecheck
are clean.

### Dataset isolation

Answers, threads, graph reads and audit rows are all scoped to the active dataset; a thread
cannot be continued against a different dataset, and the same question asked against a
replaced dataset resolves to the new dataset's canonical ids with no leakage.

### Testing

| Command | Result |
|---------|--------|
| `cd backend && /tmp/clvenv/bin/python -m pytest tests/ -q` (full backend suite) | **515 passed, 0 failed, EXIT=0** |
| `pytest tests/test_investigator.py -q` | 105 passed |
| `pytest tests/test_investigator.py -k "roll_up or openable_sources or case_number_scope or independent or followup or dataset_level or present_sources" -q` | all passed |
| `cd frontend && npm run typecheck` | clean |
| `npm run build` | EXIT=0 |
| `npm test` | 59 passed |
| `node smoke.mjs` (production bundle vs live API) | 10/10 PASS, no console errors |

The full-suite result matters more than its number: earlier in this session the suite ended
`509 passed / 1 failed`, the failure being `test_single_active_dataset_acceptance_lifecycle`
asserting a fresh start while **5 dataset-file rows leaked from an earlier test** into the
shared session database. The leaker was found rather than guessed at, with a throwaway pytest
plugin that recorded every dataset row created per test. The trace named it precisely: the
test before the acceptance test, `test_schema_inference.py::test_accepting_mappings_requires_admin`,
left dataset *"Permission check"* **active with 5 files** — the exact number the assertion
saw (`assert 5 == 0`), because `is_active` is what the rest of the application reads. The
session-wide cleanup fixture in `tests/conftest.py` now removes dataset-owned rows (every
model carrying `dataset_id`, then the dataset itself) by difference, exactly as it already
did for cases, and the acceptance test passes **unmodified** as part of the full run.

### End-to-end investigation

Run against the live corpus (61 cases, 4237 nodes, 35183 edges):

1. *"Is there a connection between Harish Varma and Suresh Pradhan?"* (master, 8.75 s) →
   both resolve to canonical ids, `criminal_status` stays `null` for both; 2 relationships
   (direct, temporal with 4 hops), 1 hypothesis carrying 4 supporting / 1 contradicting item,
   gaps naming the missing criminal-history and social-media sources plus the single-source
   reading, next steps to corroborate or rule out. `WEAK 0.4` on **1 independent record** —
   the honest reading, not a headline. Rolled-up provenance: relationships 6 and 5 pointers
   (graph edges and dataset rows), hypothesis 10, top steps 10 and 5.
2. *"Do they share any vehicle?"* (same thread) → both entities carried with
   `matched_by: "thread-continuation"`, caveat *"No entity was named in this question; the
   analysis continued with Harish Varma, Suresh Pradhan from this investigation's own
   thread."*, objective sticky, `WEAK 0.4`, `questions_asked = 2`; the session endpoint
   returns both questions.
3. *"What connects Zafar Qureshi and Farah Bano?"* → 2 mentions, 0 resolutions, 0
   hypotheses, `INSUFFICIENT 0.1`, caveats *"2 mention(s) matched no record…"* and
   *"Nothing in this question matched a record…"*; gaps `unresolved-entity` and
   `missing-source`.
4. The same pair question scoped by case **id** and by case **number** returns the identical
   scope, counts and gap categories (table in 35.12).
5. Every pointer opened: 20/20 document pointers return 200; no `dataset`, `metric` or
   `graph_edge` pointer carries a `doc_id`; the dataset-level 404s that existed before this
   session are gone.
6. Vocabulary and status: no guilt term in the serialized answer
   (`guilty`, `culprit`, `arrest him`, …), and every entity's `criminal_status` is `null`
   because the corpus holds no authoritative record for them.

---

## Remaining gaps

1. **Single-entity questions return no relationships.** Relationship discovery is
   pair-based by design, so *"What is the role of X?"* answers with patterns, facts, next
   steps and the focused graph, but not with X's own incident edges ("X calls Y 12 times").
   A next increment should add an entity-centric edge reading.
2. **Operational-table records lack row-level pointers.** The importer stamps
   `origin: {file, row}` on those edges and the pointer is available; the investigator emits
   a `dataset` pointer instead, which is honest but not row-openable.
3. **Skipped sources are not attributed.** 12 files were skipped by the importer and the 15
   social captures produced no records; the gap says the source is absent but not that *N
   catalogued files were not parsed*.
4. **No model configured here.** Narrative sections are empty by design in this environment;
   the deterministic answer is complete without them, and the keyless path is itself the
   tested honesty case.

## Files changed

Uncommitted working tree (base `2dd5b4d`):

*Backend* — `app/investigator/orchestrator.py` (answer-level strength, gap presence wiring,
question-relevant facts, thread-continuation entities, source pointers),
`app/investigator/evidence.py` (`dataset_pointer`, `source_pointer`),
`app/investigator/gaps.py` (graph-evidenced source presence),
`app/investigator/relationships.py` (path edges and their provenance),
`app/investigator/hypotheses.py` (record-only source counting, rolled-up provenance),
`app/investigator/patterns.py` (rolled-up provenance), `app/investigator/next_steps.py`
(pointers for steps that follow up a finding), `app/investigator/schemas.py` (`provenance`
on relationships, hypotheses and next steps), `app/investigator/memory.py`,
`app/api/v1/investigate.py`,
`app/analytics/temporal.py` (edge keys for temporal paths),
`app/ai/gateway.py` and `app/ai/router.py` (pre-existing AI defects),
`tests/test_investigator.py` (105 tests), `tests/conftest.py` (session-wide case **and
dataset** cleanup), `tests/test_ai_interactive_budget.py`.

*Frontend* — `src/components/investigator/*` (evidence, pattern, hypothesis, focused graph,
timeline), `src/lib/investigator.ts`, `src/pages/InvestigatorWorkspace.tsx`,
`src/api/client.ts`, `src/App.tsx`, `src/components/Layout.tsx`, `src/i18n.ts`,
`src/styles.css`, `src/pages/CaseDetail.tsx`, `src/pages/InvestigationPage.tsx`,
plus `frontend/smoke.mjs` (10 behavioral checks, canvas shim, polling wait, opt-in
`SMOKE_DEBUG`) and `frontend/tests/investigator-reasoning.test.mjs` (59 tests).

## Tests run

`pytest tests/ -q` → **515 passed, 0 failed (EXIT=0)** · `pytest tests/test_investigator.py -q`
→ 105 passed · targeted investigator selections → all passed · `npm run typecheck` → clean ·
`npm run build` → EXIT=0 · `npm test` → 59 passed · `node smoke.mjs` vs the live API → 10/10
PASS, no console errors · live probe script (`/tmp/verify_live.py`, results in
`/tmp/verify_live.json`) → runs 1–6 above.

## Important limitations

- The only corpus exercised end-to-end is the synthetic one; the layer is dataset-agnostic
  (nothing keys off sample ids), but a second, structurally different dataset has not been
  probed live.
- Pattern quality depends on the imported corpus: on this dataset most cross-case patterns
  concern addresses and accounts rather than persons, which the output states plainly.
- The model-authored narrative path is exercised by mocked tests only; no live provider was
  called.
- UI verification is functional (mount, submit, render, open a linked document) — not a
  visual review.
- The sandbox reset mid-session, so the database, graph snapshot and Python venv were
  rebuilt from scratch; the corpus import itself is deterministic, but timings quoted here
  come from that rebuild (warm 8.7 s vs ~15 s cold for the master scope is why the harness
  polls).
- Frontend bundle size warning aside, no performance work was done beyond the caps already
  in place (60 nodes / 200 edges / 80 timeline entries / 20 gaps).
