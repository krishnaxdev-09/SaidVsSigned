"""Generate the 30 scripted conversations for T1.8 (doc 12 §A.1 item 2) with macOS text-to-speech.

15 English + 15 Hinglish; 20 mono + 10 stereo (agent left, customer right). One English mono, one Hinglish mono and one
stereo (alternating language) per synthetic agreement AG01–AG10. Five rotating trap patterns (v0–v4): plain terms,
hypothetical + understated fee, conditional zero charge + customer echo, vague rate + correction, approximate range +
zero insurance.

SYNTHETIC SPEECH: clean, evenly paced TTS voices. Results on this set are optimistic versus real phone calls and must
not be read as real-world accuracy.

Output per conversation in eval/datasets/conversations/<id>/: call.m4a, script.yaml (turns with timings, expected
claims). Run: uv run --no-project --with pyyaml==6.0.2 python eval/tools/gen_conversations.py   (needs say + ffmpeg)
"""

from __future__ import annotations

import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "datasets" / "conversations"
VERSION = "conversations-v1"
VOICES = {("en", "agent"): "Rishi", ("en", "customer"): "Samantha", ("hi", "agent"): "Lekha", ("hi", "customer"): "Rishi"}
GAP_S = 0.6


def terms(ag: str) -> dict[str, Decimal | str]:
    t = yaml.safe_load((ROOT / "datasets" / "agreements" / ag / "terms.yaml").read_text(encoding="utf-8"))
    first = {}
    for term in t["terms"]:
        first.setdefault(term["field"], term)
    fee = first.get("processing_fee")
    rate = Decimal(str(first["interest_rate"]["value"]))
    return {"lender": t["lender"].split(" (")[0].replace(" Pvt. Ltd.", "").replace(" Ltd.", ""),
            "amount": Decimal(str(first["loan_amount"]["value"])), "rate": rate.quantize(Decimal(1)),
            "emi": Decimal(str(first["emi"]["value"])), "tenure": Decimal(str(first["tenure"]["value"])),
            "fee": Decimal(str(fee["value"])) if fee and fee["quantity"] == "percent" else Decimal(2)}


def inr(d: Decimal) -> str:
    s = str(int(d))
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:]); head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join([*groups, tail]) if groups else tail


def C(t: str, kind: str, value: object = None, *, speaker: str = "agent", vague: bool = False, optional: bool = False) -> dict:
    """Expected claim: kind ∈ verifiable / hypothetical / question; value: Decimal, (lo, hi) or None."""
    v = None if value is None else ([str(value[0]), str(value[1])] if isinstance(value, tuple) else str(value))
    return {"claim_type": t, "kind": kind, "value": v, "speaker": speaker, "vague": vague, "optional": optional}


