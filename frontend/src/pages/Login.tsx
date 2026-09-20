import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { setupStatus } from "../api/client";
import { useAuth } from "../store/auth";
import { currentLang, setLang, t } from "../i18n";

import CrimeLinkLogo from "../components/CrimeLinkLogo";

const DEMO_ACCOUNTS = [
  {
    id: "DEMO-ADMIN",
    label: "Admin",
    role: "ADMIN",
    badge: "DEMO-ADMIN",
    password: "DemoAdmin@2026",
    description: "Complete operational view + administration",
  },
  {
    id: "DEMO-INVESTIGATOR",
    label: "Investigator",
    role: "INVESTIGATOR",
    badge: "DEMO-INVESTIGATOR",
    password: "DemoInvestigator@2026",
    description: "Investigate and review — CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → ACTION",
  },
  {
    id: "DEMO-VIEWER",
    label: "Viewer",
    role: "VIEWER",
    badge: "DEMO-VIEWER",
    password: "DemoViewer@2026",
    description: "Read-only review + Investigator Activity",
  },
];

export default function Login() {
  const [mode, setMode] = useState<"loading" | "login" | "setup">("loading");
  const [setupProbeError, setSetupProbeError] = useState<string | null>(null);
  const [badge, setBadge] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [station, setStation] = useState("");
  const [jurisdiction, setJurisdiction] = useState("SYN-DEV");
  const { signIn, bootstrap, busy, error } = useAuth();
  const navigate = useNavigate();
  const lang = currentLang();

  useEffect(() => {
    setupStatus()
      .then((status) => {
        setSetupProbeError(null);
        setMode(status.setup_required ? "setup" : "login");
      })
      // Defaulting to the login form is still the useful thing to do, but it
      // is a guess: if first-run setup was actually required, say so instead
      // of letting the operator discover it from a rejected login.
      .catch((err: Error) => {
        setSetupProbeError(err.message);
        setMode("login");
      });
  }, []);

  async function submitLogin(event: React.FormEvent) {
    event.preventDefault();
    const ok = await signIn(badge.trim(), password);
    if (ok) navigate("/cases", { replace: true });
  }

  async function submitSetup(event: React.FormEvent) {
    event.preventDefault();
    const ok = await bootstrap({
      badge_number: badge.trim(),
      full_name: fullName.trim(),
      password,
      station_id: station.trim(),
      jurisdiction_id: jurisdiction.trim(),
    });
    if (ok) navigate("/cases", { replace: true });
  }

  async function handleDemoLogin(account: typeof DEMO_ACCOUNTS[0]) {
    setBadge(account.badge);
    setPassword(account.password);
    // Auto-submit after populating
    setTimeout(async () => {
      const ok = await signIn(account.badge, account.password);
      if (ok) navigate("/cases", { replace: true });
    }, 100);
  }

  const setup = mode === "setup";

  return (
    <div className="login">
      <form className="login-card" onSubmit={setup ? submitSetup : submitLogin}>
        <div className="login-brand" style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px" }}>
          <CrimeLinkLogo variant="dark" showSubtitle={false} />
          <div>
            <h1 style={{ margin: 0, fontSize: "20px", fontWeight: 800, color: "var(--cl-ink)" }}>{t("app.title", lang)}</h1>
            <p style={{ margin: 0, fontSize: "12px", color: "var(--cl-text-3)" }}>{t("app.subtitle", lang)}</p>
          </div>
        </div>

        <h2>{setup ? t("setup.heading", lang) : t("login.heading", lang)}</h2>

        {error && (
          <div className="alert" role="alert">
            {error}
          </div>
        )}

        {setupProbeError && !error && (
          <div className="alert" role="alert">
            Could not check whether first-run setup is still required
            ({setupProbeError}). Showing the sign-in form — if this instance has
            never been set up, sign-in will fail and setup must be completed
            first.
          </div>
        )}

        {mode === "loading" && <p className="login-note">{t("state.loading", lang)}</p>}

        {mode !== "loading" && (
          <>
            <label htmlFor="badge">{t("login.badge", lang)}</label>
            <input
              id="badge"
              name="badge_number"
              value={badge}
              onChange={(e) => setBadge(e.target.value)}
              autoComplete="username"
              required
            />

            {setup && (
              <>
                <label htmlFor="fullName">{t("setup.fullName", lang)}</label>
                <input
                  id="fullName"
                  name="full_name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  autoComplete="name"
                  required
                />
                <label htmlFor="station">{t("setup.station", lang)}</label>
                <input
                  id="station"
                  name="station_id"
                  value={station}
                  onChange={(e) => setStation(e.target.value)}
                  required
                />
                <label htmlFor="jurisdiction">{t("setup.jurisdiction", lang)}</label>
                <input
                  id="jurisdiction"
                  name="jurisdiction_id"
                  value={jurisdiction}
                  onChange={(e) => setJurisdiction(e.target.value)}
                  placeholder="SYN-DEV"
                  required
                />
                <p className="login-note">{t("setup.jurisdictionHint", lang)}</p>
              </>
            )}

            <label htmlFor="password">{t("login.password", lang)}</label>
            <input
              id="password"
              name="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={setup ? "new-password" : "current-password"}
              required
            />

            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy
                ? t("state.loading", lang)
                : setup
                  ? t("setup.submit", lang)
                  : t("login.submit", lang)}
            </button>

            <p className="login-note">{setup ? t("setup.note", lang) : t("login.note", lang)}</p>

            {!setup && (
              <div className="demo-access">
                <div className="demo-access-header">
                  <span className="demo-badge">QUICK DEMO ACCESS</span>
                  <h3>Demo Access — Evaluator Ready</h3>
                  <p>
                    Hosted instance contains complete demonstration dataset. No upload required. All three roles access same demo data scope with different permissions.
                  </p>
                </div>
                <div className="demo-accounts-list">
                  {DEMO_ACCOUNTS.map((account) => (
                    <button
                      key={account.id}
                      type="button"
                      className="demo-account-card"
                      onClick={() => handleDemoLogin(account)}
                      disabled={busy}
                    >
                      <div className="demo-account-top">
                        <span className="demo-account-title">Login as {account.label}</span>
                        <span className={`demo-role-tag role-${account.role.toLowerCase()}`}>{account.role}</span>
                      </div>
                      <div className="demo-account-meta">
                        <span className="demo-badge-id">{account.badge}</span>
                        <span className="demo-desc">{account.description}</span>
                      </div>
                    </button>
                  ))}
                </div>
                <div className="demo-access-footer">
                  Demo accounts: DEMO-ADMIN / DEMO-INVESTIGATOR / DEMO-VIEWER — same data scope, different operation permissions. Backend authorization enforced.
                </div>
              </div>
            )}
          </>
        )}

        <div className="lang-switch">
          {[
            { code: "en", label: "English" },
            { code: "hi", label: "हिन्दी" },
            { code: "te", label: "తెలుగు" },
            { code: "ta", label: "தமிழ்" },
          ].map((l) => (
            <button
              key={l.code}
              type="button"
              className={`lang-btn ${lang === l.code ? "on" : ""}`}
              onClick={() => setLang(l.code as "en" | "hi" | "te" | "ta")}
            >
              {l.label}
            </button>
          ))}
        </div>
      </form>
    </div>
  );
}
