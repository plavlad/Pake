"""Конфигурация приложения."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "500"))

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto / cuda / cpu
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto")
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ru")

HF_TOKEN = os.getenv("HF_TOKEN", "")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5")
LLM_CHUNK_SIZE = int(os.getenv("LLM_CHUNK_SIZE", "2500"))
LLM_CHUNK_OVERLAP = int(os.getenv("LLM_CHUNK_OVERLAP", "200"))

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".wma", ".aac", ".opus", ".webm", ".mp4"}
