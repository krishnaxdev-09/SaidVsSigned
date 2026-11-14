// T2.7 "bbox + snippet E2E" with a fake API: results dashboard → evidence drawer → highlighted clause bbox on the
// page image + snippet playback of the agent's quote; HC-3 play-and-confirm; document upload screen.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "../../i18n";
import { _setFetch } from "../../lib/api";
import type { DocumentEvidence, LoanDocument, VerificationResult } from "../../lib/types";
import DocumentUpload from "../documents/DocumentUpload";
import EvidenceDrawer, { bboxStyle } from "./EvidenceDrawer";
import ResultsDashboard from "./ResultsDashboard";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const spoken = { kind: "spoken", claim_id: "c1", quote: "The processing fee is only 1 percent.", start_ms: 61000, end_ms: 64000,
  speaker_role: "agent", role_assignment: "user_confirmed", quote_confirmed_by_user: true } as const;
const doc: DocumentEvidence = { kind: "document", document_id: "d1", document_version: 1, page: 1, clause_id: "cl1",
  clause_text: "Item | Details\nProcessing fee | 2% of loan amount + GST", ocr_conf: 1, text_source: "text_layer",
  bboxes: [{ x0: 0.1, y0: 0.25, x1: 0.4, y1: 0.28 }, { x0: 0.4, y0: 0.25, x1: 0.9, y1: 0.28 }] };
const result = (over: Partial<VerificationResult>): VerificationResult => ({
  id: "r1", claim_id: "c1", version: 2, is_current: true, status: "CONFLICT", reason_code: null,
  headline: "Possible mismatch: processing fee", reason: 'The agent said "…" (01:01). Page 1 says "…".', severity: "medium",
  suggested_question: "Ask the agent to explain the difference in writing before you sign.", evidence: [spoken, doc],
  changed_because: { trigger: "document_ready", previous_status: "NOT_VERIFIED", previous_version: 1 }, created_at: "", ...over });

const calls: { method: string; url: string; body?: unknown }[] = [];
let play: ReturnType<typeof vi.spyOn>;
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

beforeEach(() => {
  calls.length = 0;
  play = vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  _setFetch(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url, body: init?.body instanceof FormData ? init.body : init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/results")) {
      return json([
        result({}),
        result({ id: "r2", claim_id: "c2", status: "UNCLEAR", reason_code: "low_asr_confidence", headline: "Needs clarification: EMI",
                 engine_verdicts: [{ engine: "contract", missing: ["quote_confirmation"] }],
                 evidence: [{ ...spoken, claim_id: "c2", quote: "EMI 36,000", start_ms: 90000, end_ms: 92000 }],
                 changed_because: { trigger: "initial", previous_status: null, previous_version: null } }),
        result({ id: "r4", claim_id: "c4", status: "UNCLEAR", reason_code: "low_asr_confidence", headline: "Needs clarification: loan amount",
                 engine_verdicts: [{ engine: "contract", missing: [] }] }),
        result({ id: "r3", claim_id: "c3", status: "MATCH", headline: "Matches the document: tenure", severity: "info",
                 changed_because: { trigger: "initial", previous_status: null, previous_version: null } }),
      ]);
    }
    if (url.includes("/claims?")) {
      return json([{ id: "c2", values: [{ raw_text: "36,000" }], quote_confirmed_by_user: false }, { id: "c1", values: [] }]);
    }
    if (url.includes("/history")) return json([result({ version: 1, status: "NOT_VERIFIED" }), result({})]);
    if (url.includes("/pages/1/image")) return json({ url: "https://minio.test/d1/v1/pages/1.webp", expires_in: 300 });
    if (url.includes("/audio?")) return json({ url: "https://minio.test/snippet.m4a" });
    if (url.includes("/confirm-quote")) return json({});
    if (url.endsWith("/reverify")) return json({ queued: 3 }, 202);
    if (url.endsWith("/sessions/s/reports") && method === "POST") return json({ report_id: "rep1", status: "queued" }, 202);
    if (url.endsWith("/reports/rep1")) return json({ report_id: "rep1", status: "ready", url: "https://minio.test/s/rep1.pdf" });
    if (url.endsWith("/documents") && method === "GET") {
      return json([{ document_id: "d9", session_id: "s", kind: "kfs", source: "photos", version: 1, status: "ready", stage: "ready",
        status_code: null, page_count: 2, warnings: ["blurry_page:2"], pages: [] } satisfies LoanDocument]);
    }
    if (url.endsWith("/documents") || url.includes("/pages/2")) return json({ document_id: "d9", version: 1, status: "queued" }, 202);
    return json({}, 404);
  });
});

function wrap(ui: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);
}

