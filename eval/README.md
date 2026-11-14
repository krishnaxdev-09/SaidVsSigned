# Evaluation harness

Datasets and reports per doc 12 §A. No real customer data may be placed here (doc 12 §A.1).

- `datasets/`: synthetic data only
- `reports/`: dated eval reports (dataset version + config hash). No accuracy figure may be quoted without one.
- `tools/`: generators and checkers

## Dataset status
| Dataset (doc 12 §A.1) | Status |
|---|---|
| 1. 10 synthetic loan agreements / KFS | ✅ digital PDFs + ground truth (`datasets/agreements/AG01–AG10`, `agreements-v1`). ⬜ print-and-scan and 3 phone-photo variants per agreement are manual and still TODO (needed for E5, Phase 2) |
| 2. 30 scripted recorded conversations | ⬜ Phase 1 (T1.8) |
| 3. 200 claim–clause pairs | ⬜ Phase 2 |
| 4. Q&A set | ⬜ Phase 3 |
| 5. Perturbation sets | ⬜ Phase 4 |

## Agreements (`agreements-v1`)
Each `datasets/agreements/<id>/` has `agreement.pdf` (2 pages: KFS table, then clauses) and `terms.yaml`
(ground truth: every key term with field name per doc 04 §4, value, exact `raw_text`, page and location:
`kfs_table`, `clause`, `small_print` or `footnote`). All names are fictional.

| ID | Product | Traps |
|---|---|---|
| AG01 | Personal | 2% + GST fee |
| AG02 | Personal | fee in footnote |
| AG03 | Personal | % + GST fee; insurance deducted |
| AG04 | Consumer durable | flat rate in small print |
| AG05 | Home (floating) | % + GST fee; prepayment on floating |
| AG06 | Two-wheeler | penal interest |
| AG07 | Business | 2% + GST fee; insurance deducted; penal interest |
| AG08 | Personal | flat rate in small print; fee in footnote; % + GST fee |
| AG09 | LAP (floating) | % + GST fee; prepayment on floating; penal interest |
| AG10 | Personal | none (control) |

Regenerate (byte-identical output) and check that every `raw_text` is on its stated page:
```
uv run --no-project --with reportlab==4.2.5 --with pyyaml==6.0.2 python eval/tools/gen_agreements.py
uv run --no-project --with pypdf==5.1.0 --with pyyaml==6.0.2 python eval/tools/check_agreements.py
```
Printed EMIs and totals come from generator arithmetic on synthetic data. They are not the finance engine
(T3.1), which stays gated by HC-5.
