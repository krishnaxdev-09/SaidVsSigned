"""T2.3 evaluation (doc 05 §9): clause-segmentation boundary F1 on 5 documents, plus doc_terms accuracy.

Gold: eval/datasets/agreements/segmentation_gold.yaml (hand-labelled clause starts) and each agreement's terms.yaml.
Runs the real pipeline (Docling → segment → lexicon pass → Gemma structuring pass → normaliser) on the digital PDF and
on a SIMULATED phone photo (eval/tools/run_e5.py `photo`, good light) of each document.

Run from backend/:  set -a; . ../.env; set +a; PYTHONPATH=. uv run python ../eval/tools/run_t23_segmentation.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import numpy as np
import yaml
from rapidfuzz import fuzz

from app.llm.bedrock_mantle import from_settings
from app.modules.documents import parse, segment, terms

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from run_e5 import photo  # noqa: E402

GOLD = yaml.safe_load((ROOT / "datasets" / "agreements" / "segmentation_gold.yaml").read_text(encoding="utf-8"))
VARIANTS = ["digital", "photo_good"]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def start_of(c: segment.Clause) -> str:
    return norm(c.cells[0] if c.is_table_row and c.cells else c.text.split("\n")[0])


def boundary_counts(clauses: list[segment.Clause], gold: dict[int, list[str]], fuzzy: bool) -> tuple[int, int, int, list]:
    preds = [(c.page_start, start_of(c)) for c in clauses]
    conf = [c.ocr_conf for c in clauses]
    used: set[int] = set()
    tp, misses = 0, []
    for page, starts in gold.items():
        for g in starts:
            alts = [norm(a) for a in g.split("|")]
            hit = next((k for k, (p, s) in enumerate(preds) if k not in used and p == int(page) and any(
                s.startswith(gn) or (fuzzy and len(gn) > 3 and fuzz.ratio(s[:len(gn)], gn) >= 80) for gn in alts)), None)
            if hit is None:
                misses.append(("missed", page, g))
            else:
                used.add(hit)
                tp += 1
    extra = [("extra", p, f"{s[:40]}” (ocr_conf {conf[k] if conf[k] is None else round(conf[k], 2)})")
             for k, (p, s) in enumerate(preds) if k not in used]
    return tp, len(preds), sum(len(v) for v in gold.values()), misses + extra


def term_scores(doc_terms: list[terms.Term], clauses: list[segment.Clause], truth: list[dict]) -> dict[str, int]:
    page_of = {c.ord: c.page_start for c in clauses}
    got = {(page_of[t.clause_ord], t.field, t.value) for t in doc_terms if t.value is not None}
    found = sum(1 for t in truth if (t["page"], t["field"], Decimal(str(t["value"]))) in got)
    truth_vals: dict[str, set[Decimal]] = {}
    for t in truth:
        truth_vals.setdefault(t["field"], set()).add(Decimal(str(t["value"])))
    wrong = sum(1 for t in doc_terms if t.value is not None and t.field in truth_vals and t.value not in truth_vals[t.field])
    return {"truth": len(truth), "found": found, "terms": len(doc_terms), "wrong_value": wrong,
            "unparsed": sum(1 for t in doc_terms if t.value is None)}


async def main() -> None:
    llm, parser, rng = from_settings(), parse.DoclingParser(), np.random.default_rng(11)
    rows = []
    for ag, gold in GOLD["documents"].items():
        pdf = ROOT / "datasets" / "agreements" / ag / "agreement.pdf"
        truth = yaml.safe_load((pdf.parent / "terms.yaml").read_text(encoding="utf-8"))["terms"]
        pages = [np.array(im) for im, _ in parse.render_pdf(pdf)]
        for variant in VARIANTS:
            with tempfile.TemporaryDirectory() as tmp:
                if variant == "digital":
                    d = parser.parse(pdf, ocr=False, force_full_page_ocr=False)
                    parse.confidence_pass(d, set())
                else:
                    path = Path(tmp) / "p.pdf"
                    parse.images_to_pdf([parse.preprocess_photo(photo(p, "good", rng)).image for p in pages], path)
                    d = parser.parse(path, ocr=True, force_full_page_ocr=True)
                    parse.confidence_pass(d, set(range(1, len(pages) + 1)))
            clauses = segment.segment(d.items)
            lex = terms.lexicon_terms(clauses)
            llm_out, stats = await terms.llm_terms(llm, clauses, {t.clause_ord for t in lex})
            tp, n_pred, n_gold, errs = boundary_counts(clauses, gold, fuzzy=variant != "digital")
            rows.append({"ag": ag, "variant": variant, "tp": tp, "pred": n_pred, "gold": n_gold, "errs": errs,
                         "lex": term_scores(lex, clauses, truth), "all": term_scores(lex + llm_out, clauses, truth),
                         "llm": stats})
            r = rows[-1]
            print(f"{ag} {variant:10s} boundaries tp={tp} pred={n_pred} gold={n_gold}  terms lexicon "
                  f"{r['lex']['found']}/{r['lex']['truth']} +llm {r['all']['found']}/{r['all']['truth']} "
                  f"wrong={r['all']['wrong_value']}  llm={stats}  {errs}", flush=True)

    def f1(R: list[dict]) -> tuple[float, float, float]:
        tp, p, g = sum(r["tp"] for r in R), sum(r["pred"] for r in R), sum(r["gold"] for r in R)
        pr, rc = tp / p, tp / g
        return pr, rc, 2 * pr * rc / (pr + rc)

    today = dt.date.today().isoformat()
    L = [f"# T2.3 — Clause segmentation and doc_terms ({today})", "",
         "> **Synthetic documents; simulated photos.** Five generated agreements (AG02, AG04, AG06, AG08, AG10 — chosen "
         "to cover footnotes, small print and an extra KFS row) with hand-labelled clause starts "
         "(`segmentation_gold.yaml`). Real lender documents have more varied layouts (multi-column, nested sub-clauses, "
         "multi-page tables), so these figures are optimistic and are not the HC-4 acceptance measurement.", "",
         "Pipeline: Docling (reading order) → `segment.py` → lexicon pass → Gemma 4 31B structuring pass "
         "(`google.gemma-4-31b`, temperature 0, PII redacted) → doc 04 normaliser (`norm-1.0`, the only source of values).",
         "", "## Boundary F1", "", "| Variant | Precision | Recall | F1 |", "|---|---|---|---|"]
    for v in VARIANTS:
        pr, rc, f = f1([r for r in rows if r["variant"] == v])
        L.append(f"| {v} | {pr:.3f} | {rc:.3f} | **{f:.3f}** |")
    pr, rc, f = f1(rows)
    L += [f"| all | {pr:.3f} | {rc:.3f} | **{f:.3f}** |", "",
          "A predicted clause matches a gold start on the same page when its first line (first cell for table rows) "
          "starts with the gold prefix (photos: fuzzy ratio ≥ 80 on the prefix).", "",
          "## doc_terms vs terms.yaml", "",
          "| Variant | Truth terms found (lexicon only) | Found (lexicon + LLM) | Terms emitted | Wrong value | Unparsed (value=None) |",
          "|---|---|---|---|---|---|"]
    for v in VARIANTS:
        R = [r for r in rows if r["variant"] == v]
        s = {k: sum(r["all"][k] for r in R) for k in ("truth", "found", "terms", "wrong_value", "unparsed")}
        lf = sum(r["lex"]["found"] for r in R)
        L.append(f"| {v} | {lf}/{s['truth']} | {s['found']}/{s['truth']} ({100 * s['found'] / s['truth']:.1f}%) | "
                 f"{s['terms']} | {s['wrong_value']} | {s['unparsed']} |")
    L += ["", "*Found* = a doc_term with the same page, field and value as a terms.yaml entry. *Wrong value* = a term whose "
          "field appears in terms.yaml with a different value. Terms not in terms.yaml (e.g. the loan amount repeated in "
          "clause 1) are not counted as wrong.", "",
          "## Per document", "", "| Agreement | Variant | Boundaries (tp / pred / gold) | Terms found | LLM proposed → kept | Errors |",
          "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['ag']} | {r['variant']} | {r['tp']} / {r['pred']} / {r['gold']} | {r['all']['found']}/{r['all']['truth']} | "
                 f"{r['llm']['proposed']} → {r['llm']['kept']} | {'; '.join(f'{a} p{p} “{s}' + ('' if a == 'extra' else '”') for a, p, s in r['errs']) or '—'} |")
    out = ROOT / "reports" / f"{today}_T2.3_segmentation.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
