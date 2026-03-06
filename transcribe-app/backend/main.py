"""
backend/main.py — FastAPI-приложение: API-эндпоинты и фоновая обработка.

Эндпоинты:
    POST /api/upload               — загрузка аудиофайла, запуск обработки
    GET  /api/status/{task_id}     — polling статуса задачи
    GET  /api/export/{task_id}     — скачать результат (.txt или .docx)
    GET  /                         — фронтенд (статические файлы)
"""

import os
import gc
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

import aiofiles
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.pipeline import run_full_pipeline
from backend.export_utils import export_to_txt, export_to_docx

# ── Логирование ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Пути ────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent.parent
UPLOAD_DIR   = BASE_DIR / "uploads"
RESULTS_DIR  = BASE_DIR / "results"
FRONTEND_DIR = BASE_DIR / "frontend"

UPLOAD_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024   # 2 ГБ
ALLOWED_EXT   = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".flac", ".opus", ".avi", ".mkv", ".webm"}

# ── FastAPI ──────────────────────────────────────────────────
app = FastAPI(title="Юридический Транскрибатор", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Один поток для AI-обработки (не перегружаем GPU)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")

# Хранилище задач: task_id → данные задачи
tasks: Dict[str, Dict[str, Any]] = {}


# ============================================================
# API эндпоинты
# ============================================================

@app.post("/api/upload")
async def upload_audio(file: UploadFile = File(...)):
    """
    Загружает аудиофайл и запускает асинхронную обработку.
    Возвращает task_id для последующего polling'а.
    """
    suffix = Path(file.filename or "audio").suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемый формат '{suffix}'. "
                   f"Допустимые: {', '.join(sorted(ALLOWED_EXT))}",
        )

    task_id   = str(uuid.uuid4())
    safe_name = f"{task_id}{suffix}"
    file_path = UPLOAD_DIR / safe_name

    # Сохраняем файл на диск чанками (async)
    total_size = 0
    async with aiofiles.open(file_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)  # 1 МБ за раз
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                await f.close()
                file_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Файл превышает лимит 2 ГБ")
            await f.write(chunk)

    logger.info(f"[{task_id}] Загружен файл: {file.filename} ({total_size / 1024**2:.1f} МБ)")

    # Инициализируем задачу
    tasks[task_id] = {
        "status":   "processing",
        "stage":    "Файл загружен, ожидание в очереди...",
        "progress": 0,
        "filename": file.filename,
        "result":   None,
        "error":    None,
    }

    # Запускаем обработку в фоне (не блокируем HTTP-ответ)
    asyncio.create_task(_run_pipeline_task(task_id, str(file_path)))

    return {"task_id": task_id, "filename": file.filename}


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    """
    Возвращает текущий статус задачи.
    Клиент должен опрашивать этот эндпоинт раз в 2-3 секунды.
    """
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    return {
        "task_id":  task_id,
        "status":   task["status"],      # processing | completed | error
        "stage":    task["stage"],
        "progress": task["progress"],    # 0–100
        "filename": task.get("filename"),
        "result":   task["result"] if task["status"] == "completed" else None,
        "error":    task.get("error"),
    }


@app.get("/api/export/{task_id}")
async def export_result(
    task_id: str,
    format: str = Query("txt", pattern="^(txt|docx)$"),
):
    """
    Генерирует и отдаёт файл экспорта в формате TXT или DOCX.
    """
    task = tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="Обработка ещё не завершена")
    if not task.get("result"):
        raise HTTPException(status_code=400, detail="Результат отсутствует")

    result_data = task["result"]
    stem        = Path(task.get("filename", "transcript")).stem

    if format == "txt":
        out_path = RESULTS_DIR / f"{task_id}.txt"
        export_to_txt(result_data, str(out_path), filename=stem)
        return FileResponse(
            out_path,
            media_type="text/plain; charset=utf-8",
            filename=f"{stem}_протокол.txt",
        )
    else:
        out_path = RESULTS_DIR / f"{task_id}.docx"
        export_to_docx(result_data, str(out_path), filename=stem)
        return FileResponse(
            out_path,
            media_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            filename=f"{stem}_протокол.docx",
        )


# ============================================================
# Фоновая обработка
# ============================================================

async def _run_pipeline_task(task_id: str, file_path: str) -> None:
    """
    Запускает run_full_pipeline в отдельном потоке через executor,
    не блокируя event loop FastAPI.
    """
    loop = asyncio.get_event_loop()

    def _progress(stage: str, pct: int) -> None:
        """Thread-safe обновление статуса из потока pipeline."""
        if task_id in tasks:
            tasks[task_id]["stage"]    = stage
            tasks[task_id]["progress"] = max(0, min(100, pct))
        logger.info(f"[{task_id}] {pct:3d}%  {stage}")

    try:
        result = await loop.run_in_executor(
            _executor,
            lambda: run_full_pipeline(file_path, _progress),
        )

        tasks[task_id]["status"]   = "completed"
        tasks[task_id]["stage"]    = "Обработка завершена"
        tasks[task_id]["progress"] = 100
        tasks[task_id]["result"]   = result
        logger.info(f"[{task_id}] Обработка успешно завершена")

    except MemoryError:
        _set_error(
            task_id,
            "Недостаточно памяти GPU/RAM. "
            "Попробуйте файл меньшего размера или освободите память.",
        )
    except ValueError as exc:
        _set_error(task_id, str(exc))
    except RuntimeError as exc:
        _set_error(task_id, str(exc))
    except Exception as exc:
        logger.exception(f"[{task_id}] Непредвиденная ошибка")
        _set_error(task_id, f"Ошибка обработки: {exc}")
    finally:
        # Удаляем загруженный файл
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception:
            pass

        # Принудительная очистка памяти
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def _set_error(task_id: str, message: str) -> None:
    """Устанавливаем состояние ошибки для задачи."""
    if task_id in tasks:
        tasks[task_id]["status"] = "error"
        tasks[task_id]["stage"]  = "Ошибка"
        tasks[task_id]["error"]  = message
    logger.error(f"[{task_id}] ОШИБКА: {message}")


# ============================================================
# Статические файлы — фронтенд (должен быть последним!)
# ============================================================
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
