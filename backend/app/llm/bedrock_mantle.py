"""LLMProvider for Amazon Bedrock Mantle, OpenAI-compatible Chat Completions (ADR-004 working provider: Gemma 4 31B).

- Base URL `https://bedrock-mantle.<region>.api.aws/openai/v1` (Gemma 4 is served only under /openai/v1).
- `structured()` asks for a JSON object (`response_format: json_object`), gives the model the JSON Schema of the
  target pydantic model, and validates the reply. A reply that fails validation gets one corrective retry with the
  errors (doc 01 §6 "schema" class); a second failure raises SchemaError.
- Errors: 401/403 -> InputError(provider_auth); 429/5xx/timeouts -> transient (in-call retries, then job policy).
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.retry import InputError, SchemaError, call_with_retries

M = TypeVar("M", bound=BaseModel)


def _extract_json(content: str) -> Any:
    c = content.strip()
    if c.startswith("```"):
        c = c.split("\n", 1)[1] if "\n" in c else c
        c = c.rsplit("```", 1)[0]
    return json.loads(c)


class BedrockMantleLLM:
    name = "bedrock_mantle"

    def __init__(self, *, api_key: str, model: str, base_url: str, max_tokens: int = 4096, timeout_s: float = 120.0,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key
        self.model = model
        self._base = base_url.rstrip("/")
        self._max_tokens = max_tokens
        self._timeout = timeout_s
        self._transport = transport  # tests inject recorded responses

    async def _chat(self, messages: list[dict[str, Any]], *, max_tokens: int, temperature: float,
                    json_mode: bool, timeout_s: float) -> str:
        if not self._key:
            raise InputError("MANTLE_API_KEY is not configured", "provider_auth")
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": max_tokens,
                                "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        async def call() -> str:
            async with httpx.AsyncClient(base_url=self._base, timeout=timeout_s, transport=self._transport) as c:
                r = await c.post("/chat/completions", json=body, headers={"Authorization": f"Bearer {self._key}"})
            if r.status_code in (401, 403):
                raise InputError("Bedrock Mantle rejected the API key", "provider_auth")
            r.raise_for_status()
            return str(r.json()["choices"][0]["message"]["content"] or "")

        return await call_with_retries(call)

    async def structured(self, *, system: str, messages: list[dict[str, Any]], schema: type[M],
                         temperature: float = 0.0, max_tokens: int = 2000, timeout_s: float = 30) -> M:
        sys_msg = {"role": "system", "content": f"{system}\n\nReply with one JSON object only, matching this JSON "
                                                f"Schema:\n{json.dumps(schema.model_json_schema())}"}
        convo = [sys_msg, *messages]
        last_error = ""
        for attempt in range(2):  # one corrective retry
            content = await self._chat(convo, max_tokens=max_tokens, temperature=temperature, json_mode=True,
                                       timeout_s=max(timeout_s, self._timeout))
            try:
                return schema.model_validate(_extract_json(content))
            except (ValueError, ValidationError) as exc:
                last_error = str(exc)[:1000]
                if attempt == 0:
                    convo = [*convo, {"role": "assistant", "content": content},
                             {"role": "user", "content": f"That reply was invalid: {last_error}\nReply again with "
                                                         "a corrected JSON object only."}]
        raise SchemaError(f"LLM reply failed schema validation twice: {last_error}")

    async def text(self, *, system: str, messages: list[dict[str, Any]], **kw: object) -> str:
        return await self._chat([{"role": "system", "content": system}, *messages],
                                max_tokens=int(kw.get("max_tokens", self._max_tokens)),  # type: ignore[call-overload]
                                temperature=float(kw.get("temperature", 0.0)),  # type: ignore[arg-type]
                                json_mode=False, timeout_s=self._timeout)


def from_settings() -> BedrockMantleLLM:
    from app.core.config import get_settings

    s = get_settings()
    return BedrockMantleLLM(api_key=s.MANTLE_API_KEY.get_secret_value(), model=s.MANTLE_MODEL,
                            base_url=s.MANTLE_BASE_URL, max_tokens=s.MANTLE_MAX_TOKENS,
                            timeout_s=s.LLM_TIMEOUT_MS / 1000)
