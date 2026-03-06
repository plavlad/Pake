from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from app.core.config import settings
from app.services.chunking import TranscriptChunk, build_llm_chunks
from app.services.ollama_client import ollama_client


ProgressCallback = Callable[[str, int], None]


def format_timestamp(seconds: float | int | None) -> str:
    total_seconds = max(0, int(round(float(seconds or 0))))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def compose_formatted_transcript(segments: list[dict[str, Any]], speaker_map: dict[str, str]) -> str:
    lines: list[str] = []
    for segment in segments:
        speaker_id = segment["speaker"]
        speaker_name = speaker_map.get(speaker_id, speaker_id)
        lines.append(
            f"[{format_timestamp(segment['start'])} - {format_timestamp(segment['end'])}] "
            f"{speaker_name}: {segment['text']}"
        )
    return "\n".join(lines)


def extract_speakers(segments: list[dict[str, Any]], speaker_map: dict[str, str]) -> list[dict[str, str]]:
    ordered: "OrderedDict[str, str]" = OrderedDict()
    for segment in segments:
        speaker_id = segment["speaker"]
        ordered.setdefault(speaker_id, speaker_map.get(speaker_id, speaker_id))
    return [{"id": speaker_id, "name": name} for speaker_id, name in ordered.items()]


@dataclass(slots=True)
class PipelineArtifacts:
    language: str
    raw_segments: list[dict[str, Any]]


