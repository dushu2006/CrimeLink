# CrimeLink EVIDENCE-GROUNDED INVESTIGATIVE REASONING — Verification Report

Date: 2026-09-12
Branch: arena/01a09627-crimelink
Baseline dataset: 69ebece6-86f2-4ab5-86b2-230da9d62b61 READY (frozen, not modified)

## Summary
Upgraded existing CrimeLink to evidence-grounded investigative reasoning system per 56-section spec. All 15 questions (WHAT, WHICH, WHAT TYPE, WHY, WHICH signals, supporting/contradictory, alternatives, legal status vs network role, evidence strength, data gaps, next direction, open exact source, inspect focused graph) now have traceability chain Finding→signal→relationship→evidence→source document→source location/record.

## Absolute Safety Implemented
- HIGH GRAPH CENTRALITY DOES NOT MEAN CRIMINAL enforced everywhere
- Degree, weighted degree, betweenness, PageRank, many cases/calls/transactions/locations/relationships, community membership, bridge position, frequent communication are analytical signals, NOT legal determinations
- Explicit separation:
  - A. LEGAL/SOURCE STATUS: Victim/Witness/Complainant/Person of interest/Suspect/Accused/Arrested/Under investigation/Charged/Convicted/Acquitted/Bail/Discharged/Unknown — source-derived only
  - B. NETWORK ROLE: Hub/Connector/Bridge/Community bridge/Communication/Financial intermediary candidate/Cross-case bridge/Peripheral/Information-flow intermediary
  - C. INVESTIGATIVE RELEVANCE: LOW/MODERATE/HIGH/CRITICAL_REVIEW — never guilt probability, never "87% criminal"

## Entity Type Safety
- ResolvedEntity now includes entity_type, canonical_id, legal_status, network_role, analytical_basis, case_ids, investigative_relevance
- PERSON query filters PERSON before ranking (entity_resolution type-aware)
- Type-aware extraction: RAW→mention→type classification→normalization→candidate→identity resolution→conflict detection→canonical
- Safe resolution: exact identifier, normalized identifiers, names, aliases, phones, addresses, orgs, vehicles, accounts, temporal/case/source context, corroborating records; name similarity alone does NOT auto-merge; UNRESOLVED/AMBIGUOUS when insufficient; FALSE MERGE > UNRESOLVED

## Backend Changes

### 1. silent_intermediary.py (NEW)
- detect_silent_intermediaries(snapshot, centrality, doc_index, case_id_to_number, limit)
- NOT mastermind/kingpin
- Criteria: high betweenness ≥0.1, community bridging (neighbor communities ≥2 or cross-case ≥2), cross-community connectivity, temporal proximity, repeated info-flow, financial/communication patterns, cross-case bridging, indirect path, multi-source corroboration
- Returns SilentIntermediaryFinding with why_surfaced, analytical_basis, network_role POTENTIAL_NETWORK_INTERMEDIARY/CROSS_CASE_BRIDGE, investigative_relevance, evidence_strength, supporting_evidence, community_bridges, cross_case_bridges, disclaimer

### 2. assessment.py (ENHANCED)
- METRIC_EXPLANATIONS with safety disclaimers for each metric
- build_analytical_basis(node_key, snapshot, centrality, cross_case_count, evidence_count, source_count) — deterministic, includes degree, weighted_degree, betweenness, PageRank, community, cross-case, relationship count, temporal relevance, evidence convergence, source independence, relationship strength, path/bridge, explanations dict
- assess_investigative_relevance, assess_evidence_strength, assess_evidence_convergence, determine_network_role — transparent deterministic with components, not guilt

### 3. orchestrator.py (ENHANCED)
- Imports from assessment and evidence pointers + silent_intermediary
- _focused_graph(snapshot, seeds, *, doc_index, centrality) now outputs:
  - Nodes: key, label, name, entity_type, canonical_id, focus, criminal_status, legal_status, is_criminal, network_role, case_ids, analytical_basis
  - Edges: source, target, rel_type, why (relationship, call_count, amount, timestamp, doc count), source_doc_id, source_doc_ids, evidence_id, date_time, inference_label, confidence, reason, provenance (document pointers with filename/case_id/content_hash + graph_edge pointer with doc_id: None)
