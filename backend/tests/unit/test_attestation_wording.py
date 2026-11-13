"""The attestation is a customer self-declaration, never agent consent (doc 01 §5)."""

import re
from pathlib import Path

from app.modules.consent.service import NOT_VERIFIED_SUFFIX, attestation_note

FORBIDDEN = re.compile(r"agent (has )?(consented|agreed|gave consent)|agent'?s consent|consent of the agent", re.I)
TEXTS = Path(__file__).resolve().parents[2] / "data" / "consent_texts"


def test_notes_are_customer_statements_and_not_verified():
    for v in ("yes", "no", "unsure"):
        note = attestation_note(v)  # type: ignore[arg-type]
        assert note.startswith("The customer states")
        assert NOT_VERIFIED_SUFFIX in note
        assert not FORBIDDEN.search(note)


def test_draft_texts_marked_draft_and_never_claim_agent_consent():
    files = list(TEXTS.glob("*.md"))
    assert len(files) == 4
    for f in files:
        body = f.read_text(encoding="utf-8")
        assert "DRAFT" in body or "ड्राफ़्ट" in body
        assert "HC-7" in body
        assert not FORBIDDEN.search(body)
    att = (TEXTS / "attestation.en.md").read_text()
    assert "not proof that the other person agreed" in att
