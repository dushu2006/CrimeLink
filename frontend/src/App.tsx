import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Login from "./pages/Login";
import Cases from "./pages/Cases";
import CaseDetail from "./pages/CaseDetail";
import InvestigatorWorkspace from "./pages/InvestigatorWorkspace";
import Review from "./pages/Review";
import Admin from "./pages/Admin";
import Documents from "./pages/Documents";
import DocumentDetail from "./pages/DocumentDetail";
import Entities from "./pages/Entities";
import EntityDetail from "./pages/EntityDetail";
import Relationships from "./pages/Relationships";
import SourceBrowser from "./pages/SourceBrowser";
import { setUnauthorizedHandler } from "./api/client";
import { useAuth } from "./store/auth";

// Registered once at module load: a 401 that cannot be refreshed drops the
// session rather than leaving a dead console on screen.
setUnauthorizedHandler(() => useAuth.getState().signOut());

export default function App() {
  const session = useAuth((state) => state.session);

  /**
   * Each time the active dataset changes the version counter increments and
   * the entire Routes tree remounts.  This is the cheapest way to guarantee
   * that every page's in-memory state is reset and all data is re-fetched
   * from the new dataset — no individual page needs to know that a swap
   * happened.
   */
  const [datasetVersion, setDatasetVersion] = useState(0);
  useEffect(() => {
    function onDatasetChanged() {
      setDatasetVersion((v) => v + 1);
    }
    window.addEventListener("crimelink:dataset-changed", onDatasetChanged);
    return () => {
      window.removeEventListener("crimelink:dataset-changed", onDatasetChanged);
    };
  }, []);

  if (!session) {
    return (
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }

  return (
    <ErrorBoundary>
      <Routes key={datasetVersion}>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/cases" replace />} />
          <Route path="/cases" element={<Cases />} />
          <Route path="/cases/:caseId" element={<CaseDetail />} />
          {/* Investigation Analysis is now global master workspace for the active dataset,
              not per-case. Deprecated case-scoped graph and AI investigation routes removed. */}
          <Route path="/investigate" element={<InvestigatorWorkspace />} />
          <Route path="/cases/:caseId/review" element={<Review />} />
          <Route path="/review" element={<Review />} />

          {/* Every resource has its own address, so findings can be linked,
              bookmarked and reached with browser back/forward. */}
          <Route path="/documents" element={<Documents />} />
          <Route path="/documents/:docId" element={<DocumentDetail />} />
          <Route path="/entities" element={<Entities />} />
          <Route path="/entities/:entityKey" element={<EntityDetail />} />
          <Route path="/relationships" element={<Relationships />} />
          <Route path="/sources" element={<SourceBrowser />} />

          <Route path="/admin" element={<Admin />} />
          <Route path="/admin/:section" element={<Admin />} />

          <Route path="*" element={<Navigate to="/cases" replace />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  );
}
