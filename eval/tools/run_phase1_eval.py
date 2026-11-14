"""T1.8 Phase 1 experiments on the 30 synthetic conversations: E2 (WER + numeric tokens), E3 (word confidences),
E10 (diarization + auto-role), and claim extraction quality (doc 03 §10–11, doc 04 §11–12). Real ElevenLabs + Gemma.

Run from backend/:  set -a; . ../.env; set +a; PYTHONPATH=. uv run python ../eval/tools/run_phase1_eval.py
"""

from __future__ import annotations

import asyncio
import collections
import datetime as dt
import hashlib
import re
import tempfile
import unicodedata
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from app.core.config import get_settings
from app.llm.bedrock_mantle import from_settings
from app.modules.audio import media
from app.modules.claims import extract
from app.modules.claims.job import extract_window
from app.modules.claims.normalize import _phrases, eval_phrase, tokens
from app.modules.speakers import service as speakers
from app.modules.stt.base import WordToken
from app.modules.stt.elevenlabs import ElevenLabsSTT
from app.schemas.claims import VERIFIABLE_KINDS

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "datasets" / "conversations"


class LocalStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    async def put_file(self, bucket: str, key: str, path: str, content_type: str) -> None:
        self.objects[(bucket, key)] = Path(path).read_bytes()

    async def get_file(self, bucket: str, key: str, path: str) -> None:
        Path(path).write_bytes(self.objects[(bucket, key)])


# ---- text metrics ------------------------------------------------------------------------------------------------------

def norm_tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFC", text).lower().replace(",", "")
    return [w for w in re.split(r"[^\w%₹]+", t) if w]


