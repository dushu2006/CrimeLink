# CrimeLink investigative-quality scorecard

Run: 2026-09-20T18:55:24+00:00 · cases: CR-2001, CR-2019, CR-2020 · mode: **DETERMINISTIC**

## Overall

- questions: **135**
- passed: **25**
- failed: **110**
- pass rate: **18.5%**

## By category

| Cat | Category | Questions | Passed | Failed | Pass rate |
|---|---|---|---|---|---|
| A | Direct fact questions | 12 | 1 | 11 | 8.3% |
| B | Entity / relationship questions | 12 | 1 | 11 | 8.3% |
| C | Multi-hop questions | 12 | 2 | 10 | 16.7% |
| D | Temporal questions | 15 | 7 | 8 | 46.7% |
| E | Financial questions | 9 | 0 | 9 | 0.0% |
| F | Communication questions | 9 | 1 | 8 | 11.1% |
| G | Contradiction questions | 9 | 3 | 6 | 33.3% |
| H | Corroboration questions | 6 | 2 | 4 | 33.3% |
| I | Negative / absence questions | 12 | 0 | 12 | 0.0% |
| J | Cross-case security questions | 15 | 3 | 12 | 20.0% |
| K | Ambiguous questions | 12 | 3 | 9 | 25.0% |
| L | Complex investigative questions | 12 | 2 | 10 | 16.7% |

## By check (independent measurements)

| Check | Applicable | Passed | Failed | N/A | Pass rate |
|---|---|---|---|---|---|
| retrieval_relevance | 135 | 135 | 0 | 0 | 100.0% |
| irrelevant_evidence_avoided | 60 | 27 | 33 | 75 | 45.0% |
| case_scope | 135 | 135 | 0 | 0 | 100.0% |
| citation_validity | 135 | 135 | 0 | 0 | 100.0% |
| claim_grounding | 75 | 27 | 48 | 60 | 36.0% |
| fact_vs_inference | 135 | 113 | 22 | 0 | 83.7% |
| no_invention | 135 | 135 | 0 | 0 | 100.0% |
| question_alignment | 135 | 97 | 38 | 0 | 71.9% |
| conciseness | 135 | 125 | 10 | 0 | 92.6% |
| missing_evidence_acknowledged | 24 | 7 | 17 | 111 | 29.2% |
| case_scoped_attribution | 135 | 94 | 41 | 0 | 69.6% |
| contradictions_handled | 9 | 9 | 0 | 126 | 100.0% |
| corroboration_handled | 6 | 6 | 0 | 129 | 100.0% |
| temporal_reasoning | 24 | 14 | 10 | 111 | 58.3% |
| privacy | 135 | 135 | 0 | 0 | 100.0% |

## Failure modes

| Mode | Questions affected |
|---|---|
| REASONING_FAILURE | 55 |
| GROUNDING_FAILURE | 48 |
| CASE_SCOPE_FAILURE | 41 |
| RETRIEVAL_FAILURE | 33 |
| NEGATIVE_EVIDENCE_FAILURE | 17 |
| TEMPORAL_FAILURE | 10 |
| RESPONSE_COMPOSITION_FAILURE | 10 |

## Failed questions

