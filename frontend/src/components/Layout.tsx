import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import ErrorBoundary from "./ErrorBoundary";
import { useAuth } from "../store/auth";
import { currentLang, setLang, t } from "../i18n";
import { clearInflight, watchActiveDataset } from "../api/client";
import CrimeLinkLogo from "./CrimeLinkLogo";
import { CommandPalette } from "./investigator/CommandPalette";
import { getPermissions, isInvestigator, isViewer, getRoleBadge } from "../lib/rbac";

export default function Layout() {
  const { session, signOut } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const lang = currentLang();

  const [datasetNotice, setDatasetNotice] = useState<string | null>(null);
  const [datasetEpoch, setDatasetEpoch] = useState(0);
  const dismissRef = useRef<number | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [showCommandPalette, setShowCommandPalette] = useState(false);

  const role = session?.role;
  const permissions = getPermissions(role as any);
  const investigator = isInvestigator(role as any);
  const viewer = isViewer(role as any);
  const roleBadge = getRoleBadge(role as any);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setShowCommandPalette((v) => !v);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const contextInfo = useMemo(() => {
    const path = location.pathname;
    if (path.startsWith("/cases")) {
      if (path.includes("/dashboard")) return { section: "Case Workspace", detail: "Investigation Launchpad", badge: "Evidence-First" };
      if (path.match(/^\/cases\/[^/]+$/)) return { section: "Case Workspace", detail: "Case Dossier", badge: "Active Case" };
      return { section: "Cases", detail: "Investigation Registry", badge: "Cases" };
    }
    if (path.startsWith("/investigate")) return { section: "Investigate", detail: "People → Relationships → Evidence → Explanation", badge: "Person-Centric" };
    if (path.startsWith("/search")) return { section: "Search", detail: "Cases, People, Evidence", badge: "Evidence Found" };
    if (path.startsWith("/people")) return { section: "People", detail: "Person-Centric Investigation", badge: "Priority" };
    if (path.startsWith("/relationships")) return { section: "Relationships", detail: "Person → Person Only", badge: "Evidence-Grounded" };
    if (path.startsWith("/evidence")) return { section: "Evidence", detail: "Source Records", badge: "Traceable" };
    if (path.startsWith("/timeline")) return { section: "Timeline", detail: "Evidence-Oriented", badge: "Timestamp Verified" };
    if (path.startsWith("/activity")) return { section: "Investigator Activity", detail: "Completed Findings — Read-Only", badge: "Trust" };
    if (path.startsWith("/patterns")) return { section: "Patterns", detail: "Intelligence", badge: "Secondary" };
    if (path.startsWith("/audit")) return { section: "Audit", detail: "Activity & Provenance", badge: "Trust" };
    if (path.startsWith("/admin")) return { section: "System", detail: "Configuration", badge: "Admin" };
    if (path.startsWith("/entities")) return { section: "People", detail: "Directory", badge: "Person-Centric" };
    if (path.startsWith("/documents")) return { section: "Evidence", detail: "Vault", badge: "Source" };
    return { section: "CrimeLink", detail: "Investigation Workstation", badge: "Professional" };
  }, [location.pathname]);

  useEffect(() => {
    return watchActiveDataset((id) => {
      clearInflight();
      setDatasetEpoch((epoch) => epoch + 1);
      setDatasetNotice(id ? "Active dataset replaced. Views reloaded." : "No dataset active. Views reloaded.");
      if (dismissRef.current !== null) window.clearTimeout(dismissRef.current);
      dismissRef.current = window.setTimeout(() => setDatasetNotice(null), 8000);
    });
  }, []);

  const dismissNotice = useCallback(() => {
    setDatasetNotice(null);
    if (dismissRef.current !== null) window.clearTimeout(dismissRef.current);
  }, []);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) navigate(`/search?q=${encodeURIComponent(searchQuery.trim())}`);
  };

  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = localStorage.getItem("crimelink_sidebar_width");
    return saved ? Math.max(200, Math.min(500, parseInt(saved, 10))) : 270;
  });
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    return localStorage.getItem("crimelink_sidebar_collapsed") === "true";
  });

  const isResizingRef = useRef(false);

  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    isResizingRef.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMouseMove = (moveEvent: MouseEvent) => {
      if (!isResizingRef.current) return;
      const newWidth = Math.max(200, Math.min(500, moveEvent.clientX));
      setSidebarWidth(newWidth);
      localStorage.setItem("crimelink_sidebar_width", String(newWidth));
    };

    const onMouseUp = () => {
      isResizingRef.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  };

  const toggleSidebar = () => {
    const next = !sidebarCollapsed;
    setSidebarCollapsed(next);
    localStorage.setItem("crimelink_sidebar_collapsed", String(next));
  };

  const userInitials = useMemo(() => {
    if (!session?.full_name) return "OF";
    const parts = session.full_name.trim().split(/\/\s+/);
    if (parts.length >= 2) return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
    const nameParts = session.full_name.trim().split(/\s+/);
    if (nameParts.length >= 2) return `${nameParts[0][0]}${nameParts[nameParts.length - 1][0]}`.toUpperCase();
    return nameParts[0].slice(0, 2).toUpperCase();
  }, [session]);

  const path = location.pathname;
  const isActive = (prefix: string) => path.startsWith(prefix);

  return (
    <div
      className="stitch-shell"
      style={{ "--cl-sidebar-w": `${sidebarCollapsed ? 68 : sidebarWidth}px` } as React.CSSProperties}
    >
      <aside className={`stitch-sidebar ${sidebarCollapsed ? "collapsed" : ""}`}>
        <div className="sidebar-brand">
          <div className="sidebar-brand-inner">
            <CrimeLinkLogo className="sidebar-logo-svg" showSubtitle={!sidebarCollapsed} variant="light" />
          </div>
          <button
            type="button"
            className="sidebar-collapse-btn"
            onClick={toggleSidebar}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <span className="material-symbols-outlined">
              {sidebarCollapsed ? "chevron_right" : "menu_open"}
            </span>
          </button>
        </div>

        <div className="sidebar-nav-scroll">
          {/* CASE — both roles */}
          <div className="nav-section">
            <div className="nav-section-title">{t("nav.sectionCase", lang)}</div>
            <nav className="nav-links-col">
              <Link to="/cases" className={`sidebar-link ${path === "/cases" ? "active" : ""}`} title={t("nav.cases", lang)}>
                <span className="material-symbols-outlined nav-icon">folder_open</span>
                <span className="nav-label">{t("nav.cases", lang)}</span>
              </Link>
              <Link to="/cases/dashboard" className={`sidebar-link ${isActive("/cases/dashboard") ? "active" : ""}`} title={t("nav.overview", lang)}>
                <span className="material-symbols-outlined nav-icon">dashboard</span>
                <span className="nav-label">{t("nav.overview", lang)}</span>
              </Link>
            </nav>
          </div>


          {/* INVESTIGATE — Investigator only */}
          {investigator && (
            <div className="nav-section nav-section-primary">
              <div className="nav-section-title">{t("nav.sectionInvestigate", lang)}</div>
              <nav className="nav-links-col">
                <Link to="/search" className={`sidebar-link ${isActive("/search") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">search</span>
                  <span className="nav-label">{t("nav.search", lang)}</span>
                  <span className="nav-pill nav-pill-primary">⌘K</span>
                </Link>
                <Link to="/people" className={`sidebar-link ${isActive("/people") || isActive("/entities") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">person_search</span>
                  <span className="nav-label">{t("nav.people", lang)}</span>
                  <span className="nav-pill">Priority</span>
                </Link>
                <Link to="/relationships" className={`sidebar-link ${isActive("/relationships") ? "active" : ""}`} title="Relationships — Person → Person">
                  <span className="material-symbols-outlined nav-icon">polyline</span>
                  <span className="nav-label">{t("nav.relationships", lang)}</span>
                  <span className="nav-pill">Person → Person</span>
                </Link>
                <Link to="/evidence" className={`sidebar-link ${isActive("/evidence") || isActive("/documents") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">description</span>
                  <span className="nav-label">{t("nav.evidence", lang)}</span>
                </Link>
                <Link to="/timeline" className={`sidebar-link ${isActive("/timeline") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">timeline</span>
                  <span className="nav-label">{t("nav.timeline", lang)}</span>
                </Link>
                <Link to="/investigate" className={`sidebar-link ${isActive("/investigate") ? "active" : ""} sidebar-link-accent`} title="Investigate Relationship">
                  <span className="material-symbols-outlined nav-icon">fact_check</span>
                  <span className="nav-label">{t("nav.investigateRelationship", lang)}</span>
                </Link>
                <Link to="/activity" className={`sidebar-link ${isActive("/activity") ? "active" : ""}`} title="Investigator Activity (INV-0042)">
                  <span className="material-symbols-outlined nav-icon">assignment</span>
                  <span className="nav-label">{t("nav.investigatorActivity", lang)}</span>
                  <span className="nav-pill">INV-0042</span>
                </Link>
                {permissions.viewPatterns && (
                  <Link to="/patterns" className={`sidebar-link ${isActive("/patterns") ? "active" : ""}`}>
                    <span className="material-symbols-outlined nav-icon">pattern</span>
                    <span className="nav-label">{t("nav.patterns", lang)}</span>
                    <span className="nav-pill">Secondary</span>
                  </Link>
                )}
              </nav>
            </div>
          )}

          {/* VIEW — Viewer only, intentionally minimal + Investigator Activity read-only */}
          {viewer && (
            <div className="nav-section nav-section-primary">
              <div className="nav-section-title">VIEW — Read-Only</div>
              <nav className="nav-links-col">
                <Link to="/people" className={`sidebar-link ${isActive("/people") || isActive("/entities") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">person_search</span>
                  <span className="nav-label">{t("nav.people", lang)}</span>
                </Link>
                <Link to="/relationships" className={`sidebar-link ${isActive("/relationships") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">polyline</span>
                  <span className="nav-label">{t("nav.relationships", lang)}</span>
                </Link>
                <Link to="/evidence" className={`sidebar-link ${isActive("/evidence") || isActive("/documents") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">description</span>
                  <span className="nav-label">{t("nav.evidence", lang)}</span>
                </Link>
                <Link to="/timeline" className={`sidebar-link ${isActive("/timeline") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">timeline</span>
                  <span className="nav-label">{t("nav.timeline", lang)}</span>
                </Link>
                <Link to="/activity" className={`sidebar-link ${isActive("/activity") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">assignment</span>
                  <span className="nav-label">{t("nav.investigatorActivity", lang)}</span>
                  <span className="nav-pill nav-pill-info">INV-0042</span>
                </Link>
              </nav>
            </div>
          )}

          {/* SYSTEM — role aware */}
          <div className="nav-section">
            <div className="nav-section-title">{t("nav.sectionSystem", lang)}</div>
            <nav className="nav-links-col">
              {investigator && permissions.viewAudit && (
                <Link to="/audit" className={`sidebar-link ${isActive("/audit") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">receipt_long</span>
                  <span className="nav-label">{t("nav.audit", lang)}</span>
                </Link>
              )}
              {permissions.adminAccess && (
                <Link to="/admin" className={`sidebar-link ${isActive("/admin") ? "active" : ""}`}>
                  <span className="material-symbols-outlined nav-icon">settings</span>
                  <span className="nav-label">Settings</span>
                </Link>
              )}
            </nav>
          </div>
        </div>

        <div className="sidebar-security-footer">
          <div className="session-status-row">
            <span className="status-live-dot" />
            <span className="session-title">{viewer ? "Read-Only · Evidence Viewer" : "Evidence-First · Person-Centric"}</span>
            <span className="session-tls-badge">TLS 1.3</span>
          </div>
          <p className="session-jurisdiction-label">{session?.jurisdiction_id || "Investigation Workstation"} · {roleBadge.label}</p>
        </div>
        <div
          className="sidebar-resizer"
          onMouseDown={handleResizeStart}
          onDoubleClick={() => {
            setSidebarWidth(270);
            localStorage.setItem("crimelink_sidebar_width", "270");
          }}
          title="Drag to resize sidebar (Double-click to reset)"
        />
      </aside>

      <div className="stitch-main-wrap">
        <header className="stitch-executive-header">
          <div className="header-context-crumbs">
            <button
              type="button"
              className="header-sidebar-toggle-btn"
              onClick={toggleSidebar}
              title={sidebarCollapsed ? "Open sidebar" : "Collapse sidebar"}
              aria-label="Toggle sidebar"
            >
              <span className="material-symbols-outlined">
                {sidebarCollapsed ? "menu" : "menu_open"}
              </span>
            </button>
            <span className="crumb-section-text">{contextInfo.section}</span>
            <span className="material-symbols-outlined crumb-chevron">chevron_right</span>
            <span className="crumb-detail-text">{contextInfo.detail}</span>
            <span className="crumb-badge"><span className="crumb-badge-dot" />{contextInfo.badge}</span>
            {viewer && <span className="crumb-badge" style={{ background: "#eff6ff", color: "#1d4ed8", borderColor: "#dbeafe" }}>Read-Only</span>}
          </div>

          <div className="header-actions-row">
            <form onSubmit={handleSearchSubmit} className="header-search-form">
              <span className="material-symbols-outlined header-search-icon">search</span>
              <input type="text" className="header-search-input" placeholder={viewer ? "Search cases, people, evidence..." : "Search cases, people, evidence..."} value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} />
              {investigator && <kbd className="header-search-kbd">⌘K</kbd>}
            </form>

            <div className="header-divider-v" />

            <div className="header-lang-switch">
              {(["en", "hi", "te", "ta"] as const).map((code) => (
                <button key={code} type="button" className={`lang-tab-btn ${lang === code ? "active" : ""}`} onClick={() => setLang(code)}>
                  {code.toUpperCase()}
                </button>
              ))}
            </div>

            {session && (
              <div className="officer-profile-card">
                <div className="officer-avatar-circle">{userInitials}</div>
                <div className="officer-info-col">
                  <span className="officer-name-text">{session.full_name}</span>
                  <span className="officer-role-text">{session.badge_number} · {session.role}</span>
                </div>
              </div>
            )}

            <button type="button" className="header-signout-btn" onClick={() => void signOut()} title={t("nav.signout", lang)}>
              <span className="material-symbols-outlined signout-icon">logout</span>
            </button>
          </div>
        </header>

        {datasetNotice && (
          <div className="stitch-dataset-alert" role="status">
            <span>{datasetNotice}</span>
            <button className="btn-dismiss-alert" onClick={dismissNotice} type="button">Dismiss</button>
          </div>
        )}

        <main className="stitch-content-container">
          <ErrorBoundary key={`${location.pathname}:${datasetEpoch}`}>
            <Outlet />
          </ErrorBoundary>
        </main>

        <footer className="stitch-console-footer">
          <div className="footer-inner">
            <span>CrimeLink · Investigation Workstation · Evidence-First · Person-Centric</span>
            <span className="footer-dot">•</span>
            <span>People → Relationships → Evidence → Explanation → Action</span>
            <span className="footer-dot">•</span>
            <span>{viewer ? "Read-Only Viewer" : "Claim → Evidence → Original Record"}</span>
          </div>
        </footer>
      </div>

      {investigator && <CommandPalette open={showCommandPalette} onClose={() => setShowCommandPalette(false)} />}
    </div>
  );
}
