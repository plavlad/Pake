from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import RESULT_DIR


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, result_dir: Path) -> None:
        self._result_dir = result_dir
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}

    def create_task(self, filename: str, stored_path: Path) -> dict[str, Any]:
        task_id = uuid.uuid4().hex
        payload = {
            "id": task_id,
            "source_filename": filename,
            "stored_path": str(stored_path),
            "status": "queued",
            "stage": "В очереди",
            "progress": 0,
            "error": None,
            "speaker_map": {},
            "result": None,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        with self._lock:
            self._tasks[task_id] = payload
        self._persist(task_id)
        return deepcopy(payload)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
        if task:
            return deepcopy(task)

        path = self._task_path(task_id)
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        with self._lock:
            self._tasks[task_id] = data
        return deepcopy(data)

    def update(self, task_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            task = self._tasks[task_id]
            task.update(updates)
            task["updated_at"] = _utc_now()
            snapshot = deepcopy(task)
        self._persist(task_id)
        return snapshot

    def mark_processing(self, task_id: str, stage: str, progress: int) -> dict[str, Any]:
        return self.update(task_id, status="processing", stage=stage, progress=progress, error=None)

    def mark_completed(self, task_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return self.update(task_id, status="completed", stage="Готово", progress=100, result=result)

    def mark_failed(self, task_id: str, error: str, stage: str | None = None) -> dict[str, Any]:
        current = self.get(task_id) or {}
        return self.update(
            task_id,
            status="failed",
            stage=stage or current.get("stage", "Ошибка"),
            error=error,
        )

    def update_speaker_map(self, task_id: str, speaker_map: dict[str, str]) -> dict[str, Any]:
        task = self.get(task_id)
        if task is None:
            raise KeyError(task_id)

        result = task.get("result") or {}
        result["speaker_map"] = speaker_map
        return self.update(task_id, speaker_map=speaker_map, result=result)

    def _persist(self, task_id: str) -> None:
        task = self.get_from_memory(task_id)
        if task is None:
            return
        self._task_path(task_id).write_text(
            json.dumps(task, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_from_memory(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            return deepcopy(task) if task else None

    def _task_path(self, task_id: str) -> Path:
        return self._result_dir / f"{task_id}.json"


job_store = JobStore(RESULT_DIR)
