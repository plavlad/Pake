"""Интеграция с Ollama (Qwen 2.5) для пост-обработки транскрипции."""

from __future__ import annotations

import json
import logging
import httpx

from app.config import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — опытный юрист-редактор, специализирующийся на подготовке судебных протоколов и стенограмм.

Твоя задача — отредактировать текст, полученный в результате автоматического распознавания речи (STT). Текст содержит реплики разных спикеров с таймкодами.

СТРОГИЕ ПРАВИЛА:
1. СОХРАНЯЙ формат: каждая реплика должна начинаться с таймкода и метки спикера в формате "[ММ:СС - ММ:СС] SPEAKER_XX:".
2. ИСПРАВЛЯЙ ошибки распознавания речи: неправильно распознанные слова, пропущенные слова, слипшиеся слова.
3. РАССТАВЛЯЙ правильную пунктуацию: точки, запятые, тире, двоеточия, кавычки, вопросительные и восклицательные знаки.
4. НЕ МЕНЯЙ смысл сказанного. Не добавляй и не удаляй информацию.
5. НЕ ОБЪЕДИНЯЙ и НЕ РАЗДЕЛЯЙ реплики разных спикеров.
6. Юридические термины должны быть написаны корректно.
7. Числа, даты, номера статей и дел должны быть оформлены правильно.
8. Возвращай ТОЛЬКО отредактированный текст, без комментариев и пояснений."""


async def process_chunk(text: str) -> str:
    """Отправляет один чанк текста в Ollama для обработки."""
    url = f"{OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": text,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.1,
            "top_p": 0.9,
            "num_predict": 8192,
        },
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()
        except httpx.TimeoutException:
            logger.error("Таймаут при обращении к Ollama")
            raise RuntimeError("Таймаут Ollama: модель не ответила за 5 минут")
        except httpx.HTTPStatusError as e:
            logger.error("Ошибка HTTP от Ollama: %s", e.response.text)
            raise RuntimeError(f"Ошибка Ollama: {e.response.status_code}")
        except Exception as e:
            logger.error("Непредвиденная ошибка Ollama: %s", e)
            raise


async def check_ollama_available() -> bool:
    """Проверяет доступность сервера Ollama."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            return resp.status_code == 200
    except Exception:
        return False
