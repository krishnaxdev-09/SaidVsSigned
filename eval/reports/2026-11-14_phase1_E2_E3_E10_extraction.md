# Phase 1 experiments — E2, E3, E10 and claim extraction (2026-11-14)

> **Synthetic speech.** All 30 conversations are macOS text-to-speech (agent: Rishi en-IN / Lekha hi-IN; customer: Samantha en-US / Rishi en-IN), clean and evenly paced, no line noise, scripted by us. These numbers are **optimistic** and are **not** real-call accuracy. HC-3 should treat them as a pipeline sanity check and ask for real recordings before declaring language support.

- Dataset `conversations-v1` (sha256 of scripts `f00f8ca14e8d`): 30 calls = 15 English + 15 Hinglish, 20 mono + 10 stereo_split. STT `scribe_v2` (diarize, num_speakers=2 for mono); LLM `google.gemma-4-31b` on Bedrock; prompt `claim_extract_v2`; normaliser `norm-1.0`.

## Key findings for HC-3

1. **A wrong Hindi number with high confidence (critical).** In C04HI and C04ST the hi-IN voice said "साठ हज़ार" (60,000) and
   `scribe_v2` returned **₹7000** (साठ "60" heard as सात "7") with word confidence **0.975** and **0.957**, above
   `T_asr_num` 0.85. The ASR gate would not catch it; with a document stating 60,000, a correct agent statement would
   become a **false "Possible mismatch"**. This supports ADR-019 (customer plays and confirms the quote) before any
   numeric CONFLICT, at least for Hinglish/Hindi, or a stricter Hindi threshold. Decision for HC-3.
2. **Confidences do not separate errors here (E3).** Every word has a confidence (100%), but matching words average 0.980
   and non-matching 0.975. Most "non-matching" words are format differences (digits vs words, script), so this set
   cannot calibrate thresholds; real recordings are needed (Phase 4 calibration).
3. **Extraction quality is high on clean input.** Reference text: recall and precision 100% (198/198), 0 questions or
   hypotheticals read as checkable, 0 unparsed values, 0 values not from the normaliser. End to end, the only misses are
   the two misheard 60,000s above (99.0%).
4. **Diarization and auto-role.** 100% word-level diarization on the 20 mono calls; Gemma's agent suggestion correct 20/20.
   E10 found a bug, fixed during T1.8: the keyword cross-check scored per word, so short customer questions outscored
   the agent and every suggestion was flagged uncertain (20/20 → 2/20 after the fix; the 2 are LLM-reply failures that
   fell back to the heuristic, uncertain by design).
5. **WER is not meaningful on this set** (22.5% English, 45.7% Hinglish) because the reference is the TTS input text
   (digits vs number words; Devanagari vs Latin for English loanwords). Numeric-token accuracy is the useful E2 metric:
   English 100% (75/75), Hinglish 97.3% (73/75).
6. **Not done:** confirmation-screen usability with 5 users (E10) and any real (non-TTS) recordings. Pricing and the
   second-provider question (ADR-003) are not measured here.

## E2 — English
- WER 22.5% (175 edits / 778 reference words). Note: the reference is the TTS input; digits vs number words and script differences count as errors.
- Numeric-token accuracy 100.0% (75/75 spoken numbers recovered from the transcript by the normaliser).

## E2 — Hinglish (hi-IN TTS)
- WER 45.7% (544 edits / 1191 reference words). Note: the reference is the TTS input; digits vs number words and script differences count as errors.
- Numeric-token accuracy 97.3% (73/75 spoken numbers recovered from the transcript by the normaliser).
- Missed numbers: C04HI: 60000; C04ST: 60000

## E3 — word confidences (batch)
- Words with a confidence (`logprob` → exp): 100.0% of 1541 words.
- Mean confidence of words matching the reference: 0.980 (n=1202); of words that did not match: 0.975 (n=339).
- Share of non-matching words below T_asr_num 0.85: 5.9%; share of matching words below 0.85: 5.1%.

## E10 — diarization and auto-role (20 mono calls)
- Word-level diarization accuracy (best label → speaker mapping): 100.0%.
- Calls with exactly 2 speaker labels: 20/20.
- Auto-role (Gemma) agent correct: 20/20; suggestions flagged uncertain: 2/20; methods: {'llm:bedrock_mantle': 18, 'heuristic': 2}.
- Confirmation-screen usability with 5 users: **not run** (needs people; pending for HC-3).

## Claim extraction on the reference text (LLM only)
- **English:** recall 100.0% (99/99), precision 100.0% (0 extra), hypotheticals/questions taken as checkable: 0/6, values unparsed 0/81, values not from the normaliser 0.
- **Hinglish:** recall 100.0% (99/99), precision 100.0% (0 extra), hypotheticals/questions taken as checkable: 0/6, values unparsed 0/81, values not from the normaliser 0.
- **All:** recall 100.0% (198/198), precision 100.0% (0 extra), hypotheticals/questions taken as checkable: 0/12, values unparsed 0/162, values not from the normaliser 0.

