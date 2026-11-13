# 06 — Financial Maths Engine (P0-3 Hybrid Financial Reasoning)

> **Version 0.2.1 — 2026-11-13 (upload-first MVP revision; HC-5 gate §4a added in 0.2.1).** Original: `_archive/v0.1-2026-11-13/06_…md`.
> **Scope:**
> - [MVP] reducing EMI, flat EMI, processing fee, GST on fees (assumption recorded), net disbursement, total repayment, total interest, amortisation schedule. Built and tested on synthetic data before HC-5; **user-visible only after HC-5** (§4a).
> - [Gated: built and tested, **disabled for the whole MVP**, even after HC-5] APR (IRR), flat-to-effective, prepayment charge, penal charge.
> - [Deferred → Later-L6] exposure of APR/prepayment conclusions to users.
>
> **Canonical here:** `Sourced` / `SourcedFactory`, formulas, the **tolerance table**.

## 1. Purpose
Do all numeric reasoning deterministically with `Decimal`. The LLM never supplies numbers. The engine records missing inputs and assumptions explicitly and stores a calculation trace for every result.

- **Depends on:** doc 04 (claim values), doc 05 (doc terms), doc 08 (verdict models).
- **Used by:** docs 05, 08, 09 (numeric questions).

## 2. Prerequisites
Normaliser (doc 04 §6). **HC-5** before any gated formula is enabled.

## 3. Validated inputs only [MVP]
```python
class Sourced(BaseModel, frozen=True):
    value: Decimal
    source: Literal["agent_said","customer_said","document","assumption"]
    ref_table: Literal["claim_values","doc_terms","assumptions"]; ref_id: UUID | str
    gate_ok: bool                      # all doc 08 §5 gates passed for this input
    gate_reasons: list[str]            # e.g. ["low_asr_confidence"]
    note: str | None = None
    _token: object = PrivateAttr()     # set only by SourcedFactory

class SourcedFactory:
    """The ONLY way to build Sourced. Engines refuse objects without the private token."""
    def from_claim_value(self, row: ClaimValueRow, claim: ClaimRow, thresholds) -> Sourced: ...
        # requires row.normaliser_version is not None; computes gate_ok from claim's
        # asr_min_numeric_conf, role_assignment, vagueness, quote_confirmed_by_user
    def from_doc_term(self, row: DocTermRow, clause: ClauseRow, thresholds) -> Sourced: ...
        # requires normaliser_version; gate_ok from clause OCR confidences
    def assumption(self, name: Literal["gst_rate_pct","rate_basis_reducing"], value: Decimal, reason: str) -> Sourced: ...
```

Rules:
- The finance engine's public functions accept only `LoanInputs` built from `Sourced` objects. A runtime check verifies the token, and mypy enforces the types.
- **Test:** passing a dict, a float, an LLM output or a hand-built `Sourced` raises `UnvalidatedInputError`.
- Any input with `gate_ok=False` caps the verdict at UNCLEAR with that gate's reason code.
- Allowed assumptions (each recorded): `gst_rate_pct = 18` **[A, confirm at HC-5]**; `rate_basis` when unspecified (both bases are always computed — see §6).

```python
class LoanInputs(BaseModel):
    principal: Sourced | None; annual_rate_pct: Sourced | None
    rate_basis: Literal["reducing","flat","unspecified"]; tenure_months: Sourced | None
    processing_fee_pct: Sourced | None; processing_fee_abs: Sourced | None
    gst_rate_pct: Sourced; other_upfront_deductions: list[Sourced] = []
    emi: Sourced | None
    missing: list[str]; assumptions: list[str]
```

