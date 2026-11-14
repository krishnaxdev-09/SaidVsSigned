"""End-to-end synthetic demo against the running stack (`make up`): the full MVP flow over the public API.

register → session → consent + attestation → upload call (C05ST, stereo, synthetic TTS) → transcript + speakers
→ confirm speakers → claims → upload loan agreement (AG05 PDF) → document ready → verification results →
play-and-confirm the numeric quote (HC-3) → final results with evidence (clause bbox + snippet URL).
The call contains a self-correction trap: "processing fee is only 2%" … "Sorry, I mean … 3%" vs the KFS "2% + GST".

Run from the repo root:  uv run --no-project --with httpx==0.28.1 python eval/tools/demo_e2e.py [--base http://localhost:8000]
Synthetic data only (HC-7/HC-8 pending for real users).
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CALL = ROOT / "datasets" / "conversations" / "C05ST" / "call.m4a"
DOC = ROOT / "datasets" / "agreements" / "AG05" / "agreement.pdf"


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def wait(label: str, fn, timeout: float = 900, every: float = 3):  # type: ignore[no-untyped-def]
    t0 = time.time()
    while time.time() - t0 < timeout:
        out = fn()
        if out:
            print(f"  ✓ {label} ({time.time() - t0:.0f} s)", flush=True)
            return out
        time.sleep(every)
    sys.exit(f"timed out waiting for {label}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    base = ap.parse_args().base
    c = httpx.Client(base_url=base, timeout=120)
    email = f"demo-{uuid.uuid4().hex[:8]}@example.com"
    assert c.post("/api/v1/auth/register", json={"email": email, "password": "correct horse battery"}).status_code == 201
    tok = c.post("/api/v1/auth/login", json={"email": email, "password": "correct horse battery"}).json()["access_token"]
    c.headers["authorization"] = f"Bearer {tok}"
    sid = c.post("/api/v1/sessions", json={"mode": "upload", "product_type": "personal_loan", "language_hint": "en",
                                           "lender_name": "Kosh Finance (fictional)"}, headers=key()).json()["id"]
    texts = {t["kind"]: t["id"] for t in c.get("/api/v1/consent-texts", params={"locale": "en"}).json()}
    r = c.post(f"/api/v1/sessions/{sid}/consents", headers=key(), json={
        "consent_text_id": texts["processing"], "accepted": True,
        "attestation": {"attestation_text_id": texts["attestation"], "i_was_party": True, "agent_informed": "yes"}})
    assert r.status_code == 201, r.text
    print(f"session {sid}: consent recorded", flush=True)

    with CALL.open("rb") as f:
        r = c.post(f"/api/v1/sessions/{sid}/recordings", headers=key(), data={"channel_layout": "stereo_split"},
                   files={"file": ("call.m4a", f, "audio/mp4")})
    assert r.status_code == 202, r.text
    wait("transcript", lambda: c.get(f"/api/v1/sessions/{sid}/transcript").status_code == 200)
    sp = wait("speaker suggestion", lambda: (s := c.get(f"/api/v1/sessions/{sid}/speakers").json()).get("speakers")
              and any(x["suggested_role"] == "agent" for x in s["speakers"]) and s)
    roles = {s["speaker_label"]: s["suggested_role"] or "unknown" for s in sp["speakers"]}
    r = c.put(f"/api/v1/sessions/{sid}/speakers", json={"transcript_revision": sp["transcript_revision"], "roles": roles})
    assert r.status_code == 200, r.text
    print(f"  speakers confirmed: {roles}", flush=True)
    claims = wait("claims", lambda: c.get(f"/api/v1/sessions/{sid}/claims").json())
    for cl in claims:
        print(f"    claim {cl['claim_type']:16s} {cl['utterance_kind']:12s} “{cl['quote']}”")

    with DOC.open("rb") as f:
        r = c.post(f"/api/v1/sessions/{sid}/documents", headers=key(), data={"kind": "kfs"},
                   files=[("files", ("agreement.pdf", f, "application/pdf"))])
    assert r.status_code == 202, r.text
    did = r.json()["document_id"]
    wait("document ready", lambda: c.get(f"/api/v1/documents/{did}").json()["status"] in ("ready", "failed"), every=5)
    doc = c.get(f"/api/v1/documents/{did}").json()
    assert doc["status"] == "ready", doc
    print(f"  clauses: {len(c.get(f'/api/v1/documents/{did}/clauses').json())}", flush=True)

    def verified():  # type: ignore[no-untyped-def]
        res = c.get(f"/api/v1/sessions/{sid}/results").json()
        return res if res and all(x["changed_because"]["trigger"] != "initial" or x["reason_code"] != "no_document"
                                  for x in res) else None
    res = wait("results after the document", verified, every=4)
    by_claim = {x["claim_id"]: x for x in res}
    for cl in claims:
        x = by_claim.get(cl["id"])
        if x and cl["values"] and x["reason_code"] == "low_asr_confidence":
            # HC-3: the customer plays the clip and confirms the words before a numeric CONFLICT can be shown.
            snippet = c.get(f"/api/v1/sessions/{sid}/audio", params={"from_ms": cl["start_ms"], "to_ms": cl["end_ms"]})
            assert snippet.status_code == 200
            assert c.post(f"/api/v1/claims/{cl['id']}/confirm-quote", json={"confirmed": True}).status_code == 200
            print(f"  quote confirmed: “{cl['quote']}”", flush=True)
    time.sleep(10)  # verify jobs are debounced 2 s; the worker re-verifies the confirmed claim
    final = c.get(f"/api/v1/sessions/{sid}/results").json()
    print("\nRESULTS")
    for x in sorted(final, key=lambda x: ["CONFLICT", "UNCLEAR", "MATCH", "NOT_VERIFIED"].index(x["status"])):
        d = next((e for e in x["evidence"] if e["kind"] == "document"), None)
        print(f"  {x['status']:12s} {x['reason_code'] or '':22s} {x['headline']}")
        print(f"      {x['reason']}")
        if d:
            print(f"      page {d['page']} bboxes={len(d['bboxes'])} → "
                  f"{c.get(f'/api/v1/documents/{d['document_id']}/pages/{d['page']}/image').status_code == 200 and 'image ok'}")
    print(f"\nOpen http://localhost:5173/sessions/{sid} (login {email}) to see it in the UI.")


if __name__ == "__main__":
    main()
