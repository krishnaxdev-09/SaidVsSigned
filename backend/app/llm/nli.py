"""Local NLI cross-encoder for the contract engine's semantic path (doc 05 §7.4, Proposed ADR-006 approved at HC-1).

`cross-encoder/nli-deberta-v3-small` on CPU: premise = clause text, hypothesis = the claim's normalised (English)
statement → {contradiction, entailment, neutral} probabilities. Thresholds T_nli_contra / T_nli_entail (doc 08 §5)
are calibrated on the T2.6 pairs and confirmed at HC-4.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Protocol

NLI_MODEL = "cross-encoder/nli-deberta-v3-small"


class NLIProvider(Protocol):
    name: str

    async def score(self, premise: str, hypothesis: str) -> dict[str, float]: ...


class LocalNLI:
    def __init__(self, model: str = NLI_MODEL) -> None:
        self.name = model
        self._tok: Any = None
        self._model: Any = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        with self._lock:
            if self._model is None:
                from transformers import AutoModelForSequenceClassification, AutoTokenizer

                self._tok = AutoTokenizer.from_pretrained(self.name)
                self._model = AutoModelForSequenceClassification.from_pretrained(self.name).eval()

    def score_sync(self, premise: str, hypothesis: str) -> dict[str, float]:
        import torch

        self._load()
        enc = self._tok(premise, hypothesis, truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            probs = torch.softmax(self._model(**enc).logits[0], dim=-1).tolist()
        labels = {i: str(lbl).lower() for i, lbl in self._model.config.id2label.items()}
        return {labels[i]: float(p) for i, p in enumerate(probs)}

    async def score(self, premise: str, hypothesis: str) -> dict[str, float]:
        return await asyncio.to_thread(self.score_sync, premise, hypothesis)
