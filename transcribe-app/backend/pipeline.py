"""
backend/pipeline.py — Основной AI-пайплайн транскрибации и постобработки.

Порядок работы:
1. Определяем устройство (CUDA / MPS / CPU)
2. WhisperX: транскрипция large-v3 → выравнивание → диаризация
3. Очищаем GPU-память после каждого тяжёлого шага
4. Разбиваем транскрипт на чанки
5. Каждый чанк отправляем в Ollama (Qwen 2.5) с юридическим промптом
6. Объединяем и парсим ответ LLM
7. Возвращаем структурированный результат
"""

import os
import gc
import logging
from typing import List, Dict, Any, Callable, Tuple

import httpx

from backend.chunker import (
    create_chunks,
    parse_llm_output,
    merge_chunk_results,
    format_timestamp,
    segment_to_line,
)

logger = logging.getLogger(__name__)

# ── Настройки из переменных окружения ──────────────────────
HF_TOKEN      = os.getenv("HF_TOKEN", "")
OLLAMA_URL    = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL  = os.getenv("OLLAMA_MODEL", "qwen2.5")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")

ProgressCallback = Callable[[str, int], None]


# ============================================================
# Утилиты GPU
# ============================================================

def get_device_and_compute() -> Tuple[str, str]:
    """
    Определяем лучшее доступное устройство и тип вычислений.
    Возвращает (device, compute_type).
    """
    try:
        import torch

        if torch.cuda.is_available():
            total_vram = torch.cuda.get_device_properties(0).total_memory
            free_vram  = total_vram - torch.cuda.memory_allocated(0)
            free_gb    = free_vram / (1024 ** 3)
            logger.info(f"CUDA доступна. Свободная VRAM: {free_gb:.1f} GB")

            # Выбираем compute_type в зависимости от доступной VRAM
            if free_gb >= 8:
                return "cuda", "float16"
            elif free_gb >= 4:
                return "cuda", "int8_float16"
            else:
                # Мало VRAM — int8 для экономии памяти
                return "cuda", "int8"

        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            logger.info("Apple MPS доступна")
            return "mps", "float32"

    except ImportError:
        pass

    logger.warning("GPU не обнаружена. Используем CPU (обработка будет медленнее).")
    return "cpu", "int8"


