// Snippet playback (doc 02 §4): the server cuts the range and returns a 5-minute presigned URL.
import { api } from "./api";

let audio: HTMLAudioElement | null = null;

export async function playSnippet(sessionId: string, fromMs: number, toMs: number): Promise<void> {
  const from = Math.max(0, Math.floor(fromMs));
  const to = Math.max(from + 1000, Math.ceil(toMs)); // at least 1 s so short quotes are audible
  const r = await api<{ url: string }>(`/api/v1/sessions/${sessionId}/audio?from_ms=${from}&to_ms=${to}`);
  audio ??= new Audio();
  audio.src = r.url;
  await audio.play();
}

export function mmss(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
