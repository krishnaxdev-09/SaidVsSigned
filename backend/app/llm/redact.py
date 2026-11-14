"""PII redaction before any LLM call (doc 12 §B.2 "LLM / STT data minimisation"; doc 04 §3).

Redacts PAN, Aadhaar (Verhoeff-checked), bank account numbers, Indian phone numbers and email addresses, replacing
each with a typed placeholder. Loan amounts are never touched: account numbers are 9–18 *unbroken* digits, so
grouped amounts (10,00,000) and ordinary figures (36000) stay as they are.
"""

from __future__ import annotations

import re

PLACEHOLDERS = ("[PAN]", "[AADHAAR]", "[ACCOUNT]", "[PHONE]", "[EMAIL]")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PAN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{5}[0-9]{4}[A-Za-z](?![A-Za-z0-9])")
_AADHAAR = re.compile(r"(?<!\d)(\d{4})[ -]?(\d{4})[ -]?(\d{4})(?!\d)")
_PHONE = re.compile(r"(?<![\d,.])(?:\+?91[ -]?|0)?([6-9]\d{4})[ -]?(\d{5})(?![\d,.])")
_ACCOUNT = re.compile(r"(?<![\d,.])\d{9,18}(?![\d,.])")

# Verhoeff tables
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
      [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
      [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
      [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def redact(text: str) -> str:
    t = _EMAIL.sub("[EMAIL]", text)
    t = _PAN.sub("[PAN]", t)

    def aadhaar(m: re.Match[str]) -> str:
        digits = "".join(m.groups())
        return "[AADHAAR]" if digits[0] not in "01" and verhoeff_valid(digits) else m.group(0)

    t = _AADHAAR.sub(aadhaar, t)
    t = _PHONE.sub("[PHONE]", t)
    return _ACCOUNT.sub("[ACCOUNT]", t)


def contains_placeholder(text: str) -> bool:
    return any(p in text for p in PLACEHOLDERS)
