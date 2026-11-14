"""Local embedding provider (doc 09 §2, ADR-005 working choice, provisional until HC-4).

`intfloat/multilingual-e5-small` (384-dim, multilingual incl. Hindi) on CPU via transformers: mean pooling over the
last hidden state, L2-normalised, with the model's required "query: " / "passage: " prefixes. Contract text never
leaves our infrastructure. The model downloads on first use into HF_HOME.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Literal


class LocalE5Embedder:
    def __init__(self, model: str = "intfloat/multilingual-e5-small", dim: int = 384, batch_size: int = 32) -> None:
        self.name, self.dim, self.batch_size = model, dim, batch_size
        self._tok: Any = None
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        with self._lock:
            if self._model is None:
                from transformers import AutoModel, AutoTokenizer

                self._tok = AutoTokenizer.from_pretrained(self.name)
                self._model = AutoModel.from_pretrained(self.name).eval()

    def embed_sync(self, texts: list[str], *, kind: Literal["query", "passage"]) -> list[list[float]]:
        import torch

        self._load()
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [f"{kind}: {t}" for t in texts[i:i + self.batch_size]]
            enc = self._tok(batch, padding=True, truncation=True, max_length=512, return_tensors="pt")
            with torch.no_grad():
                hidden = self._model(**enc).last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            vec = torch.nn.functional.normalize((hidden * mask).sum(1) / mask.sum(1), dim=-1)
            out += vec.tolist()
        if out and len(out[0]) != self.dim:
            raise ValueError(f"embedding dim {len(out[0])} != configured {self.dim}")
        return out

    async def embed(self, texts: list[str], *, kind: Literal["query", "passage"]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_sync, texts, kind=kind)


def from_settings() -> LocalE5Embedder:
    from app.core.config import get_settings

    s = get_settings()
    if s.EMBEDDING_PROVIDER != "local_e5":
        raise ValueError(f"unsupported EMBEDDING_PROVIDER {s.EMBEDDING_PROVIDER!r}")
    return LocalE5Embedder(s.EMBEDDING_MODEL, s.EMBEDDING_DIM)