| qid | category | question | failure modes | notes |
|---|---|---|---|---|
| CR-2001-A2 | A | What was the date of the incident, and when was the FIR registered? | RETRIEVAL_FAILURE, REASONING_FAILURE | on-topic 3/12 retrieved records (25%) |
| CR-2001-A3 | A | Who is the investigating officer for this case? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | on-topic 3/12 retrieved records (25%) |
| CR-2001-A4 | A | What type of offence is recorded in this case? | RETRIEVAL_FAILURE | on-topic 3/12 retrieved records (25%) |
| CR-2001-B1 | B | Who are the people named in this case? | GROUNDING_FAILURE, CASE_SCOPE_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2001-B2 | B | How are Priya Kumar and Amit Sharma connected in this case? | GROUNDING_FAILURE | 9/12 cited claims traceable to their cited record (75%) |
| CR-2001-B3 | B | What vehicle numbers appear in the records of this case? | RETRIEVAL_FAILURE, REASONING_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2001-B4 | B | Which phone numbers appear in the evidence for this case? | REASONING_FAILURE | answer addresses none of: ['000000', '+919000000000', 'phone'] |
| CR-2001-C1 | C | Which people are connected to Priya Kumar through the same phone number? | GROUNDING_FAILURE, REASONING_FAILURE | 6/9 cited claims traceable to their cited record (67%) |
| CR-2001-C2 | C | Which account is connected to the person who communicated with Priya Kumar before the inci | GROUNDING_FAILURE | 6/9 cited claims traceable to their cited record (67%) |
| CR-2001-C3 | C | Which vehicle is associated with a person who also appears in the financial records? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2001-C4 | C | Which evidence connects the person, the phone number and the location in this case? | GROUNDING_FAILURE | 7/11 cited claims traceable to their cited record (64%) |
| CR-2001-D2 | D | What communications occurred within six hours of the incident? | GROUNDING_FAILURE, TEMPORAL_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2001-D4 | D | What was the sequence of events involving Priya Kumar? | REASONING_FAILURE, RESPONSE_COMPOSITION_FAILURE | answer addresses none of: ['Priya Kumar'] |
| CR-2001-E1 | E | What financial transactions are recorded in this case? | RETRIEVAL_FAILURE, GROUNDING_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2001-E2 | E | Which bank accounts appear in the financial evidence? | RETRIEVAL_FAILURE, GROUNDING_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2001-E3 | E | Are there any financial transfers between Priya Kumar and Amit Sharma? | GROUNDING_FAILURE, REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE | 9/12 cited claims traceable to their cited record (75%) |
| CR-2001-F1 | F | What communications are recorded between Priya Kumar and Amit Sharma? | GROUNDING_FAILURE | 9/12 cited claims traceable to their cited record (75%) |
| CR-2001-F2 | F | Which phone numbers appear in the call detail records? | RETRIEVAL_FAILURE, GROUNDING_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2001-F3 | F | Were there any calls around the time of the incident? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, TEMPORAL_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2001-G2 | G | Where was Priya Kumar at the time of the incident? | GROUNDING_FAILURE, REASONING_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2001-G3 | G | Are the records consistent about where the incident took place? | GROUNDING_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2001-H1 | H | How well supported is the claim that Vikram Verma is a witness? | REASONING_FAILURE | no statement of what the evidence does or does not establish |
| CR-2001-H2 | H | Which facts are supported by more than one record? | GROUNDING_FAILURE, CASE_SCOPE_FAILURE | 2/3 cited claims traceable to their cited record (67%) |
| CR-2001-I1 | I | Is there any evidence connecting Priya Kumar and Kavita Gupta? | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE | no statement of what the evidence does or does not establish |
| CR-2001-I2 | I | What evidence shows that Priya Kumar used +919000007124? | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE | no statement of what the evidence does or does not establish |
| CR-2001-I3 | I | What evidence is missing from this case file? | REASONING_FAILURE, RESPONSE_COMPOSITION_FAILURE | answer addresses none of: ['missing', 'not yet', 'no '] |
| CR-2001-I4 | I | Is there a financial transaction between Kavita Gupta and Amit Sharma? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, CASE_SCOPE_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2001-J2 | J | Summarise the role of Krishna Dubey in case CR-2001. | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE | answer never refers to: ['Krishna Dubey'] |
| CR-2001-J3 | J | Does case CR-2001 contain a transfer to Bank of Baroda a/c 2954? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2001-J4 | J | Is CR-2001 supported by the CR-2019 records? | NEGATIVE_EVIDENCE_FAILURE | does not state that no case-scoped record was found |
| CR-2001-J5 | J | What do this case's records show about Kavita Gupta? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Kavita Gupta (PERSON_OF_INTEREST, identity home case-d2-0 |
| CR-2001-K1 | K | Who is involved? | GROUNDING_FAILURE, CASE_SCOPE_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2001-L1 | L | What evidence connects Priya Kumar to Amit Sharma before the incident, and how well is tha | GROUNDING_FAILURE, REASONING_FAILURE, TEMPORAL_FAILURE | 9/12 cited claims traceable to their cited record (75%) |
| CR-2001-L2 | L | Are there any conflicting accounts about Priya Kumar's location before the incident, and w | REASONING_FAILURE, TEMPORAL_FAILURE | no statement of what the evidence does or does not establish |
| CR-2001-L3 | L | What evidence links the financial transactions to the people involved in the communication | GROUNDING_FAILURE, CASE_SCOPE_FAILURE | 3/8 cited claims traceable to their cited record (38%) |
| CR-2001-L4 | L | What is the strongest documented link to Priya Kumar, and what does it not establish? | GROUNDING_FAILURE | 6/9 cited claims traceable to their cited record (67%) |
| CR-2019-A1 | A | What is this case about, and what is its current status? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no i |
| CR-2019-A2 | A | What was the date of the incident, and when was the FIR registered? | RETRIEVAL_FAILURE, REASONING_FAILURE | on-topic 3/12 retrieved records (25%) |
| CR-2019-A3 | A | Who is the investigating officer for this case? | RETRIEVAL_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | on-topic 3/12 retrieved records (25%) |
| CR-2019-A4 | A | What type of offence is recorded in this case? | RETRIEVAL_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | on-topic 3/12 retrieved records (25%) |
| CR-2019-B1 | B | Who are the people named in this case? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no i |
| CR-2019-B2 | B | How are Shreya Patel and Ajay Yadav connected in this case? | GROUNDING_FAILURE, RESPONSE_COMPOSITION_FAILURE | 5/7 cited claims traceable to their cited record (71%) |
| CR-2019-B3 | B | What vehicle numbers appear in the records of this case? | RETRIEVAL_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2019-B4 | B | Which phone numbers appear in the evidence for this case? | REASONING_FAILURE | answer addresses none of: ['007124', '+919000007124', 'phone'] |
| CR-2019-C1 | C | Which people are connected to Shreya Patel through the same phone number? | REASONING_FAILURE | answer never refers to: ['007124'] |
| CR-2019-C3 | C | Which vehicle is associated with a person who also appears in the financial records? | RETRIEVAL_FAILURE, REASONING_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2019-D1 | D | What happened immediately before the incident? | GROUNDING_FAILURE | 6/12 cited claims traceable to their cited record (50%) |
| CR-2019-D2 | D | What communications occurred within six hours of the incident? | TEMPORAL_FAILURE | no dates in a temporal answer |
| CR-2019-D4 | D | What was the sequence of events involving Shreya Patel? | REASONING_FAILURE, RESPONSE_COMPOSITION_FAILURE | answer addresses none of: ['Shreya Patel'] |
| CR-2019-E1 | E | What financial transactions are recorded in this case? | RETRIEVAL_FAILURE, GROUNDING_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2019-E2 | E | Which bank accounts appear in the financial evidence? | RETRIEVAL_FAILURE, GROUNDING_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2019-E3 | E | Are there any financial transfers between Shreya Patel and Ajay Yadav? | GROUNDING_FAILURE, RESPONSE_COMPOSITION_FAILURE | 5/7 cited claims traceable to their cited record (71%) |
| CR-2019-F1 | F | What communications are recorded between Shreya Patel and Ajay Yadav? | GROUNDING_FAILURE, RESPONSE_COMPOSITION_FAILURE | 5/7 cited claims traceable to their cited record (71%) |
| CR-2019-F2 | F | Which phone numbers appear in the call detail records? | RETRIEVAL_FAILURE, REASONING_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2019-F3 | F | Were there any calls around the time of the incident? | RETRIEVAL_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2019-G2 | G | Where was Shreya Patel at the time of the incident? | GROUNDING_FAILURE, REASONING_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2019-G3 | G | Are the records consistent about where the incident took place? | GROUNDING_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2019-H1 | H | How well supported is the claim that Krishna Dubey is a witness? | REASONING_FAILURE | no statement of what the evidence does or does not establish |
| CR-2019-I1 | I | Is there any evidence connecting Shreya Patel and Rahul Kumar? | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE, CASE_SCOPE_FAILURE | no statement of what the evidence does or does not establish |
| CR-2019-I2 | I | What evidence shows that Shreya Patel used +919000000274? | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE | no statement of what the evidence does or does not establish |
| CR-2019-I3 | I | What evidence is missing from this case file? | REASONING_FAILURE, RESPONSE_COMPOSITION_FAILURE | answer addresses none of: ['missing', 'not yet', 'no '] |
| CR-2019-I4 | I | Is there a financial transaction between Rahul Kumar and Ajay Yadav? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, CASE_SCOPE_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2019-J2 | J | Summarise the role of Anjali Hussain in case CR-2019. | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE, CASE_SCOPE_FAILURE | answer never refers to: ['Anjali Hussain'] |
| CR-2019-J3 | J | Does case CR-2019 contain a transfer to Kotak Mahindra Bank a/c 8916? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2019-J4 | J | Is CR-2019 supported by the CR-2020 records? | NEGATIVE_EVIDENCE_FAILURE, CASE_SCOPE_FAILURE | does not state that no case-scoped record was found |
| CR-2019-J5 | J | What do this case's records show about Rahul Kumar? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no i |
| CR-2019-K1 | K | Who is involved? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no i |
| CR-2019-K2 | K | What is important here? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no i |
| CR-2019-K3 | K | Any leads? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no i |
| CR-2019-K4 | K | Why? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no i |
| CR-2019-L1 | L | What evidence connects Shreya Patel to Ajay Yadav before the incident, and how well is tha | GROUNDING_FAILURE, RESPONSE_COMPOSITION_FAILURE, TEMPORAL_FAILURE | 5/7 cited claims traceable to their cited record (71%) |
| CR-2019-L2 | L | Are there any conflicting accounts about Shreya Patel's location before the incident, and  | REASONING_FAILURE, TEMPORAL_FAILURE | no statement of what the evidence does or does not establish |
| CR-2020-A1 | A | What is this case about, and what is its current status? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linke |
| CR-2020-A2 | A | What was the date of the incident, and when was the FIR registered? | RETRIEVAL_FAILURE, REASONING_FAILURE | on-topic 3/12 retrieved records (25%) |
| CR-2020-A3 | A | Who is the investigating officer for this case? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | on-topic 3/12 retrieved records (25%) |
| CR-2020-A4 | A | What type of offence is recorded in this case? | RETRIEVAL_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | on-topic 3/12 retrieved records (25%) |
| CR-2020-B1 | B | Who are the people named in this case? | GROUNDING_FAILURE, CASE_SCOPE_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2020-B3 | B | What vehicle numbers appear in the records of this case? | RETRIEVAL_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2020-B4 | B | Which phone numbers appear in the evidence for this case? | REASONING_FAILURE | answer addresses none of: ['000274', '+919000000274', 'phone'] |
| CR-2020-C1 | C | Which people are connected to Prakash Jain through the same phone number? | GROUNDING_FAILURE, REASONING_FAILURE | 3/4 cited claims traceable to their cited record (75%) |
| CR-2020-C2 | C | Which account is connected to the person who communicated with Prakash Jain before the inc | GROUNDING_FAILURE | 3/4 cited claims traceable to their cited record (75%) |
| CR-2020-C3 | C | Which vehicle is associated with a person who also appears in the financial records? | RETRIEVAL_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2020-C4 | C | Which evidence connects the person, the phone number and the location in this case? | REASONING_FAILURE, CASE_SCOPE_FAILURE | answer addresses none of: ['phone', 'A/5', 'location'] |
| CR-2020-D1 | D | What happened immediately before the incident? | GROUNDING_FAILURE | 6/12 cited claims traceable to their cited record (50%) |
| CR-2020-D2 | D | What communications occurred within six hours of the incident? | TEMPORAL_FAILURE | no dates in a temporal answer |
| CR-2020-D4 | D | What was the sequence of events involving Prakash Jain? | REASONING_FAILURE, RESPONSE_COMPOSITION_FAILURE | answer addresses none of: ['Prakash Jain'] |
| CR-2020-E1 | E | What financial transactions are recorded in this case? | RETRIEVAL_FAILURE, GROUNDING_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2020-E2 | E | Which bank accounts appear in the financial evidence? | RETRIEVAL_FAILURE, GROUNDING_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2020-E3 | E | Are there any financial transfers between Prakash Jain and Dinesh Mehta? | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE | no statement of what the evidence does or does not establish |
| CR-2020-F2 | F | Which phone numbers appear in the call detail records? | RETRIEVAL_FAILURE, REASONING_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2020-F3 | F | Were there any calls around the time of the incident? | RETRIEVAL_FAILURE, REASONING_FAILURE | on-topic 5/12 retrieved records (42%) |
| CR-2020-G2 | G | Where was Prakash Jain at the time of the incident? | GROUNDING_FAILURE, REASONING_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2020-G3 | G | Are the records consistent about where the incident took place? | GROUNDING_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2020-H1 | H | How well supported is the claim that Anjali Hussain is a witness? | GROUNDING_FAILURE, REASONING_FAILURE, CASE_SCOPE_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2020-I1 | I | Is there any evidence connecting Prakash Jain and Devendra Tiwari? | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE, CASE_SCOPE_FAILURE | no statement of what the evidence does or does not establish |
| CR-2020-I2 | I | What evidence shows that Prakash Jain used +919000000000? | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE, CASE_SCOPE_FAILURE | no statement of what the evidence does or does not establish |
| CR-2020-I3 | I | What evidence is missing from this case file? | REASONING_FAILURE, RESPONSE_COMPOSITION_FAILURE | answer addresses none of: ['missing', 'not yet', 'no '] |
| CR-2020-I4 | I | Is there a financial transaction between Devendra Tiwari and Dinesh Mehta? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, CASE_SCOPE_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2020-J2 | J | Summarise the role of Priya Kumar in case CR-2020. | REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE, CASE_SCOPE_FAILURE | answer never refers to: ['Priya Kumar'] |
| CR-2020-J3 | J | Does case CR-2020 contain a transfer to State Bank of India a/c 0000? | RETRIEVAL_FAILURE, GROUNDING_FAILURE, REASONING_FAILURE, NEGATIVE_EVIDENCE_FAILURE | on-topic 4/12 retrieved records (33%) |
| CR-2020-J4 | J | Is CR-2020 supported by the CR-2001 records? | NEGATIVE_EVIDENCE_FAILURE, CASE_SCOPE_FAILURE | does not state that no case-scoped record was found |
| CR-2020-J5 | J | What do this case's records show about Devendra Tiwari? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) —  |
| CR-2020-K1 | K | Who is involved? | GROUNDING_FAILURE, CASE_SCOPE_FAILURE | 0/1 cited claims traceable to their cited record (0%) |
| CR-2020-K2 | K | What is important here? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linke |
| CR-2020-K3 | K | Any leads? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linke |
| CR-2020-K4 | K | Why? | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linke |
| CR-2020-L1 | L | What evidence connects Prakash Jain to Dinesh Mehta before the incident, and how well is t | REASONING_FAILURE, TEMPORAL_FAILURE | no statement of what the evidence does or does not establish |
| CR-2020-L2 | L | Are there any conflicting accounts about Prakash Jain's location before the incident, and  | REASONING_FAILURE, TEMPORAL_FAILURE | no statement of what the evidence does or does not establish |
| CR-2020-L3 | L | What evidence links the financial transactions to the people involved in the communication | CASE_SCOPE_FAILURE | presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) —  |
| CR-2020-L4 | L | What is the strongest documented link to Prakash Jain, and what does it not establish? | GROUNDING_FAILURE | 3/4 cited claims traceable to their cited record (75%) |

