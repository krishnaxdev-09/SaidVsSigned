import { expect, it } from "vitest";
import { applyEvent } from "./eventLog";

const ev = (seq: number) => ({ type: "session.status", session_id: "s", seq, ts: "", payload: {} });

it("drops duplicate and out-of-order events by seq", () => {
  let s = { lastSeq: 0, events: [] as ReturnType<typeof ev>[] };
  for (const n of [3, 3, 2, 5]) s = applyEvent(s, ev(n));
  expect(s.events.map((e) => e.seq)).toEqual([3, 5]);
});
