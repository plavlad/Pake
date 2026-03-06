from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Загружаем переменные окружения из .env, если файл есть.
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
STATIC_DIR = Path(__file__).resolve().parent / "static"

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5").strip()

# Ограничение на размер загрузки файла (500 МБ).
MAX_UPLOAD_SIZE_BYTES = 500 * 1024 * 1024

# Поддерживаемые расширения аудио.
SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
    ".opus",
    ".mp4",
    ".mov",
    ".wma",
}