def _free_gpu_memory() -> None:
    """Освобождаем GPU-память и запускаем GC."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# ============================================================
# WhisperX: транскрипция + выравнивание + диаризация
# ============================================================

def run_whisperx_pipeline(
    audio_path: str,
    progress: ProgressCallback,
) -> List[Dict[str, Any]]:
    """
    Запускает полный WhisperX-пайплайн.

    Этапы:
        5-15% : загрузка модели и транскрипция
        15-40%: выравнивание слов
        40-72%: диаризация и назначение спикеров

    Returns:
        Список сегментов: [{start, end, speaker, text}, ...]
    """
    try:
        import whisperx
    except ImportError as e:
        raise RuntimeError(
            "whisperx не установлен. Запустите start.sh для установки зависимостей."
        ) from e

    device, compute_type = get_device_and_compute()
    logger.info(f"Устройство: {device}, compute_type: {compute_type}")

    # ── Транскрипция ───────────────────────────────────────
    progress("Загрузка модели Whisper large-v3...", 5)

    model = whisperx.load_model(
        WHISPER_MODEL,
        device,
        compute_type=compute_type,
        language="ru",
    )

    progress("Транскрипция аудио...", 12)

    audio = whisperx.load_audio(audio_path)
    batch_size = 16 if device == "cuda" else 4
    result = model.transcribe(audio, batch_size=batch_size)

    detected_lang = result.get("language", "ru")
    logger.info(f"Обнаруженный язык: {detected_lang}, сегментов: {len(result.get('segments', []))}")

    del model
    _free_gpu_memory()

    # ── Выравнивание слов ──────────────────────────────────
    progress("Выравнивание слов по таймлайну...", 35)

    model_a, metadata = whisperx.load_align_model(
        language_code=detected_lang,
        device=device,
    )

    result = whisperx.align(
        result["segments"],
        model_a,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )

    del model_a
    _free_gpu_memory()

    # ── Диаризация ─────────────────────────────────────────
    if HF_TOKEN:
        progress("Диаризация: разделение по спикерам...", 58)

        try:
            diarize_model = whisperx.DiarizationPipeline(
                use_auth_token=HF_TOKEN,
                device=device,
            )
            diarize_segments = diarize_model(audio_path)

            progress("Назначение спикеров к репликам...", 70)

            result = whisperx.assign_word_speakers(diarize_segments, result)

            del diarize_model
            _free_gpu_memory()

        except Exception as e:
            logger.error(f"Диаризация завершилась с ошибкой: {e}. Продолжаем без неё.")
            for seg in result.get("segments", []):
                seg.setdefault("speaker", "SPEAKER_00")
    else:
        logger.warning("HF_TOKEN не установлен — диаризация пропущена.")
        for seg in result.get("segments", []):
            seg["speaker"] = "SPEAKER_00"

    # ── Формирование результата ────────────────────────────
    segments: List[Dict[str, Any]] = []
    for seg in result.get("segments", []):
        text = seg.get("text", "").strip()
        if text:
            segments.append({
                "start":   round(float(seg.get("start", 0)), 2),
                "end":     round(float(seg.get("end",   0)), 2),
                "speaker": seg.get("speaker", "SPEAKER_00"),
                "text":    text,
            })

    logger.info(f"Итого сегментов после фильтрации: {len(segments)}")
    return segments


# ============================================================
# Ollama / LLM постобработка
# ============================================================

def build_llm_prompt(context_text: str, main_text: str) -> str:
    """Строим промпт для юридической корректировки текста."""

    context_section = ""
    if context_text.strip():
        context_section = (
            "КОНТЕКСТ (предыдущие реплики — только для понимания диалога, "
            "НЕ включать в ответ):\n"
            f"{context_text}\n\n"
        )

    return (
        "Ты — опытный юрист-редактор, специализирующийся на составлении "
        "официальных протоколов судебных заседаний.\n"
        "Тебе передан фрагмент автоматической транскрипции аудиозаписи.\n\n"
        "СТРОГИЕ ПРАВИЛА:\n"
        "1. НЕ изменяй смысл сказанного — только исправляй ошибки STT.\n"
        "2. Сохраняй метки спикеров ТОЧНО как в оригинале "
        "(например: SPEAKER_00, SPEAKER_01).\n"
        "3. Сохраняй таймкоды ТОЧНО как в оригинале (формат [HH:MM:SS]).\n"
        "4. Расставляй корректную пунктуацию и заглавные буквы.\n"
        "5. Исправляй орфографические и фонетические ошибки распознавания.\n"
        "6. НЕ добавляй никаких своих комментариев, заголовков или пояснений.\n"
        "7. Каждая реплика — отдельная строка: [HH:MM:SS] SPEAKER_XX: текст\n"
        "8. Возвращай ТОЛЬКО реплики из раздела «ТЕКСТ ДЛЯ ИСПРАВЛЕНИЯ».\n\n"
        f"{context_section}"
        "ТЕКСТ ДЛЯ ИСПРАВЛЕНИЯ:\n"
        f"{main_text}\n\n"
        "ИСПРАВЛЕННЫЙ ТЕКСТ:"
    )


def call_ollama_sync(prompt: str) -> str:
    """Синхронный вызов Ollama Generate API с таймаутом 10 минут."""
    try:
        with httpx.Client(timeout=600.0) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model":  OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.1,
                        "top_p":       0.9,
                        "num_ctx":     8192,
                    },
                },
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()

    except httpx.TimeoutException:
        logger.error("Ollama: таймаут запроса")
        return ""
    except httpx.HTTPStatusError as e:
        logger.error(f"Ollama HTTP ошибка: {e.response.status_code} — {e.response.text[:200]}")
        return ""
    except Exception as e:
        logger.error(f"Ollama: непредвиденная ошибка: {e}")
        return ""


def process_with_llm(
    segments: List[Dict[str, Any]],
    progress: ProgressCallback,
) -> Tuple[List[Dict[str, str]], str]:
    """
    Обрабатываем транскрипт через Ollama Qwen 2.5 по чанкам.

    Returns:
        Кортеж (parsed_segments, full_llm_text)
        - parsed_segments : [{timestamp, speaker, text}]
        - full_llm_text   : объединённый сырой текст от LLM
    """
    chunks = create_chunks(segments, max_tokens=2500, context_lines=3)

    if not chunks:
        logger.warning("Нет чанков для LLM-обработки")
        return [], ""

    logger.info(f"LLM-обработка: {len(chunks)} чанков")
    chunk_results: List[str] = []

    for idx, chunk in enumerate(chunks):
        pct = 78 + int((idx / len(chunks)) * 20)
        progress(f"LLM-редактура: чанк {idx + 1} из {len(chunks)}...", pct)

        prompt    = build_llm_prompt(chunk["context_text"], chunk["main_text"])
        llm_reply = call_ollama_sync(prompt)

        if llm_reply:
            chunk_results.append(llm_reply)
        else:
            # Fallback: если LLM не ответила — оставляем оригинал
            logger.warning(f"LLM не вернула ответ для чанка {idx + 1}, используем оригинал")
            chunk_results.append(chunk["main_text"])

    merged_text     = merge_chunk_results(chunk_results)
    parsed_segments = parse_llm_output(merged_text)

    logger.info(f"LLM вернула {len(parsed_segments)} структурированных реплик")
    return parsed_segments, merged_text


# ============================================================
# Главная точка входа
# ============================================================

def run_full_pipeline(
    audio_path: str,
    progress: ProgressCallback,
) -> Dict[str, Any]:
    """
    Полный пайплайн: WhisperX → чанкование → LLM → структурированный результат.

    Returns:
        {
            segments  : [{timestamp, speaker, text}]  # LLM-обработанные реплики
            speakers  : [str]                          # Уникальные ID спикеров
            llm_text  : str                            # Полный текст от LLM
            raw_text  : str                            # Сырой текст до LLM
        }
    """
    # ── Шаг 1: WhisperX ──────────────────────────────────
    progress("Инициализация распознавания...", 3)
    raw_segments = run_whisperx_pipeline(audio_path, progress)

    if not raw_segments:
        raise ValueError(
            "Транскрипция не дала результатов. "
            "Проверьте качество аудио или наличие речи в файле."
        )

    # Уникальные спикеры (в порядке появления)
    seen_speakers: set = set()
    speakers: List[str] = []
    for seg in raw_segments:
        sp = seg.get("speaker", "SPEAKER_00")
        if sp not in seen_speakers:
            seen_speakers.add(sp)
            speakers.append(sp)
    speakers.sort()

    raw_text = "\n".join(
        segment_to_line(s) for s in raw_segments if s.get("text", "").strip()
    )

    # ── Шаг 2: LLM-обработка ─────────────────────────────
    progress("Передача в Qwen 2.5 для юридической редактуры...", 76)

    llm_segments, llm_text = process_with_llm(raw_segments, progress)

    # Fallback: если LLM не дала структурированный вывод
    if not llm_segments:
        logger.warning("LLM не вернула структурированные сегменты — используем оригинал")
        llm_segments = [
            {
                "timestamp": format_timestamp(s["start"]),
                "speaker":   s["speaker"],
                "text":      s["text"],
            }
            for s in raw_segments
        ]
        llm_text = raw_text

    progress("Финализация результата...", 98)

    return {
        "segments": llm_segments,
        "speakers": speakers,
        "llm_text": llm_text,
        "raw_text": raw_text,
    }
