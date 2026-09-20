/**
 * Global Search Page — Sprint 2
 * Exact identifiers → metadata → graph → semantic placeholder
 * Categories Entities/Cases/Evidence/Documents/Patterns/Locations
 * Actions navigate/focus
 */

import { GlobalSearch } from "../components/investigator/GlobalSearch";
import { useNavigate, useSearchParams } from "react-router-dom";

export default function GlobalSearchPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const initialQuery = searchParams.get("q") || "";

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
        initialQuery={initialQuery}
        onFocusEntity={(key) => navigate(`/investigate?focus=${encodeURIComponent(key)}`)}
        onOpenEvidence={(docId) => navigate(`/documents/${docId}`)}
        onFocusPattern={(patternId, caseId) => navigate(`/cases/${caseId}`)}
      />
    </div>
  );
}

