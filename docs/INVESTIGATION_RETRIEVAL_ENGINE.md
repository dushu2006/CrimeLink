# Investigation Retrieval Engine — Priority 2 (Next-Level Build Order)

**Status:** Implemented (Phase 4 of AI Gateway)
**Build Order Position:** 🔴 2 — Critical, after infra validation

## Problem

Previous retrieval (Phase 1-3) already had:
- Doc relevance filtering (budget cap 15k chars interactive, 30k raw)
- Entity detection with multi-seed BFS
- Degree-ranked trimming

But it still lacked:
- Structured query understanding (intent, temporal, spatial, evidence filters)
- Relevance ranking for nodes/edges (not just degree)
- Timeline ordering
- Evidence grounding exposure for UI ("Why?" / "Show Evidence")

Generic RAG (dump everything into vector DB) would be wrong for CrimeLink — it would lose the graph structure that makes investigation intelligence valuable.

## Architecture

```
Investigator Question
        ↓
Query Understanding (intent, entities, temporal, spatial, evidence filters)
        ↓
Entity Detection (persons, phones, vehicles, accounts, locations)
        ↓
Case / Date / Evidence Filters (date range, location, doc type)
        ↓
┌───────────────────────────┐
│ Relevant Document Search  │  ← keyword + metadata + temporal/spatial scoring
│ Relevant Node Search      │  ← entity type + keyword + spatial + confidence
│ Relevant Edge Search      │  ← rel_type + intent + temporal + confidence
│ Neighborhood Expansion    │  ← BFS from seeds, relevance-aware frontier sorting
└─────────────┬─────────────┘
              ↓
       Relevance Ranking (score all, sort, budget enforcement)
              ↓
      Context Compression (timeline ordering, evidence grounding preserved)
              ↓
        DeepSeek V4 (with evidence_refs required)
```

## Implementation

**File:** `backend/app/ai/retrieval.py`

### 1. Query Understanding

```python
@dataclass
class QueryUnderstanding:
    original_question: str
    intent: str  # connection, timeline, evidence, summary, financial, communication, location, general
    entities: List[str]
    keywords: Set[str]
    temporal: TemporalFilter | None
    spatial: SpatialFilter | None
    evidence: EvidenceFilter
    requires_timeline: bool
    requires_evidence_path: bool
    confidence: float
```

- **Intent detection:** regex patterns with plural handling (`connects?`, `transfers?`, `calls?`, etc.)
- **Temporal filter:** extracts "August 12", "2026-08-12", "around Aug 12" → `TemporalFilter(raw_text, is_around)`
- **Spatial filter:** extracts warehouse, bank, tower, CCTV, plus capitalized location names
- **Evidence filter:** doc types (FIR, CCTV, CDR, BANK_STATEMENT, WITNESS_STATEMENT), entity types (Person, Phone, Vehicle, BankAccount), rel types (CALL, TRANSFER, OWNS)

Example:
- Q: "What connects Ravi to the warehouse CCTV on Aug 12"
  → intent=connection, temporal=Aug 12, spatial=[warehouse, cctv], keywords={ravi, warehouse, cctv}, requires_evidence_path=True

- Q: "What happened around the warehouse on August 12?"
  → intent=timeline, temporal=August 12 (is_around=True), spatial=[warehouse], requires_timeline=True

- Q: "Show me all money transfers between accounts"
  → intent=financial, evidence.entity_types=[BankAccount], rel_types=[TRANSFER]

### 2. Entity Detection (Enhanced)

Existing `_detect_entities_in_question` kept, but now merged with query understanding:
- Whole-case nodes scanned for identity fields (name, phone, plate, account)
- Phone/plate regex matching for direct mentions
- Returns top 5 keys, sorted by score (full name match +5, digit-containing +5, longer token +2)
- Now also sets `query_understanding.entities`

### 3. Relevant Search + Neighborhood Expansion

- **Node scoring:** `score_node_relevance(node, understanding)` — entity type boost +10, keyword overlap ×2, spatial +5, confidence ×2
- **Edge scoring:** `score_edge_relevance(edge, understanding)` — rel_type filter +10, intent boost (financial→TRANSFER +8, communication→CALL +8), temporal +3, confidence ×2
- **Doc scoring:** `score_document_relevance(doc, understanding)` — doc type filter +15, keyword overlap ×2, spatial +5, temporal +8, filename bonus +3
- **Expansion:** `expand_neighborhood(snap, seed_keys, depth, limit, understanding)` — BFS with relevance-aware frontier sorting

### 4. Relevance Ranking

```python
def rank_and_filter_context(nodes, edges, docs, understanding, max_nodes, max_edges, max_doc_chars, max_docs) -> RankedContext
```

- Scores all nodes/edges/docs
- Sorts descending
- Enforces budget: `max_nodes=100` interactive / 300 raw, `max_edges=200/600`, `max_doc_chars=15k/30k`, `max_docs=10/15`
- Truncates last doc to fit char budget
- Logs `retrieval.ranking` with available vs included counts

### 5. Context Compression

```python
def compress_context(ranked, understanding, timeline_order=False) -> RankedContext
```

