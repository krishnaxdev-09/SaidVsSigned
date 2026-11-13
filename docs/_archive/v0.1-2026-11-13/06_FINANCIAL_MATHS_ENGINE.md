# 06 — Financial Maths Engine (Roadmap P3) — P0-3 Hybrid Financial Reasoning

## Purpose
Do all numeric reasoning deterministically in Python with `Decimal`. The LLM only extracts values (doc 04/05). The engine reports missing inputs and assumptions explicitly, and stores a full calculation trace for audit.

## Prerequisites
- Normalised values from the doc 04 normaliser.
- Doc terms from doc 05 (optional; the engine works on spoken values alone for internal-consistency checks).

## Module layout
```
engines/finance/
  money.py        # Decimal context (prec=28), rounding helpers (ROUND_HALF_UP to paise / rupee)
  formulas.py     # pure functions, each with FORMULA_ID + version
  apr.py          # cash-flow builder + IRR (Newton with bisection fallback)
  inputs.py       # LoanInputs resolution from claims + doc terms + customer context
  checks.py       # claim-type → check functions
  engine.py       # FinanceEngine(VerificationEngine)
  golden/*.yaml   # golden test cases (HC-5)
```

## Inputs resolution
```python
class Sourced(BaseModel):
    value: Decimal
    source: Literal["agent_said","customer_said","document","assumption"]
    ref_id: UUID | None           # claim id or doc_term id
    note: str | None = None

class LoanInputs(BaseModel):
    principal: Sourced | None
    annual_rate_pct: Sourced | None
    rate_basis: Literal["reducing","flat","unspecified"]
    tenure_months: Sourced | None
    processing_fee: Sourced | None            # absolute INR after resolving % of principal
    processing_fee_pct: Sourced | None
    gst_rate_pct: Sourced                     # default 18 with source="assumption" [A]
    other_upfront_charges: list[Sourced] = [] # documentation, stamp duty, insurance premium deducted
    emi: Sourced | None
    missing: list[str]
    assumptions: list[str]
```

**Resolution precedence per field:**
1. When verifying a *spoken* claim against the document, the claim value is the subject and the document value is the reference.
2. When checking the *internal consistency* of spoken numbers (EMI vs amount, rate, tenure), all inputs come from speech.
3. Never fill a missing input with an LLM guess. The only allowed defaults are those listed under Assumptions, and each is recorded.

## Formulas (each with an id and a golden test)
| ID | Formula |
|---|---|
| `emi_reducing_v1` | r = R/12/100. EMI = P·r·(1+r)^n / ((1+r)^n − 1). If r = 0 → P/n |
| `emi_flat_v1` | Interest = P·(R/100)·(n/12). EMI = (P + Interest)/n |
| `total_repayment_v1` | EMI·n (+ any balloon) |
| `total_interest_v1` | total_repayment − P |
| `fee_amount_v1` | pct·P/100 (or absolute) |
| `gst_on_fee_v1` | fee·gst_rate/100 |
| `net_disbursement_v1` | P − fee − GST − Σ upfront deductions |
| `flat_to_effective_v1` | effective annual reducing-equivalent rate = 12 × monthly IRR of (+P at t0, −EMI_flat × n) |
| `apr_v1` | APR from the IRR of cash flows: t0: +net_disbursement (what the borrower actually receives); t1..tn: −EMI. Annualisation: **[A → E7]** match the RBI KFS illustrative calculation (nominal monthly IRR × 12 vs effective (1+i)^12 − 1). Implement both; `APR_METHOD` config chosen at HC-5 |
| `prepayment_charge_v1` | pct × outstanding principal at month k (outstanding from the amortisation schedule) + GST |
| `penal_charge_v1` | as per the document's stated basis (fixed amount or % of overdue). Engine only evaluates what the document states |
| `amortisation_v1` | Schedule rows (k, opening, interest, principal, closing). Last-row adjustment to zero |

