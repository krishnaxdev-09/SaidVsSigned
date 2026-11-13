import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { api, ApiError, login } from "../../lib/api";

export default function LoginPage({ onAuthed }: { onAuthed: () => void }) {
  const { t } = useTranslation();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit(register: boolean) {
    setError(null);
    try {
      if (register) await api("/api/v1/auth/register", { method: "POST", body: { email, password } });
      await login(email, password);
      onAuthed();
      nav("/sessions");
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)); }
  }
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit(false); }}>
      <label className="block">{t("email")}<input className="mt-1 w-full rounded border p-2" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      <label className="block">{t("password")}<input className="mt-1 w-full rounded border p-2" type="password" minLength={10} value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
      {error && <p role="alert" className="text-red-700">{error}</p>}
      <div className="flex gap-3">
        <button className="rounded bg-slate-800 px-4 py-2 text-white" type="submit">{t("login")}</button>
        <button className="rounded border px-4 py-2" type="button" onClick={() => submit(true)}>{t("register")}</button>
      </div>
    </form>
  );
}
