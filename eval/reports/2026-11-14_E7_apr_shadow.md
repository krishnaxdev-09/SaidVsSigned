# E7 — APR in shadow (2026-11-14)

> **Shadow only.** Gated formulas are disabled for the whole MVP (`finance.gated_formulas_enabled=false`, startup check). Nothing here is user-visible. The comparison with the **RBI KFS illustration is pending** (figures not retrieved; none invented). Synthetic agreements.

APR = IRR of (+net disbursement, −EMI × n), annualised both ways (method chosen at HC-5).

| Agreement | Stated rate | Basis | APR nominal (12×IRR) | APR effective | numpy-financial 12×IRR | Flat→effective | Enabled |
|---|---|---|---|---|---|---|---|
| AG01 | 14.50% | reducing | 16.19% | 17.45% | 16.19% | — | False |
| AG02 | 13.25% | reducing | 13.96% | 14.89% | 13.96% | — | False |
| AG03 | 15.00% | reducing | 17.82% | 19.35% | 17.82% | — | False |
| AG04 | 11.00% | flat | 22.98% | 25.56% | 22.98% | 19.72% | False |
| AG05 | 8.75% | reducing | 8.83% | 9.20% | 8.83% | — | False |
| AG06 | 16.00% | reducing | 18.17% | 19.76% | 18.17% | — | False |
| AG07 | 17.50% | reducing | 20.62% | 22.68% | 20.62% | — | False |
| AG08 | 12.00% | flat | 25.28% | 28.43% | 25.28% | 21.57% | False |
| AG09 | 10.40% | reducing | 10.69% | 11.23% | 10.69% | — | False |
| AG10 | 12.75% | reducing | 13.95% | 14.88% | 13.95% | — | False |

- Largest difference from numpy-financial: 0.000 pp (target ≤ 0.01 pp).
- Flat-rate agreements (AG04, AG08) show the effective cost of a flat rate on a reducing basis — the trap the contract engine already flags as a basis CONFLICT.
