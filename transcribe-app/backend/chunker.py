"""
backend/chunker.py — Логика разбивки длинного транскрипта на чанки для LLM.

Алгоритм:
1. Разбиваем список сегментов на чанки по ~2500 токенов
2. К каждому чанку (кроме первого) добавляем N строк контекста из предыдущего
3. В промпт LLM явно указывается, что контекст — только для понимания,
   а обрабатывать нужно только основной блок
"""

import re
from typing import List, Dict, Any, Tuple


def format_timestamp(seconds: float) -> str:
    """Конвертируем секунды в строку формата HH:MM:SS."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def segment_to_line(seg: Dict[str, Any]) -> str:
    """Форматируем сегмент в строку вида: [HH:MM:SS] SPEAKER_XX: текст."""
    ts = format_timestamp(seg.get("start", 0))
    speaker = seg.get("speaker", "SPEAKER_00")
    text = seg.get("text", "").strip()
    return f"[{ts}] {speaker}: {text}"


def estimate_tokens(text: str) -> int:
    """
    Приблизительная оценка числа токенов.
    Для русского текста BPE-токенизатор создаёт ~1 токен на 3-4 символа.
    Берём консервативную оценку: 3 символа = 1 токен.
    """
    return max(1, len(text) // 3)


def create_chunks(
    segments: List[Dict[str, Any]],
    max_tokens: int = 2500,
    context_lines: int = 3,
) -> List[Dict[str, Any]]:
    """
    Разбиваем список сегментов на чанки с контекстным окном.

    Каждый чанк — словарь:
        - context_text  : N строк из предыдущего чанка (для LLM-контекста)
        - main_text     : основной блок для исправления
        - start_idx     : индекс первого сегмента в main
        - end_idx       : индекс последнего сегмента в main

    Args:
        segments     : список сегментов {start, end, speaker, text}
        max_tokens   : максимум токенов в основном блоке
        context_lines: число строк контекста из предыдущего чанка

    Returns:
        Список словарей-чанков
    """
    # Фильтруем сегменты без текста
    valid = [s for s in segments if s.get("text", "").strip()]
    if not valid:
        return []

    chunks: List[Dict[str, Any]] = []
    i = 0
    n = len(valid)

    while i < n:
        main_indices: List[int] = []
        current_tokens = 0
        j = i

        while j < n:
            line = segment_to_line(valid[j])
            line_tokens = estimate_tokens(line)

            # Останавливаемся если превысили лимит (но минимум 1 сегмент)
            if current_tokens + line_tokens > max_tokens and main_indices:
                break

            main_indices.append(j)
            current_tokens += line_tokens
            j += 1

        # Защита от бесконечного цикла
        if not main_indices:
            main_indices = [i]
            j = i + 1

        # Контекст: последние context_lines строк предыдущего чанка
        context_start = max(0, main_indices[0] - context_lines)
        context_indices = list(range(context_start, main_indices[0]))

        context_text = "\n".join(
            segment_to_line(valid[k]) for k in context_indices
        )
        main_text = "\n".join(
            segment_to_line(valid[k]) for k in main_indices
        )

        chunks.append({
            "context_text": context_text,
            "main_text": main_text,
            "start_idx": main_indices[0],
            "end_idx": main_indices[-1],
        })

        i = main_indices[-1] + 1

    return chunks


def parse_llm_output(llm_text: str) -> List[Dict[str, str]]:
    """
    Парсим текст, возвращённый LLM, обратно в список сегментов.

    Ожидаемый формат строк:
        [HH:MM:SS] SPEAKER_XX: текст реплики

    Returns:
        Список словарей {timestamp, speaker, text}
    """
    pattern = re.compile(
        r'^\[(\d{1,2}:\d{2}:\d{2})\]\s+(\S+?):\s+(.+)$',
        re.MULTILINE,
    )

    result = []
    for match in pattern.finditer(llm_text):
        speaker = match.group(2).rstrip(':')
        result.append({
            "timestamp": match.group(1),
            "speaker": speaker,
            "text": match.group(3).strip(),
        })

    return result


def merge_chunk_results(chunk_results: List[str]) -> str:
    """
    Объединяем ответы LLM по чанкам в единый текст.
    Убираем пустые ответы и лишние пробелы между блоками.
    """
    parts = [r.strip() for r in chunk_results if r.strip()]
    return "\n".join(parts)
