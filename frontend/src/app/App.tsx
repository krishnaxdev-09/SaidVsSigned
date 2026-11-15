import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, Route, Routes } from "react-router-dom";
import { hasAccessToken, logout, tryRestore } from "../lib/api";
import LoginPage from "../features/auth/LoginPage";
import NewSessionPage from "../features/sessions/NewSessionPage";
import SessionListPage from "../features/sessions/SessionListPage";
import SessionPage from "../features/sessions/SessionPage";

export default function App() {
  const { t, i18n } = useTranslation();
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  useEffect(() => { tryRestore().then((ok) => { setAuthed(ok); setReady(true); }); }, []);
  if (!ready) return null;
  const guard = (el: JSX.Element) => (authed || hasAccessToken() ? el : <Navigate to="/login" replace />);
  const signedIn = authed || hasAccessToken();
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-200/80 bg-white/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-3xl items-center justify-between px-4">
          <Link to="/sessions" className="flex items-center gap-2 text-[17px] font-semibold tracking-tight">
            <span aria-hidden="true" className="grid h-8 w-8 place-items-center rounded-lg bg-brand-600 text-sm font-bold text-white">S</span>
            {t("app_name")}
          </Link>
          <nav className="flex items-center gap-1 text-sm">
            {signedIn && <Link to="/sessions/new" className="btn-ghost hidden sm:inline-flex">{t("new_session")}</Link>}
            <button className="btn-ghost" onClick={() => i18n.changeLanguage(i18n.language === "en" ? "hi" : "en")}>
              {i18n.language === "en" ? "हिंदी" : "English"}
            </button>
            {authed && <button className="btn-ghost" onClick={() => logout().then(() => setAuthed(false))}>{t("logout")}</button>}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6 sm:py-8">
      <Routes>
        <Route path="/login" element={<LoginPage onAuthed={() => setAuthed(true)} />} />
        <Route path="/sessions" element={guard(<SessionListPage />)} />
        <Route path="/sessions/new" element={guard(<NewSessionPage />)} />
        <Route path="/sessions/:id" element={guard(<SessionPage />)} />
        <Route path="*" element={<Navigate to="/sessions" replace />} />
      </Routes>
      </main>
      <footer className="mx-auto max-w-3xl px-4 pb-8 text-center text-xs text-slate-400">{t("footer_note")}</footer>
    </div>
  );
}
