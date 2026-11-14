"""E7 (doc 06 §8, shadow only): APR and flat-to-effective on the 10 synthetic agreements.

Our IRR (Decimal, Newton + bisection) is cross-checked against numpy-financial. The comparison against the RBI KFS
illustration (doc 14 A7) is **pending**: the illustration's figures have not been retrieved, so none are invented
here. Everything below is shadow output: gated formulas stay disabled for the whole MVP.

Run from backend/:  set -a; . ../.env; set +a; PYTHONPATH=. uv run python ../eval/tools/run_e7.py
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal as D
from pathlib import Path

import numpy_financial as npf
import yaml

from app.modules.finance import gated as G
from app.modules.finance.sourced import LoanInputs, SourcedFactory

ROOT = Path(__file__).resolve().parents[1]
FAC = SourcedFactory()


def doc(v: object) -> object:
    return FAC.from_doc_term({"id": "e7", "value": D(str(v)), "value_raw": str(v), "normaliser_version": "norm-1.0"},
                             {"text_source": "text_layer", "ocr_conf": 1, "ocr_min_numeric_conf": 1})


def main() -> None:
    rows = []
    for i in range(1, 11):
        t = yaml.safe_load((ROOT / "datasets" / "agreements" / f"AG{i:02d}" / "terms.yaml").read_text())
        first = {}
        for x in t["terms"]:
            first.setdefault(x["field"], x)
        flat = any(x["field"] == "interest_rate" and x.get("rate_basis") == "flat" for x in t["terms"])
        fee = first.get("processing_fee")
        ded = {x["field"]: x for x in t["terms"] if x.get("deducted_from_disbursement")}.values()
        inp = LoanInputs(principal=doc(first["loan_amount"]["value"]), annual_rate_pct=doc(first["interest_rate"]["value"]),
                         tenure_months=doc(first["tenure"]["value"]), rate_basis="flat" if flat else "reducing",
                         processing_fee_pct=doc(fee["value"]) if fee and fee["quantity"] == "percent" else None,
                         processing_fee_abs=doc(fee["value"]) if fee and fee["quantity"] == "amount_inr" else None,
                         fee_includes_gst=bool(fee and fee.get("includes_gst")),
                         gst_rate_pct=FAC.assumption("gst_rate_pct", D(18), "A11"),
                         other_upfront_deductions=[doc(x["value"]) for x in ded])
        r = G.run(G.apr, inp)
        flows = [float(r.outputs["net_disbursement"])] + [-float(r.outputs["emi"])] * int(first["tenure"]["value"])
        ref = npf.irr(flows) * 1200
        fte = G.run(G.flat_to_effective, inp).outputs["apr_nominal_pct"] if flat else None
        rows.append((f"AG{i:02d}", first["interest_rate"]["value"], "flat" if flat else "reducing",
                     r.outputs["apr_nominal_pct"], r.outputs["apr_effective_pct"], round(ref, 2), fte, r.enabled))
    today = dt.date.today().isoformat()
    L = [f"# E7 — APR in shadow ({today})", "",
         "> **Shadow only.** Gated formulas are disabled for the whole MVP (`finance.gated_formulas_enabled=false`, startup "
         "check). Nothing here is user-visible. The comparison with the **RBI KFS illustration is pending** (figures not "
         "retrieved; none invented). Synthetic agreements.", "",
         "APR = IRR of (+net disbursement, −EMI × n), annualised both ways (method chosen at HC-5).", "",
         "| Agreement | Stated rate | Basis | APR nominal (12×IRR) | APR effective | numpy-financial 12×IRR | Flat→effective | Enabled |",
         "|---|---|---|---|---|---|---|---|"]
    L += [f"| {a} | {s}% | {b} | {n}% | {e}% | {ref}% | {f'{f}%' if f else '—'} | {en} |" for a, s, b, n, e, ref, f, en in rows]
    worst = max(abs(float(n) - ref) for _, _, _, n, _, ref, _, _ in rows)
    L += ["", f"- Largest difference from numpy-financial: {worst:.3f} pp (target ≤ 0.01 pp).",
          "- Flat-rate agreements (AG04, AG08) show the effective cost of a flat rate on a reducing basis — the trap the "
          "contract engine already flags as a basis CONFLICT."]
    out = ROOT / "reports" / f"{today}_E7_apr_shadow.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[6:]))


if __name__ == "__main__":
    main()