class TranscriptionPipeline:
    _whisper_models: dict[tuple[str, str], Any] = {}
    _align_models: dict[tuple[str, str], tuple[Any, Any]] = {}
    _diarizers: dict[str, Any] = {}

    def run(self, audio_path: str, progress: ProgressCallback) -> dict[str, Any]:
        artifacts = self._run_whisperx_pipeline(audio_path, progress)
        progress("Обработка LLM", 78)
        corrected_segments = self._apply_llm_correction(artifacts.raw_segments, progress)

        speaker_map = {speaker["id"]: speaker["id"] for speaker in extract_speakers(corrected_segments, {})}
        formatted_text = compose_formatted_transcript(corrected_segments, speaker_map)

        return {
            "language": artifacts.language,
            "segments": corrected_segments,
            "speaker_map": speaker_map,
            "speakers": extract_speakers(corrected_segments, speaker_map),
            "formatted_text": formatted_text,
        }

    def _run_whisperx_pipeline(self, audio_path: str, progress: ProgressCallback) -> PipelineArtifacts:
        try:
            return self._run_whisperx_pipeline_with_device(
                audio_path=audio_path,
                progress=progress,
                device=self._preferred_device(),
                compute_type="float16" if self._preferred_device() == "cuda" else "int8",
                batch_size=settings.whisper_batch_size,
            )
        except Exception as exc:
            if self._is_gpu_memory_error(exc) and self._preferred_device() == "cuda":
                progress("Нехватка памяти GPU, переход на CPU", 12)
                return self._run_whisperx_pipeline_with_device(
                    audio_path=audio_path,
                    progress=progress,
                    device="cpu",
                    compute_type="int8",
                    batch_size=2,
                )
            raise

    def _run_whisperx_pipeline_with_device(
        self,
        audio_path: str,
        progress: ProgressCallback,
        device: str,
        compute_type: str,
        batch_size: int,
    ) -> PipelineArtifacts:
        import whisperx

        progress("Распознавание", 10)
        audio = whisperx.load_audio(audio_path)
        model = self._load_whisper_model(device=device, compute_type=compute_type)
        transcription = model.transcribe(audio, batch_size=batch_size)

        progress("Выравнивание", 42)
        align_model, metadata = self._load_align_model(
            language_code=transcription["language"],
            device=device,
        )
        aligned = whisperx.align(
            transcription["segments"],
            align_model,
            metadata,
            audio,
            device,
            return_char_alignments=False,
        )

        progress("Диаризация", 60)
        diarizer = self._load_diarizer(device=device)
        diarization_segments = diarizer(audio)
        diarized = whisperx.assign_word_speakers(diarization_segments, aligned)

        return PipelineArtifacts(
            language=transcription["language"],
            raw_segments=self._build_segments(diarized),
        )

    def _load_whisper_model(self, device: str, compute_type: str) -> Any:
        import whisperx

        key = (device, compute_type)
        model = self._whisper_models.get(key)
        if model is None:
            model = whisperx.load_model(
                settings.whisper_model,
                device,
                compute_type=compute_type,
                language=None,
            )
            self._whisper_models[key] = model
        return model

    def _load_align_model(self, language_code: str, device: str) -> tuple[Any, Any]:
        import whisperx

        key = (language_code, device)
        model = self._align_models.get(key)
        if model is None:
            model = whisperx.load_align_model(language_code=language_code, device=device)
            self._align_models[key] = model
        return model

    def _load_diarizer(self, device: str) -> Any:
        import whisperx

        if not settings.hf_token:
            raise RuntimeError("Переменная окружения HF_TOKEN не задана. Без нее диаризация Pyannote не запустится.")

        diarizer = self._diarizers.get(device)
        if diarizer is None:
            diarizer = whisperx.DiarizationPipeline(use_auth_token=settings.hf_token, device=device)
            self._diarizers[device] = diarizer
        return diarizer

    def _build_segments(self, diarized_result: dict[str, Any]) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        word_segments = diarized_result.get("word_segments") or []

        # Сегменты собираются из слов, чтобы смена спикера внутри фразы не потерялась.
        current: dict[str, Any] | None = None

        for word in word_segments:
            text = normalize_whitespace(word.get("word", ""))
            if not text:
                continue

            speaker = word.get("speaker") or (current["speaker"] if current else "SPEAKER_00")
            start = float(word.get("start") or (current["end"] if current else 0.0))
            end = float(word.get("end") or start)

            if current and speaker == current["speaker"] and start - current["end"] <= 1.2:
                current["end"] = max(current["end"], end)
                current["text"] = normalize_whitespace(f"{current['text']} {text}")
                continue

            if current:
                current["text"] = current["text"].strip()
                segments.append(current)

            current = {
                "id": f"SEGMENT_{len(segments) + 1:05d}",
                "start": start,
                "end": end,
                "speaker": speaker,
                "text": text,
            }

        if current:
            current["text"] = current["text"].strip()
            segments.append(current)

        if segments:
            return segments

        fallback_segments = diarized_result.get("segments") or []
        prepared: list[dict[str, Any]] = []
        for index, segment in enumerate(fallback_segments, start=1):
            prepared.append(
                {
                    "id": f"SEGMENT_{index:05d}",
                    "start": float(segment.get("start") or 0.0),
                    "end": float(segment.get("end") or 0.0),
                    "speaker": segment.get("speaker") or "SPEAKER_00",
                    "text": normalize_whitespace(segment.get("text", "")),
                }
            )
        return prepared

    def _apply_llm_correction(
        self,
        segments: list[dict[str, Any]],
        progress: ProgressCallback,
    ) -> list[dict[str, Any]]:
        if not segments:
            return []

        corrected_map: dict[str, str] = {}
        chunks = build_llm_chunks(
            segments,
            max_tokens=settings.llm_chunk_token_limit,
            overlap_tokens=settings.llm_chunk_overlap_tokens,
        )

        for index, chunk in enumerate(chunks, start=1):
            chunk_progress = 78 + int((index / max(len(chunks), 1)) * 20)
            progress(f"Обработка LLM ({index}/{len(chunks)})", min(chunk_progress, 98))

            try:
                response = ollama_client.chat(self._build_messages(chunk), temperature=0.1)
                parsed = self._parse_llm_response(response)
            except Exception:
                parsed = {}

            for segment in chunk.segments:
                segment_id = segment["id"]
                if segment_id not in chunk.target_segment_ids:
                    continue
                corrected_map[segment_id] = normalize_whitespace(parsed.get(segment_id, segment["text"]))

        prepared: list[dict[str, Any]] = []
        for segment in segments:
            updated = dict(segment)
            updated["text"] = corrected_map.get(segment["id"], segment["text"])
            prepared.append(updated)
        return prepared

    def _build_messages(self, chunk: TranscriptChunk) -> list[dict[str, str]]:
        lines: list[str] = []
        for segment in chunk.segments:
            lines.append(
                f"[SEGMENT {segment['id']}] "
                f"[{format_timestamp(segment['start'])} - {format_timestamp(segment['end'])}] "
                f"[{segment['speaker']}] {segment['text']}"
            )

        instructions = (
            "Ты выступаешь как юрист-редактор протоколов. "
            "Исправь только ошибки распознавания, орфографию, пунктуацию и очевидные юридические термины. "
            "Смысл менять нельзя, сокращать нельзя, дополнять нельзя. "
            "Нужно сохранить КАЖДУЮ строку, ID сегмента, таймкоды и метку спикера без изменений. "
            "Верни результат построчно в том же формате, начиная каждую строку с [SEGMENT ...]."
        )
        return [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": "Фрагмент стенограммы:\n\n" + "\n".join(lines),
            },
        ]

    def _parse_llm_response(self, content: str) -> dict[str, str]:
        result: dict[str, str] = {}
        current_id: str | None = None
        current_text: list[str] = []

        def flush() -> None:
            nonlocal current_id, current_text
            if current_id:
                result[current_id] = normalize_whitespace(" ".join(current_text))
            current_id = None
            current_text = []

        pattern = re.compile(
            r"^\[SEGMENT\s+(?P<segment_id>[^\]]+)\]\s+\[(?P<time>[^\]]+)\]\s+\[(?P<speaker>[^\]]+)\]\s*(?P<text>.*)$"
        )

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            match = pattern.match(line)
            if match:
                flush()
                current_id = match.group("segment_id").strip()
                current_text = [match.group("text").strip()]
            elif current_id:
                current_text.append(line)

        flush()
        return result

    def _preferred_device(self) -> str:
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def _is_gpu_memory_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        return "cuda out of memory" in message or "cudnn" in message or "outofmemory" in message


pipeline = TranscriptionPipeline()
