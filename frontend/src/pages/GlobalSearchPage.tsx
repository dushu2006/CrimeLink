/**
 * Global Search Page — Sprint 2
 * Exact identifiers → metadata → graph → semantic placeholder
 * Categories Entities/Cases/Evidence/Documents/Patterns/Locations
 * Actions navigate/focus
 */

import { useEffect, useState } from "react";
import { GlobalSearch } from "../components/investigator/GlobalSearch";
import { AttentionCenterPanel } from "../components/investigator/AttentionCenter";
import { useNavigate, useSearchParams } from "react-router-dom";
import { globalSearch, type GlobalSearchResult } from "../api/client";

export default function GlobalSearchPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const initialQuery = searchParams.get("q") || "";
  const [autoResult, setAutoResult] = useState<GlobalSearchResult | null>(null);

  useEffect(() => {
    if (initialQuery) {
      globalSearch(initialQuery, 20)
        .then(setAutoResult)
        .catch(() => {});
    }
  }, [initialQuery]);

  return (
    <div className="global-search-page">
      <div className="page-head">
        <h1>Global Search — Professional Investigation</h1>
        <div style={{ display: "flex", gap: "8px" }}>
          <button className="cl-btn" onClick={() => navigate("/investigate")}>
            Investigator Workspace
          </button>
          <button className="cl-btn cl-btn-primary" onClick={() => navigate("/cases")}>
            Cases Registry
          </button>
        </div>
      </div>

      <GlobalSearch
        onFocusEntity={(key) => navigate(`/investigate?focus=${encodeURIComponent(key)}`)}
        onOpenEvidence={(docId) => navigate(`/documents/${docId}`)}
        onFocusPattern={(patternId, caseId) => navigate(`/cases/${caseId}`)}
      />

      <div style={{ marginTop: "24px" }}>
        <AttentionCenterPanel
          onFocusEntity={(key) => navigate(`/investigate?focus=${encodeURIComponent(key)}`)}
          onOpenEvidence={(docId) => navigate(`/documents/${docId}`)}
        />
      </div>
    </div>
  );
}