def align(ref: list[str], hyp: list[str]) -> tuple[int, list[bool]]:
    """Levenshtein distance and, for each hypothesis token, whether it aligned to an identical reference token."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    ok, i, j = [False] * m, n, m
    while i > 0 and j > 0:
        if d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            ok[j - 1] = ref[i - 1] == hyp[j - 1]
            i, j = i - 1, j - 1
        elif d[i][j] == d[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    return d[n][m], ok


def numbers_in(text: str) -> set[Decimal]:
    words = " ".join(tokens(text)).split()
    found = set()
    for a, b in _phrases(words, True):
        r = eval_phrase(words[a:b], True)
        if r is not None:
            found.add(r[0])
    return found


def expected_numbers(doc: dict) -> set[Decimal]:
    out = set()
    for c in doc["expected_claims"]:
        v = c["value"]
        vals = v if isinstance(v, list) else ([v] if v is not None else [])
        out |= {Decimal(x) for x in vals if Decimal(x) != 0}
    return out


# ---- claims matching ---------------------------------------------------------------------------------------------------

def kind_class(kind: str) -> str:
    return "verifiable" if kind in {k.value for k in VERIFIABLE_KINDS} else kind


def value_of(c: Any) -> Any:
    if not c.values:
        return None
    v = c.values[0]
    if v.value_low is not None:
        return [str(v.value_low), str(v.value_high)]
    return str(v.value) if v.value is not None else "UNPARSED"


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        return [Decimal(x) for x in a] == [Decimal(x) for x in b]
    if a is None or b is None or a == "UNPARSED":
        return a == b
    return Decimal(a) == Decimal(b)


def score_claims(exp: list[dict], act: list[Any]) -> dict[str, Any]:
    required = [e for e in exp if not e["optional"]]
    used = [False] * len(act)

    def matches(e: dict, a: Any) -> bool:
        if a.claim_type.value != e["claim_type"] or kind_class(a.utterance_kind.value) != e["kind"]:
            return False
        if e["vague"]:
            return a.vagueness == "vague"
        return a.vagueness != "vague" and _eq(value_of(a), e["value"])

    tp = 0
    hit = []
    for e in required:
        found = False
        for j, a in enumerate(act):
            if not used[j] and matches(e, a):
                used[j] = True; tp += 1; found = True; break
        hit.append(found)
    for e in exp:  # optional expected claims (customer questions): neither credit nor penalty
        if e["optional"]:
            for j, a in enumerate(act):
                if not used[j] and a.claim_type.value == e["claim_type"] and kind_class(a.utterance_kind.value) == e["kind"]:
                    used[j] = True; break
    fp = sum(1 for u in used if not u)
    # A hypothetical/question counts as misread only if it was NOT matched with its own kind and an unused checkable
    # claim with the same type and value exists (the agent's own real claim is not a misreading).
    misclassified = sum(1 for e, h in zip(required, hit, strict=True) if e["kind"] in ("hypothetical", "question") and not h
                        and any(not used[j] and a.claim_type.value == e["claim_type"]
                                and kind_class(a.utterance_kind.value) == "verifiable" and _eq(value_of(a), e["value"])
                                for j, a in enumerate(act)))
    vals = [v for a in act for v in a.values]
    return {"tp": tp, "fp": fp, "required": len(required), "misclassified": misclassified,
            "hypo_q": sum(1 for e in required if e["kind"] in ("hypothetical", "question")),
            "values": len(vals), "unparsed": sum(1 for v in vals if v.normaliser_version is None),
            "llm_values": sum(1 for v in vals if v.value is not None and v.normaliser_version is None)}


# ---- per conversation --------------------------------------------------------------------------------------------------

async def run_one(doc: dict, stt: ElevenLabsSTT, store: LocalStore, llm: Any, sem: asyncio.Semaphore) -> dict:
    async with sem:
        lang, stereo = doc["language"], doc["layout"] == "stereo_split"
        sid = uuid.uuid4()
        with tempfile.TemporaryDirectory() as tmp:
            outs = await media.normalise(DATA / doc["id"] / "call.m4a", Path(tmp), stereo_split=stereo, timeout_s=300)
            for name, p in outs.items():
                await store.put_file("audio", f"{doc['id']}/{name}", str(p), "audio/wav")
        sources = [("L", "L.wav"), ("R", "R.wav")] if stereo else [("mono", "normalized.wav")]
        segs = []
        for ch, name in sources:
            segs += await stt.transcribe_file(f"{doc['id']}/{name}", diarize=not stereo, num_speakers=None if stereo else 2,
                                              language_hint=None, keyterms=["processing fee", "EMI", "lakh"],
                                              session_id=sid, transcript_revision=1, channel=ch)  # type: ignore[arg-type]
        segs.sort(key=lambda g: g.start_ms)
        turns = doc["turns"]
        ref_text = " ".join(t["text"] for t in turns)
        hyp_text = " ".join(g.text for g in segs)
        ref_t, hyp_t = norm_tokens(ref_text), norm_tokens(hyp_text)
        dist, ok = align(ref_t, hyp_t)
        words = [w for g in segs for w in g.words]
        conf_ok = [w.confidence for w, good in zip([w for g in segs for w in g.words if norm_tokens(w.text)], ok, strict=False)
                   if good and w.confidence is not None]
        conf_bad = [w.confidence for w, good in zip([w for g in segs for w in g.words if norm_tokens(w.text)], ok, strict=False)
                    if not good and w.confidence is not None]
        exp_nums = expected_numbers(doc)
        found = numbers_in(hyp_text)
        res: dict[str, Any] = {"id": doc["id"], "lang": lang, "stereo": stereo, "ref_words": len(ref_t), "edits": dist,
                               "num_expected": len(exp_nums), "num_found": len(exp_nums & found),
                               "num_missed": sorted(str(x) for x in exp_nums - found),
                               "words": len(words), "with_conf": sum(1 for w in words if w.confidence is not None),
                               "conf_ok": conf_ok, "conf_bad": conf_bad, "hyp": hyp_text}
        # E10: true speaker per word from the script timeline (mono only; stereo is channel = speaker by design)
        role_map: dict[str | None, str] = {}
        if not stereo:
            def truth(ms: int) -> str:
                s = ms / 1000
                return min(turns, key=lambda t: 0 if t["start_s"] <= s <= t["end_s"] else min(abs(s - t["start_s"]), abs(s - t["end_s"])))["speaker"]
            votes: dict[str | None, collections.Counter[str]] = collections.defaultdict(collections.Counter)
            for w in words:
                votes[w.speaker_label][truth((w.start_ms + w.end_ms) // 2)] += 1
            role_map = {lab: c.most_common(1)[0][0] for lab, c in votes.items()}
            res["diar_correct"] = sum(c[role_map[lab]] for lab, c in votes.items())
            res["diar_labels"] = len(votes)
            sugg, method = await speakers.suggest(llm, segs)
            agent = [s for s in sugg if s.role == "agent"]
            res["role_ok"] = len(agent) == 1 and role_map.get(agent[0].speaker_label) == "agent"
            res["role_uncertain"] = any(s.role_confidence < speakers.CONFIDENT for s in sugg)
            res["role_method"] = method
        # Claims on the STT transcript (end to end) and on the perfect reference text (LLM only)
        drops = extract.DropLog()
        stt_claims: list[Any] = []
        for core, ctx in extract.windows(segs):
            stt_claims += await extract_window(llm, core, ctx, 1, drops)
        res["stt"] = score_claims(doc["expected_claims"], extract.consolidate(stt_claims))
        ref_segs = [SimpleNamespace(id=uuid.uuid4(), speaker_label=t["speaker"], text=" ".join(t["text"].split()),
                                    language="en" if lang == "en" else "hi",
                                    words=[WordToken(text=w, start_ms=0, end_ms=0, confidence=None, speaker_label=None,
                                                     is_numeric=False) for w in t["text"].split()]) for t in turns]
        ref_claims: list[Any] = []
        ref_drops = extract.DropLog()
        for core, ctx in extract.windows(ref_segs):
            ref_claims += await extract_window(llm, core, ctx, 1, ref_drops)
        res["ref"] = score_claims(doc["expected_claims"], extract.consolidate(ref_claims))
        res["drops"] = {k: v for k, v in {**vars(drops)}.items() if v}
        return res


def pct(a: float, b: float) -> str:
    return f"{100 * a / b:.1f}%" if b else "n/a"


def agg(rows: list[dict], key: str) -> dict[str, int]:
    out: collections.Counter[str] = collections.Counter()
    for r in rows:
        for k, v in r[key].items():
            out[k] += v
    return dict(out)


async def main() -> None:
    s = get_settings()
    store = LocalStore()
    stt = ElevenLabsSTT(store, api_key=s.ELEVENLABS_API_KEY.get_secret_value(), model_id=s.ELEVENLABS_STT_MODEL)  # type: ignore[arg-type]
    llm = from_settings()
    docs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(DATA.glob("*/script.yaml"))]
    rows = await asyncio.gather(*(run_one(d, stt, store, llm, asyncio.Semaphore(4)) for d in docs))
    data_hash = hashlib.sha256(b"".join((DATA / d["id"] / "script.yaml").read_bytes() for d in docs)).hexdigest()[:12]
    L = [f"# Phase 1 experiments — E2, E3, E10 and claim extraction ({dt.date.today().isoformat()})", "",
         "> **Synthetic speech.** All 30 conversations are macOS text-to-speech (agent: Rishi en-IN / Lekha hi-IN; customer: "
         "Samantha en-US / Rishi en-IN), clean and evenly paced, no line noise, scripted by us. These numbers are **optimistic** "
         "and are **not** real-call accuracy. HC-3 should treat them as a pipeline sanity check and ask for real recordings "
         "before declaring language support.", "",
         f"- Dataset `conversations-v1` (sha256 of scripts `{data_hash}`): 30 calls = 15 English + 15 Hinglish, 20 mono + "
         "10 stereo_split. STT `scribe_v2` (diarize, num_speakers=2 for mono); LLM `google.gemma-4-31b` on Bedrock; prompt "
         f"`{extract.PROMPT_VERSION}`; normaliser `norm-1.0`.", ""]
    for lang in ("en", "hi"):
        R = [r for r in rows if r["lang"] == lang]
        name = "English" if lang == "en" else "Hinglish (hi-IN TTS)"
        ref_w, edits = sum(r["ref_words"] for r in R), sum(r["edits"] for r in R)
        ne, nf = sum(r["num_expected"] for r in R), sum(r["num_found"] for r in R)
        L += [f"## E2 — {name}", f"- WER {pct(edits, ref_w)} ({edits} edits / {ref_w} reference words). Note: the reference "
              "is the TTS input; digits vs number words and script differences count as errors.",
              f"- Numeric-token accuracy {pct(nf, ne)} ({nf}/{ne} spoken numbers recovered from the transcript by the normaliser)."]
        missed = [f"{r['id']}: {', '.join(r['num_missed'])}" for r in R if r["num_missed"]]
        if missed:
            L.append("- Missed numbers: " + "; ".join(missed))
        L.append("")
    W = sum(r["words"] for r in rows)
    ok = [c for r in rows for c in r["conf_ok"]]
    bad = [c for r in rows for c in r["conf_bad"]]
    L += ["## E3 — word confidences (batch)", f"- Words with a confidence (`logprob` → exp): {pct(sum(r['with_conf'] for r in rows), W)} "
          f"of {W} words.",
          f"- Mean confidence of words matching the reference: {sum(ok) / max(1, len(ok)):.3f} (n={len(ok)}); of words that "
          f"did not match: {sum(bad) / max(1, len(bad)):.3f} (n={len(bad)}).",
          f"- Share of non-matching words below T_asr_num 0.85: {pct(sum(1 for c in bad if c < 0.85), len(bad))}; share of "
          f"matching words below 0.85: {pct(sum(1 for c in ok if c < 0.85), len(ok))}.", ""]
    M = [r for r in rows if not r["stereo"]]
    L += ["## E10 — diarization and auto-role (20 mono calls)",
          f"- Word-level diarization accuracy (best label → speaker mapping): {pct(sum(r['diar_correct'] for r in M), sum(r['words'] for r in M))}.",
          f"- Calls with exactly 2 speaker labels: {sum(1 for r in M if r['diar_labels'] == 2)}/{len(M)}.",
          f"- Auto-role (Gemma) agent correct: {sum(1 for r in M if r['role_ok'])}/{len(M)}; suggestions flagged uncertain: "
          f"{sum(1 for r in M if r['role_uncertain'])}/{len(M)}; methods: {dict(collections.Counter(r['role_method'] for r in M))}.",
          "- Confirmation-screen usability with 5 users: **not run** (needs people; pending for HC-3).", ""]
    for src, title in (("ref", "on the reference text (LLM only)"), ("stt", "end to end on the STT transcript")):
        L.append(f"## Claim extraction {title}")
        for lang in ("en", "hi", None):
            R = [r for r in rows if lang is None or r["lang"] == lang]
            a = agg(R, src)
            label = {"en": "English", "hi": "Hinglish", None: "All"}[lang]
            L.append(f"- **{label}:** recall {pct(a['tp'], a['required'])} ({a['tp']}/{a['required']}), precision "
                     f"{pct(a['tp'], a['tp'] + a['fp'])} ({a['fp']} extra), hypotheticals/questions taken as checkable: "
                     f"{a['misclassified']}/{a['hypo_q']}, values unparsed {a['unparsed']}/{a['values']}, values not from the "
                     f"normaliser {a['llm_values']}.")
        L.append("")
    L += ["## Doc 04 §12 / doc 03 §11 targets (on this synthetic set)",
          "- Agent claims precision ≥ 0.85 and recall ≥ 0.80; hypotheticals/questions as promises ≤ 5%; 100% verbatim anchors "
          "(enforced: non-verbatim quotes are dropped); 0 values not from the normaliser.",
          f"- Validator drops across all STT runs: {agg(rows, 'drops') or 'none'}.", "",
          "## Per conversation", "| id | lang | layout | WER | numbers | diar | role | STT recall | REF recall |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['id']} | {r['lang']} | {'stereo' if r['stereo'] else 'mono'} | {pct(r['edits'], r['ref_words'])} | "
                 f"{r['num_found']}/{r['num_expected']} | {pct(r.get('diar_correct', 0), r['words']) if not r['stereo'] else 'channel'} | "
                 f"{('✅' if r.get('role_ok') else '❌') if not r['stereo'] else '—'} | {r['stt']['tp']}/{r['stt']['required']} | "
                 f"{r['ref']['tp']}/{r['ref']['required']} |")
    out = ROOT / "reports" / f"{dt.date.today().isoformat()}_phase1_E2_E3_E10_extraction.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(out)
    print("\n".join(line for line in L if line.startswith("- ") and ("WER" in line or "accuracy" in line or "recall" in line
                                                                       or "confidence" in line or "Auto-role" in line)))


if __name__ == "__main__":
    asyncio.run(main())
