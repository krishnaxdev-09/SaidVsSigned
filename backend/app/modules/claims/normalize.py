"""Deterministic claim-value normaliser — doc 04 §6 (versioned). T1.5.

The LLM copies `raw_text` verbatim and never supplies numbers; this module alone fills `value`, `value_low`,
`value_high` and the derived labels (comparator, period, rate basis/type, GST). If anything is ambiguous,
incomplete or unsupported, the value stays unparsed (`value=None`, `normaliser_version=None`) and the verification
gate reports UNCLEAR(value_unparsed) (doc 08 §5). It never guesses.

Covered (doc 04 §6):
- digits, decimals, Indian (1,50,000) and western (150,000) grouping; Devanagari digits ०–९;
- English number words; Hindi number words in Latin and Devanagari script (romanised Hindi only when the language is
  not English, because "do"/"ek" are ordinary English words);
- multipliers hundred/sau, thousand/hazaar/k, lakh/lac/L, crore/cr, million;
- dedh = 1.5, dhai = 2.5, sava = +0.25, saadhe = +0.5, paune = −0.25 (also before a multiplier: "sava lakh");
- zero words for fee/charge fields: zero, no, nil, free, nothing, koi nahi, bilkul nahi, and (user-approved extension,
  2026-11-14) exactly "without charge" / "without any charge" (→ 0). A zero expression next to a number ("free after
  2 years") is ambiguous and stays unparsed;
- units: %, percent, pratishat, taka → percent; per month/mahina → monthly; saal/years → months for tenure;
- rate basis (interest only): flat → flat; reducing/diminishing/on balance → reducing. "Fixed" is a rate type, never
  a basis; "flat" on a fee is not a basis;
- ranges ("10 to 12", "10-12", "10 se 12", "between 10 and 12", "2 or 3") → value_low / value_high;
- comparators (up to / at least / around) and GST inclusion ("+ GST" / "including GST").
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from app.schemas.claims import ExtractedValue

NORMALISER_VERSION = "norm-1.0"

D = Decimal

# ---- vocabularies ------------------------------------------------------------------------------------------------------

EN_UNITS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
# Romanised Hindi 0–20 and common round numbers. Words outside this list are unsupported (left unparsed).
HI_UNITS: dict[str, int] = {
    "shunya": 0, "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "chhah": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "baarah": 12, "terah": 13, "chaudah": 14,
    "pandrah": 15, "solah": 16, "satrah": 17, "atharah": 18, "unnis": 19, "bees": 20, "pachchis": 25, "pachees": 25,
    "tees": 30, "chalis": 40, "chaalis": 40, "pachas": 50, "pachaas": 50, "saath": 60, "sattar": 70,
    "pachattar": 75, "assi": 80, "nabbe": 90,
}
DEV_WORDS: dict[str, str] = {  # Devanagari word -> romanised token
    "शून्य": "shunya", "एक": "ek", "दो": "do", "तीन": "teen", "चार": "char", "पांच": "paanch", "पाँच": "paanch",
    "छह": "chhe", "छः": "chhe", "सात": "saat", "आठ": "aath", "नौ": "nau", "दस": "das", "ग्यारह": "gyarah",
    "बारह": "barah", "तेरह": "terah", "चौदह": "chaudah", "पंद्रह": "pandrah", "सोलह": "solah", "सत्रह": "satrah",
    "अठारह": "atharah", "उन्नीस": "unnis", "बीस": "bees", "पच्चीस": "pachchis", "तीस": "tees", "चालीस": "chalis",
    "पचास": "pachas", "साठ": "saath", "सत्तर": "sattar", "पचहत्तर": "pachattar", "अस्सी": "assi", "नब्बे": "nabbe",
    "सौ": "sau", "हज़ार": "hazaar", "हजार": "hazaar", "लाख": "lakh", "करोड़": "crore", "करोड": "crore",
    "डेढ़": "dedh", "डेढ": "dedh", "ढाई": "dhai", "सवा": "sava", "साढ़े": "saadhe", "साढे": "saadhe", "पौने": "paune",
    "प्रतिशत": "pratishat", "रुपये": "rupees", "रुपए": "rupees", "रुपया": "rupees", "महीने": "mahine",
    "महीना": "mahina", "साल": "saal", "वर्ष": "saal", "कोई": "koi", "नहीं": "nahi", "बिल्कुल": "bilkul",
    "बिलकुल": "bilkul", "तक": "tak", "से": "se", "या": "ya", "लगभग": "lagbhag", "करीब": "kareeb",
    "प्रति": "prati", "हर": "har", "सालाना": "saalana",
}
DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_DEV_WORDS = {unicodedata.normalize("NFC", k): v for k, v in DEV_WORDS.items()}

MULTIPLIERS: dict[str, int] = {
    "hundred": 100, "sau": 100, "thousand": 1_000, "hazaar": 1_000, "hazar": 1_000, "k": 1_000,
    "lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "l": 100_000,
    "million": 1_000_000, "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000, "karod": 10_000_000,
}
FIXED_VALUES: dict[str, D] = {"dedh": D("1.5"), "dhai": D("2.5"), "dhaai": D("2.5"), "adhai": D("2.5")}
MODIFIERS: dict[str, D] = {"sava": D("0.25"), "sawa": D("0.25"), "saadhe": D("0.5"), "sadhe": D("0.5"),
                           "saade": D("0.5"), "paune": D("-0.25"), "pone": D("-0.25")}
HI_ONLY = set(HI_UNITS) | {"sau", "hazaar", "hazar", "karod"} | set(FIXED_VALUES) | set(MODIFIERS)

PERCENT_WORDS = {"%", "percent", "pct", "pratishat", "taka"}
YEAR_WORDS = {"year", "years", "yr", "yrs", "saal", "sal", "varsh", "baras"}
MONTH_WORDS = {"month", "months", "mahina", "mahine", "mahino", "mahinon"}
RANGE_CONNECTORS = {"to", "se", "-", "–", "or", "ya"}
NEGATIONS = {"not", "nahi", "never"}  # "na" is a Hindi question tag ("zero fee na?"), not a negation
# doc 04 §6 zero synonyms (fee/charge claims only); multi-word entries are matched as phrases.
ZERO_WORDS = {"zero", "no", "nil", "free", "nothing"}  # exactly doc 04 §6
ZERO_PHRASES = ("koi nahi", "bilkul nahi",
                "without charge", "without any charge")  # extension approved by the user 2026-11-14 (doc 04 §6)

# matched as phrases in the value's local window, first match wins
_COMPARATORS: tuple[tuple[str, str], ...] = (
    ("not more than", "lte"), ("no more than", "lte"), ("not exceeding", "lte"), ("up to", "lte"), ("upto", "lte"),
    ("at most", "lte"), ("maximum", "lte"), ("max", "lte"), ("se zyada nahi", "lte"), ("not less than", "gte"),
    ("at least", "gte"), ("minimum", "gte"), ("min", "gte"), ("no less than", "gte"), ("kam se kam", "gte"),
    ("approximately", "approx"), ("approx", "approx"), ("around", "approx"), ("about", "approx"),
    ("roughly", "approx"), ("nearly", "approx"), ("lagbhag", "approx"), ("kareeb", "approx"), ("takreeban", "approx"),
    ("~", "approx"), ("tak", "lte"),
)
_PERIODS: tuple[tuple[str, str], ...] = (
    ("per annum", "annual"), ("per_annum", "annual"), ("per year", "annual"), ("a year", "annual"),
    ("yearly", "annual"), ("annually", "annual"), ("annual", "annual"), ("saalana", "annual"), ("salana", "annual"),
    ("har saal", "annual"), ("saal ka", "annual"), ("per saal", "annual"), ("per month", "monthly"),
    ("per_month", "monthly"), ("a month", "monthly"), ("monthly", "monthly"), ("har mahine", "monthly"),
    ("mahine ka", "monthly"), ("per mahina", "monthly"), ("prati mahina", "monthly"), ("/month", "monthly"),
    ("one time", "one_time"), ("one-time", "one_time"), ("onetime", "one_time"), ("ek baar", "one_time"),
    ("per instance", "per_instance"), ("each time", "per_instance"), ("har baar", "per_instance"),
    ("per bounce", "per_instance"),
)
FEE_LIKE_FIELDS = {"processing_fee", "prepayment_charge", "penal_charge", "other_charge", "insurance_bundled"}


# ---- tokenising --------------------------------------------------------------------------------------------------------

_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+(?:_[a-z]+)?|[ऀ-ॿ]+|%|₹|~|–|-|/")


def _prepare(text: str) -> str:
    t = unicodedata.normalize("NFC", text).translate(DEV_DIGITS).lower()
    t = t.replace("/-", " ").replace("p.a.", " per_annum ").replace("p.a", " per_annum ")
    t = t.replace("p.m.", " per_month ").replace("rs.", " rs ")
    t = re.sub(r"(?<=\d)(k|l|lac|lacs|lakh|lakhs|cr|crore|crores|%)(?![a-z])", r" \1", t)  # 36k, 10L, 2cr, 2%
    # English hyphenated compounds ("thirty-six") are one number, not a range; Hindi "do-teen" stays a range.
    t = re.sub(r"(?<![a-z])(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)-(one|two|three|four|five|six|seven"
               r"|eight|nine)(?![a-z])", r"\1 \2", t)
    # Devanagari words map as whole tokens (a substring map would turn "से" inside "सेवा" into "se").
    return re.sub(r"[\u0900-\u097F]+", lambda m: f" {_DEV_WORDS.get(m.group(0), m.group(0))} ", t)


def tokens(text: str) -> list[str]:
    return [m.group(0).rstrip(",") for m in _TOKEN.finditer(_prepare(text))]


def _is_hindi_language(language: str | None) -> bool:
    return not (language or "").lower().startswith("en")


def _number_token(tok: str, hindi: bool) -> bool:
    if tok[0].isdigit():
        return True
    if tok in EN_UNITS or tok in MULTIPLIERS or tok in ("point", "half"):
        return hindi or tok not in HI_ONLY
    return hindi and (tok in HI_UNITS or tok in FIXED_VALUES or tok in MODIFIERS)


# ---- numbers -----------------------------------------------------------------------------------------------------------

_INDIAN = re.compile(r"^\d{1,2}(,\d{2})*,\d{3}(\.\d+)?$")
_WESTERN = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")


def parse_digits(tok: str) -> D | None:
    if "," in tok and not (_INDIAN.match(tok) or _WESTERN.match(tok)):
        return None  # e.g. 1,5,00 or 15,00: malformed grouping, do not guess
    try:
        return D(tok.replace(",", ""))
    except InvalidOperation:
        return None


def _unit_value(tok: str, hindi: bool) -> int | None:
    if tok in EN_UNITS:
        return EN_UNITS[tok]
    if hindi and tok in HI_UNITS:
        return HI_UNITS[tok]
    return None


def eval_phrase(toks: list[str], hindi: bool) -> tuple[D, bool] | None:
    """Value of one number phrase and whether it used a multiplier (lakh, thousand, ...). None if malformed."""
    if "point" in toks:  # "one point five", "8 point 75"
        i = toks.index("point")
        whole = eval_phrase(toks[:i], hindi) if toks[:i] else (D(0), False)
        frac = toks[i + 1:]
        if whole is None or not frac or whole[1]:
            return None
        digits = []
        for f in frac:
            if f.isdigit():
                digits.append(f)
            elif (v := _unit_value(f, hindi)) is not None and v < 10:
                digits.append(str(v))
            else:
                return None
        return whole[0] + D("0." + "".join(digits)), False
    total, current = D(0), None
    modifier: D | None = None
    used_mult, last_big = False, None
    i = 0
    while i < len(toks):
        tok = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if tok[0].isdigit() or (v := _unit_value(tok, hindi)) is not None or tok in FIXED_VALUES:
            if tok[0].isdigit():
                val = parse_digits(tok)
                if val is None:
                    return None
            elif tok in FIXED_VALUES:
                val = FIXED_VALUES[tok]
            else:
                val = D(v)  # type: ignore[arg-type]
            if modifier is not None:
                if tok in FIXED_VALUES:
                    return None
                val, modifier = val + modifier, None
            if current is None:
                current = val
            elif tok[0].isalpha() and current >= 20 and current % 100 != 0 and current % 10 == 0 and val < 10:
                current += val  # twenty five
            elif tok[0].isalpha() and current >= 100 and current % 100 == 0 and val < 100:
                current += val  # one hundred (and) five
            else:
                return None  # two numbers side by side: ambiguous
        elif tok in MODIFIERS:
            if modifier is not None or current is not None:
                return None
            modifier = MODIFIERS[tok]
        elif tok == "half":
            current = (current or D(0)) + D("0.5")
        elif tok == "a":
            if nxt in MULTIPLIERS and current is None:
                current = D(1)  # "a lakh"
            elif nxt != "half":  # "(two and) a half"
                return None
        elif tok == "and":
            pass
        elif tok in MULTIPLIERS:
            m = MULTIPLIERS[tok]
            if current is None:
                if modifier is None:
                    return None
                current, modifier = D(1) + modifier, None  # "sava lakh" = 1.25 lakh
            used_mult = True
            if m == 100:
                current = current * 100
            else:
                if last_big is not None and m >= last_big:
                    return None  # "five lakh two crore": out of order
                last_big = m
                total += current * m
                current = None
        else:
            return None
        i += 1
    if modifier is not None or (current is None and total == 0 and not used_mult):
        return None
    return total + (current or D(0)), used_mult


def _phrases(toks: list[str], hindi: bool) -> list[tuple[int, int]]:
    """Maximal runs of number tokens. 'and' joins only after a multiplier or before '(a) half'; 'a' only before a
    multiplier or 'half'."""
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(toks):
        if _number_token(toks[i], hindi) or (toks[i] == "a" and i + 1 < len(toks) and toks[i + 1] in (*MULTIPLIERS, "half")):
            j = i + 1
            while j < len(toks):
                t = toks[j]
                if _number_token(t, hindi):
                    j += 1
                elif t == "and" and j + 1 < len(toks) and (
                        toks[j - 1] in MULTIPLIERS or toks[j + 1] == "half"
                        or (toks[j + 1] == "a" and j + 2 < len(toks) and toks[j + 2] == "half")):
                    j += 1
                elif t == "a" and j + 1 < len(toks) and toks[j + 1] == "half":
                    j += 1
                else:
                    break
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


# ---- labels from the value's local window ------------------------------------------------------------------------------

def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text) is not None


_CUT = re.compile(r"[.;:!?।,]|\d|(?<![a-z])(?:and|aur|but|lekin)(?![a-z])", re.IGNORECASE)


def local_windows(raw_text: str, context: str | None) -> tuple[str, str]:
    """(left, right): raw_text with the quote text just before / just after it, each cut at the nearest comma,
    conjunction, other number or sentence break. Comparators are read on the left ("up to 2%"); GST, period and
    rate basis/type on the right ("2% + GST"), so labels never leak from one value to another."""
    raw = _prepare(raw_text)
    idx = context.find(raw_text) if context else -1
    if idx < 0:
        return raw, raw
    before, after = context[:idx], context[idx + len(raw_text):]  # type: ignore[index]
    b = list(_CUT.finditer(before))
    a = _CUT.search(after)
    left = before[b[-1].end():] if b else before
    right = after[: a.start()] if a else after
    return _prepare(left[-40:] + " " + raw_text), _prepare(raw_text + " " + right[:40])


def comparator_of(window: str) -> str:
    for phrase, comp in _COMPARATORS:
        if _has_phrase(window, phrase):
            return comp
    return "eq"


def period_of(window: str) -> str:
    for phrase, period in _PERIODS:
        if _has_phrase(window, phrase):
            return period
    return "unspecified"


def includes_gst_of(window: str) -> bool | None:
    if not _has_phrase(window, "gst") and "जीएसटी" not in window:
        return None
    if any(_has_phrase(window, p) for p in ("including gst", "inclusive of gst", "incl gst", "gst included",
                                            "gst inclusive", "with gst", "gst sahit", "gst samet")):
        return True
    if any(_has_phrase(window, p) for p in ("plus gst", "gst extra", "excluding gst", "exclusive of gst",
                                            "gst alag", "+ gst", "+gst", "gst additional")):
        return False
    return None


def rate_labels(window: str, field: str) -> tuple[str | None, str | None]:
    """(rate_basis, rate_type). Basis only for interest rates; 'fixed' is a type, never a basis."""
    if not field.startswith("interest"):
        return None, None
    flat = _has_phrase(window, "flat")
    reducing = any(_has_phrase(window, p) for p in ("reducing", "diminishing", "on balance", "reducing balance"))
    basis = "unspecified" if flat == reducing else ("flat" if flat else "reducing")
    fixed = _has_phrase(window, "fixed")
    floating = _has_phrase(window, "floating") or _has_phrase(window, "variable")
    rtype = "unspecified" if fixed == floating else ("fixed" if fixed else "floating")
    return basis, rtype


# ---- public API --------------------------------------------------------------------------------------------------------

def _plain(d: D | None) -> D | None:
    """Plain notation for display and JSON: 1E+6 -> 1000000, 7.75E+3 -> 7750, 1.500 -> 1.5."""
    if d is None:
        return None
    return D(format(d.normalize(), "f"))


def _parsed(ev: ExtractedValue, labels: dict[str, object], **values: D | None) -> ExtractedValue:
    return ev.model_copy(update={**labels, **{k: _plain(v) for k, v in values.items()},
                                 "normaliser_version": NORMALISER_VERSION})


def _unparsed(ev: ExtractedValue, **labels: object) -> ExtractedValue:
    return ev.model_copy(update={"value": None, "value_low": None, "value_high": None, "normaliser_version": None,
                                 **labels})


def normalise(ev: ExtractedValue, *, context: str | None = None, language: str | None = None) -> ExtractedValue:
    """Fill value fields from `ev.raw_text` (doc 04 §6). Anything the LLM put in value fields is discarded."""
    hindi = _is_hindi_language(language)
    left, right = local_windows(ev.raw_text, context)
    basis, rtype = rate_labels(right, ev.field)
    labels: dict[str, object] = {"comparator": comparator_of(left + " " + right), "includes_gst": includes_gst_of(right),
                                 "rate_basis": basis, "rate_type": rtype}
    toks = tokens(ev.raw_text)
    text = " ".join(toks)
    quantity = ev.quantity
    tenure = quantity in ("months", "years")
    labels["period"] = "unspecified" if tenure else period_of(right)
    if quantity == "years":
        labels["quantity"] = "months"  # doc 04 §6: years are converted to months

    has_percent = any(t in PERCENT_WORDS for t in toks)
    has_time_unit = any(t in YEAR_WORDS | MONTH_WORDS for t in toks)
    if has_percent and quantity != "percent":
        return _unparsed(ev, **labels)  # a % on an amount or tenure contradicts the quantity
    if quantity == "percent" and has_time_unit and labels["period"] == "unspecified":
        return _unparsed(ev, **labels)  # e.g. "2% 3 years": a time unit that is not a recognised period
    # Remove recognised comparator/period phrases first: "one time", "ek baar", "kam se kam" contain number or range
    # words that are not part of the value.
    for phrase in sorted([p for p, _ in (*_COMPARATORS, *_PERIODS)], key=len, reverse=True):
        if " " in phrase or "_" in phrase:
            text = re.sub(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", " ", text)
    words = text.split()
    zero_phrase = any(_has_phrase(text, p) for p in ZERO_PHRASES)
    if quantity != "boolean" and any(w in NEGATIONS for w in words) and not zero_phrase:
        return _unparsed(ev, **labels)

    spans = _phrases(words, hindi)
    fee_like = ev.field in FEE_LIKE_FIELDS or "fee" in ev.field or "charge" in ev.field
    if fee_like and spans and (zero_phrase or any(w in ZERO_WORDS - {"zero"} for w in words)):
        return _unparsed(ev, **labels)  # "free after 2 years", "without any charge after 6 EMIs": ambiguous
    if not spans:
        if quantity == "boolean":
            if any(w in ("no", "nahi") for w in words) or zero_phrase:
                return _parsed(ev, labels, value=D(0))
            if any(w in ("yes", "haan", "ha") for w in words):
                return _parsed(ev, labels, value=D(1))
        elif fee_like and (any(w in ZERO_WORDS for w in words) or zero_phrase):
            return _parsed(ev, {**labels, "comparator": "eq"}, value=D(0))
        return _unparsed(ev, **labels)

    def unit_convert(v: D) -> D:
        if tenure and any(w in YEAR_WORDS for w in words):
            return v * 12
        return v

    if quantity == "boolean":
        return _unparsed(ev, **labels)
    if len(spans) == 1:
        res = eval_phrase(words[spans[0][0]:spans[0][1]], hindi)
        if res is None:
            return _unparsed(ev, **labels)
        if tenure and quantity == "years" and not any(w in YEAR_WORDS | MONTH_WORDS for w in words):
            return _parsed(ev, labels, value=res[0] * 12)
        return _parsed(ev, labels, value=unit_convert(res[0]))
    if len(spans) == 2:
        (a0, a1), (b0, b1) = spans
        between = words[a1:b0]
        is_range = (len(between) == 1 and between[0] in RANGE_CONNECTORS) or (
            between == ["and"] and a0 > 0 and words[a0 - 1] == "between")
        if is_range:
            lo, hi = eval_phrase(words[a0:a1], hindi), eval_phrase(words[b0:b1], hindi)
            if lo is None or hi is None:
                return _unparsed(ev, **labels)
            lo_v, hi_v = lo[0], hi[0]
            if not lo[1] and hi[1]:  # "10 to 12 lakh": the multiplier applies to both bounds
                mult = next(MULTIPLIERS[w] for w in reversed(words[b0:b1]) if w in MULTIPLIERS)
                lo_v = lo_v * mult
            lo_v, hi_v = unit_convert(lo_v), unit_convert(hi_v)
            if lo_v >= hi_v:
                return _unparsed(ev, **labels)
            return _parsed(ev, {**labels, "comparator": "eq"}, value=None, value_low=lo_v, value_high=hi_v)
    return _unparsed(ev, **labels)  # several unrelated numbers: ambiguous