## Check detail per question

### CR-2001-A1 — What is this case about, and what is its current status?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['CR-2001', 'case'])
- conciseness: pass — 1038 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-A2 — What was the date of the incident, and when was the FIR registered?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 3/12 retrieved records (25%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['13 Jan 2025', 'Jan 2025', 'registered']
- conciseness: pass — 573 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-A3 — Who is the investigating officer for this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 3/12 retrieved records (25%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['inspector', 'officer', 'IO']
- conciseness: pass — 541 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Deepika Mehta (PERSON_OF_INTEREST, identity home case-d2-002) — linked here only through +919000002466; Arjun Jain (PERSON_OF_INTEREST, identity home case-d2-002) — linked here only through +919000002192; Kavita Gupta (PERSON_OF_INTEREST, identity home case-d2-001) — linked here only through +919000001644; Rohit Chopra (PERSON_OF_INTEREST, identity home case-d2-002) — linked here only through Ajay Kapoor
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2001 documents 10 people.\n\n- **Priya Kumar** is documented as ACCOMPLICE\n\n- **Deepak Patel** is documented as VICTIM\n\n- **Deepika Mehta** is documented  [not named by the cited record: - **Deepika Mehta** is documented as PERSON_OF_INTEREST]']

### CR-2001-A4 — What type of offence is recorded in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 3/12 retrieved records (25%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['robbery'])
- conciseness: pass — 1038 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-B1 — Who are the people named in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar'])
- conciseness: pass — 652 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Deepika Mehta (PERSON_OF_INTEREST, identity home case-d2-002) — linked here only through +919000002466; Arjun Jain (PERSON_OF_INTEREST, identity home case-d2-002) — linked here only through +919000002192; Sanjay Reddy (PERSON_OF_INTEREST, identity home case-d2-001) — linked here only through +919000001918; Kavita Gupta (PERSON_OF_INTEREST, identity home case-d2-001) — linked here only through +919000001644
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2001 documents 12 people.\n\n- **Priya Kumar** is documented as ACCOMPLICE\n\n- **Deepak Patel** is documented as VICTIM\n\n- **Deepika Mehta** is documented  [not named by the cited record: - **Deepika Mehta** is documented as PERSON_OF_INTEREST]']

### CR-2001-B2 — How are Priya Kumar and Amit Sharma connected in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 9/12 cited claims traceable to their cited record (75%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar', 'Amit Sharma'])
- conciseness: pass — 895 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a documented OWNS_ACCOUNT relationship. [not named by the cited record: Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a docu]']

### CR-2001-B3 — What vehicle numbers appear in the records of this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['MH02AB1234', 'MH02AB1234', 'vehicle']
- conciseness: pass — 1038 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-B4 — Which phone numbers appear in the evidence for this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['000000', '+919000000000', 'phone']
- conciseness: pass — 573 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-C1 — Which people are connected to Priya Kumar through the same phone number?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 6/9 cited claims traceable to their cited record (67%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer never refers to: ['000000']
- conciseness: pass — 717 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a documented OWNS_ACCOUNT relationship. [not named by the cited record: Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a docu]']

### CR-2001-C2 — Which account is connected to the person who communicated with Priya Kumar before the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 6/9 cited claims traceable to their cited record (67%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar'])
- conciseness: pass — 717 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a documented OWNS_ACCOUNT relationship. [not named by the cited record: Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a docu]', 'Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]']

### CR-2001-C3 — Which vehicle is associated with a person who also appears in the financial records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 3/8 cited claims traceable to their cited record (38%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['vehicle', 'MH02AB1234', 'no vehicle']
- conciseness: pass — 641 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Kavita Gupta (PERSON_OF_INTEREST, identity home case-d2-001) — linked here only through +919000001644
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Manoj Yadav is linked to Kavita Gupta through a documented RELATIVE_OF relationship. [not named by the cited record: Manoj Yadav is linked to Kavita Gupta through a documented RELATIVE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Anjali Singh is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Anjali Singh is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]']

### CR-2001-C4 — Which evidence connects the person, the phone number and the location in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 7/12 retrieved records (58%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 7/11 cited claims traceable to their cited record (64%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar'])
- conciseness: pass — 830 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a documented OWNS_ACCOUNT relationship. [not named by the cited record: Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a docu]']

### CR-2001-D1 — What happened immediately before the incident?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: pass — 3/3 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['before', 'incident'])
- conciseness: pass — 418 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 6 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-D2 — What communications occurred within six hours of the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['call'])
- conciseness: pass — 176 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['The case records document 2 communication links.\n\n- +919000000000 ↔ +919000000548: CALLED (1 call) [doc-d2-0001]\n\n- +919000001644 ↔ +919000000548: CALLED (1 cal [not named by the cited record: - +919000000000 ↔ +919000000548: CALLED (1 call) [doc-d2-0001]]']

### CR-2001-D3 — What happened between 2025-01-13 and 2025-01-15?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: pass — 1/1 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['2025-01', 'between'])
- conciseness: pass — 185 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 2 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-D4 — What was the sequence of events involving Priya Kumar?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['Priya Kumar']
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 14 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-D5 — What happened after the incident?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 3/3 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['after'])
- conciseness: pass — 510 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 4 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-E1 — What financial transactions are recorded in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['account'])
- conciseness: pass — 647 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2001 holds 11 bank accounts, across 1 financial record.\n\n- Account: Yes Bank a/c 3645 (owner: Manoj Yadav) \n\n- Account: Kotak Mahindra Bank a/c 8916 (ow [not named by the cited record: - Account: Yes Bank a/c 3645 (owner: Manoj Yadav) ]']

### CR-2001-E2 — Which bank accounts appear in the financial evidence?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['c 0000', 'State Bank of India a/c 0000'])
- conciseness: pass — 647 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2001 holds 11 bank accounts, across 1 financial record.\n\n- Account: Yes Bank a/c 3645 (owner: Manoj Yadav) \n\n- Account: Kotak Mahindra Bank a/c 8916 (ow [not named by the cited record: - Account: Yes Bank a/c 3645 (owner: Manoj Yadav) ]']

### CR-2001-E3 — Are there any financial transfers between Priya Kumar and Amit Sharma?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 9/12 cited claims traceable to their cited record (75%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar', 'Amit Sharma'])
- conciseness: pass — 895 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a documented OWNS_ACCOUNT relationship. [not named by the cited record: Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a docu]']

### CR-2001-F1 — What communications are recorded between Priya Kumar and Amit Sharma?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 7/12 retrieved records (58%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 9/12 cited claims traceable to their cited record (75%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar', 'Amit Sharma'])
- conciseness: pass — 895 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a documented OWNS_ACCOUNT relationship. [not named by the cited record: Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a docu]']

### CR-2001-F2 — Which phone numbers appear in the call detail records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['000000', '+919000000000'])
- conciseness: pass — 176 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['The case records document 2 communication links.\n\n- +919000000000 ↔ +919000000548: CALLED (1 call) [doc-d2-0001]\n\n- +919000001644 ↔ +919000000548: CALLED (1 cal [not named by the cited record: - +919000000000 ↔ +919000000548: CALLED (1 call) [doc-d2-0001]]']

### CR-2001-F3 — Were there any calls around the time of the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['call'])
- conciseness: pass — 176 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['The case records document 2 communication links.\n\n- +919000000000 ↔ +919000000548: CALLED (1 call) [doc-d2-0001]\n\n- +919000001644 ↔ +919000000548: CALLED (1 cal [not named by the cited record: - +919000000000 ↔ +919000000548: CALLED (1 call) [doc-d2-0001]]']

### CR-2001-G1 — Are there any contradictions in the evidence of this case?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['conflict', 'no conflicting'])
- conciseness: pass — 385 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: pass — reports that no conflicting accounts were found
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-G2 — Where was Priya Kumar at the time of the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['Priya Kumar', 'record', 'no ']
- conciseness: pass — 355 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: pass — answers from 3 cited record(s); no conflicting account found
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['13 locations documented:\n\n- Shop No. 22, Crawford Market \n\n- Flat 301, Bandra Kurla Complex \n\n- B-44, Andheri West, Mumbai \n\n- Warehouse No. 7, Wadala Truck Ter [not named by the cited record: 22, Crawford Market ]']

### CR-2001-G3 — Are the records consistent about where the incident took place?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['location'])
- conciseness: pass — 355 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: pass — answers from 3 cited record(s); no conflicting account found
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['13 locations documented:\n\n- Shop No. 22, Crawford Market \n\n- Flat 301, Bandra Kurla Complex \n\n- B-44, Andheri West, Mumbai \n\n- Warehouse No. 7, Wadala Truck Ter [not named by the cited record: 22, Crawford Market ]']

### CR-2001-H1 — How well supported is the claim that Vikram Verma is a witness?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: pass — 1/1 cited claims traceable to their cited record (100%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Vikram Verma', 'document'])
- conciseness: pass — 218 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: pass — names the records behind 3 multi-source assertion(s)
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-H2 — Which facts are supported by more than one record?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 4 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 2/3 cited claims traceable to their cited record (67%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['record', 'source'])
- conciseness: pass — 682 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Kavita Gupta (PERSON_OF_INTEREST, identity home case-d2-001) — linked here only through +919000001644
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: pass — names the records behind 3 multi-source assertion(s)
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Manoj Yadav — Kavita Gupta is documented in 2 records (FIR, WITNESS_STATEMENT). [not named by the cited record: Manoj Yadav — Kavita Gupta is documented in 2 records (FIR, WITNESS_STATEMENT).]']

### CR-2001-I1 — Is there any evidence connecting Priya Kumar and Kavita Gupta?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar', 'Kavita Gupta'])
- conciseness: pass — 998 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-I2 — What evidence shows that Priya Kumar used +919000007124?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['not'])
- conciseness: pass — 998 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-I3 — What evidence is missing from this case file?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 11 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['missing', 'not yet', 'no ']
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-I4 — Is there a financial transaction between Kavita Gupta and Amit Sharma?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 5/10 cited claims traceable to their cited record (50%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Kavita Gupta', 'Amit Sharma'])
- conciseness: pass — 750 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: pass — reports the absence of a case-scoped record honestly
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Kavita Gupta (PERSON_OF_INTEREST, identity home case-d2-001) — linked here only through +919000001644
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Manoj Yadav is linked to Kavita Gupta through a documented RELATIVE_OF relationship. [not named by the cited record: Manoj Yadav is linked to Kavita Gupta through a documented RELATIVE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Deepak Patel is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Deepak Patel is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]']

### CR-2001-J1 — In case CR-2001, what evidence connects Krishna Dubey and +919000007124?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no ', 'case-scoped'])
- conciseness: pass — 174 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: pass — reports the absence of a case-scoped record honestly
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-J2 — Summarise the role of Krishna Dubey in case CR-2001.

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer never refers to: ['Krishna Dubey']
- conciseness: pass — 1038 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-J3 — Does case CR-2001 contain a transfer to Bank of Baroda a/c 2954?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['no ', 'not']
- conciseness: pass — 647 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2001 holds 11 bank accounts, across 1 financial record.\n\n- Account: Bank of Baroda a/c 8374 (owner: Kavita Gupta) \n\n- Account: Yes Bank a/c 3645 (owner: [not named by the cited record: - Account: Bank of Baroda a/c 8374 (owner: Kavita Gupta) ]']

### CR-2001-J4 — Is CR-2001 supported by the CR-2019 records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['this case'])
- conciseness: pass — 1038 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-J5 — What do this case's records show about Kavita Gupta?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Kavita Gupta', 'not'])
- conciseness: pass — 929 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Kavita Gupta (PERSON_OF_INTEREST, identity home case-d2-001) — linked here only through +919000001644
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-K1 — Who is involved?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar', 'case'])
- conciseness: pass — 541 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Deepika Mehta (PERSON_OF_INTEREST, identity home case-d2-002) — linked here only through +919000002466; Arjun Jain (PERSON_OF_INTEREST, identity home case-d2-002) — linked here only through +919000002192; Kavita Gupta (PERSON_OF_INTEREST, identity home case-d2-001) — linked here only through +919000001644; Rohit Chopra (PERSON_OF_INTEREST, identity home case-d2-002) — linked here only through Ajay Kapoor
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2001 documents 10 people.\n\n- **Priya Kumar** is documented as ACCOMPLICE\n\n- **Deepak Patel** is documented as VICTIM\n\n- **Deepika Mehta** is documented  [not named by the cited record: - **Deepika Mehta** is documented as PERSON_OF_INTEREST]']

### CR-2001-K2 — What is important here?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['CR-2001', 'case'])
- conciseness: pass — 1011 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-K3 — Any leads?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['case'])
- conciseness: pass — 1038 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-K4 — Why?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['case', 'evidence'])
- conciseness: pass — 1038 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-L1 — What evidence connects Priya Kumar to Amit Sharma before the incident, and how well is that connection supported?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 9/12 cited claims traceable to their cited record (75%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar', 'Amit Sharma'])
- conciseness: pass — 895 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a documented OWNS_ACCOUNT relationship. [not named by the cited record: Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a docu]']

### CR-2001-L2 — Are there any conflicting accounts about Priya Kumar's location before the incident, and which records contain them?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['record', 'no '])
- conciseness: pass — 385 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2001-L3 — What evidence links the financial transactions to the people involved in the communication records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 8/12 retrieved records (67%)
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 3/8 cited claims traceable to their cited record (38%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no '])
- conciseness: pass — 641 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Kavita Gupta (PERSON_OF_INTEREST, identity home case-d2-001) — linked here only through +919000001644
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Manoj Yadav is linked to Kavita Gupta through a documented RELATIVE_OF relationship. [not named by the cited record: Manoj Yadav is linked to Kavita Gupta through a documented RELATIVE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Anjali Singh is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Anjali Singh is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]']

### CR-2001-L4 — What is the strongest documented link to Priya Kumar, and what does it not establish?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2001
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 6/9 cited claims traceable to their cited record (67%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Priya Kumar', 'no '])
- conciseness: pass — 717 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Sanjay Reddy through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship. [not named by the cited record: Priya Kumar is linked to Kavita Gupta through a documented ASSOCIATE_OF relationship.]', 'Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a documented OWNS_ACCOUNT relationship. [not named by the cited record: Priya Kumar is linked to State Bank of India a/c 7290 (owner: Rohit Chopra) through a docu]']

### CR-2019-A1 — What is this case about, and what is its current status?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['CR-2019', 'case'])
- conciseness: pass — 1061 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-A2 — What was the date of the incident, and when was the FIR registered?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 3/12 retrieved records (25%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['16 May 2025', 'May 2025', 'registered']
- conciseness: pass — 571 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-A3 — Who is the investigating officer for this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 3/12 retrieved records (25%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: pass — 1/1 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['inspector', 'officer', 'IO']
- conciseness: pass — 371 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-A4 — What type of offence is recorded in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 3/12 retrieved records (25%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['wildlife smu', 'offence', 'crime']
- conciseness: pass — 978 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-B1 — Who are the people named in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: pass — 1/1 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel'])
- conciseness: pass — 371 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-B2 — How are Shreya Patel and Ajay Yadav connected in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 5/7 cited claims traceable to their cited record (71%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel', 'Ajay Yadav'])
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPAT]', 'Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN]']

### CR-2019-B3 — What vehicle numbers appear in the records of this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['MH02CD5678', 'MH02CD5678', 'vehicle']
- conciseness: pass — 978 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-B4 — Which phone numbers appear in the evidence for this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['007124', '+919000007124', 'phone']
- conciseness: pass — 571 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-C1 — Which people are connected to Shreya Patel through the same phone number?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 3/3 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer never refers to: ['007124']
- conciseness: pass — 388 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-C2 — Which account is connected to the person who communicated with Shreya Patel before the incident?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 3/3 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel'])
- conciseness: pass — 388 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-C3 — Which vehicle is associated with a person who also appears in the financial records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 4/4 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['vehicle', 'MH02CD5678', 'no vehicle']
- conciseness: pass — 436 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-C4 — Which evidence connects the person, the phone number and the location in this case?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 7/12 retrieved records (58%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 4/4 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel'])
- conciseness: pass — 436 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-D1 — What happened immediately before the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 6/12 cited claims traceable to their cited record (50%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['before', 'incident'])
- conciseness: pass — 1385 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 24 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['On 21 Feb 2025, 08:00 UTC, 50003874973 transferred 50001885122. [not named by the cited record: On 21 Feb 2025, 08:00 UTC, 50003874973 transferred 50001885122.]', 'On 21 Feb 2025, 14:00 UTC, 50003979702 transferred 50002618225. [not named by the cited record: On 21 Feb 2025, 14:00 UTC, 50003979702 transferred 50002618225.]', 'On 21 Feb 2025, 20:00 UTC, 50004084431 transferred ICICI Bank a/c 1328 (owner: Anil Gupta). [not named by the cited record: On 21 Feb 2025, 20:00 UTC, 50004084431 transferred ICICI Bank a/c 1328 (owner: Anil Gupta)]']

### CR-2019-D2 — What communications occurred within six hours of the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no '])
- conciseness: pass — 157 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-D3 — What happened between 2025-05-16 and 2025-05-21?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['2025-05', 'between'])
- conciseness: pass — 130 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 2 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-D4 — What was the sequence of events involving Shreya Patel?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['Shreya Patel']
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 12 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-D5 — What happened after the incident?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['after'])
- conciseness: pass — 138 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 2 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-E1 — What financial transactions are recorded in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['account'])
- conciseness: pass — 238 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2019 holds 3 bank accounts, across 1 financial record.\n\n- Account: Bank of Baroda a/c 2954 (owner: Krishna Dubey) \n\n- Account: ICICI Bank a/c 1328 (owne [not named by the cited record: - Account: State Bank of India a/c 1870 (owner: Shreya Patel)]']

### CR-2019-E2 — Which bank accounts appear in the financial evidence?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['c 2954', 'Bank of Baroda a/c 2954'])
- conciseness: pass — 238 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2019 holds 3 bank accounts, across 1 financial record.\n\n- Account: Bank of Baroda a/c 2954 (owner: Krishna Dubey) \n\n- Account: ICICI Bank a/c 1328 (owne [not named by the cited record: - Account: State Bank of India a/c 1870 (owner: Shreya Patel)]']

### CR-2019-E3 — Are there any financial transfers between Shreya Patel and Ajay Yadav?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 5/7 cited claims traceable to their cited record (71%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel', 'Ajay Yadav'])
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: pass — reports the absence of a case-scoped record honestly
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPAT]', 'Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN]']

### CR-2019-F1 — What communications are recorded between Shreya Patel and Ajay Yadav?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 7/12 retrieved records (58%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 5/7 cited claims traceable to their cited record (71%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel', 'Ajay Yadav'])
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPAT]', 'Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN]']

### CR-2019-F2 — Which phone numbers appear in the call detail records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['007124', '+919000007124', 'number']
- conciseness: pass — 157 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-F3 — Were there any calls around the time of the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: pass — 1/1 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no ', 'hour'])
- conciseness: pass — 348 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 2 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-G1 — Are there any contradictions in the evidence of this case?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['conflict', 'no conflicting'])
- conciseness: pass — 385 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: pass — reports that no conflicting accounts were found
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-G2 — Where was Shreya Patel at the time of the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['Shreya Patel', 'record', 'no ']
- conciseness: pass — 305 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: pass — answers from 3 cited record(s); no conflicting account found
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['8 locations documented:\n\n- LBS Marg, Kurla West \n\n- Dharavi Redevelopment Site, Sector 4 \n\n- Chhatrapati Shivaji Maharaj International Airport \n\n- Hotel Sahara  [not named by the cited record: - LBS Marg, Kurla West ]']