def script(lang: str, variant: int, k: dict) -> tuple[list[tuple[str, str]], list[dict]]:
    r, fee, emi, ten, amt, lender = k["rate"], k["fee"], k["emi"], k["tenure"], k["amount"], k["lender"]
    fee_s = str(fee.normalize())
    if lang == "en":
        turns = [("agent", f"Hello, this is Rahul calling from {lender}."),
                 ("customer", "What is the processing fee?"),
                 ("agent", f"The processing fee is only {fee_s} percent."),
                 ("agent", f"Your loan of {inr(amt)} rupees is approved."),
                 ("agent", f"Your EMI will be {inr(emi)} rupees for {ten} months.")]
    else:
        turns = [("agent", f"नमस्ते, मैं {lender} से राहुल बोल रहा हूँ।"),
                 ("customer", "Processing fee kitni hai?"),
                 ("agent", f"प्रोसेसिंग फीस सिर्फ {fee_s} प्रतिशत है।"),
                 ("agent", f"आपका {inr(amt)} रुपये का लोन approve हो गया है।"),
                 ("agent", f"आपकी EMI {inr(emi)} रुपये होगी, {ten} महीने के लिए।")]
    exp = [C("lender_identity", "verifiable"), C("processing_fee", "question", speaker="customer", optional=True),
           C("processing_fee", "verifiable", fee), C("loan_amount", "verifiable", amt), C("emi", "verifiable", emi),
           C("tenure", "verifiable", ten)]
    en = lang == "en"
    if variant == 0:
        turns.append(("agent", f"The interest rate is {r} percent reducing." if en else f"इंटरेस्ट रेट {r} प्रतिशत है।"))
        exp.append(C("interest_rate", "verifiable", r))
    elif variant == 1:
        turns.append(("agent", f"If you had a salary account with us, the rate would have been {r - 1} percent." if en
                      else f"अगर आपका सैलरी अकाउंट होता, तो रेट {r - 1} प्रतिशत होता।"))
        exp.append(C("interest_rate", "hypothetical", r - 1))
    elif variant == 2:
        turns.append(("agent", "If you pay every EMI on time, there is no penal charge." if en
                      else "अगर आप हर EMI टाइम पर भरेंगे, तो पेनल चार्ज कोई नहीं।"))
        turns.append(("customer", f"So the EMI is {inr(emi)} rupees, right?" if en else f"To EMI {inr(emi)} rupees hai na?"))
        exp += [C("penal_charge", "verifiable", 0), C("emi", "question", emi, speaker="customer")]
    elif variant == 3:
        turns.append(("agent", "Our interest rate is the lowest in the market." if en else "हमारा इंटरेस्ट रेट मार्केट में सबसे कम है।"))
        turns.append(("agent", f"Sorry, I mean the processing fee is {fee + 1} percent." if en
                      else f"माफ़ कीजिए, प्रोसेसिंग फीस {fee + 1} प्रतिशत है।"))
        exp += [C("interest_rate", "verifiable", vague=True), C("processing_fee", "verifiable", fee + 1)]
    else:
        turns.append(("agent", f"The interest will be around {r} to {r + 1} percent." if en
                      else f"इंटरेस्ट लगभग {r} से {r + 1} प्रतिशत रहेगा।"))
        turns.append(("agent", "There is no insurance charge." if en else "इंश्योरेंस चार्ज कोई नहीं है।"))
        exp += [C("interest_rate", "verifiable", (r, r + 1)), C("insurance_bundled", "verifiable", 0)]
    return turns, exp


def _ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *args], check=True)


def _dur(p: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                check=True, capture_output=True, text=True).stdout)


def render(cid: str, lang: str, stereo: bool, turns: list[tuple[str, str]], exp: list[dict], meta: dict) -> None:
    d = OUT / cid
    d.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        parts, timeline, clock = [], [], 0.0
        for i, (spk, text) in enumerate(turns):
            aiff, wav = t / f"{i}.aiff", t / f"{i}.wav"
            subprocess.run(["say", "-v", VOICES[(lang, spk)], "-o", str(aiff), text], check=True)
            pan = ("pan=stereo|c0=c0|c1=0*c0" if spk == "agent" else "pan=stereo|c0=0*c0|c1=c0") if stereo else "anull"
            _ff("-i", str(aiff), "-af", pan, "-ar", "16000", "-ac", "2" if stereo else "1", str(wav))
            dur = _dur(wav)
            timeline.append({"speaker": spk, "text": text, "start_s": round(clock, 3), "end_s": round(clock + dur, 3)})
            parts += [wav]
            clock += dur + GAP_S
        gap = t / "gap.wav"
        _ff("-f", "lavfi", "-i", f"anullsrc=r=16000:cl={'stereo' if stereo else 'mono'}", "-t", str(GAP_S), str(gap))
        lst = t / "list.txt"
        lst.write_text("".join(f"file '{p}'\nfile '{gap}'\n" for p in parts))
        _ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "aac", "-b:a", "64k", str(d / "call.m4a"))
    doc = {"dataset_version": VERSION, "id": cid, "synthetic_tts": True, "language": lang,
           "layout": "stereo_split" if stereo else "mono", **meta, "voices": {s: VOICES[(lang, s)] for s in ("agent", "customer")},
           "turns": timeline, "expected_claims": exp}
    (d / "script.yaml").write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main() -> None:
    n = 0
    for i in range(10):
        ag = f"AG{i + 1:02d}"
        k = terms(ag)
        for lang, stereo, suffix in (("en", False, "EN"), ("hi", False, "HI"), ("en" if i % 2 == 0 else "hi", True, "ST")):
            variant = (i + {"EN": 0, "HI": 2, "ST": 4}[suffix]) % 5
            turns, exp = script(lang, variant, k)
            render(f"C{ag[2:]}{suffix}", lang, stereo, turns, exp, {"agreement": ag, "variant": variant})
            n += 1
    print(f"wrote {n} conversations to {OUT}")


if __name__ == "__main__":
    main()
