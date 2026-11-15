// Pipeline order: step states, and the session page calls each step's endpoints only after the previous step is done.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "../../i18n";
import { _setFetch } from "../../lib/api";
import SessionPage from "./SessionPage";
import { currentStep, pipelineSteps, type PipelineInput } from "./pipeline";

vi.mock("../../lib/events", () => ({ connectEvents: () => () => {} }));

const consent = { active: true, attestation_note: "The customer states the agent was informed of the recording (not independently verified)." };
const rec = (stage: string) => ({ recording_id: "r", session_id: "s", status: stage, stage, status_code: null, duration_ms: 1000,
  format: "m4a", channel_layout: "mono", warnings: [], replaced_by: null, created_at: "" });
const speakers = (confirmed: boolean) => ({ transcript_revision: 1, confirmed, overrides: [], speakers: [
  { speaker_label: "speaker_0", speaker_source: "diarized", segment_count: 1, suggested_role: "agent", role_confidence: 1,
    suggestion_uncertain: false, speaker_role: "agent", role_assignment: confirmed ? "user_confirmed" : "auto",
    confirmed_at: null, quotes: [] }] });
const base: PipelineInput = { consent: undefined, recording: undefined, speakers: undefined, claims: undefined,
  documents: undefined, results: undefined };

it("computes step states in order and opens the first unfinished step", () => {
  let s = pipelineSteps(base);
  expect(s.map((x) => x.state)).toEqual(["todo", "locked", "locked", "locked", "locked", "locked", "locked"]);
  s = pipelineSteps({ ...base, consent: consent as never, recording: rec("transcribing") as never });
  expect(s.map((x) => x.state).slice(0, 3)).toEqual(["done", "working", "locked"]);
  expect(currentStep(s)).toBe("recording");
  s = pipelineSteps({ ...base, consent: consent as never, recording: rec("ready") as never, speakers: speakers(false) as never });
  expect(currentStep(s)).toBe("speakers");
  s = pipelineSteps({ ...base, consent: consent as never, recording: rec("ready") as never, speakers: speakers(true) as never,
    claims: [], documents: [{ status: "ready" }] as never, results: [{}] as never });
  expect(s.every((x) => x.state === "done" || x.id === "ask")).toBe(true);
  expect(currentStep(s)).toBe("ask");
});

let world: { consent: boolean; recording: string | null; confirmed: boolean; docReady: boolean };
const calls: string[] = [];
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { "content-type": "application/json" } });
afterEach(cleanup);
beforeEach(() => {
  calls.length = 0;
  _setFetch(async (input) => {
    const url = String(input).replace("/api/v1/sessions/s", "");
    calls.push(url);
    if (url === "/state") return json({ session: { id: "s", state: world.consent ? "ready" : "consent_pending", state_reason: null },
      consent: world.consent ? consent : { active: false }, last_event_seq: 0 });
    if (url.startsWith("/api/v1/consent-texts")) return json([]);
    if (url === "/recording") return json(world.recording ? rec(world.recording) : null);
    if (url === "/speakers") return json(speakers(world.confirmed));
    if (url.startsWith("/claims")) return json([]);
    if (url === "/transcript") return json({ transcript_revision: 1, is_current: true, audio_stream_id: "r", provider: "x", segments: [] });
    if (url === "/documents") return json(world.docReady ? [{ document_id: "d", session_id: "s", kind: "kfs", source: "pdf",
      version: 1, status: "ready", stage: "ready", status_code: null, page_count: 2, warnings: [], pages: [] }] : []);
    if (url === "/results") return json([]);
    return json([]);
  });
});

function page() {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={["/sessions/s"]}><Routes><Route path="/sessions/:id" element={<SessionPage />} /></Routes></MemoryRouter>
  </QueryClientProvider>);
}

it("before consent: only the session state and consent texts are requested", async () => {
  world = { consent: false, recording: null, confirmed: false, docReady: false };
  page();
  await screen.findByTestId("step-consent");
  await waitFor(() => expect(calls).toContain("/state"));
  expect(calls.some((c) => ["/recording", "/speakers", "/documents", "/results"].includes(c) || c.startsWith("/claims"))).toBe(false);
  expect(screen.getByTestId("step-recording").dataset.state).toBe("locked");
});

it("recording still transcribing: speakers, claims, documents and results are not requested yet", async () => {
  world = { consent: true, recording: "transcribing", confirmed: false, docReady: false };
  page();
  await waitFor(() => expect(screen.getByTestId("step-recording").dataset.state).toBe("working"));
  expect(calls).toContain("/recording");
  expect(calls.some((c) => c === "/speakers" || c.startsWith("/claims") || c === "/documents" || c === "/results")).toBe(false);
});

it("speakers not confirmed: claims and documents wait; the speakers step is open", async () => {
  world = { consent: true, recording: "ready", confirmed: false, docReady: false };
  page();
  await waitFor(() => expect(screen.getByTestId("step-speakers").dataset.state).toBe("todo"));
  expect(screen.getByTestId("step-speakers").querySelector("button")!.getAttribute("aria-expanded")).toBe("true");
  expect(calls.some((c) => c.startsWith("/claims") || c === "/documents" || c === "/results")).toBe(false);
});

it("everything done in order: results requested only after a document is ready", async () => {
  world = { consent: true, recording: "ready", confirmed: true, docReady: false };
  page();
  await waitFor(() => expect(screen.getByTestId("step-documents").dataset.state).toBe("todo"));
  expect(calls).toContain("/documents");
  expect(calls).not.toContain("/results");
  const order = ["/state", "/recording", "/speakers", "/claims?state=all", "/documents"].map((u) => calls.indexOf(u));
  expect(order.every((i) => i >= 0)).toBe(true);
  expect([...order].sort((a, b) => a - b)).toEqual(order);  // hit in pipeline order
});
