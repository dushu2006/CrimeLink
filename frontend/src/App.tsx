import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import ErrorBoundary from "./components/ErrorBoundary";
import Login from "./pages/Login";
import Cases from "./pages/Cases";
import CaseDetail from "./pages/CaseDetail";
import GraphPage from "./pages/GraphPage";
import Review from "./pages/Review";
import Admin from "./pages/Admin";
import { setUnauthorizedHandler } from "./api/client";
import { useAuth } from "./store/auth";

// Registered once at module load: a 401 that cannot be refreshed drops the
// session rather than leaving a dead console on screen.
setUnauthorizedHandler(() => useAuth.getState().signOut());

export default function App() {
  const session = useAuth((state) => state.session);

  if (!session) {
    return (
      <Routes>
        <Route path="*" element={<Login />} />
      </Routes>
    );
  }

  return (
    <ErrorBoundary>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/cases" replace />} />
          <Route path="/cases" element={<Cases />} />
          <Route path="/cases/:caseId" element={<CaseDetail />} />
          <Route path="/cases/:caseId/graph" element={<GraphPage />} />
          <Route path="/cases/:caseId/review" element={<Review />} />
          <Route path="/review" element={<Review />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<Navigate to="/cases" replace />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  );
}