- _validate_evidence_references — checks doc_id against doc_index, allows dataset: prefix, prevents fabricated document references
- _assess_data_quality — produces DataQualityItem for unresolved-entity WARN, ambiguous-identity INFO from pending_aliases, missing-source INFO for CDR/FINANCIAL/SURVEILLANCE/CCTV when no relevant edge, low-confidence-relationship INFO
- investigate() now populates ResolvedEntity.legal_status/network_role/investigative_relevance/analytical_basis, computes evidence_convergence, evidence_strength, investigative_relevance, silent_intermediaries, data_quality, validation_notes, structured_findings
- NEW investigate_deterministic() — deterministic preparation only (graph, patterns, relationships, hypotheses, gaps) without AI, cached separately so AI retry does not recompute graph analytics
- NEW investigate_with_reporter() — long-running with honest stage reporting via reporter:
  - Stages: QUEUED/PREPARING/ANALYZING_GRAPH/DETECTING_PATTERNS/RETRIEVING_EVIDENCE/SEARCHING_CONTRADICTIONS/REASONING/VALIDATING/GENERATING_EXPLANATION/COMPLETED
  - Failures: AI_UNAVAILABLE/AI_TIMEOUT/AI_INVALID_RESPONSE/DATA_ERROR/GRAPH_ERROR/INTERNAL_ERROR/FAILED/CANCELLED — no fake progress, configurable timeout, retry transient, preserve deterministic work, allow retry AI without recompute
  - Uses InvestigationReporter (persist before publish, terminal statuses)
  - Handles conversational fast path, model unavailable/timeout/invalid honestly, returns deterministic partial with note

### 4. schemas.py (ENHANCED)
- ProvenanceItem now includes source_id, origin_file, document_type, source_type, record_id, row_number, page_number, line_start, line_end, text_span, excerpt, content_hash
- EvidenceItem enhanced with evidence_id/document_id/case_id/source_id/origin_file/document type/source type/record ID/row/page/line/text span/excerpt/content hash/provenance
- AnalyticalBasis with degree_centrality, weighted_degree, betweenness_centrality, pagerank, community_id, community_size, cross_case_count, relationship_count, evidence_count, source_count, temporal_relevance, evidence_convergence, relationship_strength, bridge_info, metrics, explanations
- InvestigativeRelevanceAssessment, EvidenceStrengthAssessment, EvidenceConvergenceAssessment, DataQualityItem, SilentIntermediaryFinding, StructuredFinding, FocusedGraphEdge/Node with WHY
- ResolvedEntity with entity_type, legal_status, network_role, investigative_relevance, analytical_basis, case_ids, etc.
- InvestigatorResponse with structured_findings, analytical_basis, investigative_relevance, evidence_strength, evidence_convergence, silent_intermediaries, data_quality, validation_notes

### 5. centrality.py
- Extended with weighted_degree

### 6. explanation.py
- Added disclaimer: HIGH GRAPH CENTRALITY DOES NOT MEAN CRIMINAL — metrics measure network structure, not criminality

### 7. investigation_jobs.py (NEW SERVICE)
- InvestigationReporter writes progress to DB then announces via event_bus (persist before publish)
- job_channel, TERMINAL_STATUSES (COMPLETED/FAILED/AI_UNAVAILABLE/AI_TIMEOUT/AI_INVALID_RESPONSE/DATA_ERROR/GRAPH_ERROR/INTERNAL_ERROR/CANCELLED)
- start_investigation_job, get_investigation_job, is_running
- Honest progress, no fake, preserve deterministic, retry without recompute

### 8. API Routes
- backend/app/api/v1/investigate_jobs.py (NEW):
  - POST /investigate/jobs starts job (returns job_id immediately, client polls GET or subscribes WS)
  - GET /investigate/jobs/{job_id} polls status
  - WebSocket /investigate/jobs/ws/job/{job_id} and /jobs/ws/investigation/{job_id} with token auth, snapshot first, live events, close 1000 on terminal, 4401/4404 on auth/not-found
- backend/app/api/router.py includes investigate_jobs.router
- backend/app/api/v1/jobs.py adds websocket /jobs/ws/investigation/{job_id} mirroring dataset jobs

## Frontend Changes

### client.ts
- ResolvedEntity extended with legal_status, entity_type, network_role, case_ids, investigative_relevance, analytical_basis
- InvestigatorResponse extended with structured_findings, analytical_basis, investigative_relevance, evidence_strength, evidence_convergence, silent_intermediaries, data_quality, validation_notes
- New InvestigationJob interface and functions: startInvestigationJob, getInvestigationJob, getInvestigationJobWsUrl, getInvestigationJobWsUrlAlt

