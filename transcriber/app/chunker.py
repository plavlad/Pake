"""Логика разбивки транскрипции на чанки для LLM-обработки.

Текст разбивается на куски по ~CHUNK_SIZE слов с перекрытием OVERLAP слов,
чтобы LLM не теряла контекст диалога на стыках чанков.
Разбивка происходит на границах реплик спикеров, а не посреди фразы.
"""

from __future__ import annotations

from dataclasses import dataclass
from app.config import LLM_CHUNK_SIZE, LLM_CHUNK_OVERLAP


@dataclass
class Segment:
    """Одна реплика с таймкодом и спикером."""
    speaker: str
    start: float
    end: float
    text: str

    def word_count(self) -> int:
        return len(self.text.split())

    def to_text_line(self) -> str:
        start_fmt = _format_time(self.start)
        end_fmt = _format_time(self.end)
        return f"[{start_fmt} - {end_fmt}] {self.speaker}: {self.text}"


def _format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def build_segments(whisperx_result: dict) -> list[Segment]:
    """Преобразует результат WhisperX (с диаризацией) в список сегментов."""
    raw_segments = whisperx_result.get("segments", [])
    segments: list[Segment] = []

    for seg in raw_segments:
        speaker = seg.get("speaker", "UNKNOWN")
        start = seg.get("start", 0.0)
        end = seg.get("end", 0.0)
        text = seg.get("text", "").strip()
        if text:
            segments.append(Segment(speaker=speaker, start=start, end=end, text=text))

    return _merge_consecutive_segments(segments)


def _merge_consecutive_segments(segments: list[Segment]) -> list[Segment]:
    """Объединяет последовательные сегменты одного спикера."""
    if not segments:
        return []

    merged: list[Segment] = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if seg.speaker == prev.speaker and (seg.start - prev.end) < 2.0:
            prev.end = seg.end
            prev.text = prev.text + " " + seg.text
        else:
            merged.append(seg)

    return merged


def chunk_segments(
    segments: list[Segment],
    chunk_size: int = LLM_CHUNK_SIZE,
    overlap: int = LLM_CHUNK_OVERLAP,
) -> list[list[Segment]]:
    """Разбивает сегменты на чанки по ~chunk_size слов с перекрытием.

    Перекрытие: последние ~overlap слов предыдущего чанка добавляются
    в начало следующего, чтобы LLM сохраняла контекст.
    """
    if not segments:
        return []

    chunks: list[list[Segment]] = []
    current_chunk: list[Segment] = []
    current_words = 0

    for seg in segments:
        wc = seg.word_count()

        if current_words + wc > chunk_size and current_chunk:
            chunks.append(current_chunk)
            overlap_chunk, overlap_words = _get_overlap(current_chunk, overlap)
            current_chunk = list(overlap_chunk)
            current_words = overlap_words

        current_chunk.append(seg)
        current_words += wc

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _get_overlap(
    chunk: list[Segment], target_words: int
) -> tuple[list[Segment], int]:
    """Берёт последние сегменты чанка, пока не наберётся ~target_words."""
    overlap_segs: list[Segment] = []
    total = 0

    for seg in reversed(chunk):
        wc = seg.word_count()
        if total + wc > target_words and overlap_segs:
            break
        overlap_segs.insert(0, seg)
        total += wc

    return overlap_segs, total


def segments_to_text(segments: list[Segment]) -> str:
    """Форматирует сегменты в текстовый блок для отправки в LLM."""
    return "\n".join(seg.to_text_line() for seg in segments)
