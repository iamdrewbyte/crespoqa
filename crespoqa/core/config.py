"""Runtime configuration from environment variables / .env."""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    temp_dir: Path
    max_file_bytes: int
    doc_timeout_s: int
    soffice: str
    log_dir: Path
    log_level: str
    workers: int = 1
    ttl_minutes: int = 30
    host: str = "127.0.0.1"
    port: int = 8000
    db_path: Path = Path("data/crespoqa.db")
    cookie_secure: bool = False
    session_hours: int = 8
    ai_enabled: bool = False
    ai_base_url: str = ""
    ai_model: str = ""
    ai_api_key: str = ""
    ai_timeout_s: int = 120
    ai_chunk_chars: int = 12000
    ai_max_chunks: int = 30
    ai_allow_remote: bool = False
    output_suffix: str = ""
    auth_enabled: bool = False
    save_dir: str = ""


def _flag(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes")


def load_settings() -> Settings:
    _load_dotenv()
    env = os.environ.get
    return Settings(
        temp_dir=Path(env("CRESPOQA_TEMP_DIR") or tempfile.gettempdir()) / "crespoqa",
        max_file_bytes=int(float(env("CRESPOQA_MAX_FILE_MB") or 50) * 1024 * 1024),
        doc_timeout_s=int(env("CRESPOQA_DOC_TIMEOUT_S") or 180),
        soffice=env("CRESPOQA_SOFFICE") or "",
        log_dir=Path(env("CRESPOQA_LOG_DIR") or "logs"),
        log_level=(env("CRESPOQA_LOG_LEVEL") or "INFO").upper(),
        workers=max(1, int(env("CRESPOQA_WORKERS") or 1)),
        ttl_minutes=max(1, int(env("CRESPOQA_TTL_MINUTES") or 30)),
        host=env("CRESPOQA_HOST") or "127.0.0.1",
        port=int(env("CRESPOQA_PORT") or 8000),
        db_path=Path(env("CRESPOQA_DB") or "data/crespoqa.db"),
        cookie_secure=(env("CRESPOQA_COOKIE_SECURE") or "").lower() in ("1", "true", "yes"),
        session_hours=int(env("CRESPOQA_SESSION_HOURS") or 8),
        ai_enabled=_flag(env("AI_ENABLED")),
        ai_base_url=(env("AI_BASE_URL") or "").rstrip("/"),
        ai_model=env("AI_MODEL") or "",
        ai_api_key=env("AI_API_KEY") or "",
        ai_timeout_s=int(env("AI_TIMEOUT_S") or 120),
        ai_chunk_chars=int(env("AI_CHUNK_CHARS") or 12000),
        ai_max_chunks=int(env("AI_MAX_CHUNKS") or 30),
        ai_allow_remote=_flag(env("AI_ALLOW_REMOTE")),
        output_suffix=env("CRESPOQA_OUTPUT_SUFFIX") or "",
        save_dir=(env("CRESPOQA_SAVE_DIR") or "").strip(),
        auth_enabled=(env("CRESPOQA_AUTH") or "off").strip().lower() in ("on", "1", "true", "yes"),
    )