it("shows the four statuses, opens evidence with the clause bbox highlighted and plays the quote", async () => {
  wrap(<ResultsDashboard sessionId="s" />);
  await screen.findByText("Possible mismatch: processing fee");
  expect(screen.getByTestId("count-CONFLICT").textContent).toContain("1");
  expect(screen.getByTestId("count-UNCLEAR").textContent).toContain("2");
  expect(screen.getByTestId("count-MATCH").textContent).toContain("1");
  expect(screen.getByTestId("count-NOT_VERIFIED").textContent).toContain("0");
  expect(screen.getAllByTestId("result")[0].textContent).toContain("Possible mismatch");  // CONFLICT first
  expect(screen.getAllByText(/Updated because the document was processed \(was Couldn't check\)/).length).toBeGreaterThan(0);

  fireEvent.click(within(screen.getAllByTestId("result")[0]).getByText("Evidence"));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByTestId("clause-text").textContent).toContain("2% of loan amount + GST");
  const img = await within(dialog).findByRole("img");
  expect(img.getAttribute("src")).toBe("https://minio.test/d1/v1/pages/1.webp");
  const boxes = within(dialog).getAllByTestId("bbox");
  expect(boxes).toHaveLength(2);
  expect(boxes[0].style.left).toBe("10%");
  expect(boxes[0].style.top).toBe("25%");
  expect(parseFloat(boxes[0].style.width)).toBeCloseTo(30);
  expect(parseFloat(boxes[0].style.height)).toBeCloseTo(3);
  await within(dialog).findByText("v1: Couldn't check");

  fireEvent.click(within(dialog).getByLabelText("play 1:01"));
  await waitFor(() => expect(play).toHaveBeenCalled());
  expect(calls.some((c) => c.url.endsWith("/audio?from_ms=60500&to_ms=64500"))).toBe(true);
});

it("offers play-and-confirm only for an unconfirmed numeric low-ASR result", async () => {
  wrap(<ResultsDashboard sessionId="s" />);
  const btn = await screen.findByText("Yes, that's what was said");
  expect(screen.getAllByText("Yes, that's what was said")).toHaveLength(1);
  fireEvent.click(btn);
  await waitFor(() => expect(calls.some((c) => c.url.endsWith("/claims/c2/confirm-quote") && c.method === "POST")).toBe(true));
  fireEvent.click(screen.getByText("Check again"));
  await waitFor(() => expect(calls.some((c) => c.url.endsWith("/sessions/s/reverify"))).toBe(true));
});

it("bbox style converts normalised top-left coordinates to percentages", () => {
  expect(bboxStyle({ x0: 0, y0: 0, x1: 1, y1: 0.5 })).toEqual({ left: "0%", top: "0%", width: "100%", height: "50%" });
});

it("drawer closes", async () => {
  const onClose = vi.fn();
  wrap(<EvidenceDrawer sessionId="s" result={result({})} onClose={onClose} />);
  fireEvent.click(await screen.findByText("Close"));
  expect(onClose).toHaveBeenCalled();
});

it("document upload sends kind and files in page order and offers a retake for a blurry photo page", async () => {
  wrap(<DocumentUpload sessionId="s" />);
  await screen.findByText(/Page 2 looks blurry/);
  const input = screen.getByLabelText(/Choose the document/i) as HTMLInputElement;
  const f1 = new File(["a"], "p1.jpg", { type: "image/jpeg" });
  const f2 = new File(["b"], "p2.jpg", { type: "image/jpeg" });
  fireEvent.change(input, { target: { files: [f1, f2] } });
  fireEvent.click(screen.getByRole("button", { name: "Upload" }));
  await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/sessions/s/documents"))).toBe(true));
  const form = calls.find((c) => c.method === "POST" && c.url.endsWith("/documents"))!.body as FormData;
  expect(form.get("kind")).toBe("kfs");
  expect((form.getAll("files") as File[]).map((f) => f.name)).toEqual(["p1.jpg", "p2.jpg"]);
  const retake = screen.getByText("Retake page 2").querySelector("input")!;
  fireEvent.change(retake, { target: { files: [new File(["c"], "p2b.jpg", { type: "image/jpeg" })] } });
  await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/documents/d9/pages/2"))).toBe(true));
});

it("downloads the report: creates it with an idempotency key, then opens the presigned PDF", async () => {
  const open = vi.spyOn(window, "open").mockReturnValue(null);
  wrap(<ResultsDashboard sessionId="s" />);
  fireEvent.click(await screen.findByText("Download report (PDF)"));
  await waitFor(() => expect(open).toHaveBeenCalledWith("https://minio.test/s/rep1.pdf", "_blank", "noopener"));
  const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/sessions/s/reports"));
  expect(post).toBeTruthy();
});

it("shows calculation evidence when the server sends it", async () => {
  const calc = { kind: "calculation" as const, calculation_id: "k1", formula_id: "emi_reducing_v1", input_sources: ["document"],
    summary: "From the document: emi = ₹35902.17; the agent said ₹30000." };
  wrap(<EvidenceDrawer sessionId="s" result={result({ evidence: [spoken, calc, doc] })} onClose={() => {}} />);
  expect((await screen.findByTestId("calc-summary")).textContent).toContain("35902.17");
});
