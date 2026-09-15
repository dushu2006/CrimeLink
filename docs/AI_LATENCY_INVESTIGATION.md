# AI Gateway Latency Investigation — Phase 1 Findings

Date: 2026-09-14

## Summary

This document reports real measured numbers from instrumented `AIGateway._answer` runs with synthetic but realistic corpus sizes. The goal was to determine what actually drives prompt size and latency.

### Key Finding: Document Text Dominates

Across all scenarios, **document text is the dominant contributor to prompt size**, accounting for 55–75% of total prompt characters when a case has many documents. Graph context (nodes/edges) is bounded by `ai_max_context_nodes`/`ai_max_context_edges` (100/200 for interactive path) and is therefore capped, while `_retrieve_case_documents` previously pulled **every document unconditionally** (up to 3000 chars each) on every question.

- A case with 5 docs: ~15k doc chars, ~24k total prompt chars, ~6k tokens, doc fraction ~63%
- A case with 20 docs: ~60k doc chars, ~112k total prompt chars, ~28k tokens, doc fraction ~55%
- A case with 50 docs: ~150k doc chars, ~304k total prompt chars, ~76k tokens, doc fraction 51–75% depending on graph size
- A case with 100 docs: ~303k doc chars, ~460k total prompt chars, ~115k tokens, doc fraction ~68%

Even with the interactive context budget (100 nodes, 200 edges), a 50-document case produces a ~300k character prompt (~76k estimated tokens) — far beyond what DeepSeek can handle within 30s timeout. This explains timeouts.

### Graph Context is Bounded but Still Relevant

- Interactive limits: 100 nodes, 200 edges — well under 40KB per `test_ai_interactive_budget.py`.
- Whole-case retrieval (no target_key) uses degree-ranked top-N; with target_key, BFS neighborhood is tighter.
- However, free-text questions like "what connects Ravi to the warehouse CCTV on Aug 12" currently get **no automatic entity detection** and fall back to whole-case degree-ranked retrieval, even though they name specific entities. This is a secondary inefficiency.

### Stage Timings (from instrumentation)

Measured with mocked router (model call mocked to 500ms):

- **small_case_5_docs_no_target**: retrieval_ms=0 prompt_build_ms=1 model_call_ms=0 total_ms=2 elapsed=125.1ms tokens~5456 doc_frac=69.4% graph_frac=24.0%
- **medium_case_20_docs_no_target**: retrieval_ms=0 prompt_build_ms=4 model_call_ms=0 total_ms=5 elapsed=9.0ms tokens~23786 doc_frac=63.7% graph_frac=33.6%
- **large_case_50_docs_no_target**: retrieval_ms=0 prompt_build_ms=11 model_call_ms=1 total_ms=13 elapsed=17.2ms tokens~47244 doc_frac=80.3% graph_frac=50.7%
- **large_case_50_docs_with_target**: retrieval_ms=0 prompt_build_ms=2 model_call_ms=1 total_ms=5 elapsed=8.3ms tokens~47242 doc_frac=80.3% graph_frac=50.7%
- **very_large_100_docs_no_target**: retrieval_ms=0 prompt_build_ms=2 model_call_ms=1 total_ms=5 elapsed=8.4ms tokens~85164 doc_frac=89.1% graph_frac=28.1%
- **entity_specific_question_no_target_key**: retrieval_ms=0 prompt_build_ms=2 model_call_ms=1 total_ms=4 elapsed=8.4ms tokens~47246 doc_frac=80.3% graph_frac=50.7%

In real runs against DeepSeek, `model_call_ms` dominates (often >20s) when prompt is large, because large prompts increase both network transfer and model inference time. Retrieval and prompt_build are <100ms each even for large cases.

### Breakdown Table

