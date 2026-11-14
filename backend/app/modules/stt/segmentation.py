"""Provider-independent transcript building (doc 03 §6 steps 2–3).

- Numeric tokens: digits, ₹/Rs/%, English number words, Hindi number words in Devanagari, and romanised Hindi number
  words when the language is not English (romanised "do"/"ek" are ordinary English words otherwise).
- Segments split at a speaker change, a silence gap > 1.2 s, or 30 s of length.
- asr_avg_conf / asr_min_numeric_conf are computed from word confidences; None when no confidences exist.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from app.core.ids import uuid7
from app.modules.stt.base import TranscriptSegment, WordToken

SPLIT_GAP_MS = 1200
MAX_SEGMENT_MS = 30_000

_EN_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty",
    "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand", "lakh", "lakhs", "lac", "lacs", "crore",
    "crores", "million", "billion", "half", "quarter", "percent", "nil",
}
# Romanised Hindi number words (doc 04 §6 vocabulary). Only counted when the language is not English.
_HI_ROMAN_NUMBER_WORDS = {
    "ek", "do", "teen", "char", "chaar", "paanch", "panch", "chhe", "che", "saat", "aath", "nau", "das", "gyarah",
    "barah", "baarah", "bees", "pachchis", "pachees", "tees", "chalis", "pachas", "pachaas", "sau", "hazaar", "hazar",
    "lakh", "crore", "karod", "dedh", "dhai", "sava", "saadhe", "sadhe", "paune", "pratishat", "taka",
}
_HI_DEVANAGARI_NUMBER_WORDS = {
    "शून्य", "एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "छः", "सात", "आठ", "नौ", "दस", "बीस", "पचास", "सौ",
    "हज़ार", "हजार", "लाख", "करोड़", "करोड", "डेढ़", "ढाई", "सवा", "साढ़े", "पौने", "प्रतिशत",
}
_HAS_DIGIT = re.compile(r"[0-9०-९]")
# Only punctuation is stripped: Devanagari vowel signs (e.g. the "ो" in दो) are combining marks that \w does not match.
_PUNCT = ".,!?;:\"'()[]{}…–—-।॥“”‘’"


def is_numeric_token(text: str, language: str | None = None) -> bool:
    t = text.strip()
    if not t:
        return False
    if _HAS_DIGIT.search(t) or "₹" in t or "%" in t:
        return True
    core = t.strip(_PUNCT).lower()
    if core in ("rs", "inr"):
        return True
    if core in _EN_NUMBER_WORDS or core in _HI_DEVANAGARI_NUMBER_WORDS:
        return True
    return core in _HI_ROMAN_NUMBER_WORDS and not (language or "").lower().startswith("en")


def logprob_to_confidence(logprob: float | None) -> float | None:
    """doc 03 §4: confidence normalised to 0..1 as exp(logprob); None when the provider gives nothing."""
    if logprob is None:
        return None
    return max(0.0, min(1.0, math.exp(logprob)))


@dataclass
class RawWord:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None
    speaker_label: str | None


def build_segments(words: list[RawWord], *, session_id: uuid.UUID, transcript_revision: int,
                   channel: Literal["mono", "L", "R"], language: str | None) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    current: list[RawWord] = []

    def flush() -> None:
        if not current:
            return
        tokens = [WordToken(text=w.text, start_ms=w.start_ms, end_ms=w.end_ms, confidence=w.confidence,
                            speaker_label=w.speaker_label, is_numeric=is_numeric_token(w.text, language))
                  for w in current]
        confs = [t.confidence for t in tokens if t.confidence is not None]
        numeric = [t.confidence for t in tokens if t.is_numeric]
        segments.append(TranscriptSegment(
            id=uuid7(), session_id=session_id, transcript_revision=transcript_revision, channel=channel,
            speaker_label=current[0].speaker_label, text=" ".join(t.text for t in tokens),
            start_ms=current[0].start_ms, end_ms=current[-1].end_ms, words=tokens, language=language,
            asr_avg_conf=sum(confs) / len(confs) if confs else None,
            # None if any numeric word lacks a confidence, so the ASR gate treats it as low (doc 08 §5).
            asr_min_numeric_conf=(min(numeric) if numeric and None not in numeric else None)))  # type: ignore[type-var]
        current.clear()

    for w in words:
        if current and (w.speaker_label != current[-1].speaker_label
                        or w.start_ms - current[-1].end_ms > SPLIT_GAP_MS
                        or w.end_ms - current[0].start_ms > MAX_SEGMENT_MS):
            flush()
        current.append(w)
    flush()
    return segments
