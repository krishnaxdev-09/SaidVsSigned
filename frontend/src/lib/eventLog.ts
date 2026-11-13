import type { EventEnvelope } from "./types";

// Pure ordering/dedupe rule (doc 11 §2/§5): drop seq <= last seen.
export function applyEvent(state: { lastSeq: number; events: EventEnvelope[] }, ev: EventEnvelope) {
  if (ev.seq <= state.lastSeq) return state;
  return { lastSeq: ev.seq, events: [...state.events, ev] };
}