## 4. Formulas
| ID | MVP | Formula |
|---|---|---|
| `emi_reducing_v1` | ✅ | r = R/1200. EMI = P·r(1+r)^n/((1+r)^n−1); r = 0 → P/n |
| `emi_flat_v1` | ✅ | I = P·R/100·n/12; EMI = (P+I)/n |
| `total_repayment_v1` | ✅ | EMI·n |
| `total_interest_v1` | ✅ | total_repayment − P |
| `fee_amount_v1` | ✅ | pct·P/100 or absolute |
| `gst_on_fee_v1` | ✅ | fee·gst/100 (assumption recorded unless the document states GST) |
| `net_disbursement_v1` | ✅ | P − fee − GST − Σ deductions |
| `amortisation_v1` | ✅ (when needed, e.g. outstanding balance) | Schedule; last-row adjustment |
| `apr_v1` | 🔒 gated | IRR of (+net_disbursement, −EMI×n). Annualisation method chosen at HC-5 after E7 |
| `flat_to_effective_v1` | 🔒 gated | 12 × monthly IRR of the flat schedule |
| `prepayment_charge_v1` | 🔒 gated | pct × outstanding(k) + GST |
| `penal_charge_v1` | 🔒 gated | per the document's stated basis |

A gated formula returns `NOT_VERIFIED(calculation_not_enabled)`. Its results are computed in **shadow** for evaluation only. v0.1's informational "APR differs from quoted rate" note is **hidden until HC-5**.

IRR (gated): Newton with bisection fallback; |NPV| < ₹0.01; 200 iterations. Non-convergence → `NOT_VERIFIED(calc_not_converged)` (canonical in doc 08).

## 4a. HC-5 finance gate (canonical; added v0.2.1)
HC-5 is the finance sign-off checkpoint. It is **pending**, and nothing below approves it.

**Allowed before HC-5 (Phase 3 work on synthetic data):**
- Implement **all** formulas in §4, including the gated ones (APR, flat-to-effective, prepayment, penal), plus `SourcedFactory`, traces and the tolerance table.
- Unit, golden, property and `UnvalidatedInputError` tests.
- Run the eval harness and E7 on **synthetic or team data only**.
- Store traces with `enabled=false` (shadow) for review.
- Run the engine in dev/staging with synthetic sessions so the orchestrator and UI paths can be built and tested. Finance-derived displays appear **only** behind the internal flag `finance.preview_internal=true`, which is refused in any environment with real users.

**Hidden or disabled until HC-5 is approved** (`finance.mvp_formulas_user_visible=false`):
- Every user-facing finance output in the results dashboard, evidence drawer, report and Ask:
  - EMI, total repayment and interest, net disbursement, GST amounts, INR impact figures
  - the "agent's numbers are consistent" note
  - `UNCLEAR(internal_inconsistency)`
- Verdicts that need a formula, i.e. C-CALC-DOC MATCH/CONFLICT and percent ↔ INR conversions in the contract engine. While the flag is off, the finance engine returns `NOT_VERIFIED(calculation_not_enabled)` to the orchestrator, and the contract engine returns that code wherever a comparison needs a formula.
- Direct like-for-like comparisons (spoken "2%" vs document "2%") need no formula and are **not** gated.
- The Ask finance route answers "This calculation isn't available yet."

**After HC-5 approval** (recorded with approver name and date; never assumed):
- `finance.mvp_formulas_user_visible=true` exposes **only** the MVP set: reducing/flat EMI, fee, GST with an assumption label, net disbursement, total repayment, total interest, amortisation.
- The **gated set stays disabled in the MVP even after HC-5** (`finance.gated_formulas_enabled=false`). HC-5 approves its methods for later. Exposure is Later-L6. Prepayment and penal conclusions also need HC-6 because they are regulatory.

**Enforcement:**
```yaml
# backend/config/features.yaml
finance:
  preview_internal: false              # dev/staging synthetic only
  mvp_formulas_user_visible: false     # true only with hc5_approval filled
  gated_formulas_enabled: false        # stays false for the MVP
  hc5_approval: { approved_by: null, approved_at: null, record: null }
rules:
  user_visible: false                  # per-rule enablement also needs HC-6 (doc 07)
```
- **Startup check:** the app refuses to start if `mvp_formulas_user_visible=true` while `hc5_approval.approved_by` is null, or if `preview_internal=true` in an environment flagged `real_users=true`.
- Flag changes are audited (`admin.feature_flag_change`).

