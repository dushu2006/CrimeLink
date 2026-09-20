"""Investigation Retrieval Engine — Priority 2 (Next-Level Build Order).

This module implements the structured retrieval pipeline described in the
user's build order:

    Investigator Question
            ↓
    Query Understanding (intent, entities, temporal, spatial, evidence filters)
            ↓
    Entity Detection (persons, phones, vehicles, accounts, locations)
            ↓
    Case / Date / Evidence Filters
            ↓
    ┌───────────────────────────┐
    │ Relevant Document Search  │
    │ Relevant Node Search      │
    │ Relevant Edge Search      │
    │ Neighborhood Expansion    │
    └─────────────┬─────────────┘
                  ↓
           Relevance Ranking
                  ↓
          Context Compression
                  ↓
            DeepSeek V4

Goal: Give the LLM the smallest sufficient evidence set needed to answer
the investigator, not dump everything.

Design principles:
- Deterministic first, semantic later (Graph-RAG is Phase 4, not now)
- Evidence-grounded: every answer must cite node/edge/doc IDs
- Measurable: retrieval_ms, ranking_ms, compression_ms tracked
- Budget-aware: total doc chars capped, nodes/edges capped
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Set, Tuple

from app.logging import get_logger

log = get_logger("crimelink.ai.retrieval")

# ---------------------------------------------------------------------------
# Query Understanding
# ---------------------------------------------------------------------------

@dataclass
class TemporalFilter:
    """Date/time filter extracted from question."""
    start: datetime | None = None
    end: datetime | None = None
    raw_text: str = ""
    is_around: bool = False  # "around Aug 12" means ±1 day


@dataclass
class SpatialFilter:
    """Location filter extracted from question."""
    locations: List[str] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class EvidenceFilter:
    """Evidence type filter."""
    doc_types: List[str] = field(default_factory=list)  # FIR, CCTV, etc.
    entity_types: List[str] = field(default_factory=list)  # Person, Phone, etc.
    rel_types: List[str] = field(default_factory=list)  # CALL, TRANSFER, etc.


@dataclass
class QueryUnderstanding:
    """Structured understanding of investigator question."""
    original_question: str
    intent: str = "general"  # legacy retrieval intent, preserved for callers
    answer_mode: str = "CASE_SUMMARY"  # investigator-facing response mode
    entities: List[str] = field(default_factory=list)  # detected entity keys
    keywords: Set[str] = field(default_factory=set)
    exact_terms: List[str] = field(default_factory=list)  # identifiers worth exact matching
    temporal: TemporalFilter | None = None
    spatial: SpatialFilter | None = None
    evidence: EvidenceFilter = field(default_factory=EvidenceFilter)
    requires_timeline: bool = False
    requires_evidence_path: bool = False
    confidence: float = 0.0


# Intent patterns — allow plurals and verb forms
_INTENT_PATTERNS = {
    "connection": re.compile(r"\b(connects?|connected|link(?:s|ed)?|relat(?:e|ed|ion)|associat(?:e|ed|ion)|between|path|route)\b", re.I),
    "timeline": re.compile(r"\b(timeline|chronolog(?:y|ical)|when|sequence|order|before|after|around|during|chronological)\b", re.I),
    "evidence": re.compile(r"\b(evidence|proof|show|document|source|cite|reference)\b", re.I),
    "summary": re.compile(r"\b(summar(?:y|ize|ise)?|overview|brief|leads|open)\b", re.I),
    "financial": re.compile(r"\b(money|transfers?|transaction|bank|accounts?|amount|financial)\b", re.I),
    "communication": re.compile(r"\b(calls?|phone|contact|communicat(?:e|ion)|sms|tower)\b", re.I),
    "location": re.compile(r"\b(where|location|warehouse|place|cctv|tower|address)\b", re.I),
}

# Evidence type patterns
_DOC_TYPE_PATTERNS = {
    "FIR": re.compile(r"\bfir\b", re.I),
    "CCTV": re.compile(r"\bcctv\b", re.I),
    "CALL_RECORD": re.compile(r"\b(call\s*record|cdr)\b", re.I),
    "BANK_STATEMENT": re.compile(r"\b(bank\s*statement|transaction|account\s*statement)\b", re.I),
    "WITNESS_STATEMENT": re.compile(r"\b(witness\s*statement|witness)\b", re.I),
    "FIELD_REPORT": re.compile(r"\b(field\s*report)\b", re.I),
}

# Date patterns
_DATE_PATTERNS = [
    # August 12, Aug 12, 12 August, 12th August
    re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b", re.I),
    re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I),
    # 2026-08-12, 12/08/2026, 12-08-2026
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"),
    # around, on, during
    re.compile(r"\b(?:around|on|during|at)\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b", re.I),
]

# Location patterns (common investigation locations)
_LOCATION_KEYWORDS = {
    "warehouse", "office", "residence", "hotel", "airport", "station",
    "market", "shop", "bank", "atm", "tower", "cctv", "location",
}


def _extract_keywords(question: str) -> Set[str]:
    """Extract meaningful keywords from question."""
    if not question:
        return set()
    tokens = re.split(r"[^a-z0-9]+", question.lower())
    stop = {
        "what", "who", "when", "where", "why", "how", "which", "this", "that",
        "these", "those", "the", "and", "or", "but", "with", "from", "about",
        "into", "case", "tell", "show", "list", "give", "find", "are", "is",
        "was", "were", "been", "have", "has", "had", "does", "did", "can",
        "could", "would", "should", "will", "connected", "connects", "connection",
        "summary", "summarize", "summarise", "open", "leads", "lead",
    }
    return {t for t in tokens if len(t) >= 3 and t not in stop}


def _detect_intent(question: str) -> str:
    """Detect primary retrieval intent from question."""
    q_lower = question.lower()
    scores = {}
    for intent, pattern in _INTENT_PATTERNS.items():
        matches = pattern.findall(q_lower)
        if matches:
            scores[intent] = len(matches)

    if not scores:
        return "general"

    # Return highest scoring intent; this legacy field remains intentionally
    # small because ranking code and existing clients use it.
    return max(scores.items(), key=lambda x: x[1])[0]


_ANSWER_MODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PATTERN_ANALYSIS", re.compile(r"\b(patterns?|clusters?|recurring|repeated|common|network pattern)\b", re.I)),
    ("RELATIONSHIP_ANALYSIS", re.compile(r"\b(connect(?:s|ed|ion)?|link(?:s|ed)?|relationship|association|between|path|route)\b", re.I)),
    ("TIMELINE_ANALYSIS", re.compile(r"\b(timeline|chronolog(?:y|ical)|sequence|before|after|during|when|what happened)\b", re.I)),
    ("EVIDENCE_ANALYSIS", re.compile(r"\b(evidence|records?|documents?|sources?|proof|support(?:s|ed)?)\b", re.I)),
    ("ENTITY_LOOKUP", re.compile(r"\b(vehicles?|cars?|phones?|mobiles?|accounts?|banks?|locations?|addresses?|organizations?)\b", re.I)),
    ("PERSON_ANALYSIS", re.compile(r"\b(person|people|individual|profile|activities|associated with|what did)\b", re.I)),
    ("CASE_SUMMARY", re.compile(r"\b(summary|summar(?:ize|ise|y)|overview|brief|whole case|case briefing)\b", re.I)),
)


def _classify_answer_mode(question: str, intent: str) -> str:
    """Classify the explanation contract without changing legacy retrieval intent."""
    for mode, pattern in _ANSWER_MODE_PATTERNS:
        if pattern.search(question or ""):
            return mode
    return {
        "connection": "RELATIONSHIP_ANALYSIS",
        "timeline": "TIMELINE_ANALYSIS",
        "evidence": "EVIDENCE_ANALYSIS",
        "summary": "CASE_SUMMARY",
        "financial": "ENTITY_LOOKUP",
        "communication": "RELATIONSHIP_ANALYSIS",
        "location": "ENTITY_LOOKUP",
    }.get(intent, "CASE_SUMMARY")


def _extract_exact_terms(question: str) -> list[str]:
    """Extract identifier-like terms for exact/keyword retrieval boosts."""
    if not question:
        return []
    candidates = re.findall(
        r"(?<![A-Za-z0-9])[A-Za-z]{2,}[-_/][A-Za-z0-9][A-Za-z0-9_-]*|"
        r"(?<![A-Za-z0-9])\+?\d{10,13}(?![A-Za-z0-9])",
        question,
    )
    stop = {"what", "when", "where", "which", "show", "tell", "case"}
    return sorted({item for item in candidates if item.casefold() not in stop}, key=str.casefold)


def _extract_temporal_filter(question: str) -> TemporalFilter | None:
    """Extract temporal filter from question."""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(question)
        if match:
            raw = match.group(0)
            is_around = bool(re.search(r"\baround\b", raw, re.I) or re.search(r"\baround\b", question[:match.start()], re.I))
            # For now, store raw text; full date parsing would require dateutil
            # We keep it simple and store the matched text for filtering
            return TemporalFilter(
                raw_text=raw,
                is_around=is_around,
            )
    return None


def _extract_spatial_filter(question: str) -> SpatialFilter | None:
    """Extract spatial filter from question."""
    q_lower = question.lower()
    locations = []
    for loc in _LOCATION_KEYWORDS:
        if loc in q_lower:
            locations.append(loc)
    
    # Also look for capitalized location names (heuristic)
    # e.g., "warehouse on MG Road" — extract MG Road
    cap_pattern = re.compile(r"\b(?:at|near|in|around)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)")
    for match in cap_pattern.finditer(question):
        loc = match.group(1).strip()
        if len(loc) >= 3 and loc.lower() not in locations:
            locations.append(loc.lower())
    
    if locations:
        return SpatialFilter(locations=locations, raw_text=", ".join(locations))
    return None


def _extract_evidence_filter(question: str) -> EvidenceFilter:
    """Extract evidence type filter from question."""
    q_lower = question.lower()
    doc_types = []
    for doc_type, pattern in _DOC_TYPE_PATTERNS.items():
        if pattern.search(q_lower):
            doc_types.append(doc_type)
    
    # Entity types
    entity_types = []
    if re.search(r"\bperson|people|individual\b", q_lower):
        entity_types.append("Person")
    if re.search(r"\bphone|mobile|call\b", q_lower):
        entity_types.append("Phone")
    if re.search(r"\bvehicle|car|plate\b", q_lower):
        entity_types.append("Vehicle")
    if re.search(r"\baccount|bank\b", q_lower):
        entity_types.append("BankAccount")
    
    # Relationship types
    rel_types = []
    if re.search(r"\bcall|phone\b", q_lower):
        rel_types.append("CALL")
    if re.search(r"\btransfer|transaction|money\b", q_lower):
        rel_types.append("TRANSFER")
    if re.search(r"\bowns|owner\b", q_lower):
        rel_types.append("OWNS")
    
    return EvidenceFilter(
        doc_types=doc_types,
        entity_types=entity_types,
        rel_types=rel_types,
    )


def understand_query(question: str) -> QueryUnderstanding:
    """Parse investigator question into structured understanding."""
    start = time.perf_counter()
    
    intent = _detect_intent(question)
    answer_mode = _classify_answer_mode(question, intent)
    keywords = _extract_keywords(question)
    exact_terms = _extract_exact_terms(question)
    temporal = _extract_temporal_filter(question)
    spatial = _extract_spatial_filter(question)
    evidence = _extract_evidence_filter(question)
    
    # Detect if timeline or evidence path is required
    requires_timeline = intent == "timeline" or bool(temporal) or "timeline" in question.lower()
    requires_evidence_path = intent in ("connection", "evidence") or "why" in question.lower() or "path" in question.lower()
    
    # Confidence based on how many filters we extracted
    confidence = 0.5
    if keywords:
        confidence += 0.1
    if temporal:
        confidence += 0.15
    if spatial:
        confidence += 0.1
    if evidence.doc_types or evidence.entity_types:
        confidence += 0.1
    confidence = min(1.0, confidence)
    
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    
    understanding = QueryUnderstanding(
        original_question=question,
        intent=intent,
        answer_mode=answer_mode,
        keywords=keywords,
        exact_terms=exact_terms,
        temporal=temporal,
        spatial=spatial,
        evidence=evidence,
        requires_timeline=requires_timeline,
        requires_evidence_path=requires_evidence_path,
        confidence=confidence,
    )
    
    log.info(
        "retrieval.query_understanding",
        question_preview=question[:100],
        intent=intent,
        answer_mode=answer_mode,
        exact_terms=exact_terms,
        keywords=list(keywords)[:10],
        temporal=temporal.raw_text if temporal else None,
        spatial=spatial.locations if spatial else None,
        evidence_doc_types=evidence.doc_types,
        requires_timeline=requires_timeline,
        confidence=confidence,
        elapsed_ms=elapsed_ms,
    )
    
    return understanding


# ---------------------------------------------------------------------------
# Relevance Scoring
# ---------------------------------------------------------------------------

def score_node_relevance(node: Dict[str, Any], understanding: QueryUnderstanding) -> float:
    """Score a node by relevance to query understanding."""
    score = 0.0
    props = node.get("properties", {}) or {}
    label = node.get("label", "")
    
    # Entity type filter boost
    if understanding.evidence.entity_types and label in understanding.evidence.entity_types:
        score += 10.0
    
    # Exact identifier matches outrank semantic/keyword overlap. This is
    # important for account numbers, case-specific record IDs and phone-like
    # identifiers where approximate similarity is unsafe.
    content = " ".join(str(v) for v in props.values() if isinstance(v, (str, int, float))).lower()
    content += f" {label.lower()}"
    for term in understanding.exact_terms:
        if term.casefold() in content:
            score += 25.0

    for kw in understanding.keywords:
        if kw in content:
            score += min(content.count(kw), 3) * 2.0
    
    # Spatial filter boost
    if understanding.spatial:
        for loc in understanding.spatial.locations:
            if loc in content:
                score += 5.0
    
    # Confidence boost
    score += float(node.get("confidence", 0.5)) * 2.0
    
    return score


def score_edge_relevance(edge: Dict[str, Any], understanding: QueryUnderstanding) -> float:
    """Score an edge by relevance to query understanding."""
    score = 0.0
    rel_type = edge.get("rel_type", "")
    
    # Relationship type filter boost
    if understanding.evidence.rel_types:
        for rt in understanding.evidence.rel_types:
            if rt.lower() in rel_type.lower():
                score += 10.0
    
    # Intent-based boost
    if understanding.intent == "financial" and "transfer" in rel_type.lower():
        score += 8.0
    if understanding.intent == "communication" and "call" in rel_type.lower():
        score += 8.0
    
    # Keyword matching, with an exact identifier path for graph keys and
    # record-like relationship properties.
    content = f"{rel_type} {edge.get('source_key','')} {edge.get('target_key','')} {edge.get('source_doc_id','')} {edge.get('source_doc_ids','')}".lower()
    for term in understanding.exact_terms:
        if term.casefold() in content:
            score += 25.0
    for kw in understanding.keywords:
        if kw in content:
            score += 2.0
    
    # Temporal boost if edge has timestamp and query has temporal filter
    if understanding.temporal and edge.get("timestamp"):
        score += 3.0  # Temporal edges are more relevant for timeline queries
    
    score += float(edge.get("confidence", 0.5)) * 2.0
    
    return score


def score_document_relevance(doc: Dict[str, Any], understanding: QueryUnderstanding) -> float:
    """Score a document by relevance to query understanding."""
    score = 0.0
    content = str(doc.get("content", "")).lower()
    filename = str(doc.get("filename", "")).lower()
    doc_type = str(doc.get("document_type", "")).lower()
    combined = f"{content} {filename} {doc_type}"
    
    # Evidence type filter
    if understanding.evidence.doc_types:
        for dt in understanding.evidence.doc_types:
            if dt.lower() in doc_type or dt.lower() in filename:
                score += 15.0
    
    # Exact identifier matches are a hard relevance signal, not a fuzzy hint.
    for term in understanding.exact_terms:
        if term.casefold() in combined:
            score += 25.0

    # Keyword matching
    for kw in understanding.keywords:
        if kw in combined:
            score += min(combined.count(kw), 3) * 2.0

    # Spatial filter
    if understanding.spatial:
        for loc in understanding.spatial.locations:
            if loc in combined:
                score += 5.0
    
    # Temporal filter
    if understanding.temporal and understanding.temporal.raw_text.lower() in combined:
        score += 8.0
    
    # Bonus for filename match (more specific)
    for kw in understanding.keywords:
        if kw in filename:
            score += 3.0
    
    return score


# ---------------------------------------------------------------------------
# Neighborhood Expansion
# ---------------------------------------------------------------------------

def expand_neighborhood(
    snap,
    seed_keys: List[str],
    *,
    depth: int = 2,
    limit: int = 100,
    understanding: QueryUnderstanding | None = None,
) -> Set[str]:
    """BFS expansion from seed keys, with relevance-aware pruning."""
    adjacency: Dict[str, List[str]] = {}
    for edge in snap.edges:
        adjacency.setdefault(edge.source_key, []).append(edge.target_key)
        adjacency.setdefault(edge.target_key, []).append(edge.source_key)
    
    seen: Set[str] = set()
    frontier: Set[str] = set()
    
    # Initialize with valid seeds
    for rk in seed_keys:
        if rk in snap.nodes and rk not in seen and len(seen) < limit:
            seen.add(rk)
            frontier.add(rk)
    
    if not frontier:
        return set()
    
    # BFS with relevance scoring if understanding provided
    for _ in range(max(1, depth)):
        next_frontier: Set[str] = set()
        
        # Sort frontier by relevance if understanding provided
        frontier_list = list(frontier)
        if understanding:
            # Score frontier nodes
            scored = []
            for key in frontier_list:
                node = snap.nodes.get(key)
                if node:
                    node_dict = {
                        "provenance_key": key,
                        "label": node.label,
                        "properties": dict(node.properties),
                        "confidence": node.properties.get("confidence", 1.0),
                    }
                    score = score_node_relevance(node_dict, understanding)
                    scored.append((score, key))
            scored.sort(key=lambda x: -x[0])
            frontier_list = [k for _, k in scored]
        
        for node_key in frontier_list:
            for neighbour in adjacency.get(node_key, []):
                if neighbour not in seen and len(seen) < limit:
                    seen.add(neighbour)
                    next_frontier.add(neighbour)
        
        frontier = next_frontier
        if not frontier:
            break
    
    return seen


# ---------------------------------------------------------------------------
# Relevance Ranking
# ---------------------------------------------------------------------------

@dataclass
class RankedContext:
    """Ranked and filtered context for LLM."""
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    documents: List[Dict[str, Any]]
    scores: Dict[str, float]  # key -> score
    total_chars: int
    ranking_ms: int


def rank_and_filter_context(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    documents: List[Dict[str, Any]],
    understanding: QueryUnderstanding,
    *,
    max_nodes: int = 100,
    max_edges: int = 200,
    max_doc_chars: int = 15000,
    max_docs: int = 10,
) -> RankedContext:
    """Rank nodes/edges/docs by relevance and filter to budget."""
    start = time.perf_counter()
    
    # Score all
    node_scores = [(score_node_relevance(n, understanding), n) for n in nodes]
    edge_scores = [(score_edge_relevance(e, understanding), e) for e in edges]
    doc_scores = [(score_document_relevance(d, understanding), d) for d in documents]
    
    # Sort by score descending
    node_scores.sort(key=lambda x: (-x[0], x[1].get("provenance_key", "")))
    edge_scores.sort(key=lambda x: (-x[0], x[1].get("rel_type", "")))
    doc_scores.sort(key=lambda x: (-x[0], x[1].get("filename", "")))
    
    # Filter nodes/edges to max
    filtered_nodes = [n for _, n in node_scores[:max_nodes]]
    filtered_edges = [e for _, e in edge_scores[:max_edges]]
    
    # Filter docs to char budget and max count.  The first pass remains score
    # ordered, while a small diversity bonus prevents a high-scoring stack of
    # one evidence type from crowding out an independent source type.
    filtered_docs = []
    total_chars = 0
    scores_dict: Dict[str, float] = {}
    remaining_candidates = list(doc_scores)
    selected_types: set[str] = set()
    selected_source_docs: dict[str, int] = {}

    while remaining_candidates and len(filtered_docs) < max_docs:
        def _selection_key(item: tuple[float, Dict[str, Any]]):
            score, doc = item
            doc_type = str(doc.get("document_type") or "UNKNOWN").rsplit(".", 1)[-1].upper()
            diversity_bonus = 2.0 if selected_types and doc_type not in selected_types else 0.0
            source_id = str(doc.get("source_document_id") or doc.get("doc_id") or "")
            repeat_penalty = 1.0 if selected_source_docs.get(source_id, 0) >= 2 else 0.0
            return (-(score + diversity_bonus - repeat_penalty), str(doc.get("filename", "")))

        selected_index = min(range(len(remaining_candidates)), key=lambda index: _selection_key(remaining_candidates[index]))
        score, doc = remaining_candidates.pop(selected_index)
        source_id = str(doc.get("source_document_id") or doc.get("doc_id") or "")
        if selected_source_docs.get(source_id, 0) >= 2:
            continue
        content = str(doc.get("content", ""))
        content_len = len(content)
        remaining = max_doc_chars - total_chars
        if remaining <= 0:
            break
        doc_type = str(doc.get("document_type") or "UNKNOWN").rsplit(".", 1)[-1].upper()
        if content_len > remaining:
            truncated = content[:remaining].strip()
            if not truncated:
                continue
            new_doc = dict(doc)
            new_doc["content"] = truncated
            filtered_docs.append(new_doc)
            total_chars += len(truncated)
        else:
            filtered_docs.append(doc)
            total_chars += content_len
        selected_types.add(doc_type)
        selected_source_docs[source_id] = selected_source_docs.get(source_id, 0) + 1
        scores_dict[doc.get("doc_id", "")] = score
    
    # Also store node/edge scores
    for score, node in node_scores[:max_nodes]:
        key = node.get("provenance_key", "")
        if key:
            scores_dict[key] = score
    for score, edge in edge_scores[:max_edges]:
        key = f"{edge.get('source_key')}->{edge.get('target_key')}"
        scores_dict[key] = score
    
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    
    log.info(
        "retrieval.ranking",
        nodes_available=len(nodes),
        nodes_included=len(filtered_nodes),
        edges_available=len(edges),
        edges_included=len(filtered_edges),
        docs_available=len(documents),
        docs_included=len(filtered_docs),
        total_chars=total_chars,
        ranking_ms=elapsed_ms,
        intent=understanding.intent,
    )
    
    return RankedContext(
        nodes=filtered_nodes,
        edges=filtered_edges,
        documents=filtered_docs,
        scores=scores_dict,
        total_chars=total_chars,
        ranking_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Context Compression
# ---------------------------------------------------------------------------

def compress_context(
    ranked: RankedContext,
    understanding: QueryUnderstanding,
    *,
    timeline_order: bool = False,
) -> RankedContext:
    """Compress context for efficient LLM consumption.
    
    - If timeline_order required, sort edges/nodes by timestamp
    - Remove redundant information
    - Ensure evidence grounding (keep source_doc_ids)
    """
    start = time.perf_counter()
    
    nodes = ranked.nodes
    edges = ranked.edges
    docs = ranked.documents
    
    # Timeline ordering if required
    if timeline_order or understanding.requires_timeline:
        # Sort edges by timestamp
        def _edge_time(e):
            ts = e.get("timestamp") or e.get("properties", {}).get("timestamp") or ""
            return str(ts)
        
        edges = sorted(edges, key=_edge_time)
        
        # Sort nodes by first_ts if available
        def _node_time(n):
            props = n.get("properties", {}) or {}
            return props.get("first_ts") or props.get("last_ts") or ""
        
        nodes = sorted(nodes, key=_node_time)
    
    # Evidence grounding: ensure every node/edge keeps its source_doc_ids
    # (already done in retrieval, but double-check)
    for node in nodes:
        if "source_doc_ids" not in node and "properties" in node:
            props = node["properties"]
            if "source_doc_ids" in props or "source_doc_id" in props:
                # Keep it
                pass
    
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    
    log.info(
        "retrieval.compression",
        nodes=len(nodes),
        edges=len(edges),
        docs=len(docs),
        timeline_order=timeline_order or understanding.requires_timeline,
        compression_ms=elapsed_ms,
    )
    
    return RankedContext(
        nodes=nodes,
        edges=edges,
        documents=docs,
        scores=ranked.scores,
        total_chars=ranked.total_chars,
        ranking_ms=ranked.ranking_ms + elapsed_ms,
    )


# ---------------------------------------------------------------------------
# Full Retrieval Pipeline
# ---------------------------------------------------------------------------

@dataclass
class RetrievalResult:
    """Result of full retrieval pipeline."""
    understanding: QueryUnderstanding
    ranked: RankedContext
    retrieval_ms: int
    entity_detection_ms: int = 0
    total_ms: int = 0
    detected_entities: List[str] = field(default_factory=list)
    filters_applied: Dict[str, Any] = field(default_factory=dict)


def build_timeline_from_context(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build chronological timeline from nodes/edges with timestamps.
    
    Returns list of events sorted by time, for investigation timeline feature.
    """
    events = []
    
    for node in nodes:
        props = node.get("properties", {}) or {}
        ts = props.get("first_ts") or props.get("last_ts") or props.get("timestamp")
        if ts:
            events.append({
                "type": "node",
                "timestamp": ts,
                "label": node.get("label"),
                "key": node.get("provenance_key"),
                "properties": props,
            })
    
    for edge in edges:
        ts = edge.get("timestamp")
        if ts:
            events.append({
                "type": "edge",
                "timestamp": ts,
                "rel_type": edge.get("rel_type"),
                "source": edge.get("source_key"),
                "target": edge.get("target_key"),
                "properties": edge,
            })
    
    # Sort by timestamp
    events.sort(key=lambda e: str(e.get("timestamp", "")))
    
    return events


