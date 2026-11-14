"""T1.5 normaliser table (doc 04 §6, §11: ≥ 300 cases incl. Hinglish/Devanagari).

Each case: (raw_text, quantity, field, language, expected). `expected` is None for "must stay unparsed" or a dict of
the fields to check (value, lo, hi, q, cmp, period, basis, type, gst). Groups: digits & grouping, English words,
Hindi (Latin + Devanagari), fractions, multipliers, units & tenure, zero words, ranges, comparators, GST, rate basis
vs type, ambiguous/unsupported, mixed language.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.modules.claims.normalize import EN_UNITS, HI_UNITS, NORMALISER_VERSION, normalise
from app.schemas.claims import ExtractedValue

P, A, M, Y, C, B = "percent", "amount_inr", "months", "years", "count", "boolean"
FEE, RATE, EMI, AMT, TEN, INS, OTH, PEN, PRE = ("processing_fee", "interest_rate", "emi", "loan_amount", "tenure",
                                               "insurance_bundled", "other_charge", "penal_charge", "prepayment_charge")
EN, HI = "en", "hi"
X = None  # must stay unparsed


def v(value: Any, **kw: Any) -> dict[str, Any]:
    return {"value": Decimal(str(value)), **kw}


def rng(lo: Any, hi: Any, **kw: Any) -> dict[str, Any]:
    return {"value": None, "lo": Decimal(str(lo)), "hi": Decimal(str(hi)), **kw}


CASES: list[tuple[str, str, str, str | None, dict[str, Any] | None]] = [
    # ---- digits, decimals, grouping (positive / boundary / negative) ----
    ("1%", P, FEE, EN, v(1)), ("2 %", P, FEE, EN, v(2)), ("12.5%", P, RATE, EN, v("12.5")),
    ("0.5%", P, FEE, EN, v("0.5")), ("0%", P, FEE, EN, v(0)), ("100%", P, FEE, EN, v(100)),
    ("8.75 percent", P, RATE, EN, v("8.75")), ("14.50% p.a.", P, RATE, EN, v("14.50", period="annual")),
    ("36,000", A, EMI, EN, v(36000)), ("36000", A, EMI, EN, v(36000)), ("Rs. 36,000", A, EMI, EN, v(36000)),
    ("Rs 36,000/-", A, EMI, EN, v(36000)), ("₹36,000", A, EMI, EN, v(36000)), ("INR 36,000", A, EMI, EN, v(36000)),
    ("1,50,000", A, AMT, EN, v(150000)), ("12,50,000", A, AMT, EN, v(1250000)), ("1,00,00,000", A, AMT, EN, v(10000000)),
    ("150,000", A, AMT, EN, v(150000)), ("1,500,000", A, AMT, EN, v(1500000)), ("1,000", A, OTH, EN, v(1000)),
    ("4,999", A, FEE, EN, v(4999)), ("2,360.50", A, FEE, EN, v("2360.50")), ("Rs. 7,750 per month", A, EMI, EN, v(7750, period="monthly")),
    ("1,5,00", A, AMT, EN, X), ("15,00", A, AMT, EN, X), ("1,50,00", A, AMT, EN, X), ("12,3456", A, AMT, EN, X),
    ("1,500,00", A, AMT, EN, X), ("5000", A, OTH, EN, v(5000)), ("590 per instance", A, OTH, EN, v(590, period="per_instance")),
    ("₹500", A, OTH, EN, v(500)), ("500 rupees", A, OTH, EN, v(500)), ("rupees 500", A, OTH, EN, v(500)),
    ("36k", A, EMI, EN, v(36000)), ("36 K", A, EMI, EN, v(36000)), ("1.5k", A, FEE, EN, v(1500)),
    ("10L", A, AMT, EN, v(1000000)), ("10 lac", A, AMT, EN, v(1000000)), ("10 lacs", A, AMT, EN, v(1000000)),
    ("2cr", A, AMT, EN, v(20000000)), ("2.5 crore", A, AMT, EN, v(25000000)), ("1.5 lakh", A, AMT, EN, v(150000)),
    ("10 lakh", A, AMT, EN, v(1000000)), ("10 lakhs", A, AMT, EN, v(1000000)), ("5 crores", A, AMT, EN, v(50000000)),
    ("36 thousand", A, EMI, EN, v(36000)), ("2 million", A, AMT, EN, v(2000000)), ("3 hundred", A, OTH, EN, v(300)),
    # ---- English words ----
    ("one percent", P, FEE, EN, v(1)), ("two percent", P, FEE, EN, v(2)), ("twelve percent", P, RATE, EN, v(12)),
    ("ten lakh rupees", A, AMT, EN, v(1000000)), ("thirty six thousand", A, EMI, EN, v(36000)),
    ("thirty-six thousand", A, EMI, EN, v(36000)), ("forty-five", C, OTH, EN, v(45)),
    ("do-teen percent", P, FEE, HI, rng(2, 3)), ("ten-twelve percent", P, RATE, EN, rng(10, 12)),
    ("twenty five thousand", A, EMI, EN, v(25000)), ("fifteen hundred", A, OTH, EN, v(1500)),
    ("one hundred and five", A, OTH, EN, v(105)), ("two thousand and twenty", A, OTH, EN, v(2020)),
    ("one lakh twenty thousand", A, AMT, EN, v(120000)), ("two crore fifty lakh", A, AMT, EN, v(25000000)),
    ("a lakh", A, AMT, EN, v(100000)), ("one and a half lakh", A, AMT, EN, v(150000)),
    ("two and a half percent", P, FEE, EN, v("2.5")), ("half percent", P, FEE, EN, v("0.5")),
    ("one point five percent", P, FEE, EN, v("1.5")), ("eight point seven five percent", P, RATE, EN, v("8.75")),
    ("point five percent", P, FEE, EN, v("0.5")), ("zero percent", P, RATE, EN, v(0)),
    ("ninety nine", A, OTH, EN, v(99)), ("seventy", C, OTH, EN, v(70)), ("five lakh two crore", A, AMT, EN, X),
    ("lakh", A, AMT, EN, X), ("hundred", A, OTH, EN, X), ("one point", P, FEE, EN, X),
    ("one point lakh", P, FEE, EN, X), ("twenty thirty", A, OTH, EN, X), ("five twenty", A, OTH, EN, X),
    # ---- Hindi (Latin script) ----
    ("do percent", P, FEE, HI, v(2)), ("ek pratishat", P, FEE, HI, v(1)), ("teen pratishat", P, FEE, HI, v(3)),
    ("das lakh", A, AMT, HI, v(1000000)), ("pachas hazaar", A, AMT, HI, v(50000)), ("bees hazaar", A, EMI, HI, v(20000)),
    ("ek lakh pachas hazaar", A, AMT, HI, v(150000)), ("do crore", A, AMT, HI, v(20000000)), ("paanch sau", A, OTH, HI, v(500)),
    ("barah pratishat", P, RATE, HI, v(12)), ("chaudah percent", P, RATE, HI, v(14)), ("pandrah taka", P, RATE, HI, v(15)),
    ("do taka", P, FEE, HI, v(2)), ("teen hazaar", A, FEE, HI, v(3000)), ("chhe mahine", M, TEN, HI, v(6)),
    ("barah mahine", M, TEN, HI, v(12)), ("do saal", M, TEN, HI, v(24)), ("teen saal", M, TEN, HI, v(36)),
    ("paanch saal", Y, TEN, HI, v(60, q="months")), ("ek hazaar paanch sau", A, FEE, HI, v(1500)),
    ("pachattar hazaar", A, AMT, HI, v(75000)), ("nabbe hazaar", A, AMT, HI, v(90000)), ("sau rupaye", A, OTH, HI, X),
    ("das se barah pratishat", P, RATE, HI, rng(10, 12)), ("do ya teen percent", P, FEE, HI, rng(2, 3)),
    ("ek do", A, OTH, HI, X), ("chhattis hazaar", A, EMI, HI, X),  # 36 is not in the supported word list
    ("pachpan", A, OTH, HI, X), ("do percent", P, FEE, EN, X), ("ek lakh", A, AMT, EN, X),
    # ---- Devanagari ----
    ("दो प्रतिशत", P, FEE, HI, v(2)), ("एक प्रतिशत", P, FEE, HI, v(1)), ("दस लाख", A, AMT, HI, v(1000000)),
    ("दो लाख", A, AMT, HI, v(200000)), ("पचास हज़ार", A, AMT, HI, v(50000)), ("पचास हजार", A, AMT, HI, v(50000)),
    ("दो करोड़", A, AMT, HI, v(20000000)), ("दो करो\u095c", A, AMT, HI, v(20000000)),  # precomposed ड़
    ("पांच सौ", A, OTH, HI, v(500)),
    ("१५ प्रतिशत", P, RATE, HI, v(15)), ("१,५०,००० रुपये", A, AMT, HI, v(150000)), ("३६,०००", A, EMI, HI, v(36000)),
    ("१२.५%", P, RATE, HI, v("12.5")), ("०", C, OTH, HI, v(0)), ("डेढ़ लाख", A, AMT, HI, v(150000)),
    ("ढाई हज़ार", A, FEE, HI, v(2500)), ("सवा लाख", A, AMT, HI, v(125000)), ("साढ़े तीन लाख", A, AMT, HI, v(350000)),
    ("पौने दो लाख", A, AMT, HI, v(175000)), ("तीन साल", M, TEN, HI, v(36)), ("बारह महीने", M, TEN, HI, v(12)),
    ("दस से बारह प्रतिशत", P, RATE, HI, rng(10, 12)), ("कोई नहीं", A, FEE, HI, v(0)), ("बिल्कुल नहीं", A, FEE, HI, v(0)),
    ("सेवा", A, AMT, HI, X), ("सेवा शुल्क", A, FEE, HI, X), ("दो प्रतिशत नहीं", P, FEE, HI, X),
    ("लगभग दो प्रतिशत", P, FEE, HI, v(2, cmp="approx")), ("दो लाख तक", A, AMT, HI, v(200000, cmp="lte")),
    # ---- fractions: dedh, dhai, sava, saadhe, paune ----
    ("dedh lakh", A, AMT, HI, v(150000)), ("dhai lakh", A, AMT, HI, v(250000)), ("dhai percent", P, FEE, HI, v("2.5")),
    ("dedh percent", P, FEE, HI, v("1.5")), ("dedh saal", M, TEN, HI, v(18)), ("dhai saal", M, TEN, HI, v(30)),
    ("sava do percent", P, FEE, HI, v("2.25")), ("saadhe teen lakh", A, AMT, HI, v(350000)),
    ("saadhe das pratishat", P, RATE, HI, v("10.5")), ("paune do lakh", A, AMT, HI, v(175000)),
    ("paune teen percent", P, FEE, HI, v("2.75")), ("sava lakh", A, AMT, HI, v(125000)), ("sava sau", A, OTH, HI, v(125)),
    ("dedh sau", A, OTH, HI, v(150)), ("dhai hazaar", A, FEE, HI, v(2500)), ("dedh crore", A, AMT, HI, v(15000000)),
    ("paune lakh", A, AMT, HI, v(75000)), ("sadhe paanch lakh", A, AMT, HI, v(550000)), ("adhai lakh", A, AMT, HI, v(250000)),
    ("sava", A, AMT, HI, X), ("saadhe", P, FEE, HI, X), ("sava dedh", A, AMT, HI, X), ("dedh dhai", A, AMT, HI, X),
    ("dedh lakh", A, AMT, EN, X),  # romanised Hindi is not read in English-language segments
    # ---- tenure: years → months ----
    ("36 months", M, TEN, EN, v(36)), ("3 years", M, TEN, EN, v(36)), ("3 years", Y, TEN, EN, v(36, q="months")),
    ("2.5 years", M, TEN, EN, v(30)), ("24", M, TEN, EN, v(24)), ("3", Y, TEN, EN, v(36, q="months")),
    ("6 months", Y, TEN, EN, v(6, q="months")), ("240 months", M, TEN, EN, v(240)), ("20 years", M, TEN, EN, v(240)),
    ("one year", M, TEN, EN, v(12)), ("five years", M, TEN, EN, v(60)), ("48 months", M, TEN, EN, v(48)),
    ("10 yrs", M, TEN, EN, v(120)), ("1 yr", M, TEN, EN, v(12)), ("36 mahine", M, TEN, HI, v(36)),
    ("12%", M, TEN, EN, X), ("3 years 6 months", M, TEN, EN, X),  # two numbers: not combined, not guessed
    # ---- zero words (fee/charge fields only) ----
    ("zero", A, FEE, EN, v(0)), ("no processing fee", A, FEE, EN, v(0)), ("nil", A, FEE, EN, v(0)),
    ("free", A, FEE, EN, v(0)), ("nothing", A, FEE, EN, v(0)), ("koi nahi", A, FEE, HI, v(0)),
    ("bilkul nahi", A, FEE, HI, v(0)), ("koi nahi", A, PRE, HI, v(0)), ("no charges", A, PRE, EN, v(0)),
    ("no penal charges", A, PEN, EN, v(0)), ("nil", P, PEN, EN, v(0)), ("no insurance deduction", A, INS, EN, v(0)),
    ("zero fee na", A, FEE, HI, v(0)), ("free", A, OTH, EN, v(0)), ("zero processing fee", A, FEE, EN, v(0)),
    ("no", A, RATE, EN, X), ("free", P, RATE, EN, X), ("nothing", A, EMI, EN, X), ("nil", A, AMT, EN, X),
    ("koi nahi", A, AMT, HI, X), ("no", M, TEN, EN, X), ("zero", P, RATE, EN, v(0)),  # "zero" is a number word
    # ---- "without (any) charge": user-approved §6 extension (2026-11-14), fee/charge fields only ----
    ("without charge", A, PRE, EN, v(0)), ("without any charge", A, PRE, EN, v(0)),
    ("Without Any Charge", A, PRE, EN, v(0)), ("without any charge", A, FEE, EN, v(0)),
    ("without charge", P, PEN, EN, v(0)), ("without any charge", A, OTH, HI, v(0)),
    ("without any charge", A, INS, EN, v(0)), ("prepay without any charge", A, PRE, EN, v(0)),
    ("without any charge", A, AMT, EN, X), ("without any charge", P, RATE, EN, X),  # not a fee/charge field
    ("without charge", A, EMI, EN, X), ("without any charge", M, TEN, EN, X),
    ("without any charges", A, PRE, EN, X), ("without charges", A, PRE, EN, X),  # only the exact approved phrases
    ("with charge", A, PRE, EN, X), ("without", A, PRE, EN, X), ("without any", A, PRE, EN, X),
    ("without any charge after 6 EMIs", A, PRE, EN, X),  # zero expression + a number: ambiguous
    ("without charge after 2 years", A, PRE, EN, X), ("no charge after 6 months", A, PRE, EN, X),
    ("free after 2 years", A, FEE, EN, X), ("koi nahi 2%", P, FEE, HI, X),
    # ---- ranges ----
    ("10 to 12%", P, RATE, EN, rng(10, 12)), ("10-12%", P, RATE, EN, rng(10, 12)), ("10 – 12%", P, RATE, EN, rng(10, 12)),
    ("between 10 and 12 percent", P, RATE, EN, rng(10, 12)), ("2 or 3 percent", P, FEE, EN, rng(2, 3)),
    ("10 to 12 lakh", A, AMT, EN, rng(1000000, 1200000)), ("1 to 1.5 lakh", A, AMT, EN, rng(100000, 150000)),
    ("2 to 3 years", M, TEN, EN, rng(24, 36)), ("10 se 12 tak", P, RATE, HI, rng(10, 12, cmp="eq")),
    ("12 to 10%", P, RATE, EN, X), ("10 to 10%", P, RATE, EN, X), ("10 and 12 percent", P, RATE, EN, X),
    ("10, 12 percent", P, RATE, EN, X), ("2% and 3%", P, FEE, EN, X), ("1% or 2% or 3%", P, FEE, EN, X),
    # ---- comparators ----
    ("up to 2%", P, FEE, EN, v(2, cmp="lte")), ("upto 2%", P, FEE, EN, v(2, cmp="lte")),
    ("maximum 2%", P, FEE, EN, v(2, cmp="lte")), ("at most 3%", P, FEE, EN, v(3, cmp="lte")),
    ("not more than 2%", P, FEE, EN, v(2, cmp="lte")), ("no more than 2%", P, FEE, EN, v(2, cmp="lte")),
    ("at least 12%", P, RATE, EN, v(12, cmp="gte")), ("minimum 10,000", A, OTH, EN, v(10000, cmp="gte")),
    ("not less than 9%", P, RATE, EN, v(9, cmp="gte")), ("kam se kam das hazaar", A, OTH, HI, v(10000, cmp="gte")),
    ("around 2%", P, FEE, EN, v(2, cmp="approx")), ("about 36,000", A, EMI, EN, v(36000, cmp="approx")),
    ("approximately 12%", P, RATE, EN, v(12, cmp="approx")), ("roughly 10 lakh", A, AMT, EN, v(1000000, cmp="approx")),
    ("lagbhag do percent", P, FEE, HI, v(2, cmp="approx")), ("~2%", P, FEE, EN, v(2, cmp="approx")),
    ("2 lakh tak", A, AMT, HI, v(200000, cmp="lte")), ("2%", P, FEE, EN, v(2, cmp="eq")),
    ("2% maximum", P, FEE, EN, v(2, cmp="lte")), ("nearly 2%", P, FEE, EN, v(2, cmp="approx")),
    # ---- GST ----
    ("2% + GST", P, FEE, EN, v(2, gst=False)), ("2%+GST", P, FEE, EN, v(2, gst=False)),
    ("2% plus GST", P, FEE, EN, v(2, gst=False)), ("2% GST extra", P, FEE, EN, v(2, gst=False)),
    ("4,999 inclusive of GST", A, FEE, EN, v(4999, gst=True)), ("4,999 including GST", A, FEE, EN, v(4999, gst=True)),
    ("999 GST included", A, FEE, EN, v(999, gst=True)), ("2% excluding GST", P, FEE, EN, v(2, gst=False)),
    ("2% GST", P, FEE, EN, v(2, gst=None)), ("2 percent", P, FEE, EN, v(2, gst=None)), ("2% gst alag", P, FEE, HI, v(2, gst=False)),
    # ---- rate basis vs rate type ("fixed" is never "flat") ----
    ("12% flat", P, RATE, EN, v(12, basis="flat", type="unspecified")),
    ("12% flat rate", P, RATE, EN, v(12, basis="flat")), ("12% reducing", P, RATE, EN, v(12, basis="reducing")),
    ("12% on reducing balance", P, RATE, EN, v(12, basis="reducing")),
    ("12% diminishing", P, RATE, EN, v(12, basis="reducing")), ("12% on balance", P, RATE, EN, v(12, basis="reducing")),
    ("12% fixed", P, RATE, EN, v(12, basis="unspecified", type="fixed")),
    ("12% fixed rate", P, RATE, EN, v(12, basis="unspecified", type="fixed")),
    ("12% floating", P, RATE, EN, v(12, type="floating")), ("12% variable", P, RATE, EN, v(12, type="floating")),
    ("12% fixed flat", P, RATE, EN, v(12, basis="flat", type="fixed")),
    ("12% flat reducing", P, RATE, EN, v(12, basis="unspecified")),
    ("12% fixed floating", P, RATE, EN, v(12, type="unspecified")),
    ("12%", P, RATE, EN, v(12, basis="unspecified", type="unspecified")),
    ("flat 2%", P, FEE, EN, v(2, basis=None, type=None)),  # "flat" on a fee is not an interest basis
    ("fixed 999", A, FEE, EN, v(999, basis=None, type=None)),
    ("11% p.a. flat", P, RATE, EN, v(11, basis="flat", period="annual")),
    ("1.2% per month", P, RATE, EN, v("1.2", period="monthly")), ("12% per annum", P, RATE, EN, v(12, period="annual")),
    ("12% yearly", P, RATE, EN, v(12, period="annual")), ("12% saal ka", P, RATE, HI, v(12, period="annual")),
    ("2% mahine ka", P, PEN, HI, v(2, period="monthly")), ("2% per month on overdue", P, PEN, EN, v(2, period="monthly")),
    ("12% 3 years", P, RATE, EN, X),
    # ---- units contradicting the quantity ----
    ("2%", A, FEE, EN, X), ("2%", M, TEN, EN, X), ("2 percent", A, AMT, EN, X), ("36 months", P, RATE, EN, X),
    # ---- negations, vague, unsupported, empty ----
    ("not 2%", P, FEE, EN, X), ("2% nahi", P, FEE, HI, X), ("never 2%", P, FEE, EN, X),
    ("very low", P, RATE, EN, X), ("best rate", P, RATE, EN, X), ("kuch nahi", A, FEE, HI, X),
    ("few thousand", A, FEE, EN, X), ("some charges", A, OTH, EN, X), ("market rate", P, RATE, EN, X),
    ("", P, FEE, EN, X), ("   ", A, FEE, EN, X), ("%", P, FEE, EN, X), ("rupees", A, FEE, EN, X),
    ("₹", A, FEE, EN, X), ("abc", A, AMT, EN, X), ("10 20", A, AMT, EN, X), ("1 2 3", A, AMT, EN, X),
    ("12% and 10 lakh", P, RATE, EN, X), ("lakh crore", A, AMT, EN, X),
    # ---- boolean ----
    ("no", B, INS, EN, v(0)), ("yes", B, INS, EN, v(1)), ("haan", B, INS, HI, v(1)), ("nahi", B, INS, HI, v(0)),
    ("koi nahi", B, INS, HI, v(0)), ("maybe", B, INS, EN, X), ("2", B, INS, EN, X),
    # ---- mixed language / Hinglish ----
    ("do lakh rupees", A, AMT, HI, v(200000)), ("10 lakh ka loan", A, AMT, HI, v(1000000)),
    ("2% processing fee", P, FEE, HI, v(2)), ("sirf ek percent", P, FEE, HI, v(1)), ("only 1%", P, FEE, EN, v(1)),
    ("36,000 har mahine", A, EMI, HI, v(36000, period="monthly")), ("36000 per month", A, EMI, EN, v(36000, period="monthly")),
    ("EMI 36 hazaar", A, EMI, HI, v(36000)), ("teen percent flat", P, RATE, HI, v(3, basis="flat")),
    ("barah percent fixed", P, RATE, HI, v(12, type="fixed", basis="unspecified")),
    ("do lakh tak", A, AMT, HI, v(200000, cmp="lte")), ("ek baar 999", A, FEE, HI, v(999, period="one_time")),
    ("999 one time", A, FEE, EN, v(999, period="one_time")), ("har baar 590", A, OTH, HI, v(590, period="per_instance")),
    ("10 lakh se 12 lakh", A, AMT, HI, rng(1000000, 1200000)), ("dedh se do lakh", A, AMT, HI, rng(150000, 200000)),
    ("1.5 pratishat", P, FEE, HI, v("1.5")), ("ढाई percent", P, FEE, HI, v("2.5")), ("दो %", P, FEE, HI, v(2)),
    ("2 प्रतिशत", P, FEE, HI, v(2)), ("₹ दस हज़ार", A, FEE, HI, v(10000)), ("12 साल", M, TEN, HI, v(144)),
]

# Generated groups: every English and Hindi vocabulary word maps to its value (positive), and Hindi-only words are
# rejected in English segments (negative).
_GEN = [(f"{w} percent", P, FEE, EN, v(n)) for w, n in EN_UNITS.items()]
_GEN += [(f"{w} percent", P, FEE, HI, v(n)) for w, n in HI_UNITS.items()]
_GEN += [(f"{w} percent", P, FEE, EN, X) for w in HI_UNITS if w not in EN_UNITS]
_seen = {c[:4] for c in CASES}
CASES += [c for c in _GEN if c[:4] not in _seen]


def _check(r: ExtractedValue, exp: dict[str, Any] | None) -> None:
    if exp is None:
        assert r.normaliser_version is None and r.value is None and r.value_low is None and r.value_high is None, r
        return
    assert r.normaliser_version == NORMALISER_VERSION, r
    keys = {"value": "value", "lo": "value_low", "hi": "value_high", "q": "quantity", "cmp": "comparator",
            "period": "period", "basis": "rate_basis", "type": "rate_type", "gst": "includes_gst"}
    for k, attr in keys.items():
        if k in exp:
            assert getattr(r, attr) == exp[k], f"{attr}: {getattr(r, attr)!r} != {exp[k]!r}  ({r})"
    if "lo" not in exp:
        assert r.value_low is None and r.value_high is None


def test_table_has_at_least_300_cases() -> None:
    assert len(CASES) >= 300, len(CASES)
    assert len({(c[0], c[1], c[2], c[3]) for c in CASES}) == len(CASES), "duplicate cases"


@pytest.mark.parametrize(("raw", "quantity", "field", "lang", "expected"), CASES,
                         ids=[f"{i}:{c[0]!r}/{c[1]}/{c[3]}" for i, c in enumerate(CASES)])
def test_normaliser_table(raw: str, quantity: str, field: str, lang: str | None, expected: dict[str, Any] | None) -> None:
    ev = ExtractedValue(field=field, raw_text=raw or " ", quantity=quantity)  # type: ignore[arg-type]
    _check(normalise(ev, language=lang), expected)


# ---- context window: labels come only from text next to the value -----------------------------------------------------

def test_gst_from_quote_context() -> None:
    q = "The processing fee is 2% plus GST, deducted upfront."
    r = normalise(ExtractedValue(field=FEE, raw_text="2%", quantity="percent"), context=q, language=EN)
    assert r.value == 2 and r.includes_gst is False


def test_labels_do_not_leak_to_other_values() -> None:
    q = "Fee is 2% + GST and the EMI is 36,000 per month."
    emi = normalise(ExtractedValue(field=EMI, raw_text="36,000", quantity="amount_inr"), context=q, language=EN)
    fee = normalise(ExtractedValue(field=FEE, raw_text="2%", quantity="percent"), context=q, language=EN)
    assert emi.includes_gst is None and emi.period == "monthly"
    assert fee.includes_gst is False and fee.period == "unspecified"


def test_basis_from_context_never_turns_fixed_into_flat() -> None:
    q = "Interest is 12% fixed for the full tenure."
    r = normalise(ExtractedValue(field=RATE, raw_text="12%", quantity="percent"), context=q, language=EN)
    assert r.rate_type == "fixed" and r.rate_basis == "unspecified"


# ---- the LLM never supplies values or labels ------------------------------------------------------------------------

def test_llm_labels_are_replaced_by_text_derived_ones() -> None:
    ev = ExtractedValue(field=RATE, raw_text="12% fixed", quantity="percent", rate_basis="flat", comparator="lte",
                        period="monthly", includes_gst=True)
    r = normalise(ev, language=EN)
    assert (r.rate_basis, r.rate_type, r.comparator, r.period, r.includes_gst) == (
        "unspecified", "fixed", "eq", "unspecified", None)


def test_unparsed_value_clears_everything_numeric() -> None:
    r = normalise(ExtractedValue(field=FEE, raw_text="very low", quantity="percent"), language=EN)
    assert (r.value, r.value_low, r.value_high, r.normaliser_version) == (None, None, None, None)


@settings(max_examples=400, deadline=None)
@given(raw=st.text(min_size=1, max_size=40), quantity=st.sampled_from([P, A, M, Y, C, B]),
       field=st.sampled_from([FEE, RATE, EMI, AMT, TEN, INS]), lang=st.sampled_from([EN, HI, None]))
def test_property_never_raises_and_is_consistent(raw: str, quantity: str, field: str, lang: str | None) -> None:
    r = normalise(ExtractedValue(field=field, raw_text=raw, quantity=quantity), language=lang)  # type: ignore[arg-type]
    parsed = r.normaliser_version is not None
    has_value = r.value is not None or (r.value_low is not None and r.value_high is not None)
    assert parsed == has_value
    if r.value_low is not None:
        assert r.value is None and r.value_high is not None and r.value_low < r.value_high
    if quantity != Y:  # years are converted to months, so a second pass reads different units by design
        assert normalise(r, language=lang) == r  # idempotent on its own output


def test_synthetic_agreement_terms_are_never_wrong() -> None:
    """All 101 ground-truth terms of eval/datasets/agreements: a value is either exact or left unparsed, never wrong.
    Baseline 2026-11-14 after the approved "without (any) charge" extension: 97 exact, 4 unparsed (whole clauses with
    several numbers)."""
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[3] / "eval" / "datasets" / "agreements"
    exact = total = 0
    for f in sorted(root.glob("*/terms.yaml")):
        for term in yaml.safe_load(f.read_text(encoding="utf-8"))["terms"]:
            total += 1
            r = normalise(ExtractedValue(field=term["field"], raw_text=term["raw_text"], quantity=term["quantity"]),
                          language=EN)
            if r.value is not None:
                assert r.value == Decimal(str(term["value"])), (f.parent.name, term["raw_text"], r.value)
                exact += 1
    assert total == 101 and exact >= 97


@pytest.mark.parametrize(("raw", "q", "lang", "plain"), [
    ("10 lakh", A, EN, "1000000"), ("7,750 rupees", A, EN, "7750"), ("दस लाख", A, HI, "1000000"),
    ("5 saal", Y, HI, "60"), ("36 hazaar", A, HI, "36000"), ("8,400", A, EN, "8400"), ("1.50%", P, EN, "1.5"),
    ("dedh lakh", A, HI, "150000"), ("10 to 12 lakh", A, EN, "1000000"),
])
def test_values_are_plain_decimals(raw: str, q: str, lang: str, plain: str) -> None:
    """No scientific notation reaches the API, UI or report (1E+6 must be 1000000)."""
    r = normalise(ExtractedValue(field=AMT if q == A else FEE, raw_text=raw, quantity=q), language=lang)  # type: ignore[arg-type]
    shown = r.value if r.value is not None else r.value_low
    assert str(shown) == plain and "E" not in str(shown)
    assert "E" not in r.model_dump_json()
