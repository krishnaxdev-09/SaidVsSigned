# 03 — ElevenLabs STT, Speaker Attribution & Audio Processing

> **Version 0.2 — 2026-11-13 (upload-first MVP revision).** Original: `_archive/v0.1-2026-11-13/03_…md`.
> **Scope:**
> - [MVP] Scribe v2 **batch** transcription with diarization; word timestamps and confidence; speaker role assignment with **one-tap user confirmation**; transcript revisions.
> - [MVP-limited] Hindi/Hinglish, only to the extent E2 validates.
> - [Deferred] realtime adapter, VAD gating and inferred live roles (Later-L1); second STT provider (Later-L8); advanced denoising (Later-L8).
>
> **Canonical here:** speaker attribution model (§5). Confidence thresholds and gates are canonical in doc 08 §5.

## 1. Purpose
Turn a normalised recording into timestamped, speaker-attributed transcript segments with word-level confidence. Get the customer to confirm which speaker is the agent before any CONFLICT can be produced.

- **Depends on:** doc 02, doc 01 (retry), doc 11 (tables).
- **Feeds:** doc 04.

## 2. Prerequisites
- Phase 0.
- An ElevenLabs API key (not provided yet; do not invent one).
- **E2** (Hinglish accuracy) and **E3** (batch confidence fields) must report at HC-3 before Phase 1 exits.

## 3. Facts (retrieved 2026-11-13; re-verify at build time)
| Fact | Label |
|---|---|
| Scribe v2 batch supports speaker diarization (max speakers 32–48; sources differ) | [V] |
| Batch returns word-level timestamps | [V] |
| Batch word-level confidence/logprob field | **[A] → E3.** Smoke test 2026-11-14 (`scribe_v2`, one 11 s English synthetic call): every word had `logprob`, `start`, `end`, `speaker_id`. One sample only; E3 must confirm on the dataset, incl. Hinglish |
| Hindi/Hinglish quality, code-switching | **[A] → E2** |
| Keyterms supported | [V, secondary] |
| Pricing: realtime $0.39/h (vendor) vs $0.28/h (third party); batch pricing to confirm | Confirm at HC-3 |
| Realtime endpoint `wss://api.elevenlabs.io/v1/speech-to-text/realtime`, `scribe_v2_realtime`, partial/committed events, no realtime diarization | [V] — used only in Later-L1 |

Sources: master report §22.

## 4. Interfaces
```python
class WordToken(BaseModel):
    text: str; start_ms: int; end_ms: int
    confidence: float | None            # normalised 0..1 (exp(logprob) if logprob); None if absent
    speaker_label: str | None           # e.g. "spk_0" (diarization)
    is_numeric: bool                    # computed: digits, number words (en/hi/hinglish), %, ₹

class TranscriptSegment(BaseModel):     # persisted; canonical columns in doc 11
    id: UUID; session_id: UUID; transcript_revision: int
    channel: Literal["mono","L","R"]
    speaker_label: str | None
    text: str; start_ms: int; end_ms: int
    words: list[WordToken]; language: str | None
    asr_avg_conf: float | None; asr_min_numeric_conf: float | None

class STTProvider(Protocol):
    name: str
    async def transcribe_file(self, object_key: str, *, diarize: bool, num_speakers: int | None,
                              language_hint: str | None, keyterms: list[str]) -> list[TranscriptSegment]: ...
    # [Deferred → Later-L1]
    async def stream(self, cfg: "STTStreamConfig", audio: AsyncIterator[bytes]) -> AsyncIterator["PartialSegment | TranscriptSegment"]: ...
```

## 5. Speaker attribution (canonical) [Accepted 2026-11-13: one-tap confirmation]
Two separate fields, used by docs 04, 08, 10 and 11:
- `speaker_source`: how speakers were separated.
  - `diarized`: mono upload.
  - `channel`: stereo split.
  - `user`: a user override on a segment.
  - `inferred`: Later-L1 only.
- `role_assignment`: how the agent/customer role was set. `auto` (heuristic/LLM) or `user_confirmed`.

**Flow (MVP):**
1. Batch STT with `diarize=true` and `num_speakers=2` (configurable 2–4).
2. **Auto role suggestion:** an LLM structured call over each speaker's first ~90 s of text returns `{speaker_label: agent|customer|other, role_confidence}`, supported by heuristics (quotes rates/fees, product pitching). This sets `role_assignment=auto`.
3. **Confirmation screen (doc 10):** "Who is the agent?" For each speaker it shows 3 short quotes with play buttons. The customer taps once to confirm or swap. The mapping is stored in `speaker_assignments` with `role_assignment=user_confirmed`, `confirmed_by`, `confirmed_at`, and the event is audited.
4. **Per-segment override:** the user can reassign a single segment (`speaker_source=user`, `role_assignment=user_confirmed`).
5. Until step 3 is done, claims can be extracted and verified, but every CONFLICT is gated to `UNCLEAR(speaker_unconfirmed)` (doc 08 §5). Confirmation triggers re-verification (`changed_because=speaker_confirmed`).
6. **Stereo split:** the user maps L/R on the same screen. The mapping is user-confirmed and `speaker_source=channel`.

