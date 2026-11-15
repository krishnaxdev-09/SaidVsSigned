// Live microphone capture with a fake MediaRecorder: start → chunks PUT in order → stop → finish(chunks = n).
// Upload stays the default mode and is unchanged.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import "../../i18n";
import { _setFetch } from "../../lib/api";
import RecordingPanel from "./RecordingPanel";

class FakeRecorder {
  static isTypeSupported = (m: string) => m.startsWith("audio/webm");
  static last: FakeRecorder;
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  constructor(public stream: unknown, public opts: { mimeType: string }) { FakeRecorder.last = this; }
  start() {}
  emit(text: string) { this.ondataavailable?.({ data: new Blob([text], { type: "audio/webm" }) }); }
  stop() { this.emit("tail"); this.onstop?.(); }
}

const calls: { method: string; url: string; body?: unknown }[] = [];
const json = (b: unknown, s = 200) => new Response(JSON.stringify(b), { status: s, headers: { "content-type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => {
  calls.length = 0;
  vi.stubGlobal("MediaRecorder", FakeRecorder);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop: vi.fn() }] }) } });
  _setFetch(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ method, url, body: init?.body instanceof Blob ? "blob" : init?.body ? JSON.parse(String(init.body)) : undefined });
    if (url.endsWith("/recording")) return json(null);
    if (url.endsWith("/sessions/s/live")) return json({ live_id: "L", status: "open" }, 201);
    if (url.includes("/chunks/")) return json({ live_id: "L", status: "open", chunks: 1, bytes: 4 });
    if (url.endsWith("/finish")) return json({ recording_id: "rec", status: "queued", duplicate: false }, 202);
    return json({});
  });
});

function panel() {
  render(<QueryClientProvider client={new QueryClient()}><RecordingPanel sessionId="s" /></QueryClientProvider>);
}

it("keeps upload as the default and records live on request", async () => {
  panel();
  expect(((await screen.findByLabelText("Upload a recording file")) as HTMLInputElement).checked).toBe(true);
  expect(screen.getByRole("button", { name: "Upload" })).toBeTruthy();  // unchanged upload UI
  fireEvent.click(screen.getByLabelText("Record live from the microphone"));
  fireEvent.click(await screen.findByText("Start recording"));
  await screen.findByText("Stop and check the call");
  FakeRecorder.last.emit("a");
  FakeRecorder.last.emit("b");
  fireEvent.click(screen.getByText("Stop and check the call"));
  await waitFor(() => expect(calls.some((c) => c.url.endsWith("/live/L/finish"))).toBe(true));
  const seq = calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.url.replace("/api/v1", "")}`);
  expect(seq).toEqual(["POST /sessions/s/live", "PUT /live/L/chunks/0", "PUT /live/L/chunks/1", "PUT /live/L/chunks/2",
                       "POST /live/L/finish"]);
  expect(calls.find((c) => c.url.endsWith("/finish"))!.body).toEqual({ chunks: 3 });
  expect(FakeRecorder.last.opts.mimeType).toBe("audio/webm;codecs=opus");
});

it("discarding a live recording deletes it", async () => {
  panel();
  fireEvent.click(await screen.findByLabelText("Record live from the microphone"));
  fireEvent.click(await screen.findByText("Start recording"));
  fireEvent.click(await screen.findByText("Discard"));
  await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url.endsWith("/live/L"))).toBe(true));
});
