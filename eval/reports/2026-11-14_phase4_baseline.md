# Phase 4 evaluation baseline — 2026-11-14

> Synthetic, offline gate perturbation check. This report does not measure real-call accuracy.

- Threshold configuration: `backend/config/thresholds.yaml` (`0.2.2-initial`, sha256 `be20bf05194c`)
- Perturbations: 6
- Low-confidence / disallowed perturbations becoming CONFLICT: **0** (target 0)
- Result: **PASS**

## Cases

| Perturbation | Expected | Result |
|---|---|---|
| low numeric ASR confidence | not CONFLICT | `UNCLEAR` |
| missing numeric ASR confidence | not CONFLICT | `UNCLEAR` |
| low semantic ASR confidence | not CONFLICT | `UNCLEAR` |
| speaker not confirmed | not CONFLICT | `UNCLEAR` |
| quote not confirmed | not CONFLICT | `UNCLEAR` |
| Hindi numeric claim | not CONFLICT | `UNCLEAR` |

## Existing measured reports

- E2/E3/E10 and extraction: `2026-11-14_phase1_E2_E3_E10_extraction.md`
- OCR: `2026-11-14_E5_ocr.md`
- Retrieval: `2026-11-14_E6_retrieval.md`
- Contract confusion matrix: `2026-11-14_T2.6_contract_confusion.md`
- Ask routing and citations: `2026-11-14_T3.4_ask.md`

## Still required before T4.1 can be marked complete

- Provider-backed full evaluation on the Phase 4 perturbation datasets.
- Threshold calibration report for ASR, OCR, retrieval, and NLI thresholds.
- End-to-end batch latency measurement and clarification-coverage measurement.
- Real-user UX evidence remains gated by HC-7/HC-8 and must not use customer data.