- If `requires_timeline` or `timeline_order=True`, sorts edges/nodes by timestamp
- Preserves `source_doc_ids` for evidence grounding
- Logs `retrieval.compression`

### 6. Timeline Building

```python
def build_timeline_from_context(nodes, edges) -> List[Dict]
```

- Collects events with timestamps from node `first_ts`/`last_ts` and edge `timestamp`
- Sorts chronologically
- Used for investigation timeline feature (Priority 🟠 5)

## Integration into Gateway

**File:** `backend/app/ai/gateway.py`

- Added import: `from app.ai.retrieval import understand_query, rank_and_filter_context, compress_context, build_timeline_from_context`
- In `_answer()`, after intent triage:
  1. `query_understanding = understand_query(question)` → logs `retrieval.query_understanding` with intent, keywords, temporal, spatial
  2. Entity detection (existing) now also sets `query_understanding.entities`
  3. Subgraph retrieval (existing multi-seed BFS)
  4. **New:** `ranked = rank_and_filter_context(...)` with query understanding
  5. **New:** `compressed = compress_context(ranked, understanding, timeline_order=requires_timeline)`
  6. Fallback to old `_filter_relevant_documents` if ranking fails
  7. Timeline built if `requires_timeline`
  8. Context report enhanced with:
     - `query_intent`, `query_keywords`, `temporal_filter`, `spatial_filter`, `evidence_filter`, `requires_timeline`, `requires_evidence_path`, `query_confidence`, `ranking_ms`, `timeline_events_count`
     - `evidence_grounding`: node_ids (20), edge_ids (20), doc_ids, timeline (20) — for UI "Why?" / "Show Evidence" / "Show graph path" / "Open source document" / "Expand relationships"

## Evidence-Grounded AI (Priority 🔴 3)

Every AI response must still carry `evidence_refs` and go through `FindingResult` validation — unchanged.

New evidence grounding fields in `context_report["evidence_grounding"]` enable future UI:

```
AI Conclusion
      ↓
Supporting Evidence
      ↓
Node IDs (from evidence_grounding.node_ids)
      ↓
Relationship IDs (edge_ids)
      ↓
Case IDs
      ↓
Source documents (doc_ids)
```

UI can then implement:
- **Why this conclusion?** → show reasoning_steps + evidence_refs
- **Show evidence** → highlight doc_ids
- **Show graph path** → highlight node_ids + edge_ids in graph
- **Open source document** → fetch doc_id
- **Expand relationships** → use node_ids as seeds for deeper BFS

## Performance Targets (from build order)

```
Question
   ↓
Retrieval:       < 300 ms  (measured: retrieval_ms + query_understanding_ms + ranking_ms)
Context build:   < 100 ms  (context_ms + prompt_build_ms)
First AI token:  < 2–5 sec target (model_ttft_ms)
AI response:     < 10–20 sec target (model_call_ms)
```

Measured via `StageTimer`:
- `query_understanding_ms`, `retrieval_ms`, `ranking_ms`, `timeline_ms`, `context_ms`, `prompt_build_ms`, `model_call_ms`, `total_ms`
- `estimated_prompt_tokens = len(context) // 4`
- `prompt_chars_total`, `prompt_chars_graph`, `prompt_chars_documents`

## Testing

**File:** `backend/tests/test_investigation_retrieval_engine.py` — 14 tests:
- Intent detection (connection, timeline, financial, summary)
- Temporal/spatial/evidence filter extraction
- Node/edge/doc relevance scoring
- Ranking respects budget (nodes, edges, docs, chars)
- Compression timeline ordering
- Timeline building chronological

**Existing:** `test_ai_retrieval_filtering.py` updated — whole-case now capped to 100 nodes (interactive max) via ranking engine, but still > entity-specific 20 nodes.

All 24 AI retrieval tests pass.

## Next Steps (Build Order)

- **Priority 3: Evidence-Grounded AI** — UI buttons "Why?" / "Show Evidence" / "Show graph path" using `evidence_grounding` field
- **Priority 4: Graph-RAG** — Layer C semantic retrieval (embeddings) + Layer D graph expansion from semantic hits (currently Layer A exact + Layer B metadata only)
- **Priority 5: Timeline** — Use `build_timeline_from_context` to render chronological view
- **Priority 6: Copilot** — Auto-generate graph path for "What connects A and B?"
- **Priority 7: Hypotheses** — Supporting/contradicting evidence analysis
- **Priority 8: Explainability** — Confidence model with evidence strength, source agreement, etc.
- **Priority 9-12:** RBAC, performance, vector infra, K8s

## Why Not Vector RAG Yet?

Deterministic retrieval (graph + metadata + keyword) already gives 10x reduction in doc chars (150k→15k) and 83% reduction in tokens for entity-specific queries (47k→5.3k) with nodes 300→20 (from `docs/AI_LATENCY_INVESTIGATION.md`).

Adding vector DB before fixing deterministic retrieval would just add latency and cost without solving the core problem: giving LLM smallest sufficient evidence set.

Graph-RAG should be Layer C after Layer A (exact) and Layer B (metadata) work — which is now done.
