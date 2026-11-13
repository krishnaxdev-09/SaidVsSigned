# 10 — React PWA Frontend (built incrementally P0–P8)

## Purpose
Build a mobile-first, installable PWA for the customer. It must be usable by non-expert, possibly less-literate users, in English and Hindi. It provides:
- live transcript and claim cards
- evidence drawers with audio and page highlights
- camera document capture
- Ask-your-contract
- an exportable session report

## Prerequisites
Doc 01 skeleton, API/WS contracts (doc 11).

## Stack [R]
- React 18 + TypeScript + Vite
- Tailwind CSS
- `vite-plugin-pwa` (Workbox)
- React Router
- TanStack Query for REST
- Zustand for live session state
- i18next (en, hi)
- `pdf`-free page viewer: server-rendered WebP page images with an SVG overlay for bboxes
- Wavesurfer.js (or a plain `<audio>` element with a seek API) for snippet playback

## Structure
```
frontend/src/
  app/            routes.tsx, AuthProvider, QueryClient, ErrorBoundary
  lib/            api.ts, events.ts (WS client w/ resume), auth.ts, format.ts (₹, lakh/crore, mm:ss)
  audio/          capture.ts (mic/tab), pcm-encoder.worklet.ts, wsAudio.ts, support.ts (feature detection)
  features/
    session-new/  ModePicker, LenderPicker, ConsentScreen, AgentConsentReadout
    live/         LiveScreen, RecordingBanner, TranscriptPane, ClaimCardList, StatusChip, CaptureHealth
    documents/    CameraCapture (multi-page), UploadDropzone, IngestProgress, PageViewer (bbox overlay), RetakePrompt
    results/      ResultsBoard (grouped by status), ResultCard, EvidenceDrawer, CalcTrace, RuleSource, QuestionsToAsk
    ask/          AskScreen, AnswerBubble, CitationChip
    report/       ReportPreview, ExportButton
    settings/     Language, Retention, DeleteData
  components/     Button, Sheet, Tabs, Badge, Skeleton, EmptyState, Toast
  i18n/           en.json, hi.json
  pwa/            manifest, sw config
```

## Key screens
1. **New session:** mode picker with capability detection (Meet tab is disabled with an explanation on unsupported browsers), optional lender search, language, then **consent** (customer checkbox with plain-language explanation, and an agent read-out script with an "Agent agreed" button or a recorded verbal consent option).
2. **Live screen:**
   - Top: a persistent **red recording banner** with timer, Pause and Stop.
   - Middle: transcript with speaker chips (`Agent`, `You`, `Agent (inferred)` with a dashed outline; tap to swap).
   - Bottom sheet: claim cards that appear live with a status chip:
     - Conflict = red + ⚠
     - Unclear = amber + ?
     - Not verified = grey + ○
     - Match = green + ✓

     Colour is never the only signal (icon + text).
   - A floating **"Add document"** button opens camera capture.
   - A capture-health indicator shows input levels per channel and STT status.
3. **Camera capture:**
   - `getUserMedia({video:{facingMode:'environment'}})` with an edge-guide overlay, or a fallback `<input type=file accept=image/* capture=environment>`.
   - Multi-page thumbnails with reorder and delete, and a client-side blur check (Laplacian variance on a downscaled canvas) prompting a retake.
   - Upload as one document.
4. **Results board:**
   - Grouped: Conflict → Unclear → Not verified → Match, each with a count.
   - A card shows the headline, "Agent said: '…' (04:12)", "Document says: '…' (Page 3)" and a "Why" line.
   - **Evidence drawer:**
     - audio snippet player (start_ms − 2 s → end_ms + 2 s, via `GET /sessions/{id}/audio?channel&from&to`)
     - full quote with confidence hint ("audio was unclear" if low)
     - page image with **highlighted bbox**, plus zoom
     - calculation trace table (inputs with source badges: *agent said*, *document*, *assumed*)
     - rule card with the official link and effective date
     - per-engine verdicts in a collapsible list
     - "Ask the agent:" suggested question with a copy button
5. **Ask:** chat with suggested questions. Answers show citation chips that open the PageViewer at the highlighted clause. `NOT_IN_DOCUMENT` is styled distinctly.
6. **Report:** summary plus the full list, exported as PDF (server-generated) or shared via the Web Share API.

## Live state model
```ts
type SessionLive = {
  seq: number;                                   // last event seq (for resume)
  segments: Record<string, Segment>; order: string[];
  partial: Record<'mic'|'tab'|'upload', string>;
  claims: Record<string, Claim>;
  results: Record<string /*claimId*/, VerificationResult>;
  documents: Record<string, DocumentStatus>;
  capture: { mic?: ChannelHealth; tab?: ChannelHealth; stt: 'ok'|'reconnecting'|'down' };
};
```
- Reducer per event type.
- Out-of-order or duplicate events are ignored by `seq`.
- On reconnect, call `GET /sessions/{id}/state` (snapshot) if the gap exceeds 500 events. Otherwise resume with `since`.

## Accessibility & inclusion (SDG 10)
- Minimum 16 px body text, 44 px tap targets, WCAG AA contrast.
- Hindi strings reviewed by a native speaker. Numbers in Indian format (₹5,00,000 / "5 lakh").
- Plain-language glossary popovers (APR, KFS, flat vs reducing).
- Optional read-aloud of result headlines (SpeechSynthesis), behind a toggle.

## PWA
- Installable. App-shell precache. **No caching of API or media responses** (personal data).
- Offline: show "You're offline — recording continues locally". Live mode keeps a MediaRecorder backup locally and offers to upload it when back online.
- Use the Wake Lock API during live sessions.

## Testing
- Vitest + Testing Library: reducers (event ordering), StatusChip and ResultCard rendering for all 4 statuses, i18n completeness.
- Playwright E2E with fake WS server fixtures (doc 12). Camera and mic are mocked with Chrome's `--use-fake-device-for-media-stream --use-file-for-fake-audio-capture=…`.
- Lighthouse PWA and accessibility score ≥ 90.

## Acceptance criteria
- A claim card appears ≤ 300 ms after its `claim.created` event.
- Evidence drawer: audio plays from the right timestamp (±0.5 s) and the bbox highlight is visible on the right page.
- All 4 statuses are distinguishable without colour.
- Works on Android Chrome (live mic, camera) and desktop Chrome/Edge (Meet tab).

## Human review
UX review of the live and results screens with 3–5 target users before P8. Copy review for accusation-free wording.

## Common failures
- Rendering the PDF client-side with heavy libraries on low-end phones. Use server page images.
- bbox coordinate systems: Docling uses PDF points with a bottom-left origin [A: verify]. Normalise to a 0–1 top-left system server-side.
- A missing user gesture before `AudioContext.resume()` on mobile.