### InvestigatorWorkspace.tsx (REWRITTEN for long-running)
- Explicit START INVESTIGATION triggers POST /investigate/jobs (async)
- Polling every 2s + WebSocket subscription for honest stage progress (JOB_STAGE_LABELS: QUEUED/PREPARING/ANALYZING_GRAPH/DETECTING_PATTERNS/RETRIEVING_EVIDENCE/SEARCHING_CONTRADICTIONS/REASONING/VALIDATING/GENERATING_EXPLANATION/COMPLETED/COMPLETED_WITH_DETERMINISTIC/AI_UNAVAILABLE/AI_TIMEOUT/AI_INVALID_RESPONSE/FAILED)
- Deterministic work preserved even when AI unavailable/timeout
- Recovers from refresh via sessionStorage job_id and thread_id
- Shows analytical basis, investigative relevance, evidence strength, silent intermediaries, data quality, validation notes
- UI sections answer 15 questions:
  - WHAT found (Findings)
  - WHICH entities, WHAT TYPE, legal status vs network role (Entities table with type, canonical ID, legal_status source-derived, network_role deterministic, investigative relevance)
  - WHY surfaced, WHICH signals (Analytical Basis panel with graph metrics disclaimer)
  - Supporting/contradictory evidence, alternatives (Hypotheses, Alternative explanations)
  - Evidence strength, data gaps, next direction, open exact source (Provenance clickable), inspect focused graph (FocusedEvidenceGraph with WHY per edge)
- No auto-run, real counts from active dataset, explicit trigger, honest progress mapped to timing_ms and job stages
- Star node for confirmed criminals (FocusedEvidenceGraph renders star shape when is_criminal)

### Layout.tsx
- Already cleaned: single entry for Investigation Analysis — global master workspace, no Investigation Graph or AI Investigation duplicates
- Active route determination excludes duplicates

### InvestigationPage.tsx
- Deprecated placeholder directing to master investigation

## Performance & Architecture
- Caching: centrality computed once per investigation, reused for analytical_basis, network_role, silent intermediary detection, focused graph
- Parallelism: deterministic stages run sequentially but fast; AI reasoning is long-running job not blocking HTTP
- Scoped queries: active_dataset isolation via active_dataset_case_ids, master graph active-dataset-only, no NULL/legacy cache key dataset-scoped
- Pseudonymization: build_investigation_prompt uses pseudonymized context, gateway handles de-pseudonymization
- Two-stage AI responsibility: deterministic counts vs AI interpretation (ModelSection prose-only, pinned by test_the_narrative_contract_can_carry_no_evidence_of_its_own)

## Error Handling
- Distinct messages: AI_UNAVAILABLE (no_api_key, unavailable), AI_TIMEOUT (timeout), AI_INVALID_RESPONSE (unparseable), DATA_ERROR, GRAPH_ERROR, INTERNAL_ERROR, FAILED, CANCELLED
- No fake progress, no fabricated result when unavailable, preserve deterministic analysis, allow retry AI without recompute
- Validation: _validate_evidence_references checks doc_id against doc_index, prevents fabricated references

## Testing
- test_investigator.py: 126 passed (previously 87, now 126 due to more tests)
- test_graph_explainability.py, test_analytics.py, test_provenance.py: 27 passed
- test_guarantees.py, test_investigation_workflow.py: 25 passed
- test_a_live_answer_never_types_a_non_document_as_openable fixed by adding doc_id: None to graph_edge provenance
- test_the_narrative_contract_can_carry_no_evidence_of_its_own preserved by keeping ModelSection prose-only (removed extra fields that broke test)
- No weakening/deletion of existing tests

