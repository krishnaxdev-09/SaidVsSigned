"""doc 03 §6 steps 2–3: numeric tokens, confidence normalisation, segmentation."""

import math
import uuid

import pytest

from app.modules.stt.segmentation import RawWord, build_segments, is_numeric_token, logprob_to_confidence


@pytest.mark.parametrize("tok", ["10", "36,000", "1%", "₹5000", "Rs.", "INR", "lakh", "crore", "one", "Thirty",
                                 "percent", "१०", "दो", "लाख", "प्रतिशत", "1.5"])
def test_numeric_tokens(tok: str) -> None:
    assert is_numeric_token(tok, "en")


@pytest.mark.parametrize("tok", ["fee", "Hello,", "insurance", "do", "ek", ""])
def test_non_numeric_in_english(tok: str) -> None:
    assert not is_numeric_token(tok, "en")


@pytest.mark.parametrize("tok", ["do", "dedh", "dhai", "hazaar", "pratishat", "sau"])
def test_romanised_hindi_numbers_count_outside_english(tok: str) -> None:
    assert is_numeric_token(tok, "hi") and is_numeric_token(tok, None)


def test_confidence_from_logprob() -> None:
    assert logprob_to_confidence(None) is None
    assert logprob_to_confidence(0.0) == 1.0
    assert logprob_to_confidence(-0.5) == pytest.approx(math.exp(-0.5))


def _w(text: str, a: int, b: int, spk: str = "speaker_0", conf: float | None = 0.9) -> RawWord:
    return RawWord(text=text, start_ms=a, end_ms=b, confidence=conf, speaker_label=spk)


def _seg(words: list[RawWord]):  # type: ignore[no-untyped-def]
    return build_segments(words, session_id=uuid.uuid4(), transcript_revision=1, channel="mono", language="en")


def test_split_on_speaker_change_and_gap() -> None:
    segs = _seg([_w("Fee", 0, 300), _w("is", 350, 500), _w("ok?", 600, 900, "speaker_1"),
                 _w("Yes", 950, 1100, "speaker_1"), _w("later", 2400, 2600, "speaker_1")])  # 1.3 s gap
    assert [(g.speaker_label, g.text) for g in segs] == [("speaker_0", "Fee is"), ("speaker_1", "ok? Yes"),
                                                         ("speaker_1", "later")]
    assert (segs[0].start_ms, segs[0].end_ms) == (0, 500)


def test_gap_of_exactly_limit_does_not_split() -> None:
    assert len(_seg([_w("a", 0, 100), _w("b", 1300, 1400)])) == 1


def test_max_segment_length_30s() -> None:
    words = [_w(f"w{i}", i * 1000, i * 1000 + 900) for i in range(40)]
    segs = _seg(words)
    assert len(segs) == 2 and all(g.end_ms - g.start_ms <= 30_000 for g in segs)


def test_confidence_aggregates() -> None:
    g = _seg([_w("fee", 0, 100, conf=0.9), _w("10", 150, 300, conf=0.8), _w("%", 310, 400, conf=0.95)])[0]
    assert g.asr_avg_conf == pytest.approx((0.9 + 0.8 + 0.95) / 3)
    assert g.asr_min_numeric_conf == pytest.approx(0.8)
    assert [w.is_numeric for w in g.words] == [False, True, True]


def test_missing_confidence_means_none() -> None:
    g = _seg([_w("fee", 0, 100, conf=None), _w("10", 150, 300, conf=None)])[0]
    assert g.asr_avg_conf is None and g.asr_min_numeric_conf is None
    h = _seg([_w("10", 0, 100, conf=0.9), _w("20", 150, 300, conf=None)])[0]
    assert h.asr_min_numeric_conf is None  # a numeric word without confidence counts as low (doc 08 §5)
