# 02 — Audio Input & Capture

> **Version 0.2 — 2026-11-13 (upload-first MVP revision).** Original (full live-capture design): `_archive/v0.1-2026-11-13/02_…md`.
> **Scope:**
> - [MVP] recorded audio upload: validation, dedupe, normalisation, storage.
> - [Deferred] in-person live mic and Google Meet tab + mic capture (Later-L1/L2); Meet Media API spike (Later-L3); headless bots and cellular capture (not planned / research).
>
> ADR-008 capture-mode decisions are **unchanged**; only their schedule moves (ADR-015).

## 1. Purpose [MVP]
Accept a recording of a loan-agent conversation that the customer was part of, validate it, deduplicate it, normalise it for batch STT (doc 03), and store it securely for evidence playback.

**Depends on:**
- doc 01 (consent + attestation, jobs, retry, errors)
- doc 11 (`audio_streams`, idempotency)
- doc 12 (upload security)

**Feeds:** doc 03.

## 2. Prerequisites
- Phase 0 complete.
- An active customer consent and recording attestation (doc 01 §5).

## 3. Accepted input
| Item | Rule |
|---|---|
| Formats | WAV, MP3, M4A/AAC, WebM/Opus, OGG/Opus. Identified by **magic bytes** (`RIFF…WAVE`, ID3 or MPEG frame sync, `ftyp`, EBML `1A45DFA3`, `OggS`), never by extension or the client's MIME type |
| Size / length | ≤ 200 MB and ≤ 180 min (`too_large`, `too_long`) |
| Channels | Mono or stereo. Optional `channel_layout: mono \| stereo_split` (stereo where each side is one speaker, e.g. call recorders) |
| One recording per session | MVP. A second, different file needs a new session or the replace endpoint (§5) |

## 4. API
`POST /api/v1/sessions/{id}/recordings` (multipart; header `Idempotency-Key` required).
- Fields: `file`, `channel_layout?`, `recorded_on?`.
- Responses:
  - `202 {recording_id, status:"queued", duplicate:false}`
  - `200 {recording_id, duplicate:true}` if the same sha256 already exists in this session
- Errors: `consent_required`, `unsupported_media`, `too_large`, `too_long`, `conflict_idempotency_key`.

Other endpoints:
- `GET /api/v1/recordings/{id}` → `{status, stage, duration_ms, format, channel_layout, warnings[]}`
- `PUT /api/v1/recordings/{id}` — replace the recording. Creates a new `audio_streams` row and **transcript revision**, and supersedes the old claims (doc 04 §7). Audited.
- `GET /api/v1/sessions/{id}/audio?from_ms=&to_ms=` → presigned URL for a snippet (5 min TTL). Snippets are cut server-side from the normalised FLAC.

Events: `recording.status{recording_id, stage: uploaded|validating|normalising|transcribing|diarizing|ready|failed, progress, code?}`.

## 5. Pipeline (worker job `audio.ingest:{audio_stream_id}`)
1. **Streaming upload:** the API streams to object storage `audio/{session}/original/{uuid}` while computing sha256 and enforcing the size limit. There is no full in-memory buffering.
2. **Dedupe:** if `(session_id, sha256)` already exists (and is not deleted), delete the new object and return the existing id (`duplicate:true`).
3. **Validate:** magic bytes, then `ffprobe` (30 s timeout) for codec, duration and channels. Corrupt or undecodable files → `input` error, `failed(unsupported_media)`.
4. **Normalise:** `ffmpeg -ac 1 -ar 16000 -sample_fmt s16` → `normalized.wav`, plus `normalized.flac` for playback.
   - `stereo_split`: also produce `L.wav` and `R.wav`. The user maps each side to a speaker on the speaker-confirmation screen (doc 03 §5).
   - Loudness normalisation (`loudnorm`) is applied only to the STT copy.
5. **Optional enhancement:** off. Advanced denoising is **[Deferred → Later-L8]** unless E9 shows a gain (doc 03 §6).
6. **Enqueue** `stt.batch:{audio_stream_id}:{transcript_revision}` (doc 03).

**Idempotency:** each step writes to deterministic keys (`audio/{session}/{stream_id}/…`), so a re-run overwrites the same objects. Status transitions only move forward. Retries follow doc 01 §6.

## 6. Error handling
| Case | Class | Behaviour |
|---|---|---|
| Upload interrupted | transient (client) | Client retries with the same Idempotency-Key |
| Unknown magic bytes | input | 415 `unsupported_media`; no retry |
| ffprobe/ffmpeg timeout | transient | Retry per policy, then `failed` |
| Silent or near-silent audio (RMS below threshold for > 95% of the file) | input | `failed(no_speech_detected)` with a user message |

## 7. Testing
- Fixtures: 5 formats × mono/stereo, plus corrupt files, a renamed `.exe`, an empty file and a 3 h+ file.
- Asserts:
  - normalised duration within ±50 ms
  - sha256 dedupe returns the same id
  - the same Idempotency-Key replays the response
  - a different body with the same key returns 409
  - re-running a job produces identical objects

## 8. Acceptance criteria (Phase 1)
- The 5 formats normalise correctly.
- Invalid files are rejected with the right code and no retries.
- Duplicate uploads create no duplicate jobs or rows.
- Snippet URLs play the requested range (±0.25 s).

## 9. Human review
None of its own. Covered by HC-2 and HC-3 in Phase 1.

## 10. Common failures
- Trusting the file extension.
- Buffering a 200 MB upload in memory.
- Forgetting to dedupe before enqueuing STT, so the transcription is paid for twice.

---

## Deferred designs (preserved; not part of MVP)

### D1. In-person PWA microphone — Later-L1
- `getUserMedia` with EC/NS/AGC.
- An AudioWorklet resamples to 16 kHz mono s16le in 20 ms frames, batched every 100 ms.
- Audio WS `/ws/sessions/{id}/audio?channel=mic&ticket=` with `start|pause|resume|end` control messages, `ack`/`slow_down` backpressure and a 30 s client buffer.
- Server writes 30 s PCM segments. A Silero VAD gate skips silences over 2 s and keeps an offset map.
- A persistent recording indicator and the agent consent read-out screen.

### D2. Google Meet tab + mic — Later-L2
- `getDisplayMedia({video:true, audio:true, …})`. Chromium desktop only [V]. The user must share a **tab** with audio ticked.
- Stop the video track immediately.
- Separate mic and tab WS channels: mic = customer, tab = remote/agent.
- No extra noise suppression on tab audio.
- Experiment E4: 30-min stability, with the tab hidden.

### D3. Meet Media API bot — Later-L3 research spike
Developer Preview [V]:
- The Cloud project, the OAuth principal **and all participants** must be enrolled.
- Restricted audio scope.
- 3 virtual audio streams.

Exit is a go/no-go memo. No production code before GA.

### D4. Not planned
- Headless-browser Meet bots: ToS risk, brittle.
- Cellular call capture: a browser cannot capture far-end call audio. Future research only.

Full v0.1 text, flows and sources are kept in the archive and in master report §8.
