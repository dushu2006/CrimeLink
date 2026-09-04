import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { setupStatus } from "../api/client";
import { useAuth } from "../store/auth";
import { currentLang, setLang, t } from "../i18n";

export default function Login() {
  const [mode, setMode] = useState<"loading" | "login" | "setup">("loading");
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
      .then((status) => setMode(status.setup_required ? "setup" : "login"))
      .catch(() => setMode("login"));
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

  const setup = mode === "setup";

  return (
    <div className="login">
      <form className="login-card" onSubmit={setup ? submitSetup : submitLogin}>
        <div className="login-brand">
          <div className="login-mark" aria-hidden="true" />
          <div>
            <h1>{t("app.title", lang)}</h1>
            <p>{t("app.subtitle", lang)}</p>
          </div>
        </div>

        <h2>{setup ? t("setup.heading", lang) : t("login.heading", lang)}</h2>

        {error && (
          <div className="alert" role="alert">
            {error}
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

            {/* This block only renders once `mode` is known, so the former
                `mode === "loading"` guard here was unreachable. */}
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy
                ? t("state.loading", lang)
                : setup
                  ? t("setup.submit", lang)
                  : t("login.submit", lang)}
            </button>

            <p className="login-note">{setup ? t("setup.note", lang) : t("login.note", lang)}</p>
          </>
        )}

        <div className="lang-switch">
          <button type="button" className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>
            English
          </button>
          <button type="button" className={lang === "hi" ? "on" : ""} onClick={() => setLang("hi")}>
            हिन्दी
          </button>
        </div>
      </form>
    </div>
  );
}