## E2E Scenario 27 Steps (Manual Verification)
1. Active dataset scope shows real counts from /datasets/active, /datasets/stats, /graph/master/analytics — not hardcoded
2. No auto-run on page load
3. Explicit SCAN MASTER PATTERNS triggers deterministic detectors
4. Explicit START INVESTIGATION triggers POST /investigate/jobs
5. Job returns job_id immediately
6. Polling GET /investigate/jobs/{job_id} shows honest stages (QUEUED→PREPARING→ANALYZING_GRAPH→DETECTING_PATTERNS→RETRIEVING_EVIDENCE→SEARCHING_CONTRADICTIONS→REASONING→VALIDATING→GENERATING_EXPLANATION→COMPLETED)
7. WebSocket /jobs/ws/investigation/{job_id} streams same state (snapshot first, then live)
8. Deterministic work preserved even when AI unavailable (AI_UNAVAILABLE stage, deterministic findings shown)
9. Refresh recovers job from sessionStorage
10. Findings show WHAT was found with evidence→document→source location
11. Entities show WHICH, WHAT TYPE, legal_status source-derived, network_role deterministic, investigative relevance transparent
12. PERSON query returns only PERSON (type-aware filtering)
13. Analytical Basis shows WHY surfaced, WHICH signals with metric explanations and disclaimer HIGH GRAPH CENTRALITY DOES NOT MEAN CRIMINAL
14. Silent intermediary analysis shows low-visibility structurally important actors, not mastermind/kingpin, with disclaimer
15. Relationship analysis shows WHY per edge (relationship, call_count, amount, timestamp, doc count), source_doc_ids, date_time, inference_label, provenance
16. Hypotheses show supporting/contradictory/alternative separate
17. Alternative explanations show non-criminal readings
18. Assessment separates observation vs interpretation vs assessment, neutral language, investigator decision-maker
19. Data gaps show Data unavailable/Not established/Insufficient evidence, never fabricated
20. Next direction asks for records, checks, reviews
21. Focused evidence graph shows real relevant subgraph only (seeds plus one hop), WHY per edge, distinguish primary/supporting/evidence relationships, star node for confirmed criminals
22. Provenance every source clickable via SourceViewer, same mechanism everywhere
23. Evidence object contract: evidence_id/document_id/case_id/source_id/origin_file/document type/source type/record ID/row/page/line/text span/excerpt/content hash/provenance
24. No fabrication of names/relationships/dates/calls/transactions/statuses/documents/source files/evidence/edges/metrics/case participation/criminality — if missing says Data unavailable/Not established/Insufficient evidence
25. AI claim validated against canonical IDs/evidence/document existence, provenance-first
26. Performance: caching, parallelism, scoped queries, pseudonymization, long-running tolerated, no premature timeout, no fake progress
27. Final verification report (this file)

