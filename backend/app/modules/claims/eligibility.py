"""Which claims could ever back a CONFLICT — doc 08 §5 gates 1–4, applied to a claim (T1.6, user clarifications
2026-11-14). The T2.5 orchestrator applies these as gates; this module only lists what blocks a claim.

- Questions, hypotheticals, opinions and customer statements, and anything a confirmed customer said, are never
  verifiable agent claims → NOT_VERIFIED(not_agent_claim). Customer questions are kept for context only.
- A speaker who is not a user-confirmed agent → UNCLEAR(speaker_unconfirmed).
- A spoken value the normaliser could not parse → UNCLEAR(value_unparsed).
- A vague claim → UNCLEAR(vague_claim); vague claims carry no values, so never a numeric mismatch.
Utterance-kind variation between promise / assertion / conditional does not change eligibility.
"""

from __future__ import annotations

from app.schemas.claims import VERIFIABLE_KINDS, Claim
from app.schemas.verification import ReasonCode


def conflict_blockers(claim: Claim) -> list[ReasonCode]:
    blockers: list[ReasonCode] = []
    if claim.utterance_kind not in VERIFIABLE_KINDS or claim.speaker_role == "customer" or not claim.verifiable:
        blockers.append(ReasonCode.NOT_AGENT_CLAIM)
    if not (claim.speaker_role == "agent" and claim.role_assignment == "user_confirmed"):
        blockers.append(ReasonCode.SPEAKER_UNCONFIRMED)
    if any(v.normaliser_version is None for v in claim.values):
        blockers.append(ReasonCode.VALUE_UNPARSED)
    if claim.vagueness == "vague":
        blockers.append(ReasonCode.VAGUE_CLAIM)
    return blockers


def can_back_conflict(claim: Claim) -> bool:
    return not conflict_blockers(claim)
