import type { Speaker, SpeakerRole } from "../../lib/types";

// One tap = one agent: the tapped speaker becomes the agent and everyone else the customer side.
export function rolesForAgent(labels: string[], agentLabel: string): Record<string, SpeakerRole> {
  return Object.fromEntries(labels.map((l) => [l, l === agentLabel ? "agent" : "customer"]));
}

// The suggestion can be accepted in one tap only if it names exactly one agent and is not marked uncertain.
export function confidentSuggestedAgent(speakers: Speaker[]): string | null {
  const agents = speakers.filter((s) => s.suggested_role === "agent");
  if (agents.length !== 1 || speakers.some((s) => s.suggestion_uncertain)) return null;
  return agents[0].speaker_label;
}

export function confirmedAgent(speakers: Speaker[]): string | null {
  return speakers.find((s) => s.role_assignment === "user_confirmed" && s.speaker_role === "agent")?.speaker_label ?? null;
}
