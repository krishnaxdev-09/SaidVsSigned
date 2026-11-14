"""Production job handlers, by kind (doc 11 §2 job table)."""

from __future__ import annotations

from app.core.storage import ObjectStore
from app.worker.runner import Registry


def register_all(registry: Registry, store: ObjectStore) -> None:
    from app.modules.audio.ingest import make_ingest_handler

    registry.register("audio.ingest", make_ingest_handler(store))  # T1.1, doc 02 §5
