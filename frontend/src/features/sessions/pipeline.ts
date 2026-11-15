// Session pipeline: the order the backend works in (doc 10 §2 upload-first flow).
// consent → recording (upload → transcribed) → speakers (confirm) → claims (+ transcript) → documents (ready)
// → results → ask & report. A step's panel (and its API calls) only mounts once the step before it is done.
import type { Claim, ConsentStatus, LoanDocument, Recording, Speakers, VerificationResult } from "../../lib/types";

export type StepId = "consent" | "recording" | "speakers" | "claims" | "documents" | "results" | "ask";
export type StepState = "locked" | "todo" | "working" | "done" | "failed";
export interface Step { id: StepId; state: StepState; detail?: string }

export interface PipelineInput {
  consent: ConsentStatus | undefined;
  recording: Recording | null | undefined;
  speakers: Speakers | undefined;
  claims: Claim[] | undefined;
  documents: LoanDocument[] | undefined;
  results: VerificationResult[] | undefined;
}

export const STEP_ORDER: StepId[] = ["consent", "recording", "speakers", "claims", "documents", "results", "ask"];

export function pipelineSteps(p: PipelineInput): Step[] {
  const steps: Step[] = [];
  const push = (id: StepId, state: StepState, detail?: string) => {
    const prevDone = steps.every((s) => s.state === "done");
    steps.push({ id, state: prevDone ? state : "locked", detail: prevDone ? detail : undefined });
  };
  push("consent", p.consent?.active ? "done" : "todo");
  const rec = p.recording;
  push("recording", !rec ? "todo" : rec.stage === "ready" ? "done" : rec.stage === "failed" ? "failed" : "working",
       rec && rec.stage !== "ready" ? rec.stage : undefined);
  push("speakers", p.speakers?.confirmed ? "done" : p.speakers?.speakers?.length ? "todo" : "working");
  const active = (p.claims ?? []).filter((c) => c.state === "active");
  push("claims", p.claims === undefined ? "working" : "done", p.claims ? String(active.length) : undefined);
  const docs = p.documents ?? [];
  const ready = docs.some((d) => d.status === "ready");
  push("documents", ready ? "done" : docs.some((d) => d.status === "failed") && !docs.some((d) => d.status !== "failed")
    ? "failed" : docs.length ? "working" : "todo", docs.find((d) => d.status !== "ready")?.stage);
  // Results arrive per claim (verify jobs, debounced); questions/hypotheticals are hidden from the list by design.
  const results = p.results ?? [];
  push("results", results.length ? "done" : "working", String(results.length));
  push("ask", "todo");
  return steps;
}

/** The step to open by default: the first one that isn't done (or the last). */
export function currentStep(steps: Step[]): StepId {
  return (steps.find((s) => s.state !== "done") ?? steps[steps.length - 1]).id;
}

export const unlocked = (steps: Step[], id: StepId) => steps.find((s) => s.id === id)?.state !== "locked";
