import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { hasAccessToken, logout, tryRestore } from "../lib/api";
import { Logo } from "./Logo";
import LoginPage from "../features/auth/LoginPage";
import NewSessionPage from "../features/sessions/NewSessionPage";
import SessionListPage from "../features/sessions/SessionListPage";
import SessionPage from "../features/sessions/SessionPage";

const Narrow = ({ children }: { children: JSX.Element }) => <div className="mx-auto max-w-4xl">{children}</div>;

export default function App() {
  const { t, i18n } = useTranslation();
  const { pathname } = useLocation();
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  useEffect(() => { tryRestore().then((ok) => { setAuthed(ok); setReady(true); }); }, []);
  if (!ready) return null;
  const guard = (el: JSX.Element) => (authed || hasAccessToken() ? el : <Navigate to="/login" replace />);
  const signedIn = authed || hasAccessToken();
  const toggleLang = () => i18n.changeLanguage(i18n.language === "en" ? "hi" : "en");
  const langLabel = i18n.language === "en" ? "हिंदी" : "English";
  // The sign-in screen is a full page of its own, without the app chrome.
  if (pathname === "/login") {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage onAuthed={() => setAuthed(true)} langLabel={langLabel} onToggleLang={toggleLang} />} />
      </Routes>
    );
  }
  const navItem = ({ isActive }: { isActive: boolean }) =>
    `relative flex h-12 items-center px-1 text-[13px] font-medium transition ${isActive
      ? "text-slate-900 after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-slate-900" : "text-slate-500 hover:text-slate-900"}`;
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-12 max-w-6xl items-center gap-6 px-4 sm:px-6">
          <Link to="/sessions" className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
            <Logo />
            {t("app_name")}
          </Link>
          {signedIn && (
            <nav className="hidden items-center gap-5 sm:flex" aria-label="Main">
              <NavLink to="/sessions" end className={navItem}>{t("sessions_nav")}</NavLink>
            </nav>)}
          <div className="ml-auto flex items-center gap-1">
            <button className="btn-ghost" onClick={toggleLang}>{langLabel}</button>
            {authed && <button className="btn-ghost" onClick={() => logout().then(() => setAuthed(false))}>{t("logout")}</button>}
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
      <Routes>
        <Route path="/sessions" element={guard(<Narrow><SessionListPage /></Narrow>)} />
        <Route path="/sessions/new" element={guard(<Narrow><NewSessionPage /></Narrow>)} />
        <Route path="/sessions/:id" element={guard(<SessionPage />)} />
        <Route path="*" element={<Navigate to={signedIn ? "/sessions" : "/login"} replace />} />
      </Routes>
      </main>
      <footer className="border-t border-slate-200">
        <div className="mx-auto max-w-6xl px-4 py-4 text-xs text-slate-500 sm:px-6">{t("footer_note")}</div>
      </footer>
    </div>
  );
}
