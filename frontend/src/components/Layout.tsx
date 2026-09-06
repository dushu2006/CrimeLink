import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import ErrorBoundary from "./ErrorBoundary";
import { useAuth } from "../store/auth";
import { currentLang, setLang, t } from "../i18n";
import { clearInflight, watchActiveDataset } from "../api/client";

export default function Layout() {
  const { session, signOut } = useAuth();
  const location = useLocation();
  const lang = currentLang();

  // Active-dataset staleness guard: when the active dataset is replaced —
  // through this tab, another tab, or Administration on another machine —
  // every page's client state (selected case, search, graph canvas) belongs
  // to a corpus that is no longer real. Bump an epoch so the current view
  // remounts and refetches, and drop in-flight dedup entries so no response
  // produced from the old dataset is replayed to the new mount. The backend
  // independently 404s stale ids; this keeps the UI honest about WHY.
  const [datasetNotice, setDatasetNotice] = useState<string | null>(null);
  const [datasetEpoch, setDatasetEpoch] = useState(0);
  const dismissRef = useRef<number | null>(null);

  useEffect(() => {
    return watchActiveDataset((id) => {
      clearInflight();
      setDatasetEpoch((epoch) => epoch + 1);
      setDatasetNotice(
        id
          ? "The active dataset was replaced. Every view has been reloaded against the new dataset — stale selections were cleared."
          : "No dataset is active any more. Every view has been reloaded.",
      );
      if (dismissRef.current !== null) window.clearTimeout(dismissRef.current);
      dismissRef.current = window.setTimeout(() => setDatasetNotice(null), 12_000);
    });
  }, []);

  const dismissNotice = useCallback(() => {
    setDatasetNotice(null);
    if (dismissRef.current !== null) window.clearTimeout(dismissRef.current);
  }, []);

  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <strong>{t("app.title", lang)}</strong>
            <span className="brand-sub">{t("app.subtitle", lang)}</span>
          </div>
        </div>
        <nav>
          <NavLink to="/cases" className={({ isActive }) => (isActive ? "nav-link on" : "nav-link")}>
            {t("nav.cases", lang)}
          </NavLink>
          <NavLink to="/documents" className={({ isActive }) => (isActive ? "nav-link on" : "nav-link")}>
            Documents
          </NavLink>
          <NavLink to="/entities" className={({ isActive }) => (isActive ? "nav-link on" : "nav-link")}>
            Entities
          </NavLink>
          <NavLink to="/relationships" className={({ isActive }) => (isActive ? "nav-link on" : "nav-link")}>
            Relationships
          </NavLink>
          <NavLink to="/sources" className={({ isActive }) => (isActive ? "nav-link on" : "nav-link")}>
            Sources
          </NavLink>
          <NavLink to="/review" className={({ isActive }) => (isActive ? "nav-link on" : "nav-link")}>
            {t("nav.review", lang)}
          </NavLink>
          {session?.role === "ADMIN" && (
            <NavLink to="/admin" className={({ isActive }) => (isActive ? "nav-link on" : "nav-link")}>
              {t("nav.admin", lang)}
            </NavLink>
          )}
        </nav>
        <div className="who">
          <div className="lang-switch">
            <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>
              EN
            </button>
            <button className={lang === "hi" ? "on" : ""} onClick={() => setLang("hi")}>
              HI
            </button>
          </div>
          {session && (
            <div className="who-id">
              <span>{session.full_name}</span>
              <span className="muted">
                {session.badge_number} · {session.role} · {session.jurisdiction_id}
              </span>
            </div>
          )}
          <button className="btn btn-small" onClick={() => void signOut()}>
            {t("nav.signout", lang)}
          </button>
        </div>
      </header>
      <div className="env-banner" role="status">
        {t("env.banner", lang)}
      </div>
      {datasetNotice && (
        <div className="alert alert-ok" role="status" style={{ margin: "8px 16px 0" }}>
          <span>{datasetNotice}</span>
          {" "}
          <button className="btn btn-small" onClick={dismissNotice} type="button">
            Dismiss
          </button>
        </div>
      )}
      <main className="content">
        {/* One screen failing must not take the console down with it. The
            dataset epoch in the key forces a full remount when the active
            dataset changes, so no view can keep rendering the replaced
            corpus from stale component state. */}
        <ErrorBoundary key={`${location.pathname}:${datasetEpoch}`}>
          <Outlet />
        </ErrorBoundary>
      </main>
      <footer className="footer">
        CrimeLink · Evidence-backed investigation · Every action is recorded in a
        hash-chained, tamper-evident audit log.
      </footer>
    </div>
  );
}
