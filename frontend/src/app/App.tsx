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
  return (
    <div className="mx-auto max-w-2xl p-4">
      <header className="mb-6 flex items-center justify-between">
        <Link to="/sessions" className="text-xl font-semibold">{t("app_name")}</Link>
        <div className="flex gap-3 text-sm">
          <button className="underline" onClick={() => i18n.changeLanguage(i18n.language === "en" ? "hi" : "en")}>
            {i18n.language === "en" ? "हिंदी" : "English"}
          </button>
          {authed && <button className="underline" onClick={() => logout().then(() => setAuthed(false))}>{t("logout")}</button>}
        </div>
      </header>
      <Routes>
        <Route path="/login" element={<LoginPage onAuthed={() => setAuthed(true)} />} />
        <Route path="/sessions" element={guard(<SessionListPage />)} />
        <Route path="/sessions/new" element={guard(<NewSessionPage />)} />
        <Route path="/sessions/:id" element={guard(<SessionPage />)} />
        <Route path="*" element={<Navigate to="/sessions" replace />} />
      </Routes>
    </div>
  );
}
