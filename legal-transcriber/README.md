# Legal Transcriber

Локальное веб-приложение для транскрибации, диаризации и юридической пост-обработки аудиозаписей.

## Структура проекта

```text
legal-transcriber/
├── app/
│   ├── api/
│   │   └── routes.py
│   ├── core/
│   │   └── config.py
│   ├── data/
│   │   ├── results/
│   │   └── uploads/
│   ├── services/
│   │   ├── chunking.py
│   │   ├── job_store.py
│   │   ├── ollama_client.py
│   │   └── transcription.py
│   ├── static/
│   │   ├── app.js
│   │   ├── index.html
│   │   └── styles.css
│   └── main.py
├── requirements.txt
└── run.py
```

## Обязательные зависимости хоста

- Python 3.10+
- ffmpeg
- Ollama
- экспортированная переменная `HF_TOKEN`

## Быстрый запуск

```bash
cd legal-transcriber
export HF_TOKEN=your_huggingface_token
python3 run.py
```

Скрипт сам:

1. проверит Python, ffmpeg и Ollama;
2. при необходимости создаст `.venv`;
3. пропустит `pip install`, если `requirements.txt` не менялся и модули уже доступны;
4. проверит, что Ollama запущена;
5. скачает модель `qwen2.5`, если она отсутствует;
6. поднимет FastAPI-сервер на `0.0.0.0:8000`.

## Настройки через переменные окружения

- `HF_TOKEN` — обязателен для Pyannote/diarization
- `OLLAMA_MODEL` — по умолчанию `qwen2.5`
- `OLLAMA_BASE_URL` — по умолчанию `http://127.0.0.1:11434`
- `APP_HOST` — по умолчанию `0.0.0.0`
- `APP_PORT` — по умолчанию `8000`
- `WHISPER_MODEL` — по умолчанию `large-v3`
- `LLM_CHUNK_TOKEN_LIMIT` — по умолчанию `2400`
- `LLM_CHUNK_OVERLAP_TOKENS` — по умолчанию `220`
- `TASK_CONCURRENCY` — по умолчанию `1`

## API

- `POST /api/tasks` — загрузка аудиофайла и создание задачи
- `GET /api/tasks/{task_id}` — получение статуса и результата
- `PUT /api/tasks/{task_id}/speaker-map` — сохранение переименований спикеров
- `GET /api/tasks/{task_id}/export?format=txt|docx` — экспорт результата
