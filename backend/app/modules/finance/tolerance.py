"""Tolerance table — doc 06 §5 (canonical; docs 05 and 08 reference it). All values [Proposed] until HC-5."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

Cmp = Literal["match", "rounding", "conflict"]
D = Decimal

TOLERANCE_TABLE = {  # quantity → (MATCH within, possible_rounding band); "pp" = percentage points
    "percent": ("±0.05 pp", "±0.25 pp"),
    "amount_inr": ("±max(₹1, 0.5%)", "±3%"),
    "emi": ("±max(₹2, 0.3%)", "±3%"),
    "months": ("exact", "±1 month"),
    "approx": ("±5%", "±10%"),
    "lte/gte": ("directional", "—"),
}


def compare(quantity: str, field: str, spoken: Decimal, doc: Decimal, comparator: str = "eq") -> tuple[Cmp, str]:
    """Spoken vs document value in the same unit. Returns (outcome, tolerance row used)."""
    diff = abs(spoken - doc)
    if comparator == "lte":  # "up to X": the document must not exceed it
        return ("match" if doc <= spoken else "conflict"), "lte/gte"
    if comparator == "gte":  # "at least X"
        return ("match" if doc >= spoken else "conflict"), "lte/gte"
    if comparator == "approx":
        base = abs(doc) or D(1)
        return ("match" if diff <= base * D("0.05") else "rounding" if diff <= base * D("0.10") else "conflict"), "approx"
    if quantity == "percent":
        return ("match" if diff <= D("0.05") else "rounding" if diff <= D("0.25") else "conflict"), "percent"
    if quantity == "months":
        return ("match" if diff == 0 else "rounding" if diff <= 1 else "conflict"), "months"
    if field == "emi":
        ok = max(D(2), abs(doc) * D("0.003"))
        return ("match" if diff <= ok else "rounding" if diff <= abs(doc) * D("0.03") else "conflict"), "emi"
    ok = max(D(1), abs(doc) * D("0.005"))
    return ("match" if diff <= ok else "rounding" if diff <= abs(doc) * D("0.03") else "conflict"), "amount_inr"