**IRR:** Newton–Raphson on monthly rate with an initial guess of R/1200, falling back to bisection on [0, 1]. Convergence |NPV| < ₹0.01. Max 200 iterations. On non-convergence → UNCLEAR `calc_not_converged`.

## Checks by claim type
| Claim | Check | Needs |
|---|---|---|
| `emi` | spoken EMI vs `emi_*` from (P, R, n, basis). If basis unspecified, compute **both** reducing and flat. Match if the said EMI ≈ either one, with a note saying which. If it matches only flat while the agent quoted a "low rate", note that the effective rate is X% | P, R, n |
| `interest_rate` | vs document rate: number and basis. Also compare the spoken rate with the document **APR**: an agent who quotes 11% while the APR is 16.4% gets an *informational* note (not a conflict) explaining the difference | doc |
| `processing_fee` / `other_charge` | vs document. Also compute the INR impact | doc |
| `net_disbursement` ("you'll get full 5 lakh") | `net_disbursement_v1` from document fees/deductions vs the spoken amount → CONFLICT if the doc deducts fees and the agent said "full amount" | doc |
| `total_cost` ("you'll pay only 1 lakh interest") | `total_interest_v1` | P, R, n (+doc) |
| `prepayment_charge` | spoken vs document %. Rules engine separately checks legality (doc 07) | doc |
| `insurance_bundled` ("insurance is free") | if the doc shows a premium deducted or added → CONFLICT, with amount | doc |

**Tolerances [R]:**
- EMI: ±max(₹2, 0.3%). Agents round, so a stated 9,800 vs computed 9,813 → MATCH with a note "rounded".
- Percent: ±0.05 pp.
- `approx` claims: ±5%.
- Outside tolerance but within 3% → UNCLEAR `possible_rounding`.

## Output
```python
class CalculationTrace(BaseModel):
    id: UUID; formula_id: str; formula_version: int
    inputs: dict[str, Sourced]; outputs: dict[str, Decimal]
    assumptions: list[str]; missing: list[str]
    tolerance: dict; created_at: datetime
```
The engine returns an `EngineVerdict` (doc 08) with `CalculationEvidence(trace_id)`. Reason text is a template, e.g.:
"For ₹5,00,000 at 10.5% reducing for 60 months, EMI = ₹10,747. Agent said ₹9,800 (at 04:12). Difference ₹947/month (₹56,820 over the loan)."

**Missing inputs** → `NOT_VERIFIED(reason_code="missing_inputs", missing=[…])` plus a suggested question.
**Assumption affects verdict** → at most `UNCLEAR`. Example: basis unspecified and only one basis matches.

## Testing
- `golden/*.yaml`: ≥ 60 cases hand-computed in a spreadsheet and cross-checked with a second tool (e.g. numpy-financial `pmt`/`irr` in tests only):
  - EMI both bases, zero rate, 1-month tenure, large P (₹5 crore)
  - fee + GST, net disbursement, APR with fees
  - prepayment at months 6/12/36
- Property tests (hypothesis): EMI_reducing ≤ EMI_flat for the same R > 0 and n > 1. Amortisation closes at 0 ± ₹1. APR ≥ nominal rate when fees > 0.
- **E7:** reproduce the RBI KFS circular's illustrative APR example exactly.

## Acceptance criteria
- 100% of golden cases within ±₹1 (amounts) and ±0.01 pp (rates).
- Every finance verdict has a stored trace with all inputs sourced.
- No code path reads an LLM-produced number other than via the normaliser (enforced by type: the engine accepts only `Sourced` built from DB rows).

## Human review
**HC-5:** golden table, APR method, GST default (18% [A]), and tolerance values. Ideally signed off by someone with lending/finance background.

## Common failures
- Float arithmetic. Use Decimal everywhere and only round for display.
- Treating "fixed rate" as flat.
- Computing APR on the gross principal instead of the net disbursement.
- Using the annual rate as a monthly rate. Unit tests on r.