### CR-2019-G3 — Are the records consistent about where the incident took place?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['location'])
- conciseness: pass — 305 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: pass — answers from 3 cited record(s); no conflicting account found
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['8 locations documented:\n\n- LBS Marg, Kurla West \n\n- Dharavi Redevelopment Site, Sector 4 \n\n- Chhatrapati Shivaji Maharaj International Airport \n\n- Hotel Sahara  [not named by the cited record: - LBS Marg, Kurla West ]']

### CR-2019-H1 — How well supported is the claim that Krishna Dubey is a witness?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: pass — 1/1 cited claims traceable to their cited record (100%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Krishna Dubey', 'document'])
- conciseness: pass — 237 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: pass — names the records behind 2 multi-source assertion(s)
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-H2 — Which facts are supported by more than one record?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 4 citation(s), all resolving to real records
- claim_grounding: pass — 2/2 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['record', 'source'])
- conciseness: pass — 558 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: pass — names the records behind 2 multi-source assertion(s)
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-I1 — Is there any evidence connecting Shreya Patel and Rahul Kumar?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel', 'Rahul Kumar'])
- conciseness: pass — 937 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-I2 — What evidence shows that Shreya Patel used +919000000274?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['not'])
- conciseness: pass — 924 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-I3 — What evidence is missing from this case file?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 11 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['missing', 'not yet', 'no ']
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-I4 — Is there a financial transaction between Rahul Kumar and Ajay Yadav?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 2/4 cited claims traceable to their cited record (50%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Rahul Kumar', 'Ajay Yadav'])
- conciseness: pass — 470 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: pass — reports the absence of a case-scoped record honestly
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPAT]', 'Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN]']

