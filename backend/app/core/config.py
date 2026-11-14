"""Settings (doc 01 §4 step 2). Env-only; secrets are SecretStr and never logged."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENVIRONMENT: str = "local"  # local | test | staging | production
    REAL_USERS: bool = False  # doc 06 §4a / doc 12: must stay false until HC-7 + HC-8

    # Connections
    DATABASE_URL: str = "postgresql+asyncpg://saidsigned:saidsigned@localhost:5432/saidsigned"
    S3_ENDPOINT: str = "http://localhost:9000"
    S3_ACCESS_KEY: SecretStr = SecretStr("")
    S3_SECRET_KEY: SecretStr = SecretStr("")
    S3_PUBLIC_ENDPOINT: str = ""  # browser-reachable endpoint for presigned URLs; empty = S3_ENDPOINT
    S3_REGION: str = "us-east-1"
    S3_BUCKETS: tuple[str, ...] = ("audio", "documents", "exports")

    # Auth
    JWT_SECRET: SecretStr  # required, no default
    JWT_ACCESS_TTL: int = 900
    JWT_REFRESH_TTL: int = 1_209_600
    WS_TICKET_TTL: int = 30
    COOKIE_SECURE: bool = True  # false only for local http dev

    # Providers
    ELEVENLABS_API_KEY: SecretStr = SecretStr("")
    ELEVENLABS_STT_MODEL: str = "scribe_v2"
    ELEVENLABS_BASE_URL: str = "https://api.elevenlabs.io"
    STT_NUM_SPEAKERS: int = 2  # doc 03 §5: num_speakers=2 by default (configurable 2–4)
    STT_TIMEOUT_S: float = 600.0
    LLM_PROVIDER: str = ""  # "bedrock_mantle" (ADR-004 working provider)
    LLM_MODEL: str = ""
    LLM_TIMEOUT_MS: int = 120_000
    MANTLE_BASE_URL: str = "https://bedrock-mantle.us-east-1.api.aws/openai/v1"
    MANTLE_API_KEY: SecretStr = SecretStr("")
    MANTLE_MODEL: str = "google.gemma-4-31b"
    MANTLE_MAX_TOKENS: int = 4096
    # ADR-005 working choice (provisional until HC-4): local multilingual E5 small, 384-dim, CPU (no data leaves infra).
    EMBEDDING_PROVIDER: str = "local_e5"
    EMBEDDING_MODEL: str = "intfloat/multilingual-e5-small"
    EMBEDDING_DIM: int = 384

    # Limits
    RETENTION_DAYS: int = 30  # [Q-11] pending HC-7
    MAX_UPLOAD_MB_AUDIO: int = 200
    MAX_AUDIO_MINUTES: int = 180
    FFPROBE_TIMEOUT_S: int = 30
    FFMPEG_TIMEOUT_S: int = 900
    MAX_UPLOAD_MB_DOC: int = 40
    MAX_DOC_PAGES: int = 60
    MAX_PHOTO_PAGES: int = 20
    MAX_PHOTO_MB: int = 15  # doc 05 §4: each photo ≤ 15 MB

    # Jobs (doc 01 §4 step 8, doc 11 §2)
    JOB_LEASE_SECONDS: int = 600
    WORKER_POLL_SECONDS: float = 1.0

    # Config files
    THRESHOLDS_FILE: Path = BACKEND_DIR / "config" / "thresholds.yaml"
    FEATURES_FILE: Path = BACKEND_DIR / "config" / "features.yaml"

    @property
    def asyncpg_dsn(self) -> str:
        """Plain DSN for the dedicated asyncpg LISTEN connection."""
        return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


class StartupCheckError(RuntimeError):
    pass


def load_features(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def check_features(features: dict[str, Any], *, real_users: bool) -> None:
    """Startup check (doc 06 §4a). Refuse to start on an unsafe flag combination."""
    fin = features.get("finance", {})
    approval = fin.get("hc5_approval") or {}
    if fin.get("mvp_formulas_user_visible") and not approval.get("approved_by"):
        raise StartupCheckError("finance.mvp_formulas_user_visible=true requires a recorded HC-5 approval")
    if fin.get("gated_formulas_enabled"):
        raise StartupCheckError("finance.gated_formulas_enabled must stay false for the MVP (doc 06 §4a)")
    if fin.get("preview_internal") and real_users:
        raise StartupCheckError("finance.preview_internal is refused where REAL_USERS=true")
    if features.get("rules", {}).get("user_visible"):
        raise StartupCheckError("rules.user_visible requires per-rule HC-6 approval (doc 07); not available in Phase 0")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
