// Sign-in / create-account card. Same calls as before: register (create mode) → login → /sessions.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Logo } from "../../app/Logo";
import { api, ApiError, login } from "../../lib/api";

type Mode = "login" | "register";
const MIN_PASSWORD = 10; // backend: RegisterIn.password min_length=10

export function friendlyError(e: unknown, mode: Mode, t: (k: string) => string): string {
  if (e instanceof ApiError) {
    if (e.code === "email_taken") return t("auth_err_taken");
    if (e.code === "unauthorized") return t("auth_err_credentials");
    if (e.status === 429) return t("auth_err_rate");
    if (e.status === 422 || e.code === "validation_error") return mode === "register" ? t("auth_err_invalid") : t("auth_err_credentials");
  }
  return t("auth_err_generic");
}

function Field({ id, label, icon, hint, children }:
  { id: string; label: string; icon: React.ReactNode; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-slate-700">{label}</label>
      <div className="relative">
        <span className="pointer-events-none absolute inset-y-0 left-2.5 flex items-center text-slate-400" aria-hidden="true">{icon}</span>
        {children}
      </div>
      {hint && <p id={`${id}-hint`} className="text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

const input = "h-11 w-full rounded-md border shadow-card sm:h-10 border-slate-300 bg-white pl-9 pr-3 text-sm text-slate-900 placeholder:text-slate-400 " +
  "outline-none transition focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20";

export default function LoginPage({ onAuthed, langLabel, onToggleLang }:
  { onAuthed: () => void; langLabel?: string; onToggleLang?: () => void }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const register = mode === "register";
  const tooShort = register && password.length > 0 && password.length < MIN_PASSWORD;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (register && password.length < MIN_PASSWORD) { setError(t("auth_err_short")); return; }
    setError(null); setBusy(true);
    try {
      if (register) await api("/api/v1/auth/register", { method: "POST", body: { email: email.trim(), password } });
      await login(email.trim(), password);
      onAuthed();
      nav("/sessions");
    } catch (err) {
      setError(friendlyError(err, mode, t));
    } finally {
      setBusy(false);
    }
  }

  const switchTo = (m: Mode) => { setMode(m); setError(null); };

  return (
    <div className="flex min-h-screen bg-white">
      <section aria-hidden="true" className="hidden w-[46%] max-w-[640px] flex-col justify-between gap-10 bg-ink p-10 text-slate-50 lg:flex xl:p-14">
        <div className="flex items-center gap-2 text-[15px] font-semibold"><Logo inverted />{t("app_name")}</div>
        <div className="max-w-lg space-y-7">
          <p className="text-[34px] font-semibold leading-[1.15] tracking-tight">{t("auth_tagline")}</p>
          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-ink-line bg-ink-line text-sm">
            <div className="space-y-2 bg-ink-soft p-4">
              <span className="block text-xs uppercase tracking-[0.06em] text-ink-muted">{t("login_said")} · <span className="font-mono">04:12</span></span>
              <span className="block">{t("login_demo_said")}</span>
            </div>
            <div className="space-y-2 bg-ink-soft p-4">
              <span className="block text-xs uppercase tracking-[0.06em] text-ink-muted">{t("login_signed")} · <span className="font-mono">14.2</span></span>
              <span className="block">{t("login_demo_signed")}</span>
            </div>
          </div>
          <ol className="space-y-3 text-sm text-slate-200">
            {["login_step1", "login_step2", "login_step3"].map((k, i) => (
              <li key={k} className="flex gap-3"><span className="font-mono text-ink-muted">0{i + 1}</span>{t(k)}</li>))}
          </ol>
        </div>
        <p className="text-xs text-ink-muted">{t("footer_note")}</p>
      </section>

      <div className="relative flex flex-1 items-center justify-center p-6 sm:p-10">
      <div className="absolute inset-x-6 top-5 flex items-center justify-between sm:inset-x-10">
        <span className="flex items-center gap-2 text-[15px] font-semibold lg:invisible"><Logo />{t("app_name")}</span>
        {onToggleLang && <button type="button" className="btn-ghost" onClick={onToggleLang}>{langLabel}</button>}
      </div>
      <div className="w-full max-w-[360px]">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            {register ? t("auth_title_register") : t("auth_title_login")}</h1>
          <p className="mt-1 text-sm text-slate-500 lg:hidden">{t("auth_tagline")}</p>
        </div>

        <div role="tablist" aria-label={t("auth_mode")} className="mb-6 grid grid-cols-2 gap-1 rounded-md bg-slate-100 p-1 text-[13px]">
          {(["login", "register"] as const).map((m) => (
            <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => switchTo(m)}
              className={`h-9 rounded font-medium transition sm:h-8 ${mode === m ? "bg-white font-semibold text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900"}`}>
              {m === "login" ? t("login") : t("register")}</button>))}
        </div>

        <form className="space-y-4" onSubmit={submit} noValidate={false}>
          <Field id="email" label={t("email")} icon={
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></svg>}>
            <input id="email" className={input} type="email" inputMode="email" autoComplete="email" autoCapitalize="none"
              spellCheck={false} placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </Field>

          <Field id="password" label={t("password")} hint={register ? t("auth_password_hint") : undefined} icon={
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>}>
            <input id="password" className={`${input} pr-20 ${tooShort ? "border-clarify-fg" : ""}`} type={show ? "text" : "password"}
              autoComplete={register ? "new-password" : "current-password"} minLength={register ? MIN_PASSWORD : undefined}
              aria-describedby={register ? "password-hint" : undefined} placeholder="••••••••••"
              value={password} onChange={(e) => setPassword(e.target.value)} required />
            <button type="button" onClick={() => setShow((s) => !s)} aria-pressed={show}
              className="absolute inset-y-0 right-1 my-auto h-9 rounded px-2 text-[13px] font-medium text-brand-600 hover:bg-brand-50 sm:h-8">
              {show ? t("auth_hide") : t("auth_show")}</button>
          </Field>

          {error && <p role="alert" className="alert-error">{error}</p>}

          <button type="submit" disabled={busy}
            className="h-11 w-full rounded-md bg-brand-600 text-sm font-medium text-white shadow-card transition hover:bg-brand-700 disabled:opacity-60 sm:h-10">
            {busy ? t("auth_working") : register ? t("register") : t("login")}</button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-500">
          {register ? t("auth_have_account") : t("auth_no_account")}{" "}
          <button type="button" className="link"
            onClick={() => switchTo(register ? "login" : "register")}>{register ? t("login") : t("register")}</button>
        </p>
      </div>
      </div>
    </div>
  );
}
