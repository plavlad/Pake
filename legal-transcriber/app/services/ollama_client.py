from __future__ import annotations

from typing import Any

import requests

from app.core.config import settings


class OllamaClient:
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.1) -> str:
        response = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "messages": messages,
                "options": {
                    "temperature": temperature,
                },
            },
            timeout=(30, 600),
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        message = payload.get("message") or {}
        content = message.get("content")
        if not content:
            raise RuntimeError("Ollama вернула пустой ответ.")
        return content


ollama_client = OllamaClient(settings.ollama_base_url, settings.ollama_model)
