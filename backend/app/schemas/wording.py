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


# ---- T2.5: field labels and templated reasons (doc 08 §3.1 meanings, §8). English only; Hindi at T4.4. ------------

FIELD_LABELS: dict[str, str] = {
    "processing_fee": "processing fee", "interest_rate": "interest rate", "emi": "EMI", "tenure": "tenure",
    "loan_amount": "loan amount", "net_disbursement": "amount you receive", "prepayment_charge": "prepayment charge",
    "penal_charge": "late-payment charge", "insurance_bundled": "insurance", "other_charge": "other charges",
    "total_cost": "total cost", "document_assurance": "document promise", "general_assurance": "promise",
    "lender_identity": "lender",
}

REASON_TEXT: dict[ReasonCode, str] = {
    ReasonCode.NO_DOCUMENT: "No loan document has been uploaded and processed yet.",
    ReasonCode.CLAUSE_NOT_FOUND: "We couldn't find a clause about this in your documents.",
    ReasonCode.PAGE_UNREADABLE: "The page that covers this couldn't be read.",
    ReasonCode.MISSING_INPUTS: "Some numbers needed for this check are missing.",
    ReasonCode.CALC_NOT_CONVERGED: "This calculation couldn't be completed.",
    ReasonCode.CALCULATION_NOT_ENABLED: "This check needs a calculation that isn't available yet.",
    ReasonCode.APPLICABILITY_UNKNOWN: "We don't have enough facts to know whether this rule applies.",
    ReasonCode.RULE_NOT_ENABLED: "This rule check is not enabled.",
    ReasonCode.ENTITY_NOT_FOUND: "This lender isn't in our reference data.",
    ReasonCode.ENGINE_NOT_APPLICABLE: "We don't check this kind of statement yet.",
    ReasonCode.NOT_AGENT_CLAIM: "This was a question, a hypothetical or something the customer said, so it isn't checked.",
    ReasonCode.ENGINE_ERROR: "Something went wrong while checking this. You can try again.",
    ReasonCode.LOW_ASR_CONFIDENCE: "We're not sure the recording was heard correctly here. Play the clip and confirm the words.",
    ReasonCode.LOW_OCR_CONFIDENCE: "The document text here was hard to read.",
    ReasonCode.SPEAKER_UNCONFIRMED: "Confirm who the agent is before this can be checked.",
    ReasonCode.VAGUE_CLAIM: "The statement is too general to compare with the document.",
    ReasonCode.VALUE_UNPARSED: "We couldn't read the number in the statement.",
    ReasonCode.CONDITION_MISMATCH: "The statement and the document describe different conditions.",
    ReasonCode.SEMANTIC_DISAGREEMENT: "Our checks disagree about whether the document supports this.",
    ReasonCode.POSSIBLE_ROUNDING: "The numbers differ slightly; this may be rounding.",
    ReasonCode.ASSUMPTION_DEPENDENT: "The answer depends on an assumption the document doesn't state.",
    ReasonCode.INCOMPARABLE_UNITS: "The statement and the document use units we can't compare.",
    ReasonCode.INTERNAL_INCONSISTENCY: "The agent's numbers don't agree with each other.",
    ReasonCode.PUBLIC_DATA_ONLY: "Only general published information is available for this.",
    ReasonCode.AWAITING_CONFIRMATION: "Waiting for confirmation.",
}

QUESTION_TEMPLATES.update({
    ReasonCode.LOW_ASR_CONFIDENCE: "Play the clip and confirm what the agent said.",
    ReasonCode.LOW_OCR_CONFIDENCE: "Upload a clearer photo of page {page}.",
    ReasonCode.SPEAKER_UNCONFIRMED: "Confirm which speaker is the agent.",
    ReasonCode.VAGUE_CLAIM: "Ask the agent for the exact figure in writing.",
    ReasonCode.CONDITION_MISMATCH: "Ask the agent which condition applies to you.",
    ReasonCode.SEMANTIC_DISAGREEMENT: "Ask the agent to show where this is written.",
    ReasonCode.POSSIBLE_ROUNDING: "Ask the agent for the exact figure in writing.",
    ReasonCode.ASSUMPTION_DEPENDENT: "Ask the agent whether GST and other charges are included.",
    ReasonCode.INCOMPARABLE_UNITS: "Ask the agent for this in rupees.",
})
CONFLICT_QUESTION = "Ask the agent to explain the difference in writing before you sign."


def mm_ss(ms: int) -> str:
    return f"{ms // 60000:02d}:{ms // 1000 % 60:02d}"
