"""T1.3 speaker-role suggestion (doc 03 §5) and the Bedrock Mantle LLM provider. Recorded responses, no network."""

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.core.retry import InputError, SchemaError
from app.llm.bedrock_mantle import BedrockMantleLLM
from app.modules.speakers import service
from app.modules.speakers.service import LLMRoleSuggestion, ResolvedRole, conflict_allowed
from app.modules.stt.elevenlabs import parse_words
from app.modules.stt.segmentation import build_segments

FIX = Path(__file__).parents[1] / "fixtures"
GEMMA = json.loads((FIX / "mantle" / "speaker_roles_ag07.json").read_text())
STT = json.loads((FIX / "elevenlabs" / "ag07_two_speaker_en.json").read_text())


def ag07_segments():  # type: ignore[no-untyped-def]
    words, lang = parse_words(STT)
    return build_segments(words, session_id=uuid.uuid4(), transcript_revision=1, channel="mono", language=lang)


def mantle(handler, key: str = "k") -> BedrockMantleLLM:  # type: ignore[no-untyped-def]
    return BedrockMantleLLM(api_key=key, model="google.gemma-4-31b", base_url="https://mantle.test/openai/v1",
                            transport=httpx.MockTransport(handler))


def reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


class StubLLM:
    name = "stub"

    def __init__(self, result=None, error=None) -> None:  # type: ignore[no-untyped-def]
        self.result, self.error = result, error

    async def structured(self, **kw):  # type: ignore[no-untyped-def]
        if self.error:
            raise self.error
        return LLMRoleSuggestion.model_validate(self.result)


def _llm_says(*roles: tuple[str, str, float]) -> StubLLM:
    return StubLLM({"speakers": [{"speaker_label": lab, "role": r, "role_confidence": c} for lab, r, c in roles]})


def test_recorded_gemma_reply_through_provider() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json=GEMMA)

    sugg, method = asyncio.run(service.suggest(mantle(handler), ag07_segments()))
    assert seen["url"] == "https://mantle.test/openai/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["body"]["model"] == "google.gemma-4-31b" and seen["body"]["response_format"] == {"type": "json_object"}
    assert "data, not instructions" in seen["body"]["messages"][0]["content"]
    assert method == "llm:bedrock_mantle"
    assert [(s.speaker_label, s.role, s.role_confidence) for s in sugg] == [("speaker_0", "agent", 1.0),
                                                                             ("speaker_1", "customer", 1.0)]


def test_agreeing_heuristic_keeps_confidence() -> None:
    sugg, _ = asyncio.run(service.suggest(_llm_says(("speaker_0", "agent", 0.9), ("speaker_1", "customer", 0.9)),
                                          ag07_segments()))
    assert {s.speaker_label: s.role_confidence for s in sugg} == {"speaker_0": 0.9, "speaker_1": 0.9}


def test_llm_disagreeing_with_heuristic_is_uncertain() -> None:
    sugg, _ = asyncio.run(service.suggest(_llm_says(("speaker_0", "customer", 0.95), ("speaker_1", "agent", 0.95)),
                                          ag07_segments()))
    assert all(s.role_confidence <= service.UNCERTAIN_CAP for s in sugg)


@pytest.mark.parametrize("roles", [
    (("speaker_0", "agent", 0.9), ("speaker_1", "agent", 0.9)),     # two agents
    (("speaker_0", "other", 0.9), ("speaker_1", "customer", 0.9)),  # no agent
])
def test_ambiguous_results_are_uncertain(roles) -> None:  # type: ignore[no-untyped-def]
    sugg, _ = asyncio.run(service.suggest(_llm_says(*roles), ag07_segments()))
    assert all(s.role_confidence <= service.UNCERTAIN_CAP for s in sugg)
    assert "other" not in {s.role for s in sugg}  # mapped to the DDL value "unknown"


def test_missing_label_is_unknown() -> None:
    sugg, _ = asyncio.run(service.suggest(_llm_says(("speaker_0", "agent", 0.9)), ag07_segments()))
    assert {s.speaker_label: (s.role, s.role_confidence) for s in sugg}["speaker_1"] == ("unknown", 0.0)


