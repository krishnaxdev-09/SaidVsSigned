// T1.7 "E2E with fakes": speaker confirmation → claims list → transcript, against a stateful fake API.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "../../i18n";
import { _setFetch } from "../../lib/api";
import type { Claim, Segment, Speakers, Transcript } from "../../lib/types";
import SpeakerConfirm from "../speakers/SpeakerConfirm";
import TranscriptView from "../transcript/TranscriptView";
import ClaimList, { valueText } from "./ClaimList";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const seg = (id: string, label: string, text: string, start: number): Segment =>
  ({ id, channel: "mono", speaker_label: label, text, start_ms: start, end_ms: start + 2000, words: [], language: "en",
     asr_avg_conf: 0.99, asr_min_numeric_conf: null });
const val = (raw: string, value: string | null, quantity = "percent") =>
  ({ field: "f", raw_text: raw, quantity, value, value_low: null, value_high: null, comparator: "eq", period: "unspecified",
     rate_basis: null, rate_type: null, includes_gst: null, normaliser_version: value === null ? null : "norm-1.0" });
const claim = (over: Partial<Claim>): Claim => ({
  id: "c", transcript_revision: 1, claim_type: "processing_fee", utterance_kind: "assertion", speaker_label: "speaker_0",
  speaker_role: "agent", role_assignment: "auto", normalized_statement: "", quote: "q", start_ms: 10160, end_ms: 12000,
  values: [], vagueness: "specific", readiness: "needs_document", verifiable: true, state: "active",
  supersedes_claim_id: null, conditions: [], ...over });

let confirmed = false;
const calls: { method: string; url: string; body?: unknown }[] = [];

beforeEach(() => {
  confirmed = false;
  calls.length = 0;
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  _setFetch(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const role = confirmed ? "user_confirmed" : "auto";
    if (url.endsWith("/speakers") && method === "PUT") { confirmed = true; return json({}); }
    if (url.endsWith("/speakers")) {
      return json({ transcript_revision: 1, confirmed, overrides: [], speakers: [
        { speaker_label: "speaker_0", speaker_source: "diarized", segment_count: 2, suggested_role: "agent", role_confidence: 1,
          suggestion_uncertain: false, speaker_role: "agent", role_assignment: role, confirmed_at: null,
          quotes: [{ segment_id: "g1", text: "The processing fee is only 1%", start_ms: 10160, end_ms: 12000 }] },
        { speaker_label: "speaker_1", speaker_source: "diarized", segment_count: 1, suggested_role: "customer", role_confidence: 1,
          suggestion_uncertain: false, speaker_role: "customer", role_assignment: role, confirmed_at: null, quotes: [] }],
      } satisfies Speakers);
    }
    if (url.includes("/claims?")) {
      return json([
        claim({ id: "fee", quote: "The processing fee is only 1%", values: [val("1%", "1")], role_assignment: role }),
        claim({ id: "q", claim_type: "processing_fee", utterance_kind: "question", speaker_label: "speaker_1",
                speaker_role: "customer", role_assignment: role, quote: "What is the processing fee?", verifiable: false }),
        claim({ id: "vague", claim_type: "general_assurance", vagueness: "vague", quote: "no hidden charges", role_assignment: role }),
        claim({ id: "emi", claim_type: "emi", quote: "Your EMI will be 36,000 rupees.", values: [val("36,000 rupees", "36000", "amount_inr")], role_assignment: role }),
      ]);
    }
    if (url.includes("/claims/") && method === "PATCH") return json(claim({ id: "fee", state: "dismissed" }));
    if (url.endsWith("/transcript")) {
      return json({ transcript_revision: 1, is_current: true, audio_stream_id: "r", provider: "elevenlabs", segments: [
        seg("g1", "speaker_0", "The processing fee is only 1%", 10160), seg("g2", "speaker_1", "What is the processing fee?", 7020)],
      } satisfies Transcript);
    }
    if (url.includes("/segments/")) return json({ segment_id: "g2", speaker_role: "agent" });
    if (url.includes("/audio?")) return json({ url: "http://localhost:9000/audio/snip.flac", expires_in: 300 });
    return json({}, 404);
  });
});

afterEach(() => vi.restoreAllMocks());

function renderAll() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SpeakerConfirm sessionId="s1" /><ClaimList sessionId="s1" /><TranscriptView sessionId="s1" />
    </QueryClientProvider>);
}

it("confirm agent → claims update → play → dismiss → fix a segment", async () => {
  renderAll();
  const cards = await screen.findAllByTestId("claim");
  expect(cards).toHaveLength(4);
  const fee = cards.find((c) => c.textContent?.includes("The processing fee is only 1%"))!;
  expect(within(fee).getByTestId("claim-value").textContent).toContain("1%");
  expect(fee.textContent).toContain("auto-detected");
  expect(cards.find((c) => c.textContent?.includes("What is the processing fee?"))!.textContent).toContain("Not checked: question");
  expect(cards.find((c) => c.textContent?.includes("no hidden charges"))!.textContent).toContain("Vague");
  expect(cards.find((c) => c.textContent?.includes("36,000"))!.textContent).toContain("₹36000");

  fireEvent.click(await screen.findByText("Yes, Speaker 1 is the agent"));
  await waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/speakers"))).toBe(true));
  await waitFor(async () => {
    const fresh = await screen.findAllByTestId("claim");
    expect(fresh.find((c) => c.textContent?.includes("only 1%"))!.textContent).not.toContain("auto-detected");
  });

  fireEvent.click(within(fee).getByRole("button", { name: /play/ }));
  await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalled());
  expect(calls.find((c) => c.url.includes("/audio?"))!.url).toContain("from_ms=9660&to_ms=12500"); // quote ±0.5 s

  fireEvent.click(within(fee).getByText("Dismiss"));
  await waitFor(() => expect(calls.find((c) => c.method === "PATCH" && c.url.endsWith("/claims/fee"))?.body)
    .toEqual({ state: "dismissed" }));

  const segs = await screen.findAllByTestId("segment");
  expect(segs.map((s) => s.querySelector("[data-testid=speaker-chip]")!.textContent)).toEqual(["Agent", "You (customer)"]);
  fireEvent.change(within(segs[1]).getByRole("combobox"), { target: { value: "agent" } });
  await waitFor(() => expect(calls.find((c) => c.url.endsWith("/segments/g2/speaker"))?.body).toEqual({ speaker_role: "agent" }));
});

it("shows unparsed values as not understood and ranges as ranges", () => {
  expect(valueText({ ...val("x", "12"), value: null, value_low: "12", value_high: "13" })).toBe("12–13%");
  expect(valueText(val("very low", null))).toBe("");
});
