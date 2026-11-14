import { expect, it } from "vitest";
import type { Speaker } from "../../lib/types";
import { confidentSuggestedAgent, confirmedAgent, rolesForAgent } from "./roles";

const sp = (label: string, over: Partial<Speaker> = {}): Speaker => ({
  speaker_label: label, speaker_source: "diarized", segment_count: 2, suggested_role: null, role_confidence: null,
  suggestion_uncertain: false, speaker_role: "unknown", role_assignment: "auto", confirmed_at: null, quotes: [], ...over,
});

it("one tap makes exactly one agent", () => {
  expect(rolesForAgent(["speaker_0", "speaker_1"], "speaker_1")).toEqual({ speaker_0: "customer", speaker_1: "agent" });
});

it("offers one-tap confirmation only for a confident single-agent suggestion", () => {
  const ok = [sp("a", { suggested_role: "agent" }), sp("b", { suggested_role: "customer" })];
  expect(confidentSuggestedAgent(ok)).toBe("a");
  expect(confidentSuggestedAgent([sp("a", { suggested_role: "agent", suggestion_uncertain: true }), sp("b")])).toBeNull();
  expect(confidentSuggestedAgent([sp("a", { suggested_role: "agent" }), sp("b", { suggested_role: "agent" })])).toBeNull();
});

it("an auto-suggested agent is not a confirmed agent", () => {
  expect(confirmedAgent([sp("a", { speaker_role: "agent", role_assignment: "auto" })])).toBeNull();
  expect(confirmedAgent([sp("a", { speaker_role: "agent", role_assignment: "user_confirmed" })])).toBe("a");
});
