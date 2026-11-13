import { api } from "./api";
import type { EventEnvelope } from "./types";

// Events WS client: single-use ticket per connection, resume with `since`, exponential reconnect (doc 11 §5).
export function connectEvents(sessionId: string, getLastSeq: () => number, onEvent: (e: EventEnvelope) => void) {
  let ws: WebSocket | null = null;
  let stopped = false;
  let backoff = 500;

  async function open() {
    if (stopped) return;
    try {
      const { ticket } = await api<{ ticket: string }>("/api/v1/auth/ws-ticket",
        { method: "POST", body: { session_id: sessionId, purpose: "events" } });
      const proto = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${proto}://${location.host}/ws/sessions/${sessionId}/events?ticket=${encodeURIComponent(ticket)}&since=${getLastSeq()}`);
      ws.onmessage = (m) => {
        const data = JSON.parse(m.data);
        if (data.type === "ping") return;
        backoff = 500;
        onEvent(data as EventEnvelope);
      };
      ws.onclose = (e) => { if (!stopped && e.code !== 4404) retry(); };
    } catch { retry(); }
  }
  function retry() { setTimeout(open, backoff); backoff = Math.min(backoff * 2, 15000); }
  open();
  return () => { stopped = true; ws?.close(); };
}
