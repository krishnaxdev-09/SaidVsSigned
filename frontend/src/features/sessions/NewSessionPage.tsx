import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { api, ApiError, newIdempotencyKey } from "../../lib/api";
import type { Session } from "../../lib/types";

const PRODUCTS = ["personal_loan", "home_loan", "vehicle_loan", "business_loan", "gold_loan", "other_loan"];
const LANGS: [string, string][] = [["en", "English"], ["hinglish", "Hinglish"], ["hi", "हिंदी"]];

export default function NewSessionPage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const key = useRef(newIdempotencyKey()); // same key on retries of this one action
  const [product, setProduct] = useState(PRODUCTS[0]);
  const [lang, setLang] = useState("en");
  const [lender, setLender] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    setBusy(true); setError(null);
    try {
      const s = await api<Session>("/api/v1/sessions", { method: "POST", idempotencyKey: key.current,
        body: { mode: "upload", product_type: product, language_hint: lang, lender_name: lender || null } });
      nav(`/sessions/${s.id}`);
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)); } finally { setBusy(false); }
  }
  return (
    <div className="mx-auto max-w-lg space-y-6">
      <div className="border-b border-slate-200 pb-5">
        <h1>{t("new_session")}</h1>
        <p className="muted mt-0.5">{t("new_session_sub")}</p>
      </div>
      <form className="card space-y-5" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="label">{t("product_type")}
          <select className="input" value={product} onChange={(e) => setProduct(e.target.value)}>
            {PRODUCTS.map((p) => <option key={p} value={p}>{t(`product_${p}`)}</option>)}</select></label>
        <fieldset>
          <legend className="label">{t("language")}</legend>
          <div className="mt-1.5 grid grid-cols-3 gap-2">
            {LANGS.map(([v, name]) => (
              <label key={v} className={`choice justify-center ${lang === v ? "choice-on" : ""}`}>
                <input type="radio" name="lang" className="sr-only" checked={lang === v} onChange={() => setLang(v)} />{name}
              </label>))}
          </div>
          <p className="muted mt-1.5 text-xs">{t(`lang_note_${lang}`)}</p>
        </fieldset>
        <label className="label">{t("lender_name")}
          <input className="input" maxLength={200} placeholder={t("lender_placeholder")} value={lender}
            onChange={(e) => setLender(e.target.value)} /></label>
        {error && <p role="alert" className="alert-error">{error}</p>}
        <button className="btn-primary w-full sm:w-auto" type="submit" disabled={busy}>{busy ? t("auth_working") : t("create")}</button>
      </form>
    </div>
  );
}
