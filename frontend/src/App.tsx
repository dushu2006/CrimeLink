import { useEffect, useState, lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import ProtectedRoute from "./components/ProtectedRoute";
import Login from "./pages/Login";
import Cases from "./pages/Cases";
import { setUnauthorizedHandler } from "./api/client";
import { useAuth } from "./store/auth";

const CaseWorkspace = lazy(() => import("./pages/CaseWorkspace"));
const PeoplePage = lazy(() => import("./pages/PeoplePage"));
const RelationshipsPage = lazy(() => import("./pages/RelationshipsPage"));
const EvidencePage = lazy(() => import("./pages/EvidencePage"));
const TimelinePage = lazy(() => import("./pages/TimelinePage"));
const InvestigatorWorkspace = lazy(() => import("./pages/InvestigatorWorkspace"));
const CaseDashboardPage = lazy(() => import("./pages/CaseDashboardPage"));
const GlobalSearchPage = lazy(() => import("./pages/GlobalSearchPage"));
const GraphPage = lazy(() => import("./pages/GraphPage"));
const Review = lazy(() => import("./pages/Review"));
const Admin = lazy(() => import("./pages/Admin"));
const Documents = lazy(() => import("./pages/Documents"));
const DocumentDetail = lazy(() => import("./pages/DocumentDetail"));
const Entities = lazy(() => import("./pages/Entities"));
const EntityDetail = lazy(() => import("./pages/EntityDetail"));
const SourceBrowser = lazy(() => import("./pages/SourceBrowser"));
const InvestigatorActivityPage = lazy(() => import("./pages/InvestigatorActivityPage"));

setUnauthorizedHandler(() => useAuth.getState().signOut());

function PageSkeleton() {
  return (
    <div className="page-skeleton">
      <div className="skeleton-hero">
        <div className="skeleton-line w-60" />
        <div className="skeleton-line w-40" />
        <div className="skeleton-line w-80" />
      </div>
      <div className="skeleton-grid">
        {[1,2,3].map((i) => (
          <div key={i} className="skeleton-card">
            <div className="skeleton-line w-80" />
            <div className="skeleton-line w-60" />
          </div>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const session = useAuth((state) => state.session);
  const [datasetVersion, setDatasetVersion] = useState(0);
  useEffect(() => {
    function onDatasetChanged() {
      setDatasetVersion((v) => v + 1);
    }
    window.addEventListener("crimelink:dataset-changed", onDatasetChanged);
    return () => window.removeEventListener("crimelink:dataset-changed", onDatasetChanged);
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
          <Route path="/cases/dashboard" element={<Suspense fallback={<PageSkeleton />}><CaseDashboardPage /></Suspense>} />
          <Route path="/cases/:caseId" element={<Suspense fallback={<PageSkeleton />}><CaseWorkspace /></Suspense>} />
          <Route path="/cases/:caseId/graph" element={<Suspense fallback={<PageSkeleton />}><GraphPage /></Suspense>} />

          {/* VIEW — both INVESTIGATOR and VIEWER */}
          <Route path="/people" element={<Suspense fallback={<PageSkeleton />}><PeoplePage /></Suspense>} />
          <Route path="/relationships" element={<Suspense fallback={<PageSkeleton />}><RelationshipsPage /></Suspense>} />
          <Route path="/evidence" element={<Suspense fallback={<PageSkeleton />}><EvidencePage /></Suspense>} />
          <Route path="/timeline" element={<Suspense fallback={<PageSkeleton />}><TimelinePage /></Suspense>} />
          {/* Investigator Activity — both roles, Viewer read-only */}
          <Route path="/activity" element={<Suspense fallback={<PageSkeleton />}><InvestigatorActivityPage /></Suspense>} />

          {/* SEARCH — Investigator only after audit */}
          <Route path="/search" element={<ProtectedRoute requiredRoles={["INVESTIGATOR", "ADMIN", "SUPERVISOR", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"]}><Suspense fallback={<PageSkeleton />}><GlobalSearchPage /></Suspense></ProtectedRoute>} />

          {/* INVESTIGATE — INVESTIGATOR only */}
          <Route path="/investigate" element={<ProtectedRoute requiredRoles={["INVESTIGATOR", "ADMIN", "SUPERVISOR", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"]}><Suspense fallback={<PageSkeleton />}><InvestigatorWorkspace /></Suspense></ProtectedRoute>} />

          {/* Legacy compatibility — same as VIEW */}
          <Route path="/entities" element={<Suspense fallback={<PageSkeleton />}><Entities /></Suspense>} />
          <Route path="/entities/:entityKey" element={<Suspense fallback={<PageSkeleton />}><EntityDetail /></Suspense>} />
          <Route path="/documents" element={<Suspense fallback={<PageSkeleton />}><Documents /></Suspense>} />
          <Route path="/documents/:docId" element={<Suspense fallback={<PageSkeleton />}><DocumentDetail /></Suspense>} />
          <Route path="/sources" element={<Suspense fallback={<PageSkeleton />}><SourceBrowser /></Suspense>} />

          {/* INTELLIGENCE — INVESTIGATOR only */}
          <Route path="/patterns" element={<ProtectedRoute requiredRoles={["INVESTIGATOR", "ADMIN", "SUPERVISOR", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"]}><Suspense fallback={<PageSkeleton />}><CaseDashboardPage /></Suspense></ProtectedRoute>} />
          <Route path="/attention" element={<ProtectedRoute requiredRoles={["INVESTIGATOR", "ADMIN", "SUPERVISOR", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"]}><Suspense fallback={<PageSkeleton />}><Review /></Suspense></ProtectedRoute>} />
          <Route path="/review" element={<ProtectedRoute requiredRoles={["INVESTIGATOR", "ADMIN", "SUPERVISOR", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"]}><Suspense fallback={<PageSkeleton />}><Review /></Suspense></ProtectedRoute>} />

          {/* SYSTEM */}
          <Route path="/audit" element={<ProtectedRoute requiredRoles={["INVESTIGATOR", "ADMIN", "SUPERVISOR", "AUDITOR", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"]}><Suspense fallback={<PageSkeleton />}><Review /></Suspense></ProtectedRoute>} />
          <Route path="/admin" element={<ProtectedRoute requiredRoles={["ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"]}><Suspense fallback={<PageSkeleton />}><Admin /></Suspense></ProtectedRoute>} />
          <Route path="/admin/:section" element={<ProtectedRoute requiredRoles={["ADMIN", "STATION_ADMIN", "DISTRICT_ADMIN", "SUPER_ADMIN"]}><Suspense fallback={<PageSkeleton />}><Admin /></Suspense></ProtectedRoute>} />

          <Route path="*" element={<Navigate to="/cases" replace />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  );
}
