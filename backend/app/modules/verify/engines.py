"""Engines registered with the orchestrator (doc 08 §6). The contract engine is `app/modules/contract` (T2.6)."""

from __future__ import annotations

from typing import Literal

from app.modules.verify.merge import Outcome
from app.modules.verify.service import EngineContext
from app.schemas.claims import Claim
from app.schemas.verification import EngineVerdict, ReasonCode, Status
from app.schemas.wording import REASON_TEXT

FINANCE_TYPES = {"emi", "total_cost", "net_disbursement"}


class FinanceEngine:
    """Finance before HC-5 (doc 06 §4a): every finance sub-check is NOT_VERIFIED(calculation_not_enabled).
    Replaced by the real finance engine in T3.1 (synthetic data only, user visibility off until HC-5)."""

    name: Literal["finance"] = "finance"
    shadow = False

    def applies(self, claim: Claim) -> bool:
        return claim.claim_type.value in FINANCE_TYPES

    async def verify(self, claim: Claim, ctx: EngineContext) -> Outcome:
        return Outcome(EngineVerdict(engine="finance", status=Status.NOT_VERIFIED,
                                     reason_code=ReasonCode.CALCULATION_NOT_ENABLED, finding_kind=None,
                                     reason=REASON_TEXT[ReasonCode.CALCULATION_NOT_ENABLED], evidence=[]))
