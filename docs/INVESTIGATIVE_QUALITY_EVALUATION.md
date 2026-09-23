# Investigative-quality evaluation — CrimeLink AI (deterministic path)

**Scope.** Evaluation of the *existing* CrimeLink AI on the synthetic demo corpus. No product
behaviour was changed while measuring: this phase added a measurement suite, ran it, and reports
what it found. Findings are recorded, not papered over; no answer was hardcoded and no question was
tuned to make a result look better.

**Artefacts**

| Artefact | Purpose |
|---|---|
| `backend/evals/investigative_quality/case_facts.py` | Ground truth read from the same sources the assistant may use: case-scoped documents, the case's graph nodes and edges |
| `backend/evals/investigative_quality/question_bank.py` | 12 categories (A–L) of questions generated per case from that fact pack |
| `backend/evals/investigative_quality/graders.py` | 15 independent checks; one machine-readable record per question |
| `backend/evals/investigative_quality/runner.py` | Runs the suite in-process (deterministic path) or over HTTP against a live server |
| `backend/evals/investigative_quality/results/` | Ignored local output directory containing the generated JSON run and Markdown scorecard |
| `backend/tests/test_investigative_quality_eval.py` | 11 tests pinning the harness itself (grounding, absence, provenance, temporal, scope, coverage) |

**Command**

```bash
cd backend
.venv/bin/python -m evals.investigative_quality.runner --cases CR-2001 CR-2019 CR-2020
# live server instead of the library, same questions and same ground truth:
.venv/bin/python -m evals.investigative_quality.runner --cases CR-2020 --base-url http://127.0.0.1:8000
```

`--base-url` labels a run `MODEL-ASSISTED` only when a model actually answered
(`model` present and `deterministic_fallback` false); otherwise it stays `DETERMINISTIC`.

---

## 1. Run configuration and how the answers were produced

* Cases: **CR-2001** (armed robbery), **CR-2019** (wildlife smuggling), **CR-2020** (tender
  manipulation) — 12 records and 11 evidence types each, plus one intelligence lead.
* Path: in-process `AIGateway.ask()` on the embedded profile, i.e. the deterministic fallback that
  runs when no provider key is configured. The run reports `mode: DETERMINISTIC`.
* Questions: **135** (45 per case), generated from each case's fact pack.
* Graph store was live for the measured run (`graph_store_available: true`). The runner detects and
  warns when a second process holds the embedded graph, in which case it falls back to the persisted
  snapshot for grading — answers produced under the lock are not comparable.

## 2. Overall result

| Measure | Value |
|---|---|
| Questions | 135 |
| Passed (no failed check) | **25 (18.5 %)** |
| Failed | 110 |
| Mode | DETERMINISTIC (no provider key in this environment) |

Every question is graded by independent checks, so the per-check table below is the more useful
view: it separates *retrieval*, *provenance*, *composition* and *reasoning* failures that a single
pass rate would blend.

## 3. Results by category (raw counts and percentages)

| Cat | Category | Questions | Passed | Failed | Pass rate |
|---|---|---|---|---|---|
| A | Direct facts | 12 | 1 | 11 | 8.3 % |
| B | Entity / relationship | 12 | 1 | 11 | 8.3 % |
| C | Multi-hop | 12 | 2 | 10 | 16.7 % |
| D | Temporal | 15 | 7 | 8 | 46.7 % |
| E | Financial | 9 | 0 | 9 | 0 % |
| F | Communication | 9 | 1 | 8 | 11.1 % |
| G | Contradiction | 9 | 3 | 6 | 33.3 % |
| H | Corroboration | 6 | 2 | 4 | 33.3 % |
| I | Negative / absence | 12 | 0 | 12 | 0 % |
| J | Cross-case security | 15 | 3 | 12 | 20 % |
| K | Ambiguous | 12 | 3 | 9 | 25 % |
| L | Complex multi-capability | 12 | 2 | 10 | 16.7 % |

Failure modes counted per question (a question can contribute more than one):