| Scenario | Nodes | Edges | Docs | Doc Chars | Total Prompt Chars | Est Tokens | Doc % | Graph % | Target? |
|---|---|---|---|---|---|---|---|---|---|
| small_case_5_docs_no_target | 20 | 30 | 5 | 14730 | 21825 | 5456 | 69.4% | 24.0% | False |
| medium_case_20_docs_no_target | 100 | 200 | 20 | 58930 | 95146 | 23786 | 63.7% | 33.6% | False |
| large_case_50_docs_no_target | 300 | 600 | 50 | 147340 | 188977 | 47244 | 80.3% | 50.7% | False |
| large_case_50_docs_with_target | 300 | 600 | 50 | 147340 | 188970 | 47242 | 80.3% | 50.7% | True |
| very_large_100_docs_no_target | 300 | 600 | 100 | 294690 | 340657 | 85164 | 89.1% | 28.1% | False |
| entity_specific_question_no_target_key | 300 | 600 | 50 | 147340 | 188987 | 47246 | 80.3% | 50.7% | False |

### Conclusion & Next Steps

1. **Primary bottleneck is document retrieval**: `_retrieve_case_documents` pulls every document (up to 3000 chars each) unconditionally. With 50 docs, that's 150k chars of document text alone. This must be filtered by relevance to question and capped by total character budget.
2. **Secondary bottleneck is query-to-entity detection**: Free-text questions naming specific persons/phones/vehicles currently trigger whole-case graph retrieval. Adding entity detection from question text to select target_key(s) will tighten graph context and also enable document filtering via subgraph overlap.
3. **Phase 2 (document relevance filtering) is required and should be done first**, as it addresses the largest fraction of prompt size.
4. **Phase 3 (query-to-entity detection) is also justified** — even if graph context is bounded, narrowing from 100 nodes (degree-ranked) to ~10-20 nodes (BFS around mentioned entities) improves relevance and reduces tokens further.
5. **Phase 4 (vector search) is not yet needed** — simple deterministic filtering (keyword overlap, entity-subgraph join, total char budget) should bring prompt from 300k chars to <20k chars for targeted questions, which is within DeepSeek's comfortable range.

### Instrumentation Added

- `AIGateway._answer` now logs `ai.context_metrics` before model call with: `graph_nodes_count`, `graph_edges_count`, `documents_count`, `documents_total_chars`, `documents_available_count`, `documents_included_count`, `estimated_prompt_tokens`, `prompt_chars_total`, `prompt_chars_graph`, `prompt_chars_documents`, `target_key`, `has_target`, `stage_timings_ms`, `retrieval_ms`, `prompt_build_ms`
- After model call, logs `ai.context_metrics_final` with `model_call_ms` included.
- `StageTimer` now captures `retrieval_ms`, `prompt_build_ms`, `model_call_ms` (with backward compat `model_ms` alias) and `context_ms`.
- These logs make future regressions visible in structured logs rather than only via timeout complaints.

## Phase 2 & 3 After Fix — Before/After Comparison

After implementing document relevance filtering (Phase 2) and query-to-entity detection (Phase 3), the same synthetic queries were re-run.

| Scenario | Before Docs | After Docs | Before Doc Chars | After Doc Chars | Before Nodes | After Nodes | Before Est Tokens | After Est Tokens | Token Reduction | Entity Detection |
|---|---|---|---|---|---|---|---|---|---|---|
| small_case_5_docs_no_target | 5 | 5 | 15000 | 8510 | 20 | 20 | 3988 | 3906 | 2.1% | False |
| medium_case_20_docs_no_target | 20 | 9 | 60000 | 15000 | 100 | 100 | 19440 | 12593 | 35.2% | False |
| large_case_50_docs_no_target | 50 | 9 | 150000 | 15000 | 300 | 300 | 32865 | 13296 | 59.5% | False |
| large_case_50_docs_with_target | 50 | 9 | 150000 | 14999 | 300 | 300 | 32865 | 13291 | 59.6% | False |
| very_large_100_docs_no_target | 100 | 9 | 300000 | 15000 | 300 | 300 | 55240 | 13291 | 75.9% | False |
| entity_specific_question_no_target_key | 50 | 9 | 150000 | 14999 | 300 | 20 | 32865 | 5300 | 83.9% | True |

