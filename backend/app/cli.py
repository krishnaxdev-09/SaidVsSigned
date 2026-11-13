"""Operational CLI. `python -m app.cli seed-consent-texts` loads data/consent_texts/*.md (immutable versions).

Re-seeding is idempotent. A changed body under an existing (kind, version, locale) is refused: texts are immutable
(doc 01 §5.1), so a change needs a new version."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import yaml
from sqlalchemy import text

from app.core.config import BACKEND_DIR, get_settings
from app.core.db import dispose_engine, init_engine
from app.core.ids import uuid7
from app.modules.consent.service import sha256_text

TEXT_DIR = BACKEND_DIR / "data" / "consent_texts"


def parse_text_file(path: Path) -> tuple[dict, str]:
    raw = path.read_text(encoding="utf-8")
    _, front, body = raw.split("---\n", 2)
    meta = yaml.safe_load(front)
    return {"kind": meta["kind"], "version": str(meta["version"]), "locale": meta["locale"]}, body.strip() + "\n"


async def seed_consent_texts(engine=None) -> int:  # type: ignore[no-untyped-def]
    own = engine is None
    engine = engine or init_engine(get_settings().DATABASE_URL)
    inserted = 0
    try:
        async with engine.begin() as conn:
            for path in sorted(TEXT_DIR.glob("*.md")):
                meta, body = parse_text_file(path)
                digest = sha256_text(body)
                existing = (await conn.execute(text(
                    "SELECT body_sha256 FROM consent_texts WHERE kind = :kind AND version = :version AND locale = :locale"),
                    meta)).first()
                if existing:
                    if existing.body_sha256 != digest:
                        raise SystemExit(f"{path.name}: body changed for an existing version; create a new version")
                    continue
                await conn.execute(text(
                    "INSERT INTO consent_texts (id, kind, version, locale, body, body_sha256, effective_from) "
                    "VALUES (:id, :kind, :version, :locale, :body, :sha, now())"),
                    {**meta, "id": uuid7(), "body": body, "sha": digest})
                inserted += 1
    finally:
        if own:
            await dispose_engine()
    return inserted


def main() -> None:
    if sys.argv[1:] == ["seed-consent-texts"]:
        n = asyncio.run(seed_consent_texts())
        print(f"consent texts inserted: {n}")
    else:
        raise SystemExit("usage: python -m app.cli seed-consent-texts")


if __name__ == "__main__":
    main()