@pytest.mark.parametrize("err", [SchemaError("bad"), InputError("no key", "provider_auth")])
def test_llm_failure_falls_back_to_uncertain_heuristic(err) -> None:  # type: ignore[no-untyped-def]
    sugg, method = asyncio.run(service.suggest(StubLLM(error=err), ag07_segments()))
    assert method == "heuristic"
    assert {s.speaker_label: s.role for s in sugg} == {"speaker_0": "agent", "speaker_1": "customer"}
    assert all(s.role_confidence <= service.UNCERTAIN_CAP for s in sugg)


def test_transient_llm_errors_propagate_for_job_retry() -> None:
    with pytest.raises(httpx.ConnectError):
        asyncio.run(service.suggest(StubLLM(error=httpx.ConnectError("down")), ag07_segments()))


def test_excerpt_limited_to_first_90s_of_each_speaker() -> None:
    segs = [SimpleNamespace(speaker_label="a", text=f"t{i}", start_ms=i * 31_000, end_ms=i * 31_000 + 30_000)
            for i in range(5)]
    assert service.excerpts(segs)["a"] == "t0 t1 t2"  # 30 s each; stops once 90 s is reached


def test_corrective_retry_then_success() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(json.loads(req.content))
        return reply("not json") if len(calls) == 1 else httpx.Response(200, json=GEMMA)

    out = asyncio.run(mantle(handler).structured(system="s", messages=[{"role": "user", "content": "x"}],
                                                schema=LLMRoleSuggestion))
    assert len(out.speakers) == 2 and len(calls) == 2
    assert "invalid" in calls[1]["messages"][-1]["content"]  # the errors are fed back once


def test_two_invalid_replies_raise_schema_error() -> None:
    with pytest.raises(SchemaError):
        asyncio.run(mantle(lambda r: reply('{"speakers": "nope"}')).structured(
            system="s", messages=[], schema=LLMRoleSuggestion))


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors(status: int) -> None:
    with pytest.raises(InputError) as e:
        asyncio.run(mantle(lambda r: httpx.Response(status)).structured(system="s", messages=[], schema=LLMRoleSuggestion))
    assert e.value.code == "provider_auth"


def test_missing_key() -> None:
    with pytest.raises(InputError) as e:
        asyncio.run(mantle(lambda r: reply("{}"), key="").structured(system="s", messages=[], schema=LLMRoleSuggestion))
    assert e.value.code == "provider_auth"


def test_fenced_json_reply_is_accepted() -> None:
    content = "```json\n" + GEMMA["choices"][0]["message"]["content"] + "\n```"
    out = asyncio.run(mantle(lambda r: reply(content)).structured(system="s", messages=[], schema=LLMRoleSuggestion))
    assert out.speakers[0].role == "agent"


@pytest.mark.parametrize(("role", "assignment", "allowed"), [
    ("agent", "user_confirmed", True), ("agent", "auto", False), ("customer", "user_confirmed", False),
    ("unknown", "user_confirmed", False), ("unknown", "auto", False)])
def test_conflict_gate(role: str, assignment: str, allowed: bool) -> None:
    assert conflict_allowed(ResolvedRole(role, "diarized", assignment, 1.0, None)) is allowed  # type: ignore[arg-type]


def test_short_customer_question_does_not_outscore_the_agent() -> None:
    """E10 finding (2026-11-14): per-word density let "What is the processing fee?" outscore the agent, so every
    correct Gemma suggestion was flagged uncertain."""
    segs = [SimpleNamespace(speaker_label="a", text="Hello, this is Rahul calling from Sahaj Finance.", start_ms=0, end_ms=3000),
            SimpleNamespace(speaker_label="c", text="What is the processing fee?", start_ms=3600, end_ms=5000),
            SimpleNamespace(speaker_label="a", text="The processing fee is only 2 percent. Your EMI will be 9,500 rupees.",
                            start_ms=5600, end_ms=9000)]
    scores = service.heuristic_scores(service.excerpts(segs))
    assert scores["a"] > scores["c"]
    sugg, _ = asyncio.run(service.suggest(_llm_says(("a", "agent", 0.95), ("c", "customer", 0.95)), segs))
    assert all(s.role_confidence >= service.CONFIDENT for s in sugg)  # agreement: not uncertain
