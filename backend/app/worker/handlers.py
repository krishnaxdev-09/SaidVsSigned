"""Production job handlers, by kind (doc 11 §2 job table)."""

from __future__ import annotations

from app.core.storage import ObjectStore
from app.llm.base import LLMProvider
from app.modules.stt.base import STTProvider
from app.worker.runner import Registry


def register_all(registry: Registry, store: ObjectStore, *, stt: STTProvider | None = None,
                 llm: LLMProvider | None = None, warm: bool = False) -> None:
    """warm=True (worker process only): load the local embedding and NLI models now, so the first verification
    doesn't spend the contract engine's 30 s timeout on model loading (found in the T2.7 real-stack run)."""
    from app.core.config import get_settings
    from app.llm.bedrock_mantle import from_settings as mantle_from_settings
    from app.modules.audio.ingest import make_ingest_handler
    from app.modules.claims.job import make_extract_handler
    from app.modules.documents.ingest import make_doc_ingest_handler
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
    registry.register("doc.ingest", make_doc_ingest_handler(store, llm=llm))  # T2.1–T2.4, doc 05 §6
    from app.llm.embeddings import from_settings as embedder_from_settings
    from app.llm.nli import LocalNLI
    from app.modules.contract.engine import ContractEngine
    from app.modules.finance.engine import FinanceEngine
    from app.modules.verify.job import make_verify_handler

    embedder, nli = embedder_from_settings(), LocalNLI()
    if warm:
        embedder.embed_sync(["warm-up"], kind="query")
        nli.score_sync("warm-up", "warm-up")
    contract = ContractEngine(embedder=embedder, llm=llm, nli=nli)
    from app.modules.rules.engine import PublicDataEngine, RulesEngine

    registry.register("verify", make_verify_handler(  # T2.5/T2.6/T3.2/T3.5, doc 08
        [contract, FinanceEngine(), RulesEngine(), PublicDataEngine()], llm=llm))
    from app.modules.report.service import make_report_handler

    registry.register("report.render", make_report_handler(store))  # T3.3, doc 10 report
    from app.modules.rag.qa import make_qa_handler

    registry.register("qa.answer", make_qa_handler(embedder, llm))  # T3.4, doc 09 §5  # T2.5/T2.6, doc 08
