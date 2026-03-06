from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .models import TranscriptSegment

ID_PREFIX = "SEGMENT_ID="
LINE_RE = re.compile(
    r"SEGMENT_ID=(?P<id>\d+)\s+\[(?P<start>\d{2}:\d{2}:\d{2})\s*-\s*(?P<end>\d{2}:\d{2}:\d{2})\]\s+(?P<speaker>[^:]+):\s*(?P<text>.*)$"
)


@dataclass
class Chunk:
    segment_ids: list[int]
    content: str


def seconds_to_timestamp(seconds: float) -> str:
    """Преобразует секунды в формат HH:MM:SS."""
    safe_seconds = max(0, int(math.floor(seconds)))
    hours = safe_seconds // 3600
    minutes = (safe_seconds % 3600) // 60
    secs = safe_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_segment_line(segment: TranscriptSegment) -> str:
    start = seconds_to_timestamp(segment.start)
    end = seconds_to_timestamp(segment.end)
    return f"{ID_PREFIX}{segment.id} [{start} - {end}] {segment.speaker}: {segment.text.strip()}"


def render_plain_text(segments: list[TranscriptSegment], speaker_map: dict[str, str] | None = None) -> str:
    """Рендерит итоговый протокол без служебных ID, с возможной заменой спикеров."""
    speaker_map = speaker_map or {}
    lines: list[str] = []
    for seg in segments:
        start = seconds_to_timestamp(seg.start)
        end = seconds_to_timestamp(seg.end)
        speaker = speaker_map.get(seg.speaker, seg.speaker)
        lines.append(f"[{start} - {end}] {speaker}: {seg.text.strip()}")
    return "\n".join(lines)


def chunk_segments(
    segments: list[TranscriptSegment],
    target_words: int = 2400,
    overlap_words: int = 220,
) -> list[Chunk]:
    """
    Делит большой транскрипт на чанки с перекрытием.
    Перекрытие снижает риск потери контекста на границах чанков.
    """
    if not segments:
        return []

    chunks: list[Chunk] = []
    idx = 0
    words_by_id = {seg.id: max(1, len(seg.text.split())) for seg in segments}

    while idx < len(segments):
        current_ids: list[int] = []
        current_lines: list[str] = []
        current_words = 0
        start_idx = idx

        while idx < len(segments):
            seg = segments[idx]
            line = format_segment_line(seg)
            line_words = max(1, len(seg.text.split()))
            if current_ids and current_words + line_words > target_words:
                break

            current_ids.append(seg.id)
            current_lines.append(line)
            current_words += line_words
            idx += 1

        if not current_ids:
            # На случай сверхдлинной реплики — кладем её отдельно.
            seg = segments[idx]
            current_ids = [seg.id]
            current_lines = [format_segment_line(seg)]
            idx += 1

        chunks.append(Chunk(segment_ids=current_ids, content="\n".join(current_lines)))

        if idx >= len(segments):
            break

        # Подготовка перекрытия в следующий чанк.
        overlap_ids: list[int] = []
        overlap_words_counter = 0
        tail_cursor = len(current_ids) - 1
        while tail_cursor >= 0 and overlap_words_counter < overlap_words:
            seg_id = current_ids[tail_cursor]
            overlap_words_counter += words_by_id.get(seg_id, 1)
            overlap_ids.insert(0, seg_id)
            tail_cursor -= 1

        if overlap_ids and idx > start_idx:
            idx = max(start_idx + 1, idx - len(overlap_ids))

    return chunks


def parse_llm_lines(llm_text: str) -> dict[int, str]:
    """
    Извлекает исправленный текст по segment_id.
    Формат должен строго содержать SEGMENT_ID=...
    """
    parsed: dict[int, str] = {}
    for raw_line in llm_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = LINE_RE.match(line)
        if not match:
            continue
        seg_id = int(match.group("id"))
        parsed[seg_id] = match.group("text").strip()
    return parsed
