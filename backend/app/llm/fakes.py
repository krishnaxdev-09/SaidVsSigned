"""Deterministic fakes for tests (doc 13 Phase 0: "provider protocols (fakes)"). Never used in production paths."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel


class FakeLLMProvider:
    name = "fake-llm"

    def __init__(self, structured_responses: list[dict] | None = None, text_response: str = "") -> None:
        self._structured = list(structured_responses or [])
        self._text = text_response
        self.calls = 0

    async def structured(self, *, system: str, messages: list[dict], schema: type[BaseModel],
                         temperature: float = 0.0, max_tokens: int = 2000, timeout_s: float = 30) -> BaseModel:
        self.calls += 1
        return schema.model_validate(self._structured.pop(0))

    async def text(self, *, system: str, messages: list[dict], **kw: object) -> str:
        self.calls += 1
        return self._text


class FakeEmbeddingProvider:
    name = "fake-embedding"

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    async def embed(self, texts: list[str], *, kind: Literal["query", "passage"]) -> list[list[float]]:
        out = []
        for t in texts:
            digest = hashlib.sha256(t.encode()).digest()
            out.append([b / 255 for b in digest[: self.dim]])
        return out
