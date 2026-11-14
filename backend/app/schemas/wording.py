"""Wording templates and the accusation deny-list — doc 08 §8 (English approved at HC-2, 2026-11-14).

Hindi templates are reviewed later (T4.4). Raw LLM rationale is never shown; only these templates.
"""

from __future__ import annotations

import re

from app.schemas.verification import ReasonCode, Status

HEADLINES: dict[Status, str] = {
    Status.CONFLICT: "Possible mismatch: {field}",
    Status.MATCH: "Matches the document: {field}",
    Status.UNCLEAR: "Needs clarification: {field}",
    Status.NOT_VERIFIED: "Couldn't check yet: {field}",
}

REASON_C_DOC = 'The agent said "{quote}" ({mm_ss}). Page {page}, {section} says: "{clause_excerpt}".'

# Clarification questions given verbatim in doc 08 §3.1 / §6. Other codes have no template yet (T2.5 adds them
# from the HC-2-approved table as engines arrive).
QUESTION_TEMPLATES: dict[ReasonCode, str] = {
    ReasonCode.NO_DOCUMENT: "Ask for the KFS / agreement and upload it.",
    ReasonCode.CLAUSE_NOT_FOUND: "Ask the agent to show where this is written.",
    ReasonCode.PAGE_UNREADABLE: "Upload a clearer photo of page {page}.",
    ReasonCode.MISSING_INPUTS: "Ask: {missing_field}?",
    ReasonCode.INTERNAL_INCONSISTENCY: "Ask the agent to confirm the EMI in writing.",
}

# doc 08 §8 deny-list (English + Hindi). Any match in a user-facing template fails the build.
DENY_LIST = ("fraud", "fraudulent", "lie", "lied", "lying", "liar", "cheat", "cheated", "cheating", "illegal",
             "illegally", "scam", "scammer", "criminal", "crime", "धोखा", "धोखाधड़ी", "झूठ", "अवैध")
_DENY = re.compile(r"(?<![\w])(" + "|".join(re.escape(w) for w in DENY_LIST) + r")(?![\w])", re.IGNORECASE)


def wording_violations(text: str) -> list[str]:
    return [m.group(0) for m in _DENY.finditer(text)]


def headline(status: Status, field: str) -> str:
    return HEADLINES[status].format(field=field)
