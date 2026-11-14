"""Check that every ground-truth raw_text appears verbatim (whitespace-normalised) on its stated PDF page.

uv run --no-project --with pypdf==5.1.0 --with pyyaml==6.0.2 python eval/tools/check_agreements.py
"""
import hashlib
import re
import sys
from pathlib import Path

import yaml
from pypdf import PdfReader

root = Path(__file__).resolve().parents[1] / "datasets" / "agreements"
norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
bad = n = 0
for d in sorted(p for p in root.iterdir() if p.is_dir()):
    truth = yaml.safe_load((d / "terms.yaml").read_text(encoding="utf-8"))
    pdf = d / "agreement.pdf"
    if hashlib.sha256(pdf.read_bytes()).hexdigest() != truth["files"]["agreement.pdf"]["sha256"]:
        print(f"{d.name}: sha256 mismatch"); bad += 1
    pages = [norm(p.extract_text()) for p in PdfReader(pdf).pages]
    for t in truth["terms"]:
        n += 1
        if norm(t["raw_text"]) not in pages[t["page"] - 1]:
            print(f"{d.name}: not on page {t['page']}: {t['field']} -> {t['raw_text']!r}"); bad += 1
print(f"{n} terms checked, {bad} problems"); sys.exit(1 if bad else 0)
