/**
 * Global Search — Professional Investigation Workflow (Sprint 2)
 *
 * Architecture: exact identifiers → metadata filtering → graph lookup → semantic (future placeholder)
 * Categories: Entities / Cases / Evidence / Documents / Patterns / Locations
 * Match context displayed
 * Actions: navigate / focus drawer / graph
 */

import { useState, useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { globalSearch, type GlobalSearchResult } from "../../api/client";

interface Props {
  onFocusEntity?: (key: string) => void;
  onOpenEvidence?: (docId: string) => void;
  onFocusPattern?: (patternId: string, caseId: string) => void;
  className?: string;
  /** Runs the first search through the same path as a typed one. */
  initialQuery?: string;
}

const CATEGORY_ORDER = ["entities", "cases", "evidence", "documents", "patterns", "locations"] as const;
// PEOPLE prioritized — relationship-first: PEOPLE → RELATIONSHIPS → EVIDENCE → EXPLANATION
const CATEGORY_LABELS: Record<string, string> = {
  entities: "People — Priority",
  cases: "Cases",
  evidence: "Evidence",
  documents: "Documents",
  patterns: "Patterns",
  locations: "Locations — Evidence",
};
const CATEGORY_ICONS: Record<string, string> = {
  entities: "👤",
  cases: "📁",
  evidence: "📄",
  documents: "📑",
  patterns: "🔍",
  locations: "📍",
};

export function GlobalSearch({ onFocusEntity, onOpenEvidence, onFocusPattern, className, initialQuery }: Props) {
  const navigate = useNavigate();
  const [query, setQuery] = useState(initialQuery ?? "");
  const [result, setResult] = useState<GlobalSearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [archStep, setArchStep] = useState(0);

  const doSearch = useCallback(async (q: string) => {
    const trimmed = q.trim();
    if (trimmed.length < 1) {
      setResult(null);
      return;
    }
    setLoading(true);
    setError(null);
    setArchStep(1);
    try {
      // Simulate architecture steps for UI feedback
      setTimeout(() => setArchStep(2), 150);
      setTimeout(() => setArchStep(3), 300);
      const res = await globalSearch(trimmed, 20);
      setResult(res);
      setArchStep(4);
      setTimeout(() => setArchStep(0), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // A deep link (?q=...) must actually search.  Fetching the result in the
  // page and never rendering it left the box empty with no error either.
  useEffect(() => {
    const trimmed = (initialQuery ?? "").trim();
    if (trimmed) void doSearch(trimmed);
    // Only on mount / when the link's query changes.
  }, [initialQuery, doSearch]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void doSearch(query);
  };

  return (
    <div className={`global-search ${className || ""}`}>
      <div className="global-search-header">
        <form onSubmit={handleSubmit} style={{ flex: 1, display: "flex", gap: "8px" }}>
          <div className="global-search-input-wrap" style={{ flex: 1 }}>
            <span className="global-search-icon">🔍</span>
            <input
              className="global-search-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search PEOPLE first, then cases, evidence, docs, patterns, locations — e.g. person name, phone, vehicle, case number"
            />
          </div>
          <button className="cl-btn cl-btn-primary" type="submit" disabled={loading || query.trim().length < 1}>
            {loading ? "Searching…" : "Search"}
          </button>
        </form>
        <div className="global-search-architecture">
          <span className={`global-search-arch-step ${archStep >= 1 ? "active" : ""}`}>Exact</span>
          <span className="global-search-arch-arrow">→</span>
          <span className={`global-search-arch-step ${archStep >= 2 ? "active" : ""}`}>Metadata</span>
          <span className="global-search-arch-arrow">→</span>
          <span className={`global-search-arch-step ${archStep >= 3 ? "active" : ""}`}>Graph</span>
          <span className="global-search-arch-arrow">→</span>
          <span className={`global-search-arch-step ${archStep >= 4 ? "active" : ""}`}>Semantic</span>
        </div>
      </div>

      {error && <div className="cl-error">{error}</div>}

      {!result && !loading && (
        <div className="cl-empty">
          <div className="cl-empty-title">Global Search</div>
          <div className="cl-empty-desc">
            Search PEOPLE first — relationship-first investigation. Exact person identifiers prioritized, then metadata, then graph traversal (PERSON→PERSON only, supporting as evidence), then semantic fallback. Every result deep-links to evidence.
          </div>
          <div style={{ marginTop: "12px", display: "flex", gap: "6px", flexWrap: "wrap", justifyContent: "center" }}>
            <span className="cl-badge cl-badge-info">Entities: name / phone / vehicle</span>
            <span className="cl-badge cl-badge-info">Cases: number / title</span>
            <span className="cl-badge cl-badge-info">Evidence: doc links</span>
            <span className="cl-badge cl-badge-info">Documents: filename</span>
            <span className="cl-badge cl-badge-info">Patterns: explanation</span>
            <span className="cl-badge cl-badge-info">Locations: address</span>
          </div>
        </div>
      )}

      {loading && (
        <div className="cl-empty">
          <div className="loading-spinner" />
          <div className="cl-empty-title">Searching…</div>
          <div className="cl-empty-desc">Running exact → metadata → graph → semantic pipeline</div>
        </div>
      )}

      {result && (
        <div className="global-search-results">
          <div style={{ display: "flex", alignItems: "center", gap: "8px", fontFamily: "var(--font-mono)", fontSize: "11px", color: "var(--text-tertiary)" }}>
            <span>Query: "{result.query}"</span>
            <span>·</span>
            <span>{result.total} total results</span>
            {Object.entries(result.counts).map(([k, v]) => v > 0 && (
              <span key={k} className="cl-badge" style={{ fontSize: "10px" }}>{k}: {v}</span>
            ))}
          </div>

          {CATEGORY_ORDER.map((cat) => {
            const items = (result.categories as any)[cat] as any[];
            if (!items || items.length === 0) return null;
            return (
              <div key={cat} className="global-search-category">
                <div className="global-search-category-header">
                  <span className="global-search-category-title">
                    <span>{CATEGORY_ICONS[cat]}</span>
                    <span>{CATEGORY_LABELS[cat]}</span>
                  </span>
                  <span className="global-search-category-count">{items.length}</span>
                </div>
                <div className="global-search-items">
                  {items.slice(0, 10).map((item: any, idx: number) => (
                    <div
                      key={`${cat}-${item.id || item.provenance_key || idx}`}
                      className="global-search-item"
                      onClick={() => {
                        if (cat === "entities" || cat === "locations") {
                          onFocusEntity?.(item.provenance_key);
                        } else if (cat === "cases") {
                          navigate(`/cases/${item.id}`);
                        } else if (cat === "documents" || cat === "evidence") {
                          onOpenEvidence?.(item.id);
                        } else if (cat === "patterns") {
                          onFocusPattern?.(item.id, item.case_id);
                        }
                      }}
                    >
                      <div className="global-search-item-main">
                        <span className="global-search-item-title">
                          {item.name || item.case_number || item.filename || item.title || item.id}
                          {item.case_number ? ` — ${item.title}` : ""}
                          {item.label ? ` (${item.label})` : ""}
                        </span>
                        <span className="global-search-item-context" title={item.match_context}>
                          {item.match_context || item.explanation || item.filename || ""}
                        </span>
                      </div>
                      <div className="global-search-item-meta">
                        <span className={`global-search-item-reason ${item.match_reason === "exact_id" ? "exact" : ""}`}>
                          {item.match_reason}
                        </span>
                        {cat === "entities" && <span className="cl-badge cl-badge-info" style={{ fontSize: "10px" }}>{item.label}</span>}
                        {cat === "cases" && <span className="cl-badge" style={{ fontSize: "10px" }}>{item.status}</span>}
                        {cat === "patterns" && <span className="cl-badge cl-badge-warning" style={{ fontSize: "10px" }}>{item.pattern_type}</span>}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}

          {result.total === 0 && (
            <div className="cl-empty">
              <div className="cl-empty-title">No results</div>
              <div className="cl-empty-desc">No matches found for "{result.query}" across entities, cases, evidence, documents, patterns, locations. Try a different term or check spelling.</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default GlobalSearch;
