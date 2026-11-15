// Sign-in / create-account card. Same calls as before: register (create mode) → login → /sessions.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
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
        <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-slate-400" aria-hidden="true">{icon}</span>
        {children}
      </div>
      {hint && <p id={`${id}-hint`} className="text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

const input = "h-12 w-full rounded-lg border border-slate-300 bg-white pl-10 pr-3 text-base text-slate-900 placeholder:text-slate-400 " +
  "shadow-sm outline-none transition focus:border-slate-900 focus:ring-2 focus:ring-slate-900/15";

export default function LoginPage({ onAuthed }: { onAuthed: () => void }) {
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
    <div className="flex min-h-[70vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
        <div className="mb-6 text-center">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            {register ? t("auth_title_register") : t("auth_title_login")}</h1>
          <p className="mt-1 text-sm text-slate-500">{t("auth_tagline")}</p>
        </div>

        <div role="tablist" aria-label={t("auth_mode")} className="mb-6 grid grid-cols-2 rounded-lg bg-slate-100 p-1 text-sm">
          {(["login", "register"] as const).map((m) => (
            <button key={m} type="button" role="tab" aria-selected={mode === m} onClick={() => switchTo(m)}
              className={`h-9 rounded-md font-medium transition ${mode === m ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
              {m === "login" ? t("login") : t("register")}</button>))}
        </div>

        <form className="space-y-4" onSubmit={submit} noValidate={false}>
          <Field id="email" label={t("email")} icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 6 9-6" /></svg>}>
            <input id="email" className={input} type="email" inputMode="email" autoComplete="email" autoCapitalize="none"
              spellCheck={false} placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </Field>

          <Field id="password" label={t("password")} hint={register ? t("auth_password_hint") : undefined} icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>}>
            <input id="password" className={`${input} pr-20 ${tooShort ? "border-amber-500" : ""}`} type={show ? "text" : "password"}
              autoComplete={register ? "new-password" : "current-password"} minLength={register ? MIN_PASSWORD : undefined}
              aria-describedby={register ? "password-hint" : undefined} placeholder="••••••••••"
              value={password} onChange={(e) => setPassword(e.target.value)} required />
            <button type="button" onClick={() => setShow((s) => !s)} aria-pressed={show}
              className="absolute inset-y-0 right-2 my-auto h-8 rounded-md px-2 text-xs font-medium text-slate-600 hover:bg-slate-100">
              {show ? t("auth_hide") : t("auth_show")}</button>
          </Field>

          {error && <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{error}</p>}

          <button type="submit" disabled={busy}
            className="h-12 w-full rounded-lg bg-slate-900 text-base font-medium text-white shadow-sm transition hover:bg-slate-800 disabled:opacity-60">
            {busy ? t("auth_working") : register ? t("register") : t("login")}</button>
        </form>

        <p className="mt-6 text-center text-sm text-slate-500">
          {register ? t("auth_have_account") : t("auth_no_account")}{" "}
          <button type="button" className="font-medium text-slate-900 underline-offset-2 hover:underline"
            onClick={() => switchTo(register ? "login" : "register")}>{register ? t("login") : t("register")}</button>
        </p>
      </div>
    </div>
  );
}
