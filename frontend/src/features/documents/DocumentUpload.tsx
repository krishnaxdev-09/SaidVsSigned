// T2.7 document upload screen (moved here from T2.1, user decision 2026-11-14; doc 05 §4–§5, doc 10): kind, PDF or
// photos in page order, processing status, page warnings and photo page retake (PUT → new version).
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, api, newIdempotencyKey, upload } from "../../lib/api";
import type { DocumentKind, LoanDocument } from "../../lib/types";

const KINDS: DocumentKind[] = ["kfs", "loan_agreement", "sanction_letter", "fee_schedule", "other"];
const PAGE_WARNING = /^(blurry_page|page_unreadable|low_ocr_page):(\d+)$/;

export default function DocumentUpload({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["documents", sessionId],
    queryFn: () => api<LoanDocument[]>(`/api/v1/sessions/${sessionId}/documents`) });
  const [kind, setKind] = useState<DocumentKind>("kfs");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useRef<string | null>(null);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try {
      await fn();
      await qc.invalidateQueries({ queryKey: ["documents", sessionId] });
    } catch (e) {
      setError(e instanceof ApiError ? `${t("upload_failed")}: ${e.message} (${e.code})` : t("upload_failed"));
    } finally {
      setBusy(false);
    }
  }

  const submit = () => run(async () => {
    key.current ??= newIdempotencyKey();
    const form = new FormData();
    form.append("kind", kind);
    for (const f of files) form.append("files", f);
    await upload(`/api/v1/sessions/${sessionId}/documents`, "POST", form, key.current);
    setFiles([]); key.current = null;
  });

  const retake = (doc: LoanDocument, page: number, file: File) => run(async () => {
    const form = new FormData();
    form.append("file", file);
    await upload(`/api/v1/documents/${doc.document_id}/pages/${page}`, "PUT", form, newIdempotencyKey());
  });

  return (
    <section className="space-y-3 rounded border border-slate-200 p-4" aria-labelledby="docs-title">
      <h2 id="docs-title" className="font-semibold">{t("documents_title")}</h2>
      <ul className="space-y-2">
        {(q.data ?? []).map((d) => {
          const pages = d.warnings.map((w) => PAGE_WARNING.exec(w)).filter(Boolean).map((m) => Number(m![2]));
          return (
            <li key={d.document_id} data-testid="document" className="rounded border p-3 text-sm">
              <p><strong>{t(`kind_${d.kind}`)}</strong> · v{d.version} · {d.page_count ?? "?"} p ·{" "}
                <span data-testid="document-stage">{d.status === "ready" ? t("doc_ready")
                  : d.status === "failed" ? t("doc_failed", { code: d.status_code ?? "" }) : d.stage}</span></p>
              {[...new Set(pages)].map((n) => (
                <div key={n} className="mt-1 flex flex-wrap items-center gap-2 text-amber-800">
                  <span>{t("retake_hint", { n })}</span>
                  {d.source === "photos" && (
                    <label className="rounded border px-2 py-1">{t("replace_page", { n })}
                      <input type="file" accept="image/jpeg,image/png,image/webp" capture="environment" className="sr-only"
                        onChange={(e) => e.target.files?.[0] && retake(d, n, e.target.files[0])} />
                    </label>)}
                </div>
              ))}
            </li>);
        })}
      </ul>
      <label className="block text-sm">{t("doc_kind")}
        <select className="mt-1 block rounded border p-2" value={kind} onChange={(e) => setKind(e.target.value as DocumentKind)}>
          {KINDS.map((k) => <option key={k} value={k}>{t(`kind_${k}`)}</option>)}
        </select>
      </label>
      <label className="block text-sm">{t("choose_doc_files")}
        <input type="file" multiple accept="application/pdf,image/jpeg,image/png,image/webp" className="mt-1 block"
          aria-describedby="doc-help" onChange={(e) => { setFiles([...(e.target.files ?? [])]); key.current = null; }} />
      </label>
      <p id="doc-help" className="text-xs text-slate-600">{t("doc_files_help")}</p>
      {files.length > 0 && <ol className="list-decimal pl-5 text-xs">{files.map((f) => <li key={f.name}>{f.name}</li>)}</ol>}
      <button className="h-11 rounded bg-slate-900 px-4 text-white disabled:opacity-50" disabled={!files.length || busy}
        onClick={submit}>{busy ? t("uploading") : t("upload")}</button>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    </section>
  );
}
