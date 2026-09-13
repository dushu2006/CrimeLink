import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import ErrorBoundary from "./ErrorBoundary";
import { useAuth } from "../store/auth";
import { currentLang, setLang, t } from "../i18n";
import { clearInflight, watchActiveDataset } from "../api/client";
import CrimeLinkLogo from "./CrimeLinkLogo";

export default function Layout() {
  const { session, signOut } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const lang = currentLang();

  // Active dataset notification and epoch
  const [datasetNotice, setDatasetNotice] = useState<string | null>(null);
  const [datasetEpoch, setDatasetEpoch] = useState(0);
  const dismissRef = useRef<number | null>(null);

  // Quick search input state
  const [searchQuery, setSearchQuery] = useState("");

  // Derive context breadcrumb title — no sessionStorage fallback: Investigation Analysis is dataset-wide.
  const contextInfo = useMemo(() => {
    const path = location.pathname;
    if (path.startsWith("/cases")) {
      if (path.includes("/investigate")) {
        return { section: "Investigation Analysis", detail: "Master Network — Active Dataset", badge: "Master Scope" };
      }
      if (path.includes("/review")) {
        return { section: "HITL Review", detail: "Human-in-the-Loop", badge: "Action Req." };
      }
      if (path.match(/^\/cases\/[^/]+$/)) {
        return { section: "Case Dossier", detail: "Case Detail", badge: "Active Case" };
      }
      return { section: "Cases Registry", detail: "Active Criminal Files", badge: "Cases" };
    }
    if (path.startsWith("/investigate")) {
      return { section: "Investigation Analysis", detail: "Master Network — Active Dataset", badge: "Master Scope" };
    }
    if (path.startsWith("/entities")) {
      return { section: "Entity Directory", detail: "Persons, Accounts & Vehicles", badge: "Biometrics & PII" };
    }
    if (path.startsWith("/relationships")) {
      return { section: "Relationships", detail: "Financial & Call Matrix", badge: "Cross-Entity" };
    }
    if (path.startsWith("/documents")) {
      return { section: "Evidence Vault", detail: "FIRs, CDRs & Statements", badge: "Tamper-Evident" };
    }
    if (path.startsWith("/sources")) {
      return { section: "Raw Sources", detail: "Operational Evidence Browser", badge: "Hex & Provenance" };
    }
    if (path.startsWith("/review")) {
      return { section: "Review Queue", detail: "Human Validation System", badge: "HITL Gate" };
    }
    if (path.startsWith("/admin")) {
      return { section: "Administration", detail: "System Configuration & Datasets", badge: "Admin Control" };
    }
    return { section: "CrimeLink Console", detail: "Law Enforcement Intelligence", badge: "Operational" };
  }, [location.pathname]);

  useEffect(() => {
    return watchActiveDataset((id) => {
      clearInflight();
      setDatasetEpoch((epoch) => epoch + 1);
      setDatasetNotice(
        id
          ? "The active dataset was replaced. Every view has been reloaded against the new dataset."
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

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      navigate(`/entities?q=${encodeURIComponent(searchQuery.trim())}`);
    }
  };

  // User initials for avatar
  const userInitials = useMemo(() => {
    if (!session?.full_name) return "OF";
    const parts = session.full_name.trim().split(/\\s+/);
    if (parts.length >= 2) return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
    return parts[0].slice(0, 2).toUpperCase();
  }, [session]);

  // Active route determination — no Investigation Graph or AI Investigation duplicates
  const currentPath = location.pathname;
  const isAnalysisActive = currentPath.includes("/investigate");
  const isReviewActive = currentPath.startsWith("/review") || currentPath.includes("/review");
  const isCasesActive =
    (currentPath === "/cases" || currentPath.startsWith("/cases/")) &&
    !isAnalysisActive &&
    !isReviewActive;
  const isEntitiesActive = currentPath.startsWith("/entities");
  const isRelationshipsActive = currentPath.startsWith("/relationships");
  const isSourcesActive = currentPath.startsWith("/sources");
  const isDocumentsActive = currentPath.startsWith("/documents");
  const isAdminActive = currentPath.startsWith("/admin");

  return (
    <div className="stitch-shell">
      <aside className="stitch-sidebar">
        <div className="sidebar-brand">
          <CrimeLinkLogo className="sidebar-logo-svg" showSubtitle={true} />
        </div>

        <div className="sidebar-nav-scroll">
          <div className="nav-section">
            <div className="nav-section-title">{t("nav.sectionOperations", lang)}</div>
            <nav className="nav-links-col">
              <Link
                to="/cases"
                className={`sidebar-link ${isCasesActive ? "active" : ""}`}
                aria-current={isCasesActive ? "page" : undefined}
              >
                <span className="material-symbols-outlined nav-icon">folder_open</span>
                <span className="nav-label">{t("nav.cases", lang)}</span>
                <span className="nav-pill">{t("cases.title", lang)}</span>
              </Link>

              <Link
                to="/entities"
                className={`sidebar-link ${isEntitiesActive ? "active" : ""}`}
                aria-current={isEntitiesActive ? "page" : undefined}
              >
                <span className="material-symbols-outlined nav-icon">person_search</span>
                <span className="nav-label">{t("nav.entities", lang)}</span>
              </Link>

              <Link
                to="/relationships"
                className={`sidebar-link ${isRelationshipsActive ? "active" : ""}`}
                aria-current={isRelationshipsActive ? "page" : undefined}
              >
                <span className="material-symbols-outlined nav-icon">polyline</span>
                <span className="nav-label">{t("nav.relationships", lang)}</span>
              </Link>

              <Link
                to="/sources"
                className={`sidebar-link ${isSourcesActive ? "active" : ""}`}
                aria-current={isSourcesActive ? "page" : undefined}
              >
                <span className="material-symbols-outlined nav-icon">dataset</span>
                <span className="nav-label">{t("nav.sources", lang)}</span>
              </Link>
            </nav>
          </div>

          <div className="nav-section">
            <div className="nav-section-title">{t("nav.sectionIntelligence", lang)}</div>
            <nav className="nav-links-col">
              {/* Single entry for Investigation Analysis — global master workspace for active dataset */}
              <Link
                to="/investigate"
                className={`sidebar-link ${isAnalysisActive ? "active" : ""}`}
                aria-current={isAnalysisActive ? "page" : undefined}
              >
                <span className="material-symbols-outlined nav-icon">fact_check</span>
                <span className="nav-label">{t("nav.reasoning", lang)}</span>
              </Link>

              <Link
                to="/review"
                className={`sidebar-link ${isReviewActive ? "active" : ""}`}
                aria-current={isReviewActive ? "page" : undefined}
              >
                <span className="material-symbols-outlined nav-icon">compare_arrows</span>
                <span className="nav-label">{t("nav.review", lang)}</span>
                <span className="nav-pill nav-pill-amber">HITL</span>
              </Link>

              <Link
                to="/documents"
                className={`sidebar-link ${isDocumentsActive ? "active" : ""}`}
                aria-current={isDocumentsActive ? "page" : undefined}
              >
                <span className="material-symbols-outlined nav-icon">shield</span>
                <span className="nav-label">{t("nav.vault", lang)}</span>
              </Link>
            </nav>
          </div>

          {session?.role === "ADMIN" && (
            <div className="nav-section">
              <div className="nav-section-title">{t("nav.sectionGovernance", lang)}</div>
              <nav className="nav-links-col">
                <Link
                  to="/admin"
                  className={`sidebar-link ${isAdminActive ? "active" : ""}`}
                  aria-current={isAdminActive ? "page" : undefined}
                >
                  <span className="material-symbols-outlined nav-icon">settings</span>
                  <span className="nav-label">{t("nav.admin", lang)}</span>
                </Link>
              </nav>
            </div>
          )}
        </div>

        <div className="sidebar-security-footer">
          <div className="session-status-row">
            <span className="status-live-dot" />
            <span className="session-title">{t("footer.sessionVerified", lang)}</span>
            <span className="session-tls-badge">TLS 1.3</span>
          </div>
          <p className="session-jurisdiction-label">
            {session?.jurisdiction_id || "AP & Telangana Cyber Directorate"}
          </p>
        </div>
      </aside>

      <div className="stitch-main-wrap">
        <header className="stitch-executive-header">
          <div className="header-context-crumbs">
            <div className="crumb-section">
              <span className="material-symbols-outlined crumb-folder-icon">folder_open</span>
              <span className="crumb-section-text">{contextInfo.section}</span>
              <span className="material-symbols-outlined crumb-chevron">chevron_right</span>
            </div>
            <span className="crumb-detail-text">{contextInfo.detail}</span>
            <span className="crumb-badge">
              <span className="crumb-badge-dot" />
              {contextInfo.badge}
            </span>
          </div>

          <div className="header-actions-row">
            <form onSubmit={handleSearchSubmit} className="header-search-form">
              <span className="material-symbols-outlined header-search-icon">search</span>
              <input
                type="text"
                className="header-search-input"
                placeholder={t("search.placeholder", lang)}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
              <kbd className="header-search-kbd">⌘K</kbd>
            </form>

            <div className="header-divider-v" />

            <div className="header-lang-switch">
              {(["en", "hi", "te", "ta"] as const).map((code) => (
                <button
                  key={code}
                  type="button"
                  className={`lang-tab-btn ${lang === code ? "active" : ""}`}
                  onClick={() => setLang(code)}
                >
                  {code.toUpperCase()}
                </button>
              ))}
            </div>

            {session && (
              <div className="officer-profile-card">
                <div className="officer-avatar-circle">{userInitials}</div>
                <div className="officer-info-col">
                  <span className="officer-name-text">{session.full_name}</span>
                  <span className="officer-role-text">
                    {session.badge_number} · {session.role}
                  </span>
                </div>
              </div>
            )}

            <button
              type="button"
              className="header-signout-btn"
              onClick={() => void signOut()}
              title={t("nav.signout", lang)}
            >
              <span className="material-symbols-outlined signout-icon">logout</span>
            </button>
          </div>
        </header>

        <div className="stitch-env-banner" role="status">
          <span className="material-symbols-outlined banner-info-icon">info</span>
          <span>{t("env.banner", lang)}</span>
        </div>

        {datasetNotice && (
          <div className="stitch-dataset-alert" role="status">
            <span>{datasetNotice}</span>
            <button className="btn-dismiss-alert" onClick={dismissNotice} type="button">
              Dismiss
            </button>
          </div>
        )}

        <main className="stitch-content-container">
          <ErrorBoundary key={`${location.pathname}:${datasetEpoch}`}>
            <Outlet />
          </ErrorBoundary>
        </main>

        <footer className="stitch-console-footer">
          <div className="footer-inner">
            <span>
              {t("footer.platformTitle", lang)} · {t("footer.evidenceBacked", lang)}
            </span>
            <span className="footer-dot">•</span>
            <span>{t("footer.auditLog", lang)}</span>
            <span className="footer-dot">•</span>
            <span>{t("footer.authorizedOnly", lang)}</span>
          </div>
        </footer>
      </div>
    </div>
  );
}
