from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "app" / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
RESULT_DIR = DATA_DIR / "results"


@dataclass(slots=True)
class Settings:
    app_name: str = "Legal Audio Transcriber"
    host: str = field(default_factory=lambda: os.getenv("APP_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("APP_PORT", "8000")))
    max_upload_size_mb: int = field(default_factory=lambda: int(os.getenv("MAX_UPLOAD_SIZE_MB", "2048")))
    whisper_model: str = field(default_factory=lambda: os.getenv("WHISPER_MODEL", "large-v3"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5"))
    ollama_base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    hf_token: str = field(default_factory=lambda: os.getenv("HF_TOKEN", ""))
    llm_chunk_token_limit: int = field(default_factory=lambda: int(os.getenv("LLM_CHUNK_TOKEN_LIMIT", "2400")))
    llm_chunk_overlap_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_CHUNK_OVERLAP_TOKENS", "220")))
    whisper_batch_size: int = field(default_factory=lambda: int(os.getenv("WHISPER_BATCH_SIZE", "8")))
    task_concurrency: int = field(default_factory=lambda: int(os.getenv("TASK_CONCURRENCY", "1")))


def ensure_directories() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()
