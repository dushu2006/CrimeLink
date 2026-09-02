import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../store/auth";
import { currentLang, setLang, t } from "../i18n";

export default function Login() {
  const [badge, setBadge] = useState("");
  const [password, setPassword] = useState("");
  const { signIn, busy, error } = useAuth();
  const navigate = useNavigate();
  const lang = currentLang();

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const ok = await signIn(badge.trim(), password);
    if (ok) navigate("/cases", { replace: true });
  }

  return (
    <div className="login">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand">
          <div className="login-mark" aria-hidden="true" />
          <div>
            <h1>{t("app.title", lang)}</h1>
            <p>{t("app.subtitle", lang)}</p>
          </div>
        </div>

        <h2>{t("login.heading", lang)}</h2>

        {error && (
          <div className="alert" role="alert">
            {error}
          </div>
        )}

        <label htmlFor="badge">{t("login.badge", lang)}</label>
        <input
          id="badge"
          name="badge_number"
          value={badge}
          onChange={(e) => setBadge(e.target.value)}
          autoComplete="username"
          required
        />

        <label htmlFor="password">{t("login.password", lang)}</label>
        <input
          id="password"
          name="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />

        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? t("state.loading", lang) : t("login.submit", lang)}
        </button>

        <p className="login-note">{t("login.note", lang)}</p>

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
