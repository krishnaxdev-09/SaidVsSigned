"""Run the deterministic Phase 4 gate perturbation checks on synthetic claims.

This offline check complements the provider-backed reports from Phases 1–3. It
does not use customer data or claim that real-call accuracy has been measured.
Run from backend/: ``PYTHONPATH=. uv run python ../eval/tools/run_phase4_eval.py``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import yaml

from app.modules.verify import gates
from app.schemas.verification import Status
from tests.unit.test_verify_merge import claim, v

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "backend"
THRESHOLDS = BACKEND / "config" / "thresholds.yaml"


def main() -> None:
    threshold_bytes = THRESHOLDS.read_bytes()
    threshold_hash = hashlib.sha256(threshold_bytes).hexdigest()[:12]
    raw = yaml.safe_load(threshold_bytes)
    thresholds = gates.Thresholds(
        raw["version"],
        raw["T_asr_num"],
        raw["T_asr_avg"],
        raw["T_ocr"],
        raw["T_ocr_num"],
        raw["T_ret"],
        raw["T_nli_contra"],
        raw["T_nli_entail"],
    )

    conflict = v("contract", Status.CONFLICT)
    cases = [
        ("low numeric ASR confidence", claim(asr_min_numeric_conf=0.5), False),
        ("missing numeric ASR confidence", claim(asr_min_numeric_conf=None), False),
        ("low semantic ASR confidence", claim(values=[], asr_avg_conf=0.5), False),
        ("speaker not confirmed", claim(role_assignment="auto"), False),
        ("quote not confirmed", claim(quote_confirmed_by_user=False), False),
        ("Hindi numeric claim", claim(), True),
    ]

    failures: list[str] = []
    for name, current, hindi in cases:
        outcome = gates.apply_gates(conflict, current, thresholds, hindi=hindi)
        if outcome.status.value == "CONFLICT":
            failures.append(name)

    report_date = dt.date.today().isoformat()
    report = [
        f"# Phase 4 evaluation baseline — {report_date}",
        "",
        "> Synthetic, offline gate perturbation check. This report does not measure real-call accuracy.",
        "",
        f"- Threshold configuration: `backend/config/thresholds.yaml` (`{raw['version']}`, sha256 `{threshold_hash}`)",
        f"- Perturbations: {len(cases)}",
        f"- Low-confidence / disallowed perturbations becoming CONFLICT: **{len(failures)}** (target 0)",
        f"- Result: **{'PASS' if not failures else 'FAIL'}**",
        "",
        "## Cases",
        "",
        "| Perturbation | Expected | Result |",
        "|---|---|---|",
    ]
    for name, current, hindi in cases:
        outcome = gates.apply_gates(conflict, current, thresholds, hindi=hindi)
        report.append(f"| {name} | not CONFLICT | `{outcome.status.value}` |")
    if failures:
        report += ["", "## Failures", "", *[f"- {failure}" for failure in failures]]

    report += [
        "",
        "## Existing measured reports",
        "",
        "- E2/E3/E10 and extraction: `2026-11-14_phase1_E2_E3_E10_extraction.md`",
        "- OCR: `2026-11-14_E5_ocr.md`",
        "- Retrieval: `2026-11-14_E6_retrieval.md`",
        "- Contract confusion matrix: `2026-11-14_T2.6_contract_confusion.md`",
        "- Ask routing and citations: `2026-11-14_T3.4_ask.md`",
        "",
        "## Still required before T4.1 can be marked complete",
        "",
        "- Provider-backed full evaluation on the Phase 4 perturbation datasets.",
        "- Threshold calibration report for ASR, OCR, retrieval, and NLI thresholds.",
        "- End-to-end batch latency measurement and clarification-coverage measurement.",
        "- Real-user UX evidence remains gated by HC-7/HC-8 and must not use customer data.",
    ]
    output = ROOT / "reports" / f"{report_date}_phase4_baseline.md"
    output.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"{output}: {len(cases)} cases, {len(failures)} failures")


if __name__ == "__main__":
    main()