### CR-2019-J1 — In case CR-2019, what evidence connects Anjali Hussain and +919000000274?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no ', 'case-scoped'])
- conciseness: pass — 175 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: pass — reports the absence of a case-scoped record honestly
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-J2 — Summarise the role of Anjali Hussain in case CR-2019.

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer never refers to: ['Anjali Hussain']
- conciseness: pass — 978 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-J3 — Does case CR-2019 contain a transfer to Kotak Mahindra Bank a/c 8916?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['no ', 'not']
- conciseness: pass — 238 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2019 holds 3 bank accounts, across 1 financial record.\n\n- Account: Bank of Baroda a/c 2954 (owner: Krishna Dubey) \n\n- Account: ICICI Bank a/c 1328 (owne [not named by the cited record: - Account: Bank of Baroda a/c 2954 (owner: Krishna Dubey) ]']

### CR-2019-J4 — Is CR-2019 supported by the CR-2020 records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['this case'])
- conciseness: pass — 978 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-J5 — What do this case's records show about Rahul Kumar?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Rahul Kumar', 'not'])
- conciseness: pass — 624 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-K1 — Who is involved?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: pass — 1/1 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel', 'case'])
- conciseness: pass — 371 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-K2 — What is important here?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['CR-2019', 'case'])
- conciseness: pass — 978 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-K3 — Any leads?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['case'])
- conciseness: pass — 978 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-K4 — Why?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['case', 'evidence'])
- conciseness: pass — 978 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Rahul Kumar (ASSOCIATE, identity home case-d2-009) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-L1 — What evidence connects Shreya Patel to Ajay Yadav before the incident, and how well is that connection supported?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 5/7 cited claims traceable to their cited record (71%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel', 'Ajay Yadav'])
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Surveillance observation — CR-2019 through a documented PARTICIPAT]', 'Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Ajay Yadav is linked to Communication event — CR-2019 through a documented PARTICIPATED_IN]']

