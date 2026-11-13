import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { api, ApiError, newIdempotencyKey } from "../../lib/api";
import type { Session } from "../../lib/types";

const PRODUCTS = ["personal_loan", "home_loan", "vehicle_loan", "business_loan", "gold_loan", "other_loan"];

export default function NewSessionPage() {
  const { t } = useTranslation();
  const nav = useNavigate();
  const key = useRef(newIdempotencyKey()); // same key on retries of this one action
  const [product, setProduct] = useState(PRODUCTS[0]);
  const [lang, setLang] = useState("en");
  const [lender, setLender] = useState("");
  const [error, setError] = useState<string | null>(null);
  async function submit() {
    try {
      const s = await api<Session>("/api/v1/sessions", { method: "POST", idempotencyKey: key.current,
        body: { mode: "upload", product_type: product, language_hint: lang, lender_name: lender || null } });
      nav(`/sessions/${s.id}`);
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)); }
  }
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <label className="block">{t("product_type")}<select className="mt-1 w-full rounded border p-2" value={product} onChange={(e) => setProduct(e.target.value)}>
        {PRODUCTS.map((p) => <option key={p} value={p}>{p}</option>)}</select></label>
      <label className="block">{t("language")}<select className="mt-1 w-full rounded border p-2" value={lang} onChange={(e) => setLang(e.target.value)}>
        <option value="en">English</option><option value="hi">हिंदी</option><option value="hinglish">Hinglish</option></select></label>
      <label className="block">{t("lender_name")}<input className="mt-1 w-full rounded border p-2" maxLength={200} value={lender} onChange={(e) => setLender(e.target.value)} /></label>
      {error && <p role="alert" className="text-red-700">{error}</p>}
      <button className="rounded bg-slate-800 px-4 py-2 text-white" type="submit">{t("create")}</button>
    </form>
  );
}
