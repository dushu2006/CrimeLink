import { NavLink, Outlet, useLocation } from "react-router-dom";
import ErrorBoundary from "./ErrorBoundary";
import { useAuth } from "../store/auth";
import { currentLang, setLang, t } from "../i18n";

export default function Layout() {
  const { session, signOut } = useAuth();
  const location = useLocation();
  const lang = currentLang();

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
      <main className="content">
        {/* One screen failing must not take the console down with it. */}
        <ErrorBoundary key={location.pathname}>
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