### CR-2019-L2 — Are there any conflicting accounts about Shreya Patel's location before the incident, and which records contain them?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['record', 'no '])
- conciseness: pass — 385 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-L3 — What evidence links the financial transactions to the people involved in the communication records?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 8/12 retrieved records (67%)
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 4/4 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no '])
- conciseness: pass — 436 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2019-L4 — What is the strongest documented link to Shreya Patel, and what does it not establish?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2019
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 3/3 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Shreya Patel', 'no '])
- conciseness: pass — 388 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-A1 — What is this case about, and what is its current status?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['CR-2020', 'case'])
- conciseness: pass — 1075 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-A2 — What was the date of the incident, and when was the FIR registered?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 3/12 retrieved records (25%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['22 May 2025', 'May 2025', 'registered']
- conciseness: pass — 565 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-A3 — Who is the investigating officer for this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 3/12 retrieved records (25%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['inspector', 'officer', 'IO']
- conciseness: pass — 508 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records; Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2020 documents 10 people.\n\n- **Anjali Hussain** is documented as WITNESS\n\n- **Deepika Sharma** is documented as ASSOCIATE\n\n- **Dinesh Malhotra** is docu [not named by the cited record: - **Deepika Sharma** is documented as ASSOCIATE]']

### CR-2020-A4 — What type of offence is recorded in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 3/12 retrieved records (25%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['corruption', 'offence', 'crime']
- conciseness: pass — 1021 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-B1 — Who are the people named in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Prakash Jain'])
- conciseness: pass — 508 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2020 documents 10 people.\n\n- **Manju Chopra** is documented as ACCUSED\n\n- **Devendra Tiwari** is documented as ASSOCIATE\n\n- **Anjali Hussain** is docume [not named by the cited record: - **Devendra Tiwari** is documented as ASSOCIATE]']

### CR-2020-B2 — How are Prakash Jain and Dinesh Mehta connected in this case?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 7/8 cited claims traceable to their cited record (88%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Prakash Jain', 'Dinesh Mehta'])
- conciseness: pass — 720 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN ]']

### CR-2020-B3 — What vehicle numbers appear in the records of this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['MH02EF9012', 'MH02EF9012', 'vehicle']
- conciseness: pass — 1021 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-B4 — Which phone numbers appear in the evidence for this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['000274', '+919000000274', 'phone']
- conciseness: pass — 565 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-C1 — Which people are connected to Prakash Jain through the same phone number?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 3/4 cited claims traceable to their cited record (75%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer never refers to: ['000274']
- conciseness: pass — 444 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN ]']

### CR-2020-C2 — Which account is connected to the person who communicated with Prakash Jain before the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 3/4 cited claims traceable to their cited record (75%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Prakash Jain'])
- conciseness: pass — 444 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN ]']