## Claim extraction end to end on the STT transcript
- **English:** recall 100.0% (99/99), precision 100.0% (0 extra), hypotheticals/questions taken as checkable: 0/6, values unparsed 0/81, values not from the normaliser 0.
- **Hinglish:** recall 98.0% (97/99), precision 98.0% (2 extra), hypotheticals/questions taken as checkable: 0/6, values unparsed 0/81, values not from the normaliser 0.
- **All:** recall 99.0% (196/198), precision 99.0% (2 extra), hypotheticals/questions taken as checkable: 0/12, values unparsed 0/162, values not from the normaliser 0.

## Doc 04 §12 / doc 03 §11 targets (on this synthetic set)
- Agent claims precision ≥ 0.85 and recall ≥ 0.80; hypotheticals/questions as promises ≤ 5%; 100% verbatim anchors (enforced: non-verbatim quotes are dropped); 0 values not from the normaliser.
- Validator drops across all STT runs: none.

## Per conversation
| id | lang | layout | WER | numbers | diar | role | STT recall | REF recall |
|---|---|---|---|---|---|---|---|---|
| C01EN | en | mono | 32.6% | 5/5 | 100.0% | ✅ | 6/6 | 6/6 |
| C01HI | hi | mono | 42.5% | 4/4 | 100.0% | ✅ | 7/7 | 7/7 |
| C01ST | en | stereo | 30.0% | 6/6 | channel | — | 7/7 | 7/7 |
| C02EN | en | mono | 25.5% | 5/5 | 100.0% | ✅ | 6/6 | 6/6 |
| C02HI | hi | mono | 49.4% | 5/5 | 100.0% | ✅ | 7/7 | 7/7 |
| C02ST | hi | stereo | 46.3% | 5/5 | channel | — | 6/6 | 6/6 |
| C03EN | en | mono | 8.8% | 4/4 | 100.0% | ✅ | 7/7 | 7/7 |
| C03HI | hi | mono | 48.2% | 6/6 | 100.0% | ✅ | 7/7 | 7/7 |
| C03ST | en | stereo | 30.2% | 5/5 | channel | — | 6/6 | 6/6 |
| C04EN | en | mono | 21.8% | 5/5 | 100.0% | ✅ | 7/7 | 7/7 |
| C04HI | hi | mono | 48.5% | 4/5 | 100.0% | ✅ | 5/6 | 6/6 |
| C04ST | hi | stereo | 42.0% | 3/4 | channel | — | 6/7 | 7/7 |
| C05EN | en | mono | 34.6% | 6/6 | 100.0% | ✅ | 7/7 | 7/7 |
| C05HI | hi | mono | 46.1% | 5/5 | 100.0% | ✅ | 6/6 | 6/6 |
| C05ST | en | stereo | 33.3% | 5/5 | channel | — | 7/7 | 7/7 |
| C06EN | en | mono | 11.1% | 5/5 | 100.0% | ✅ | 6/6 | 6/6 |
| C06HI | hi | mono | 40.2% | 4/4 | 100.0% | ✅ | 7/7 | 7/7 |
| C06ST | hi | stereo | 44.6% | 6/6 | channel | — | 7/7 | 7/7 |
| C07EN | en | mono | 11.5% | 5/5 | 100.0% | ✅ | 6/6 | 6/6 |
| C07HI | hi | mono | 50.0% | 5/5 | 100.0% | ✅ | 7/7 | 7/7 |
| C07ST | en | stereo | 13.6% | 5/5 | channel | — | 6/6 | 6/6 |
| C08EN | en | mono | 37.9% | 4/4 | 100.0% | ✅ | 7/7 | 7/7 |
| C08HI | hi | mono | 45.2% | 6/6 | 100.0% | ✅ | 7/7 | 7/7 |
| C08ST | hi | stereo | 40.3% | 5/5 | channel | — | 6/6 | 6/6 |
| C09EN | en | mono | 10.9% | 5/5 | 100.0% | ✅ | 7/7 | 7/7 |
| C09HI | hi | mono | 48.5% | 5/5 | 100.0% | ✅ | 6/6 | 6/6 |
| C09ST | en | stereo | 5.4% | 4/4 | channel | — | 7/7 | 7/7 |
| C10EN | en | mono | 30.0% | 6/6 | 100.0% | ✅ | 7/7 | 7/7 |
| C10HI | hi | mono | 44.6% | 5/5 | 100.0% | ✅ | 6/6 | 6/6 |
| C10ST | hi | stereo | 48.3% | 5/5 | channel | — | 7/7 | 7/7 |