## 5. Tolerances (canonical; docs 05 and 08 reference this table)
| Quantity | MATCH if within | `UNCLEAR(possible_rounding)` band | Else |
|---|---|---|---|
| Percent rates and fees | ±0.05 pp | ±0.25 pp | CONFLICT-eligible |
| INR fee / amount | ±max(₹1, 0.5%) | ±3% | CONFLICT-eligible |
| EMI | ±max(₹2, 0.3%) | ±3% | CONFLICT-eligible |
| Tenure | exact months | ±1 month | CONFLICT-eligible |
| `approx` comparator ("around") | ±5% | ±10% | CONFLICT-eligible |
| `lte` / `gte` | directional | — | CONFLICT-eligible |

All values are **[Proposed]** and confirmed at HC-5. "CONFLICT-eligible" means doc 08 §4–§5 still decide.

## 6. Checks by claim type (MVP)
| Claim | Check | Primary evidence |
|---|---|---|
| `emi` | Spoken EMI vs EMI from **document** P, R, n (C-CALC-DOC). If the basis is unspecified, compute both: matching only one → `UNCLEAR(assumption_dependent)` with the note "matches only if the rate is {basis}" | document inputs |
| `emi` (no document) | Spoken EMI vs spoken P, R, n → `calculation_internal` sub-check only. Consistent → shown as a note under `NOT_VERIFIED(no_document)`; inconsistent → `UNCLEAR(internal_inconsistency)` | — |
| `net_disbursement` ("you get the full 5 lakh") | `net_disbursement_v1` from document fees/deductions vs the spoken amount | document |
| `total_cost` | `total_interest_v1` from document terms | document |
| `processing_fee` / `other_charge` | Compare is done by the contract engine; finance adds the INR impact and GST (assumption-tagged) | document |
| `insurance_bundled` ("free") | Premium deducted or added in the document → amount | document |
| `interest_rate` | The contract engine compares number + basis. The APR comparison is gated | document |

Missing inputs → `NOT_VERIFIED(missing_inputs)` + question ("Ask: what is the tenure in months?").

## 7. Calculation trace (persisted, immutable)
```python
class CalculationTrace(BaseModel):
    id: UUID; session_id: UUID; formula_id: str; formula_version: int
    inputs: dict[str, Sourced]; outputs: dict[str, Decimal]
    assumptions: list[str]; missing: list[str]; tolerance_row: str
    enabled: bool                      # False for shadow (gated) runs
    created_at: datetime
```
Template example: "For ₹5,00,000 at 10.5% (reducing) for 60 months from the KFS (page 1), EMI = ₹10,747. The agent said ₹9,800 (04:12). Difference: ₹947/month."

## 8. Testing
- Golden table `golden/*.yaml`: ≥ 60 hand-computed cases, cross-checked with numpy-financial **in tests only**.
- Property tests:
  - reducing EMI ≤ flat EMI (R > 0, n > 1)
  - amortisation closes to 0 ±₹1
  - net disbursement ≤ P
- `UnvalidatedInputError` tests (§3).
- Gated formulas return `calculation_not_enabled` while their flag is off.
- E7 (APR vs the RBI KFS illustration) runs in shadow.

## 9. Acceptance criteria (Phase 3 exit)
- 100% of golden cases within ±₹1 / ±0.01 pp.
- Every finance verdict has a trace whose inputs all come from the factory.
- No user-visible finance output before HC-5 approval is recorded; no APR, flat-to-effective, prepayment or penal output anywhere in the MVP (flag + startup-check tests).

## 10. Human review
**HC-5:** golden table, tolerances, the GST assumption, the APR method (only for enabling later), and a finance-literate reviewer recorded by name. Not yet done.

## 11. Common failures
- Floats instead of Decimal.
- Treating the annual rate as monthly.
- APR on gross rather than net disbursement (gated anyway).
- Treating "fixed" as "flat".
