"""Gates — doc 08 §5, applied to every engine verdict before merge (T2.5).

Order: 1 speaker → 2 verifiability → 3 value → 4 vagueness → 5 ASR (+ HC-3 quote confirmation, Hindi rule).
Gates 6 (OCR) and 7 (assumption) live in the contract and finance engines; 8 (public-data cap) is enforced here too.
A gate only ever lowers a verdict; it never creates MATCH or CONFLICT.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.claims import VERIFIABLE_KINDS, Claim
from app.schemas.verification import EngineVerdict, ReasonCode, Status
from app.schemas.wording import REASON_TEXT

DECISIVE = {Status.CONFLICT, Status.MATCH}


@dataclass(frozen=True)
class Thresholds:
    version: str
    T_asr_num: float
    T_asr_avg: float
    T_ocr: float
    T_ocr_num: float
    T_ret: float | None


def lower(v: EngineVerdict, status: Status, code: ReasonCode) -> EngineVerdict:
    return v.model_copy(update={"status": status, "reason_code": code, "finding_kind": v.finding_kind,
                                "reason": REASON_TEXT[code]})


def is_numeric(claim: Claim) -> bool:
    return bool(claim.values)


def apply_gates(v: EngineVerdict, claim: Claim, th: Thresholds, *, hindi: bool) -> EngineVerdict:
    # 1. Speaker gate: a CONFLICT needs a user-confirmed agent. MATCH may rest on an auto role (labelled in the UI).
    if v.status == Status.CONFLICT and not (claim.speaker_role == "agent" and claim.role_assignment == "user_confirmed"):
        v = lower(v, Status.UNCLEAR, ReasonCode.SPEAKER_UNCONFIRMED)
    # 2. Verifiability: confirmed customer, or a question / hypothetical / opinion / customer statement.
    if (claim.speaker_role == "customer" and claim.role_assignment == "user_confirmed") \
            or claim.utterance_kind not in VERIFIABLE_KINDS:
        return lower(v, Status.NOT_VERIFIED, ReasonCode.NOT_AGENT_CLAIM)
    if v.status not in DECISIVE:
        return v
    # 3. Value gate.
    if any(val.normaliser_version is None for val in claim.values):
        return lower(v, Status.UNCLEAR, ReasonCode.VALUE_UNPARSED)
    # 4. Vagueness.
    if claim.vagueness == "vague":
        return lower(v, Status.UNCLEAR, ReasonCode.VAGUE_CLAIM)
    # 5. ASR gate. Missing confidence counts as below threshold.
    if is_numeric(claim):
        if claim.asr_min_numeric_conf is None or claim.asr_min_numeric_conf < th.T_asr_num:
            return lower(v, Status.UNCLEAR, ReasonCode.LOW_ASR_CONFIDENCE)
        # HC-3 / ADR-019: a numeric CONFLICT also needs the customer's quote confirmation, in every language; Hindi
        # (not yet supported) never shows a numeric CONFLICT.
        if v.status == Status.CONFLICT and (hindi or not claim.quote_confirmed_by_user):
            return lower(v, Status.UNCLEAR, ReasonCode.LOW_ASR_CONFIDENCE)
    elif claim.asr_avg_conf is None or claim.asr_avg_conf < th.T_asr_avg:
        return lower(v, Status.UNCLEAR, ReasonCode.LOW_ASR_CONFIDENCE)
    # 8. Public-data cap.
    if v.engine == "public_data":
        return lower(v, Status.UNCLEAR, ReasonCode.PUBLIC_DATA_ONLY)
    return v
