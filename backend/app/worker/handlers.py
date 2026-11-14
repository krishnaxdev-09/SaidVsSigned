"""Production job handlers, by kind (doc 11 §2 job table)."""

from __future__ import annotations

from app.core.storage import ObjectStore
from app.llm.base import LLMProvider
from app.modules.stt.base import STTProvider
from app.worker.runner import Registry


def register_all(registry: Registry, store: ObjectStore, *, stt: STTProvider | None = None,
                 llm: LLMProvider | None = None) -> None:
    from app.core.config import get_settings
    from app.llm.bedrock_mantle import from_settings as mantle_from_settings
    from app.modules.audio.ingest import make_ingest_handler
    from app.modules.claims.job import make_extract_handler
    from app.modules.speakers.job import make_suggest_handler
    from app.modules.stt.elevenlabs import ElevenLabsSTT
    from app.modules.stt.job import make_stt_handler

    if stt is None:
        s = get_settings()
        stt = ElevenLabsSTT(store, api_key=s.ELEVENLABS_API_KEY.get_secret_value(), model_id=s.ELEVENLABS_STT_MODEL,
                            base_url=s.ELEVENLABS_BASE_URL, timeout_s=s.STT_TIMEOUT_S)
    registry.register("audio.ingest", make_ingest_handler(store))  # T1.1, doc 02 §5
    if llm is None:
        llm = mantle_from_settings()  # ADR-004 working provider: Gemma 4 31B on Bedrock Mantle
    registry.register("stt.batch", make_stt_handler(stt))  # T1.2, doc 03 §6
    registry.register("speakers.suggest", make_suggest_handler(llm))  # T1.3, doc 03 §5
    registry.register("claims.extract", make_extract_handler(llm))  # T1.6, doc 04 §5