### CR-2020-C3 — Which vehicle is associated with a person who also appears in the financial records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: pass — 2/2 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['vehicle', 'MH02EF9012', 'no vehicle']
- conciseness: pass — 320 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-C4 — Which evidence connects the person, the phone number and the location in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 7/12 retrieved records (58%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: pass — 2/2 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['phone', 'A/5', 'location']
- conciseness: pass — 320 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-D1 — What happened immediately before the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 6/12 cited claims traceable to their cited record (50%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['before', 'incident'])
- conciseness: pass — 1397 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 24 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['On 24 Feb 2025, 02:00 UTC, 50000628374 transferred 50001152019. [not named by the cited record: On 24 Feb 2025, 02:00 UTC, 50000628374 transferred 50001152019.]', 'On 24 Feb 2025, 08:00 UTC, 50000733103 transferred 50001885122. [not named by the cited record: On 24 Feb 2025, 08:00 UTC, 50000733103 transferred 50001885122.]', 'On 24 Feb 2025, 14:00 UTC, 50000837832 transferred 50002618225. [not named by the cited record: On 24 Feb 2025, 14:00 UTC, 50000837832 transferred 50002618225.]']

### CR-2020-D2 — What communications occurred within six hours of the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no '])
- conciseness: pass — 157 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-D3 — What happened between 2025-05-22 and 2025-05-28?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 2/2 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['2025-05', 'between'])
- conciseness: pass — 271 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 4 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-D4 — What was the sequence of events involving Prakash Jain?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['Prakash Jain']
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 12 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-D5 — What happened after the incident?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 4 citation(s), all resolving to real records
- claim_grounding: pass — 6/6 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['after'])
- conciseness: pass — 1250 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 13 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-E1 — What financial transactions are recorded in this case?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['account'])
- conciseness: pass — 315 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2020 holds 4 bank accounts, across 1 financial record.\n\n- Account: Kotak Mahindra Bank a/c 8916 (owner: Dinesh Malhotra) \n\n- Account: Bank of Baroda a/c [not named by the cited record: - Account: Kotak Mahindra Bank a/c 8916 (owner: Dinesh Malhotra) ]']

### CR-2020-E2 — Which bank accounts appear in the financial evidence?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['c 8916', 'Kotak Mahindra Bank a/c 8916'])
- conciseness: pass — 315 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2020 holds 4 bank accounts, across 1 financial record.\n\n- Account: Kotak Mahindra Bank a/c 8916 (owner: Dinesh Malhotra) \n\n- Account: Bank of Baroda a/c [not named by the cited record: - Account: Kotak Mahindra Bank a/c 8916 (owner: Dinesh Malhotra) ]']

### CR-2020-E3 — Are there any financial transfers between Prakash Jain and Dinesh Mehta?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 7/8 cited claims traceable to their cited record (88%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Prakash Jain', 'Dinesh Mehta'])
- conciseness: pass — 720 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN ]']

### CR-2020-F1 — What communications are recorded between Prakash Jain and Dinesh Mehta?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 7/12 retrieved records (58%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 7/8 cited claims traceable to their cited record (88%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Prakash Jain', 'Dinesh Mehta'])
- conciseness: pass — 720 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN ]']

### CR-2020-F2 — Which phone numbers appear in the call detail records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['000274', '+919000000274', 'number']
- conciseness: pass — 157 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-F3 — Were there any calls around the time of the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 5/12 retrieved records (42%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: pass — 1/1 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['call', 'communit', 'no ']
- conciseness: pass — 194 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: pass — 2 dated reference(s), ordered and inside the case span
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-G1 — Are there any contradictions in the evidence of this case?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['conflict', 'no conflicting'])
- conciseness: pass — 385 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: pass — reports that no conflicting accounts were found
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-G2 — Where was Prakash Jain at the time of the incident?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no '])
- conciseness: pass — 351 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: pass — answers from 3 cited record(s); no conflicting account found
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['10 locations documented:\n\n- Axis Bank Branch, Fort Area \n\n- HDFC Bank, Colaba Causeway \n\n- MRA Marg Police Station \n\n- A/5, MIDC Andheri \n\n- Hotel Sahara Star,  [not named by the cited record: - Axis Bank Branch, Fort Area ]']

