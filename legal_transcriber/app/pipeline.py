from __future__ import annotations

import traceback
from typing import Callable

import requests
import torch
import whisperx

from .config import HF_TOKEN, OLLAMA_MODEL, OLLAMA_URL
from .models import TranscriptSegment
from .utils import chunk_segments, parse_llm_lines, render_plain_text

StageCallback = Callable[[str, int, str], None]


class PipelineError(RuntimeError):
    pass


class LegalTranscriptionPipeline:
    """
    Основной AI-пайплайн:
    1) WhisperX STT
    2) Выравнивание
    3) Диаризация
    4) LLM-корректировка через Ollama (Qwen 2.5)
    """

    def __init__(self) -> None:
        self.preferred_device = "cuda" if torch.cuda.is_available() else "cpu"

    def _ollama_generate(self, prompt: str) -> str:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_ctx": 8192,
            },
        }
        response = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json=payload,
            timeout=600,
        )
        response.raise_for_status()
        data = response.json()
        return str(data.get("response", "")).strip()

    def _build_llm_prompt(self, chunk_text: str) -> str:
        return f"""
Ты — юридический редактор протоколов судебных и досудебных аудиозаписей.
Исправь ошибки ASR (распознавания речи), восстанови пунктуацию, орфографию и читаемость.

Критичные правила:
1. НЕЛЬЗЯ менять смысл сказанного.
2. НЕЛЬЗЯ удалять строки.
3. НЕЛЬЗЯ объединять и разбивать строки.
4. НЕЛЬЗЯ менять SEGMENT_ID, таймкоды и метки спикеров.
5. Нужно вернуть результат СТРОГО в том же построчном формате:
   SEGMENT_ID=<число> [HH:MM:SS - HH:MM:SS] SPEAKER_XX: <исправленный текст>

Текст для правки:
{chunk_text}
""".strip()

    def _normalize_segments(self, diarized_result: dict) -> list[TranscriptSegment]:
        normalized: list[TranscriptSegment] = []
        for idx, seg in enumerate(diarized_result.get("segments", [])):
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", start))
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            speaker = str(seg.get("speaker", "SPEAKER_00"))
            normalized.append(
                TranscriptSegment(
                    id=idx,
                    start=start,
                    end=end,
                    speaker=speaker,
                    text=text,
                )
            )
        return normalized

    def _transcribe_with_fallback(
        self,
        audio,
        preferred_device: str,
        batch_size: int = 8,
    ) -> tuple[dict, str]:
        """
        Пытается выполнить STT на GPU, а при OOM делает fallback на CPU.
        """
        try:
            compute_type = "float16" if preferred_device == "cuda" else "int8"
            stt_model = whisperx.load_model(
                "large-v3",
                device=preferred_device,
                compute_type=compute_type,
                language=None,
            )
            return stt_model.transcribe(audio, batch_size=batch_size), preferred_device
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower() or preferred_device != "cuda":
                raise
            # Fallback при нехватке памяти GPU.
            torch.cuda.empty_cache()
            stt_model = whisperx.load_model(
                "large-v3",
                device="cpu",
                compute_type="int8",
                language=None,
            )
            return stt_model.transcribe(audio, batch_size=2), "cpu"

    def run(self, audio_path: str, on_stage: StageCallback) -> dict:
        try:
            on_stage("transcribing", 10, "Распознавание аудио (WhisperX)")
            audio = whisperx.load_audio(audio_path)
            stt_result, device = self._transcribe_with_fallback(
                audio,
                preferred_device=self.preferred_device,
            )

            language = str(stt_result.get("language", "unknown"))
            on_stage("aligning", 35, "Выравнивание таймкодов")
            align_model, metadata = whisperx.load_align_model(
                language_code=language if language != "unknown" else "ru",
                device=device,
            )
            aligned = whisperx.align(
                stt_result["segments"],
                align_model,
                metadata,
                audio,
                device,
                return_char_alignments=False,
            )

            on_stage("diarization", 55, "Диаризация спикеров")
            if not HF_TOKEN:
                raise PipelineError(
                    "Не задан HF_TOKEN. Укажите токен Hugging Face в .env для диаризации."
                )

            diarize_model = whisperx.DiarizationPipeline(
                use_auth_token=HF_TOKEN,
                device=device,
            )
            diarize_segments = diarize_model(audio)
            diarized = whisperx.assign_word_speakers(diarize_segments, aligned)
            segments = self._normalize_segments(diarized)

            if not segments:
                raise PipelineError("После диаризации не осталось сегментов с текстом.")

            on_stage("llm_processing", 75, "LLM-корректировка (Qwen 2.5)")
            chunks = chunk_segments(segments, target_words=2400, overlap_words=220)
            corrected_text_by_id: dict[int, str] = {}

            for chunk_idx, chunk in enumerate(chunks, start=1):
                on_stage(
                    "llm_processing",
                    min(75 + int((chunk_idx / max(1, len(chunks))) * 20), 95),
                    f"LLM-обработка чанка {chunk_idx}/{len(chunks)}",
                )
                prompt = self._build_llm_prompt(chunk.content)
                llm_output = self._ollama_generate(prompt)
                parsed = parse_llm_lines(llm_output)

                # Если модель частично нарушила формат — оставляем исходный текст
                # для непрочитанных сегментов.
                for seg_id in chunk.segment_ids:
                    candidate = parsed.get(seg_id, "").strip()
                    if candidate:
                        corrected_text_by_id[seg_id] = candidate

            final_segments: list[TranscriptSegment] = []
            for seg in segments:
                final_segments.append(
                    TranscriptSegment(
                        id=seg.id,
                        start=seg.start,
                        end=seg.end,
                        speaker=seg.speaker,
                        text=corrected_text_by_id.get(seg.id, seg.text),
                    )
                )

            formatted_text = render_plain_text(final_segments)
            on_stage("completed", 100, "Готово")
            return {
                "language": language,
                "segments": [item.model_dump() for item in final_segments],
                "formatted_text": formatted_text,
            }
        except PipelineError:
            raise
        except requests.RequestException as exc:
            raise PipelineError(
                "Не удалось обратиться к Ollama API. Проверьте, что Ollama запущена."
            ) from exc
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "out of memory" in msg:
                raise PipelineError(
                    "Нехватка памяти GPU/CPU во время обработки. "
                    "Попробуйте более короткий файл или отключите другие задачи."
                ) from exc
            raise PipelineError(f"Ошибка выполнения пайплайна: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            trace = traceback.format_exc(limit=4)
            raise PipelineError(f"Непредвиденная ошибка: {exc}\n{trace}") from exc
