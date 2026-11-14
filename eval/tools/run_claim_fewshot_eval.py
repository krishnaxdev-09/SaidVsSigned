"""Leave-one-out evaluation of the claim extractor on its 24 few-shot examples (T1.6 HC-2 review).

For each example, the prompt contains the OTHER 23 examples; the real LLM extracts the held-out conversation; the
output is anchored, normalised and compared with the expected claims.

A claim matches when claim_type and utterance_kind are equal, the quotes overlap, and the normalised values are
equal (or both have none). Writes a markdown report with every expected and actual claim.

Run from backend/:  set -a; . ../.env; set +a; PYTHONPATH=. uv run python ../eval/tools/run_claim_fewshot_eval.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.llm.bedrock_mantle import from_settings
from app.modules.claims import extract
from app.modules.claims.normalize import normalise
from app.schemas.claims import ExtractedValue
from app.modules.stt.base import WordToken

REPORTS = Path(__file__).resolve().parents[1] / "reports"


def segs_of(ex: dict[str, Any]) -> list[Any]:
    out = []
    for s in ex["segments"]:
        words = [WordToken(text=w, start_ms=0, end_ms=0, confidence=None, speaker_label=s["speaker"], is_numeric=False)
                 for w in s["text"].split()]
        out.append(SimpleNamespace(id=uuid.uuid4(), speaker_label=s["speaker"], text=" ".join(s["text"].split()),
                                   language="en" if ex["language"] == "en" else "hi", words=words, start_ms=0, end_ms=0))
    return out


def expected_rows(ex: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for c in ex["claims"]:
        quote = " ".join(a["text"] for a in c["anchors"])
        vals = [normalise(ExtractedValue(field=v["field"], raw_text=v["raw_text"], quantity=v["quantity"]), context=quote,
                          language="en" if ex["language"] == "en" else "hi") for v in c["values"]]
        rows.append({"type": c["claim_type"], "kind": c["utterance_kind"], "vagueness": c["vagueness"], "quote": quote,
                     "values": [_fmt(v) for v in vals]})
    return rows


def _fmt(v: ExtractedValue) -> str:
    if v.normaliser_version is None:
        return f"{v.raw_text!r}→unparsed"
    num = f"{v.value_low}–{v.value_high}" if v.value_low is not None else f"{v.value}"  # already plain (normaliser)
    return f"{v.raw_text!r}→{num}"


def _overlap(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a in b or b in a or len(set(a.split()) & set(b.split())) >= max(1, min(len(a.split()), len(b.split())) // 2)


VERIFIABLE = {"promise", "assertion", "conditional"}


def _kind_ok(a: str, e: str, lenient: bool) -> bool:
    """Strict: identical kind. Lenient (user rule 2026-11-14): variation allowed only when verification
    eligibility is unchanged, i.e. both kinds verifiable or both identical."""
    return a == e or (lenient and a in VERIFIABLE and e in VERIFIABLE)


def match(exp: list[dict[str, Any]], act: list[dict[str, Any]], lenient: bool = False) -> tuple[int, list[bool], list[bool]]:
    used = [False] * len(act)
    exp_hit = []
    for e in exp:
        hit = False
        for j, a in enumerate(act):
            if not used[j] and a["type"] == e["type"] and _kind_ok(a["kind"], e["kind"], lenient) \
                    and a["vagueness"] == e["vagueness"] and _overlap(a["quote"], e["quote"]) \
                    and [x.split("→")[1] for x in a["values"]] == [x.split("→")[1] for x in e["values"]]:
                used[j] = hit = True
                break
        exp_hit.append(hit)
    return sum(exp_hit), exp_hit, used


async def run_one(llm: Any, ex: dict[str, Any], sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        segs = segs_of(ex)
        text_block, refs = extract.render_segments(segs, [])
        msgs = [*extract.fewshot_messages(exclude_id=ex["id"]), {"role": "user", "content": text_block}]
        out = await llm.structured(system=extract.SYSTEM_PROMPT, messages=msgs, schema=extract.LLMWindowOutput,
                                   temperature=0.0, max_tokens=4000, timeout_s=120)
        drops = extract.DropLog()
        built = [extract.build_claim(c, refs, set(refs), 1, drops) for c in out.claims]
        act = [{"type": b.claim_type.value, "kind": b.utterance_kind.value, "vagueness": b.vagueness, "quote": b.quote,
                "values": [_fmt(v) for v in b.values]} for b in built if b is not None]
        exp = expected_rows(ex)
        n, exp_hit, used = match(exp, act)
        n_len, _, _ = match(exp, act, lenient=True)
        return {"ex": ex, "exp": exp, "act": act, "matched": n, "matched_lenient": n_len, "exp_hit": exp_hit, "act_hit": used,
                "drops": {k: v for k, v in vars(drops).items() if v}}


async def main() -> None:
    llm = from_settings()
    examples = extract.load_fewshots()
    sem = asyncio.Semaphore(6)
    results = await asyncio.gather(*(run_one(llm, ex, sem) for ex in examples))
    tp = sum(r["matched"] for r in results)
    tp_len = sum(r["matched_lenient"] for r in results)
    n_exp = sum(len(r["exp"]) for r in results)
    n_act = sum(len(r["act"]) for r in results)
    fewshot_hash = hashlib.sha256(extract.FEWSHOTS_PATH.read_bytes()).hexdigest()[:12]
    lines = [f"# Claim extraction — leave-one-out on the 24 few-shots ({dt.date.today().isoformat()})", "",
             f"- Model: `{llm.model}` (Bedrock Mantle), prompt `{extract.PROMPT_VERSION}`, few-shots sha256 `{fewshot_hash}`, "
             "temperature 0.", "- Each example is held out of the prompt (23 others as few-shots) and extracted by the real model.",
             "- Strict match = same claim_type, utterance_kind and vagueness, overlapping quote, equal normalised values "
             "(ranges and approximation included).",
             "- Eligibility-equivalent match = the same, except the kind may differ between promise / assertion / "
             "conditional (all verifiable), per the user's rule of 2026-11-14.",
             f"- **Eligibility-equivalent: {tp_len}/{n_exp}.**",
             f"- **Expected claims {n_exp}, extracted {n_act}, matched {tp}.** Recall {tp / n_exp:.2f}, "
             f"precision {tp / max(1, n_act):.2f}. Small, self-authored set: this is a sanity check, not the T1.8 eval.",
             "- Gemma is not fully deterministic at temperature 0: borderline utterance kinds (promise/assertion/"
             "conditional, all verifiable) can differ between runs.",
             ""]
    for r in results:
        ex = r["ex"]
        lines += [f"## {ex['id']} ({ex['language']}) — {ex['teaches']}", "",
                  *(f"> {s['ref']} [{s['speaker']}]: {s['text']}" for s in ex["segments"]), "",
                  "| | type / kind / vagueness | quote | values |", "|---|---|---|---|"]
        for e, hit in zip(r["exp"], r["exp_hit"], strict=True):
            lines.append(f"| expected {'✅' if hit else '❌ missed'} | {e['type']} / {e['kind']} / {e['vagueness']} | "
                         f"{e['quote']} | {', '.join(e['values']) or '—'} |")
        for a, hit in zip(r["act"], r["act_hit"], strict=True):
            lines.append(f"| Gemma {'✅' if hit else '⚠️ extra/different'} | {a['type']} / {a['kind']} / {a['vagueness']} | "
                         f"{a['quote']} | {', '.join(a['values']) or '—'} |")
        if r["drops"]:
            lines.append(f"\nDropped by validators: {r['drops']}")
        lines.append("")
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"{dt.date.today().isoformat()}_claim_extract_fewshot_loo.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"expected {n_exp}, extracted {n_act}, strict {tp}, eligibility-equivalent {tp_len} -> {out}")
    for r in results:
        print(f"  {r['ex']['id']}: {r['matched']}/{len(r['exp'])} matched, {len(r['act'])} extracted", r["drops"] or "")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