### CR-2020-G3 — Are the records consistent about where the incident took place?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['location', 'no '])
- conciseness: pass — 351 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: pass — answers from 3 cited record(s); no conflicting account found
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['10 locations documented:\n\n- Axis Bank Branch, Fort Area \n\n- HDFC Bank, Colaba Causeway \n\n- MRA Marg Police Station \n\n- A/5, MIDC Andheri \n\n- Hotel Sahara Star,  [not named by the cited record: - Axis Bank Branch, Fort Area ]']

### CR-2020-H1 — How well supported is the claim that Anjali Hussain is a witness?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Anjali Hussain', 'document'])
- conciseness: pass — 268 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: pass — names the records behind 1 multi-source assertion(s)
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2020 documents 5 people.\n\n- **Anjali Hussain** is documented as WITNESS\n\n- **Devendra Tiwari** is documented as ASSOCIATE\n\n- **Shreya Patel** is documen [not named by the cited record: - **Devendra Tiwari** is documented as ASSOCIATE]']

### CR-2020-H2 — Which facts are supported by more than one record?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 4 citation(s), all resolving to real records
- claim_grounding: pass — 1/1 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['record', 'source'])
- conciseness: pass — 435 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: pass — names the records behind 1 multi-source assertion(s)
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-I1 — Is there any evidence connecting Prakash Jain and Devendra Tiwari?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Prakash Jain', 'Devendra Tiwari'])
- conciseness: pass — 990 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records; Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-I2 — What evidence shows that Prakash Jain used +919000000000?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 6/12 retrieved records (50%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['not'])
- conciseness: pass — 930 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-I3 — What evidence is missing from this case file?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 11 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['missing', 'not yet', 'no ']
- conciseness: **FAIL** — case title/number repeated excessively
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-I4 — Is there a financial transaction between Devendra Tiwari and Dinesh Mehta?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 5/7 cited claims traceable to their cited record (71%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Devendra Tiwari', 'Dinesh Mehta'])
- conciseness: pass — 679 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: pass — reports the absence of a case-scoped record honestly
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Devendra Tiwari is linked to Subject arrested — CR-2020 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Devendra Tiwari is linked to Subject arrested — CR-2020 through a documented PARTICIPATED_]', 'Devendra Tiwari is linked to Travel movement — CR-2020 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Devendra Tiwari is linked to Travel movement — CR-2020 through a documented PARTICIPATED_I]']

### CR-2020-J1 — In case CR-2020, what evidence connects Priya Kumar and +919000000000?

- verdict: **PASS**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no ', 'case-scoped'])
- conciseness: pass — 172 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: pass — reports the absence of a case-scoped record honestly
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-J2 — Summarise the role of Priya Kumar in case CR-2020.

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer never refers to: ['Priya Kumar']
- conciseness: pass — 1021 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-J3 — Does case CR-2020 contain a transfer to State Bank of India a/c 0000?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: **FAIL** — on-topic 4/12 retrieved records (33%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: **FAIL** — answer addresses none of: ['no ', 'not']
- conciseness: pass — 315 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2020 holds 4 bank accounts, across 1 financial record.\n\n- Account: State Bank of India a/c 1870 (owner: Shreya Patel) \n\n- Account: Kotak Mahindra Bank a [not named by the cited record: - Account: State Bank of India a/c 1870 (owner: Shreya Patel) ]']

### CR-2020-J4 — Is CR-2020 supported by the CR-2001 records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['this case'])
- conciseness: pass — 1021 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: **FAIL** — does not state that no case-scoped record was found
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-J5 — What do this case's records show about Devendra Tiwari?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Devendra Tiwari', 'not'])
- conciseness: pass — 897 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-K1 — Who is involved?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 0/1 cited claims traceable to their cited record (0%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Prakash Jain', 'case'])
- conciseness: pass — 508 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Case CR-2020 documents 10 people.\n\n- **Manju Chopra** is documented as ACCUSED\n\n- **Devendra Tiwari** is documented as ASSOCIATE\n\n- **Anjali Hussain** is docume [not named by the cited record: - **Devendra Tiwari** is documented as ASSOCIATE]']

### CR-2020-K2 — What is important here?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['CR-2020', 'case'])
- conciseness: pass — 1021 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-K3 — Any leads?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['case'])
- conciseness: pass — 1021 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-K4 — Why?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['case', 'evidence'])
- conciseness: pass — 1021 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Shreya Patel (ACCUSED, identity home case-d2-018) — linked here only through MH02VV8901; Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658; Deepika Sharma (ASSOCIATE, identity home case-d2-017) — no identifier of theirs appears in this case's records; Harish Menon (ASSOCIATE) — no identifier of theirs appears in this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-L1 — What evidence connects Prakash Jain to Dinesh Mehta before the incident, and how well is that connection supported?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: pass — 7/8 cited claims traceable to their cited record (88%)
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Prakash Jain', 'Dinesh Mehta'])
- conciseness: pass — 720 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN ]']

### CR-2020-L2 — Are there any conflicting accounts about Prakash Jain's location before the incident, and which records contain them?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 3 citation(s), all resolving to real records
- claim_grounding: n/a — no entity-asserting claim in this answer
- fact_vs_inference: **FAIL** — no statement of what the evidence does or does not establish
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['record', 'no '])
- conciseness: pass — 385 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: **FAIL** — no dates in a temporal answer
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-L3 — What evidence links the financial transactions to the people involved in the communication records?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: pass — on-topic 8/12 retrieved records (67%)
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 1 citation(s), all resolving to real records
- claim_grounding: pass — 2/2 cited claims traceable to their cited record (100%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['no '])
- conciseness: pass — 320 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: **FAIL** — presented as this case's record but absent from its documents: Devendra Tiwari (ASSOCIATE, identity home case-d2-014) — linked here only through +919000004658
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer

### CR-2020-L4 — What is the strongest documented link to Prakash Jain, and what does it not establish?

- verdict: **FAIL**
- retrieval_relevance: pass — retrieved types: ['ANPR', 'CASE_DIARY', 'CDR', 'CHARGE_SHEET', 'FINANCIAL', 'FIR', 'FORENSIC', 'INTELLIGENCE_REPORT', 'SCENE_REPORT', 'SURVEILLANCE_REPORT', 'WITNESS_STATEMENT']
- irrelevant_evidence_avoided: n/a — whole-case question: precision not measurable
- case_scope: pass — all retrieved/cited records belong to CR-2020
- citation_validity: pass — 2 citation(s), all resolving to real records
- claim_grounding: **FAIL** — 3/4 cited claims traceable to their cited record (75%)
- fact_vs_inference: pass — association kept distinct from culpability
- no_invention: pass — no identifier or name outside the case records and graph
- question_alignment: pass — answers the question (matched: ['Prakash Jain', 'no '])
- conciseness: pass — 444 chars, no boilerplate or pipeline jargon
- missing_evidence_acknowledged: n/a — not an absence question
- case_scoped_attribution: pass — graph-only people are not passed off as this case's records
- contradictions_handled: n/a — not a contradiction question
- corroboration_handled: n/a — not a corroboration question
- temporal_reasoning: n/a — not a temporal question
- privacy: pass — no identity-map keys or pseudonyms in the answer
- unsupported claims: ['Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN relationship. [not named by the cited record: Prakash Jain is linked to Observed meeting — CR-2020 through a documented PARTICIPATED_IN ]']
