"""Менеджер фоновых задач обработки аудио."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Stage(str, Enum):
    QUEUED = "queued"
    LOADING = "loading"
    TRANSCRIBING = "transcribing"
    ALIGNING = "aligning"
    DIARIZING = "diarizing"
    LLM_PROCESSING = "llm_processing"
    DONE = "done"
    ERROR = "error"

STAGE_LABELS = {
    Stage.QUEUED: "В очереди",
    Stage.LOADING: "Загрузка модели",
    Stage.TRANSCRIBING: "Распознавание речи",
    Stage.ALIGNING: "Выравнивание по словам",
    Stage.DIARIZING: "Диаризация (определение спикеров)",
    Stage.LLM_PROCESSING: "Обработка текста (LLM)",
    Stage.DONE: "Готово",
    Stage.ERROR: "Ошибка",
}


@dataclass
class Task:
    task_id: str
    filename: str
    filepath: str
    stage: Stage = Stage.QUEUED
    progress: float = 0.0
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "filename": self.filename,
            "stage": self.stage.value,
            "stage_label": STAGE_LABELS.get(self.stage, self.stage.value),
            "progress": self.progress,
            "error": self.error,
            "has_result": self.result is not None,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class TaskManager:
    """Потокобезопасное хранилище задач (in-memory)."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def create(self, filename: str, filepath: str) -> Task:
        task_id = uuid.uuid4().hex[:12]
        task = Task(task_id=task_id, filename=filename, filepath=filepath)
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def update_stage(self, task_id: str, stage: Stage, progress: float = 0.0) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.stage = stage
            task.progress = progress
            if stage == Stage.DONE:
                task.finished_at = time.time()

    def set_error(self, task_id: str, error: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.stage = Stage.ERROR
            task.error = error
            task.finished_at = time.time()

    def set_result(self, task_id: str, result: dict) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.result = result

    def list_all(self) -> list[dict]:
        return [t.to_dict() for t in sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)]


task_manager = TaskManager()
