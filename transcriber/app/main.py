"""Главное FastAPI-приложение транскрибатора."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import UPLOAD_DIR, MAX_UPLOAD_SIZE_MB, ALLOWED_EXTENSIONS, BASE_DIR
from app.tasks import task_manager, Stage
from app.pipeline import run_pipeline
from app.export import export_txt, export_docx
from app.llm import check_ollama_available

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Транскрибатор", version="1.0.0")

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# ── Модели запросов ──────────────────────────────────────────

class ExportRequest(BaseModel):
    task_id: str
    format: str = "txt"
    speaker_map: dict[str, str] | None = None
    title: str = "Протокол"


# ── Эндпоинты ────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/api/health")
async def health():
    ollama_ok = await check_ollama_available()
    return {
        "status": "ok",
        "ollama": ollama_ok,
    }


@app.post("/api/upload")
async def upload_audio(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "Имя файла не указано")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Неподдерживаемый формат: {ext}. Допустимые: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_SIZE_MB:
        raise HTTPException(400, f"Файл слишком большой ({size_mb:.1f} МБ). Максимум: {MAX_UPLOAD_SIZE_MB} МБ")

    task = task_manager.create(filename=file.filename, filepath="")

    save_path = UPLOAD_DIR / f"{task.task_id}{ext}"
    with open(save_path, "wb") as f:
        f.write(content)

    task.filepath = str(save_path)

    asyncio.get_event_loop().create_task(_run_in_background(task.task_id, str(save_path)))

    return {"task_id": task.task_id, "filename": file.filename}


async def _run_in_background(task_id: str, filepath: str) -> None:
    """Запускает пайплайн в фоне, оборачивая CPU-bound операции."""
    loop = asyncio.get_event_loop()
    await run_pipeline(task_id, filepath)


@app.get("/api/tasks")
async def list_tasks():
    return task_manager.list_all()


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    return task.to_dict()


@app.get("/api/tasks/{task_id}/result")
async def get_result(task_id: str):
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    if task.stage != Stage.DONE or task.result is None:
        raise HTTPException(400, "Результат ещё не готов")
    return task.result


@app.post("/api/export")
async def export(req: ExportRequest):
    task = task_manager.get(req.task_id)
    if not task:
        raise HTTPException(404, "Задача не найдена")
    if task.stage != Stage.DONE or task.result is None:
        raise HTTPException(400, "Результат ещё не готов")

    text = task.result["text"]

    if req.format == "docx":
        docx_bytes = export_docx(text, speaker_map=req.speaker_map, title=req.title)
        filename = Path(task.filename).stem + "_протокол.docx"
        return Response(
            content=docx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        txt_content = export_txt(text, speaker_map=req.speaker_map)
        filename = Path(task.filename).stem + "_протокол.txt"
        return Response(
            content=txt_content.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