| Mode | Questions affected |
|---|---|
| REASONING_FAILURE | 55 |
| GROUNDING_FAILURE | 48 |
| CASE_SCOPE_FAILURE | 41 |
| RETRIEVAL_FAILURE | 33 |
| NEGATIVE_EVIDENCE_FAILURE | 17 |
| TEMPORAL_FAILURE | 10 |
| RESPONSE_COMPOSITION_FAILURE | 10 |

## 4. Independent measurements (per check)

| Check | Applicable | Passed | Failed | N/A | Pass rate |
|---|---|---|---|---|---|
| `retrieval_relevance` | 135 | 135 | 0 | 0 | 100 % |
| `citation_validity` | 135 | 135 | 0 | 0 | 100 % |
| `no_invention` | 135 | 135 | 0 | 0 | 100 % |
| `case_scope` | 135 | 135 | 0 | 0 | 100 % |
| `privacy` | 135 | 135 | 0 | 0 | 100 % |
| `contradictions_handled` | 9 | 9 | 0 | 126 | 100 % |
| `corroboration_handled` | 6 | 6 | 0 | 129 | 100 % |
| `conciseness` | 135 | 125 | 10 | 0 | 92.6 % |
| `fact_vs_inference` | 135 | 104 | 31 | 0 | 77.0 % |
| `question_alignment` | 135 | 100 | 35 | 0 | 74.1 % |
| `case_scoped_attribution` | 135 | 94 | 41 | 0 | 69.6 % |
| `temporal_reasoning` | 24 | 14 | 10 | 111 | 58.3 % |
| `irrelevant_evidence_avoided` | 60 | 27 | 33 | 75 | 45.0 % |
| `claim_grounding` | 78 | 27 | 51 | 57 | 34.6 % |
| `missing_evidence_acknowledged` | 21 | 7 | 14 | 114 | 33.3 % |

Two notes on reading these numbers, because they bound what each one means:

* **`irrelevant_evidence_avoided`** — all 135 answers retrieved **12 of 12** records (the whole
  authorised case set). On a 12-record corpus that is not a cost problem, but it does mean narrow
  questions are answered from the full case file: the 45 % figure measures *focus*, not retrieval
  cost or context pressure.
* **`claim_grounding`** is only applicable where an answer carried an entity-asserting claim with a
  document citation (78/135). A refusal with no citation is graded by
  `missing_evidence_acknowledged` instead, and framing sentences ("the file holds 12 documents") are
  not treated as traceability evidence either way.

## 5. What the system does well (measured, not asserted)

1. **Case scoping holds.** 135/135 answers cite and retrieve only records of the asked case;
   no foreign-case value reached an answer, including the deliberate contamination probes
   (`J1`–`J4`). Refusals stay procedural: *"The case-scoped records retrieved for Case CR-2001
   contain no documented connection involving … and Krishna Dubey. No relationship can be asserted
   from the available evidence."*
2. **Citations resolve.** 135/135 answers cite only document ids that exist in the case
   (`citation_validity` 100 %). No fabricated `[DOC-ID]` appeared anywhere in 135 answers.
3. **Nothing was invented.** 135/135 answers use names, numbers and identifiers that exist in the
   case's records or its graph (`no_invention` 100 %).
4. **Privacy holds.** No pseudonym placeholder or provenance key leaked into any answer
   (`privacy` 100 %).
5. **Contradictions and corroboration are handled honestly** on their own questions: `G1` reports
   *"No conflicting accounts were found across the 6 case records examined … no difference in the
   records was detected — it is not a statement that the accounts are complete or accurate."*;
   `H2` reports how many assertions are documented in more than one record. `H1`'s
   corroboration answers never present multi-source support as proof.
6. **Temporal answers state their own limits** when a window is empty: *"no records fall inside the
   default window; the nearest later records are shown."*

## 6. Findings — the failure categories, with evidence

Representative questions are quoted; every question's full answer, citations and per-check reasons
are generated locally by the runner in the ignored `results/` directory.

### 6.1 Attribute questions fall back to a case overview (A, B, E, F, I, K — 35 alignment failures)

Asked for a specific fact, the assistant returns the same generic overview:

| Question | Answer (excerpt) |
|---|---|
| CR-2001-A2 *"What was the date of the incident, and when was the FIR registered?"* | *"**Case CR-2001: Neelkanth Jewellers armed robbery and coordinated escape** — This case is currently recorded as closed under METRO-CENTRAL. The case file holds 12 documents covering 11 evidence types …"* (no incident date, no FIR date) |
| CR-2020-A3 *"Who is the investigating officer for this case?"* | the same overview (no officer named, though the FIR records *Inspector Priya Sharma*) |
| CR-2020-A4 *"What type of offence is recorded in this case?"* | the same overview (no offence type, though the FIR records `CORRUPTION`) |
| CR-2001-B3 *"What vehicle numbers appear in the records of this case?"* | the same overview (no registration number) |
| CR-2020-F2 *"Which phone numbers appear in the call detail records?"* | *"1 communication record was retrieved, but no dated contact between named entities could be reconstructed from them: CR-2020_CDR_02.csv"* (the CDR's numbers are not listed) |
| CR-2001-I3 *"What evidence is missing from this case file?"* | a 12-line inventory of what is on file; the missing list appears only as a trailing clause |

Reproduced identically over HTTP against the running API server, so this is the deployed path, not
a harness artefact. These questions are routed to the case-overview/summary intent, and the composer
has no attribute-answer path (dates, officer, offence class, enumerated registrations, phones,
accounts).

### 6.2 Persons attached by identity resolution are presented as this case's records (41 attribution, 48 grounding)

The case graph carries identities that no record in the case names, because an identifier in the
case's records (a phone, an account, a plate) resolves to a master identity whose attributes and
role were set elsewhere:

| Case | Persons in graph | Named by the case's records | Only in the graph |
|---|---|---|---|
| CR-2001 | 12 | 7 | Kavita Gupta, Sanjay Reddy, Arjun Jain, Deepika Mehta, Rohit Chopra |
| CR-2019 | 7 | 6 | Rahul Kumar |
| CR-2020 | 10 | 6 | Devendra Tiwari, Deepika Sharma, Shreya Patel, Harish Menon |

The answers then present those identities as the case's own documentary record:

* CR-2020-B1/K1: *"Case CR-2020 documents 10 people — **Shreya Patel** is documented as ACCUSED …"*.
  CR-2020's twelve records name six people; `Shreya Patel`'s identity home is `case-d2-018`
  (CR-2019), and her role comes from that shared identity record.
* CR-2001-B2: *"Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF
  relationship"* — the graph edge carries this case's document (`doc-d2-0004`), but **no CR-2001
  document contains the string "Sanjay Reddy"**; the document contains the phone number the identity
  was resolved from.
* Every edge measured carries a document id (0 of 12 + 59 + 66 edges lack one), so this is not a
  missing-provenance problem: it is provenance at *identifier* level being presented as provenance
  at *identity* level.

This single mechanism explains most of `grounding` and `scope-attribution` failures, and it is the
reason the person counts in answers exceed the person counts in the records.

### 6.3 Negative questions are answered with an adjacent positive list (17 negative-evidence, 31 fact-vs-inference)

* CR-2001-E3 *"Are there any financial transfers between Priya Kumar and Amit Sharma?"* → a list of
  `RELATIVE_OF` / `ASSOCIATE_OF` connections and ten further associations; not one word about
  transfers or about what was searched.
* CR-2001-I1 *"Is there any evidence connecting Priya Kumar and Kavita Gupta?"* → the case overview.
* CR-2001-J3 *"Does case CR-2001 contain a transfer to Bank of Baroda a/c 2954?"* → an enumeration of
  11 accounts held by the case.
* CR-2001-I3/J2/J4 → an inventory rather than a scoped negative statement.

Where refusal wording does exist it is exemplary (see §5.1), so the phrasing template is right; it
is not reached for these question shapes.

### 6.4 Communication answers drop timestamps, chronology answers drop order (10 temporal)

* CR-2001-F3 *"Were there any calls around the time of the incident?"* →
  *"The case records document 2 communication links. - +919000000000 ↔ +919000000548: CALLED
  (1 call) [doc-d2-0001]"* — no times at all, so the *"around the time of the incident"* part cannot
  be checked by the reader.
* CR-2001-D2 (six-hour window) and CR-2019-D2/L1/L2 (before the incident) → undated answers.
* CR-2001-D4 / CR-2019-D4 / CR-2020-D4 (sequence of events) → dated list, but the case number is
  repeated on every line (`conciseness`), and the sequence is not summarised.

### 6.5 Contradiction questions that are not "list contradictions" degrade to lists (6)

* CR-2001-G2 *"Where was Priya Kumar at the time of the incident?"* → *"13 locations documented:"*
  followed by every location in the graph, with no incident-time anchoring and no statement about
  whether the accounts agree. `G1` (the explicit contradiction question) is handled well; `G2`/`G3`
  are not.
* CR-2019/CR-2020 `G3` (*"Are the records consistent about where the incident took place?"*) → same
  pattern.

### 6.6 Small composition defects (10 conciseness, 1 blank entity)

* The case number appears once per event line in relationship/chronology answers
  (*"Shreya Patel → Financial transaction — CR-2019: PARTICIPATED_IN [doc-d2-0188]"*).
* CR-2001-J1's refusal contains an empty subject: *"no documented connection involving `  ` and
  Krishna Dubey"*, i.e. one entity name was dropped during composition.
* Two chronology items reduce to *"Event recorded"* (CR-2019-D3, CR-2020-D5), which cites a record but
  conveys nothing to an investigator.

## 7. Which category actually limits answer quality

Ranked by how many questions they spoil and how upstream they sit:

1. **Response composition / question grounding (attributes and negative questions)** — the single
   biggest limiter. It causes the 35 alignment failures in §6.1, the 17 negative-evidence failures
   in §6.3 and the 10 composition failures in §6.6. Retrieval, citations, case scoping and honesty
   are already good enough that the answer text is what fails, not the evidence behind it.
2. **Identity-level provenance (§6.2)** — causes 41 attribution and 48 grounding failures. It is a
   distinct, deeper seam: the case graph mixes "named by a record in this case" with "resolved from
   an identifier seen in this case against a shared identity", and no answer distinguishes them.
3. **Temporal rendering of communication evidence (§6.4)** — 10 failures, bounded and mechanical.
4. **Retrieval focus (§4)** — narrow questions pull the whole case file; measurable, but on this
   corpus it costs focus rather than correctness, and it never broke scoping or citations.

Not limiting: retrieval relevance, citation validity, fabrication, case isolation, privacy,
contradiction handling, corroboration handling — each measured at or near 100 %.

## 8. Recommended next engineering change (one, specific)

**Add a deterministic "asked attribute" answer path to the composer, and make every entity-facing
sentence declare whether the *records* name the entity or only the *graph* links it.**

Concretely, in the deterministic path:

1. For questions about case attributes — incident/FIR dates, investigating officer, offence class,
   enumerated registrations / phone numbers / accounts / locations — answer from the structured
   facts already in hand (`FIR`/`CASE_DIARY` fields, `ANPR`, `CDR`, `FINANCIAL` records) instead of
   the case-overview branch, and keep the licence line ("what the records do not show") short.
2. Tag every graph-derived person/relationship with its support: *named by record X* versus
   *linked through identifier Y seen in record Z (identity home: another case)*, and use that tag in
   the sentence and in the citation, so "the records name 10 people" becomes "six people are named
   by the records; four more are linked through identifiers that appear in them".
3. Route negative questions ("is there any …", "does this case contain …") to an explicit
   no-case-scoped-record answer that names what was searched, instead of an adjacent positive list.

Everything needed for (1)–(3) already exists in the gateway's retrieved facts and graph provenance;
the change is composer/claims-side, deterministic, and does not touch retrieval, the response
format, or any security property that measured 100 %.

## 9. Reproducing this run

```bash
cd backend
# stop any API server first: the embedded graph is single-writer
.venv/bin/python -m evals.investigative_quality.runner --cases CR-2001 CR-2019 CR-2020
.venv/bin/python -m pytest tests/test_investigative_quality_eval.py -q
```

The run is deterministic; repeating it yields the same verdicts. The generated JSON in the
ignored `results/` directory is the machine-readable form of the table in §3–§4.