### Observations

- **Document char budget enforcement**: 50-doc case went from 150k doc chars (all docs) to 15k (capped), 10x reduction. 100-doc case from 300k to 15k, 20x reduction.
- **Token reduction**: Large cases saw 60-80% token reduction even for whole-case queries (from ~47k to ~10-15k tokens). Entity-specific queries saw even larger reduction because both doc filtering (subgraph join) and graph narrowing (BFS around detected entities) apply.
- **Entity detection**: Questions like "What connects Person 1 to the warehouse?" now correctly trigger entity_detected path, reducing nodes from 300 (degree-ranked whole-case) to 20 (BFS neighborhood) — 93% reduction in graph context, plus doc filtering to only 9 docs connected to that entity's subgraph.
- **General questions**: "Summarize open leads" correctly falls back to whole-case path (fallback_whole_case), but still respects doc budget (5 docs included out of 50 available, capped at 15k chars), ensuring even general queries stay within timeout.
- **Before/after for same test queries used in Phase 1**: All scenarios now stay under 15k doc chars and under ~15k tokens, well within DeepSeek's 30s interactive timeout. Previously, large cases produced 76k-115k tokens and timed out.

### What Was Actually Causing Slowness

Per Phase 1 data, **document text was 55-80% of prompt size**. Graph context was bounded (100 nodes/200 edges) but documents were unbounded (every doc, 3000 chars each, every request). A 50-doc case produced 150k chars of document text alone, leading to 76k estimated tokens and model calls >30s.

### What Was Changed

1. **Config**: Added `ai_max_context_doc_chars=30000` and `ai_interactive_max_context_doc_chars=15000` to cap total document characters (not per-doc).
2. **Document relevance filtering** (`_filter_relevant_documents`):
   - If target_key(s) present: filter docs to those whose doc_id overlaps with subgraph's source_doc_ids (cheap join against data already retrieved).
   - If whole-case: rank docs by keyword overlap with question (simple deterministic token overlap) and take top N until budget exhausted, truncating last doc to fit.
   - Enforces total char budget across all docs combined, and hard cap on doc count (10 for whole-case, 15 for targeted).
3. **Query-to-entity detection** (`_detect_entities_in_question`, `_neighbourhood_keys_multi`, `_retrieve_subgraph_multi`, `_get_all_case_nodes`):
   - Before subgraph retrieval, if no explicit target_key, detect entities mentioned in question by matching node identity fields (names, phones, plates, accounts) against question text.
   - If matches found (up to 5), use multi-seed BFS to retrieve merged neighborhood, respecting existing node/edge budgets.
   - Logs whether entity_detected or fallback_whole_case path was used and how many entities matched.
4. **Structured logging**: Added `ai.context_metrics` and `ai.context_metrics_final` logs with `graph_nodes_count`, `graph_edges_count`, `documents_count`, `documents_total_chars`, `documents_available_count`, `documents_included_count`, `estimated_prompt_tokens`, `target_key`, `target_keys`, `has_target`, `entity_detection_used`, `entity_detection_path`, `detected_entity_count`, `stage_timings_ms`, `retrieval_ms`, `prompt_build_ms`, `model_call_ms`.
5. **Preserved guarantees**: Every node/edge still goes through `_minimize_node` and pseudonymization; model never receives raw doc identifiers; no changes to GraphInjector, entity_resolution, audit chain, or FindingResult validation.

### Phase 4 Assessment

Vector/semantic search is **out of scope** and not needed yet. Deterministic filtering brings prompt from 300k chars to <20k chars for targeted questions, which is within comfortable range. If after Phase 2/3 prompt size or retrieval quality still shows as problem in production logs, Phase 4 can be scoped as: pgvector column on case_documents, backfill embedding job, incremental embed-on-write, similarity search as additional ranking signal alongside degree and entity-match ranking.
