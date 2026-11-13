"""Doc 01 §7 error model: exact code table and RFC 7807 shape."""

import json
import re
from pathlib import Path

import pytest

from app.core.errors import ERROR_CODES, AppError, problem

DOC01 = Path(__file__).resolve().parents[3] / "docs" / "01_PROJECT_SETUP_AND_FOUNDATION.md"


def test_error_codes_match_canonical_doc_table():
    doc = DOC01.read_text()
    documented = {c: int(s) for c, s in re.findall(r"^\| `([a-z_]+)` \| (\d{3}) \|", doc, re.M)}
    assert documented == {c: s for c, (s, _) in ERROR_CODES.items()}


def test_problem_json_shape():
    resp = problem("consent_required", "need consent")
    body = json.loads(resp.body)
    assert resp.status_code == 403
    assert resp.media_type == "application/problem+json"
    assert set(body) == {"type", "title", "status", "detail", "code", "request_id"}
    assert body["code"] == "consent_required"


def test_unknown_code_rejected():
    with pytest.raises(ValueError):
        AppError("made_up_code")
