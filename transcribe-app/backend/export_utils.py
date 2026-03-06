"""
backend/export_utils.py — Экспорт результатов транскрибации в TXT и DOCX.
"""

import os
from datetime import datetime
from typing import List, Dict, Any


def _get_header(filename: str) -> str:
    """Формируем заголовок протокола."""
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    return (
        "ПРОТОКОЛ АУДИОЗАПИСИ\n"
        f"Файл: {filename}\n"
        f"Дата обработки: {date_str}\n"
        + "=" * 60 + "\n\n"
    )


def export_to_txt(
    result_data: Dict[str, Any],
    output_path: str,
    filename: str = "аудиозапись",
) -> None:
    """
    Экспортируем транскрипт в текстовый файл (.txt).

    Структура файла:
        Заголовок
        Список спикеров
        Разделитель
        Реплики вида: [HH:MM:SS] ИМЯ_СПИКЕРА: текст
    """
    segments: List[Dict[str, str]] = result_data.get("segments", [])
    speakers: List[str]            = result_data.get("speakers", [])

    lines = [_get_header(filename)]

    # Список спикеров
    if speakers:
        lines.append("УЧАСТНИКИ:\n")
        for sp in speakers:
            lines.append(f"  • {sp}\n")
        lines.append("\n" + "-" * 60 + "\n\n")

    # Реплики
    for seg in segments:
        ts      = seg.get("timestamp", "00:00:00")
        speaker = seg.get("speaker", "UNKNOWN")
        text    = seg.get("text", "").strip()
        if text:
            lines.append(f"[{ts}] {speaker}:\n{text}\n\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def export_to_docx(
    result_data: Dict[str, Any],
    output_path: str,
    filename: str = "аудиозапись",
) -> None:
    """
    Экспортируем транскрипт в Word-документ (.docx).

    Форматирование:
        - Заголовок: Arial 16, жирный, по центру
        - Мета-данные: Arial 10, серый
        - Каждая реплика: имя спикера жирным, текст обычным шрифтом
        - Чередование отступов для читаемости
    """
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError as e:
        raise RuntimeError(
            "python-docx не установлен. Запустите pip install python-docx."
        ) from e

    segments: List[Dict[str, str]] = result_data.get("segments", [])
    speakers: List[str]            = result_data.get("speakers", [])

    doc = Document()

    # Убираем широкие поля по умолчанию
    for section in doc.sections:
        section.top_margin    = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin   = Inches(1.2)
        section.right_margin  = Inches(1.0)

    # ── Заголовок ──
    title = doc.add_heading("ПРОТОКОЛ АУДИОЗАПИСИ", level=1)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.name = "Arial"
        run.font.size = Pt(16)
        run.font.color.rgb = RGBColor(0x1A, 0x1A, 0x2E)

    # ── Метаданные ──
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    meta_p   = doc.add_paragraph()
    meta_run = meta_p.add_run(f"Файл: {filename}   |   Дата обработки: {date_str}")
    meta_run.font.size  = Pt(9)
    meta_run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    meta_run.font.name  = "Arial"
    meta_p.alignment    = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph()  # пустая строка

    # ── Список участников ──
    if speakers:
        part_heading = doc.add_paragraph()
        part_run = part_heading.add_run("УЧАСТНИКИ:")
        part_run.bold       = True
        part_run.font.name  = "Arial"
        part_run.font.size  = Pt(11)

        for sp in speakers:
            sp_p   = doc.add_paragraph(style="List Bullet")
            sp_run = sp_p.add_run(sp)
            sp_run.font.name = "Arial"
            sp_run.font.size = Pt(10)

        doc.add_paragraph()

    # Разделитель
    sep_p = doc.add_paragraph("─" * 50)
    sep_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in sep_p.runs:
        run.font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
    doc.add_paragraph()

    # ── Реплики ──
    # Назначаем каждому спикеру уникальный цвет
    speaker_colors = _assign_speaker_colors(speakers)

    for seg in segments:
        ts      = seg.get("timestamp", "00:00:00")
        speaker = seg.get("speaker", "UNKNOWN")
        text    = seg.get("text", "").strip()
        if not text:
            continue

        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(4)
        p.paragraph_format.space_after  = Pt(4)

        # Таймкод серым
        ts_run            = p.add_run(f"[{ts}] ")
        ts_run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
        ts_run.font.size  = Pt(9)
        ts_run.font.name  = "Arial"

        # Имя спикера жирным с цветом
        color = speaker_colors.get(speaker, RGBColor(0x2C, 0x3E, 0x50))
        sp_run              = p.add_run(f"{speaker}:  ")
        sp_run.bold         = True
        sp_run.font.color.rgb = color
        sp_run.font.size    = Pt(10)
        sp_run.font.name    = "Arial"

        # Текст реплики
        text_run           = p.add_run(text)
        text_run.font.name = "Arial"
        text_run.font.size = Pt(10)

    doc.save(output_path)


def _assign_speaker_colors(speakers: List[str]) -> Dict[str, Any]:
    """Назначаем каждому спикеру уникальный цвет из палитры."""
    try:
        from docx.shared import RGBColor
    except ImportError:
        return {}

    palette = [
        RGBColor(0x1A, 0x53, 0xFF),   # синий
        RGBColor(0xC0, 0x39, 0x21),   # красный
        RGBColor(0x1E, 0x7E, 0x34),   # зелёный
        RGBColor(0x7B, 0x2D, 0x8B),   # фиолетовый
        RGBColor(0xE6, 0x6C, 0x00),   # оранжевый
        RGBColor(0x00, 0x74, 0x87),   # бирюзовый
        RGBColor(0x8B, 0x5A, 0x00),   # коричневый
        RGBColor(0x33, 0x33, 0x33),   # тёмно-серый
    ]

    return {
        speaker: palette[i % len(palette)]
        for i, speaker in enumerate(speakers)
    }
