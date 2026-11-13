"""Provider protocols (interfaces only in Phase 0; no provider is implemented or called).

LLMProvider: doc 04 §3. EmbeddingProvider: doc 09 §2. Model choice: ADR-004 / ADR-005 (open)."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel


class LLMProvider(Protocol):
    name: str

    async def structured(self, *, system: str, messages: list[dict], schema: type[BaseModel],
                         temperature: float = 0.0, max_tokens: int = 2000, timeout_s: float = 30) -> BaseModel: ...

    async def text(self, *, system: str, messages: list[dict], **kw: object) -> str: ...


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str], *, kind: Literal["query", "passage"]) -> list[list[float]]: ...