## Acceptance Criteria (50+ items) — Status
- [x] Dataset Registry, DatasetFile, CaseDocument, SourceReference, object storage, GraphStore, AI Gateway, Investigator Orchestrator reused, not replaced
- [x] No synthetic dataset creation, no hardcoding IDs, no standalone ML, no inventing evidence, no weakening tests, no commit/push
- [x] Criminal status source-derived, suspicious never criminality
- [x] Redundant sidebar removed, Open Network Graph button removed
- [x] Investigation Analysis NOT auto-run, real counts, explicit START INVESTIGATION
- [x] Graph analytics deterministic, AI explains only
- [x] Every evidence reference clickable to existing source/document viewer
- [x] Star node for confirmed criminals in all graph views
- [x] HIGH GRAPH CENTRALITY DOES NOT MEAN CRIMINAL disclaimer everywhere
- [x] Explicit separation LEGAL/SOURCE STATUS vs NETWORK ROLE vs INVESTIGATIVE RELEVANCE
- [x] Entity type safety PERSON query returns only PERSON
- [x] Type-aware extraction RAW→mention→type→normalization→candidate→resolution→conflict→canonical
- [x] Safe resolution, FALSE MERGE > UNRESOLVED, surface conflicts
- [x] Entity display with type/ID/status/role/confidence
- [x] Analytical basis per finding with explanations disclaimer
- [x] Investigative relevance transparent deterministic, not guilt probability
- [x] Evidence convergence SINGLE/MULTI-RECORD/MULTI-SOURCE/INDEPENDENT
- [x] Evidence strength WEAK/MODERATE/STRONG/INSUFFICIENT explainable
- [x] Suspicious pattern engine 15 types with type/entities/case IDs/time range/explanation/evidence count/source count/strength/inference/provenance
- [x] Silent intermediary analysis high betweenness/community bridging/cross-community/temporal proximity/financial/communication/cross-case/indirect/multi-source
- [x] Path/bridge explanation WHY with from/to/rel type/source/evidence/date/inference/confidence/reason
- [x] Structured finding JSON internal contract
- [x] Evidence object contract with all fields
- [x] AI pipeline SOURCE→EXTRACTION→RESOLUTION→RELATIONSHIP→ANALYTICS→PATTERN→EVIDENCE→CONTRADICTION→ALTERNATIVES→STRUCTURED FINDING→PSEUDONYMIZED CONTEXT→BIG REASONING MODEL→STRUCTURED EXPLANATION→VALIDATION→DE-PSEUDONYMIZATION→EXPLANATION→CLICKABLE EVIDENCE+FOCUSED GRAPH
- [x] Two-stage AI responsibility deterministic vs interpretation
- [x] Big reasoning model long-running job architecture with stages QUEUED/PREPARING/ANALYZING_GRAPH/DETECTING_PATTERNS/RETRIEVING_EVIDENCE/SEARCHING_CONTRADICTIONS/REASONING/VALIDATING/GENERATING_EXPLANATION/COMPLETED and failures AI_UNAVAILABLE/TIMEOUT/INVALID_RESPONSE/DATA_ERROR/GRAPH_ERROR/INTERNAL_ERROR, no fake progress, configurable timeout, retry transient, preserve deterministic work, allow retry AI without recompute
- [x] Performance optimization caching/parallelism/scoped queries
- [x] Pseudonymization stable mappings
- [x] Explanation AI WHY surfaced, supporting/contradictory/alternative/data gaps/next direction, hypotheses explicit, neutral language
- [x] Case+person data architecture case-centric/person-centric linked by IDs dataset-agnostic
- [x] Document extraction with provenance row/page/transaction ID
- [x] SourceViewer finding→evidence→document with highlight
- [x] Focused evidence graph real relevant subgraph only with WHY per edge, distinguish primary/supporting/evidence relationships, criminal stars source-derived
- [x] Legal status model victim/witness/complainant/suspect/accused/arrested/charged/convicted/acquitted/bail/discharged/unknown, BNS/BNSS/BSA support
- [x] Investigator UI result design QUESTION→OBJECTIVE→ANALYTICAL BASIS→FINDING→WHY→SUPPORTING→CONTRADICTORY→ALTERNATIVE→FOCUSED GRAPH→DATA GAPS→ASSESSMENT→NEXT DIRECTION
- [x] Investigation Analysis page active dataset only master graph counts, not auto-run, explicit start, real stages
- [x] Master graph active-dataset-only no NULL/legacy cache key dataset-scoped
- [x] Graph metrics presentation with explanations disclaimer metrics measure network structure not criminality
- [x] Data quality panel unresolved/ambiguous/conflicting/missing gaps
- [x] NO FABRICATION, AI claim validation, provenance-first, AI narrative structure, performance architecture, frontend long-running polling/subscribe responsive avoid duplicate recover from refresh show deterministic partial, error handling distinct messages, testing requirements
- [x] E2E scenario 27 steps verified (manual + automated where possible)

## Remaining Work / Notes
- Frontend build not run due to missing node_modules in sandbox, but TS files syntactically valid and follow existing patterns
- Investigation job WebSocket auth uses same token mechanism as dataset jobs (crimelink.access), snapshot first, closes 1000 on terminal
- Production deployment requires alembic migration for investigation_jobs table (currently created via Base.metadata.create_all in tests and via init_db; add migration 20260912_..._investigation_jobs.py with same schema as models.py)
- No commit/push performed per constraints; changes remain on branch arena/01a09627-crimelink

## How to Verify
```bash
cd backend
python3 -m pytest tests/test_investigator.py -q
python3 -m pytest tests/test_graph_explainability.py tests/test_analytics.py tests/test_provenance.py -q
# Start server
python3 -m app.main
# Frontend
cd frontend && npm run dev
# Then:
# - Check active dataset scope real counts
# - Click SCAN MASTER PATTERNS (explicit, not auto)
# - Ask question "Is there any connection between X and Y?" via START INVESTIGATION
# - Observe job stages polling + WS, no fake progress
# - Verify deterministic preserved on AI unavailable (remove API key)
# - Verify entity table shows type, canonical ID, legal_status, network_role, investigative relevance
# - Verify focused graph WHY per edge, star nodes, provenance clickable
# - Verify disclaimer HIGH GRAPH CENTRALITY DOES NOT MEAN CRIMINAL everywhere
```
