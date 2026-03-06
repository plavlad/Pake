from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

JobStage = Literal[
    "queued",
    "transcribing",
    "aligning",
    "diarization",
    "llm_processing",
    "completed",
    "failed",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TranscriptSegment(BaseModel):
    id: int
    start: float
    end: float
    speaker: str
    text: str


class JobResult(BaseModel):
    language: str
    segments: list[TranscriptSegment]
    formatted_text: str


class JobState(BaseModel):
    job_id: str
    filename: str
    stage: JobStage = "queued"
    progress: int = 0
    message: str = "Задача создана"
    error: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    result: JobResult | None = None


class ExportRequest(BaseModel):
    speaker_map: dict[str, str] = Field(default_factory=dict)