## 6. Batch pipeline (job `stt.batch:{audio_stream_id}:{transcript_revision}`)
1. Submit `normalized.wav` (or L/R) to Scribe v2 batch.
   - Parameters: `diarize`, `num_speakers`, word timestamps, keyterms, `language_code` only if the user forced a language.
   - Use the async/webhook mode for long files if the API offers it [A]. Otherwise poll with timeouts.
2. Parse the result into segments. Split at speaker change or after more than 1.2 s of silence, with a maximum of 30 s per segment.
3. Compute `is_numeric`, `asr_avg_conf` and `asr_min_numeric_conf` (None if no confidences).
4. **Persist atomically:** insert a `transcript_revisions` row, then segments and words. Re-running the same job (the dedupe key includes the revision) deletes and rewrites that revision's rows inside one transaction.
5. Run the auto role suggestion, emit `transcript.ready{transcript_revision}` and enqueue `claims.extract:{session_id}:{transcript_revision}`.

**Keyterms (≤ 50):** the lender name, plus "processing fee", "foreclosure", "prepayment", "part payment", "KFS", "APR", "EMI", "flat rate", "reducing balance", "GST", "insurance", "lakh", "crore", "penal charges".

**Transcript revisions:**
- A new revision is created only when the recording is replaced, the language is forced and the file re-run, or the STT config changes.
- Speaker confirmation **does not** create a revision; it is stored in `speaker_assignments`.
- Claims are pinned to a revision (doc 04 §7).

## 7. Noise & enhancement
- MVP: loudness normalisation only, on the STT copy.
- **[Proposed — ADR-012]:** no denoiser unless experiment **E9** (uploads × {none, RNNoise, DeepFilterNet3}) shows at least 2 points of numeric-token accuracy gain without hurting clean audio. Advanced enhancement is Later-L8.

## 8. Hinglish policy [MVP-limited]
- Supported languages in the MVP are declared **only** from the E2 results at HC-3.
- If Hinglish numeric-token accuracy is below 95%, Hinglish sessions are labelled "beta". Every numeric CONFLICT in them needs `quote_confirmed_by_user` (doc 08 §5, proposal pending HC-3), or stays UNCLEAR.

## 9. Error handling
| Case | Class | Behaviour |
|---|---|---|
| 401/403 | input (config) | Job `failed(provider_auth)`, alert. The user sees "Transcription unavailable" |
| 429 / 5xx / timeout | transient | Retry policy (doc 01 §6) |
| Empty transcript | input | `failed(no_speech_detected)` |
| Diarization finds 1 speaker | — | Segments are kept. Confirmation screen asks "Is this only the agent speaking?" and offers per-segment assignment |
| No word confidences (E3 negative) | — | `asr_*_conf = None` → ASR gate treats as low (doc 08 §5). HC-3 decides the fallback |

## 10. Testing
- **Unit:** segment splitting; numeric token detection (en, hi, Devanagari, Hinglish); confidence normalisation; role suggestion parsing; atomic revision rewrite.
- **Contract:** recorded batch responses (cassettes); no network in CI.
- **E2:** 30 scripted clips. Measure WER (en), CER and numeric-token accuracy (Hinglish).
- **E3:** which confidence fields exist.
- **E10 (new):** diarization + auto-role accuracy on 20 mono two-speaker clips, plus confirmation-screen usability with 5 users.

## 11. Acceptance criteria (Phase 1)
- Segments have speaker labels and word timestamps (±200 ms against the audio).
- Auto-role accuracy is reported (E10). The confirmation flow works for mono and stereo.
- No CONFLICT is possible before confirmation (integration test).
- Numeric-token accuracy is reported per language. MVP language support is declared from the evidence, not assumed.
- A `FakeSTTProvider` passes the same tests (provider independence).

## 12. Human review
**HC-3** covers:
- E2, E3 and E10 results
- pricing
- the `quote_confirmed_by_user` fallback
- declared languages
- whether a second provider is needed (ADR-003)

## 13. Common failures
- Treating `auto` roles as confirmed.
- Overwriting transcript text when the speaker is corrected.
- Devanagari digits not detected as numeric.

---

## Deferred designs (preserved)

### Realtime adapter — Later-L1
- ElevenLabs realtime WS `scribe_v2_realtime`, receiving `input_audio_chunk` base64 PCM. Commit strategy `vad` (silence ~0.8 s [A]).
- Map `committed_transcript_with_timestamps` to segments using the VAD offset map. Partials are display-only and never verified.
- Reconnect with backoff and a 30 s replay buffer.
- One connection per channel.
- Single-mic live roles are `speaker_source=inferred`, with a periodic batch diarization re-pass every 60 s.
- E1 measures latency and the audio enum.

### Second STT provider — Later-L8
Same `STTProvider` protocol.

### Live mic noise
Browser WebRTC NS for the live mic. RNNoise-WASM as an option (Later-L1/L8).
