"""AI-пайплайн: WhisperX → выравнивание → диаризация → LLM-коррекция."""

from __future__ import annotations

import gc
import logging

from app.config import (
    WHISPER_MODEL,
    WHISPER_DEVICE,
    WHISPER_COMPUTE_TYPE,
    WHISPER_LANGUAGE,
    HF_TOKEN,
)
from app.tasks import task_manager, Stage
from app.chunker import build_segments, chunk_segments, segments_to_text, Segment
from app.llm import process_chunk

logger = logging.getLogger(__name__)


def _detect_device() -> str:
    import torch
    if WHISPER_DEVICE != "auto":
        return WHISPER_DEVICE
    return "cuda" if torch.cuda.is_available() else "cpu"


def _detect_compute_type(device: str) -> str:
    if WHISPER_COMPUTE_TYPE != "auto":
        return WHISPER_COMPUTE_TYPE
    return "float16" if device == "cuda" else "int8"


async def run_pipeline(task_id: str, filepath: str) -> None:
    """Полный пайплайн обработки аудиофайла."""
    import torch

    device = _detect_device()
    compute_type = _detect_compute_type(device)

    logger.info("Устройство: %s, тип вычислений: %s", device, compute_type)

    try:
        # --- Этап 1: Загрузка модели и транскрипция ---
        task_manager.update_stage(task_id, Stage.LOADING, 0.0)

        import whisperx

        logger.info("Загрузка модели WhisperX '%s'...", WHISPER_MODEL)
        model = whisperx.load_model(
            WHISPER_MODEL,
            device,
            compute_type=compute_type,
            language=WHISPER_LANGUAGE,
        )

        task_manager.update_stage(task_id, Stage.TRANSCRIBING, 0.1)
        logger.info("Транскрипция файла: %s", filepath)

        audio = whisperx.load_audio(filepath)
        result = model.transcribe(audio, batch_size=16 if device == "cuda" else 4)

        del model
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()

        # --- Этап 2: Выравнивание по словам ---
        task_manager.update_stage(task_id, Stage.ALIGNING, 0.3)
        logger.info("Выравнивание...")

        align_model, metadata = whisperx.load_align_model(
            language_code=result.get("language", WHISPER_LANGUAGE),
            device=device,
        )
        result = whisperx.align(
            result["segments"],
            align_model,
            metadata,
            audio,
            device,
            return_char_alignments=False,
        )

        del align_model
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()

        # --- Этап 3: Диаризация ---
        task_manager.update_stage(task_id, Stage.DIARIZING, 0.5)
        logger.info("Диаризация...")

        if not HF_TOKEN:
            logger.warning(
                "HF_TOKEN не задан — диаризация может не работать. "
                "Установите переменную окружения HF_TOKEN."
            )

        diarize_model = whisperx.DiarizationPipeline(
            use_auth_token=HF_TOKEN or None,
            device=device,
        )
        diarize_segments = diarize_model(audio)
        result = whisperx.assign_word_speakers(diarize_segments, result)

        del diarize_model, audio
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()

        # --- Этап 4: Постобработка через LLM ---
        task_manager.update_stage(task_id, Stage.LLM_PROCESSING, 0.6)
        logger.info("Подготовка текста для LLM...")

        segments = build_segments(result)
        chunks = chunk_segments(segments)

        processed_lines: list[str] = []
        total_chunks = len(chunks)

        for i, chunk in enumerate(chunks):
            progress = 0.6 + 0.35 * ((i + 1) / total_chunks)
            task_manager.update_stage(task_id, Stage.LLM_PROCESSING, round(progress, 2))
            logger.info("LLM: обработка чанка %d/%d", i + 1, total_chunks)

            chunk_text = segments_to_text(chunk)
            corrected = await process_chunk(chunk_text)

            if i > 0:
                corrected = _remove_overlap(processed_lines, corrected)

            processed_lines.append(corrected)

        full_text = "\n".join(processed_lines)

        speakers = _extract_speakers(segments)

        task_manager.set_result(task_id, {
            "text": full_text,
            "speakers": speakers,
            "segments": [
                {
                    "speaker": s.speaker,
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                }
                for s in segments
            ],
        })
        task_manager.update_stage(task_id, Stage.DONE, 1.0)
        logger.info("Обработка завершена для задачи %s", task_id)

    except torch.cuda.OutOfMemoryError:
        msg = "Недостаточно видеопамяти (GPU OOM). Попробуйте меньшую модель или CPU-режим (WHISPER_DEVICE=cpu)."
        logger.error(msg)
        task_manager.set_error(task_id, msg)
    except Exception as e:
        logger.exception("Ошибка в пайплайне для задачи %s", task_id)
        task_manager.set_error(task_id, str(e))


def _extract_speakers(segments: list[Segment]) -> list[str]:
    """Извлекает уникальные метки спикеров в порядке появления."""
    seen: set[str] = set()
    ordered: list[str] = []
    for seg in segments:
        if seg.speaker not in seen:
            seen.add(seg.speaker)
            ordered.append(seg.speaker)
    return ordered


def _remove_overlap(existing_lines: list[str], new_text: str) -> str:
    """Убирает дублирующиеся строки из перекрытия между чанками.

    Сравнивает последние строки предыдущего результата с первыми строками
    нового чанка и отрезает повторы.
    """
    if not existing_lines:
        return new_text

    last_block = existing_lines[-1]
    last_lines = last_block.strip().split("\n")

    new_lines = new_text.strip().split("\n")

    best_cut = 0
    tail_size = min(len(last_lines), 10)

    for cut in range(1, min(len(new_lines), tail_size + 1)):
        candidate = new_lines[:cut]
        tail = last_lines[-cut:]
        if _lines_similar(tail, candidate):
            best_cut = cut

    if best_cut > 0:
        return "\n".join(new_lines[best_cut:])
    return new_text


def _lines_similar(a: list[str], b: list[str]) -> bool:
    """Грубое сравнение строк (игнорируя мелкие различия в пунктуации)."""
    if len(a) != len(b):
        return False
    for la, lb in zip(a, b):
        sa = "".join(la.split()).lower()
        sb = "".join(lb.split()).lower()
        if sa and sb:
            shorter = min(len(sa), len(sb))
            common = sum(1 for ca, cb in zip(sa[:shorter], sb[:shorter]) if ca == cb)
            if common / shorter < 0.7:
                return False
    return True