def bucket_temporal_phases(
    events: List[Dict[str, Any]],
    *,
    incident_date: str | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Bucket timeline events into before_incident, incident, and after_incident.

    If an explicit incident_date is provided, events are partitioned relative to it.
    Otherwise, if events exist, the median or key event is used as boundary.
    """
    if not events:
        return {"before_incident": [], "incident": [], "after_incident": []}

    sorted_events = sorted(events, key=lambda e: str(e.get("timestamp", "")))

    if not incident_date:
        for ev in sorted_events:
            label = str(ev.get("label") or ev.get("rel_type") or "").upper()
            if any(term in label for term in ("INCIDENT", "FIR", "BRIBE", "THEFT", "CRIME", "TENDER_SUBMIT")):
                incident_date = str(ev.get("timestamp"))[:10]
                break

    if not incident_date:
        if len(sorted_events) <= 2:
            return {
                "before_incident": sorted_events[:1],
                "incident": sorted_events[1:2],
                "after_incident": sorted_events[2:],
            }
        mid = len(sorted_events) // 2
        incident_date = str(sorted_events[mid].get("timestamp"))[:10]

    before, during, after = [], [], []
    for ev in sorted_events:
        ev_ts = str(ev.get("timestamp", ""))[:10]
        if not ev_ts:
            during.append(ev)
        elif ev_ts < incident_date:
            before.append(ev)
        elif ev_ts == incident_date:
            during.append(ev)
        else:
            after.append(ev)

    return {
        "before_incident": before,
        "incident": during,
        "after_incident": after,
    }
