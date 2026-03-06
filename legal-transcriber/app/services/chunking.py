from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any


@dataclass(slots=True)
class TranscriptChunk:
    chunk_id: int
    start_index: int
    end_index: int
    target_start_index: int
    target_end_index: int
    segments: list[dict[str, Any]]
    target_segment_ids: list[str]


def estimate_segment_tokens(text: str) -> int:
    normalized = " ".join(text.split())
    if not normalized:
        return 1
    word_estimate = max(1, len(normalized.split()))
    char_estimate = ceil(len(normalized) / 4)
    return max(word_estimate, char_estimate)


def build_llm_chunks(
    segments: list[dict[str, Any]],
    max_tokens: int = 2400,
    overlap_tokens: int = 220,
) -> list[TranscriptChunk]:
    if not segments:
        return []

    token_costs = [estimate_segment_tokens(segment["text"]) + 12 for segment in segments]
    chunks: list[TranscriptChunk] = []

    cursor = 0
    next_target_start = 0
    chunk_id = 0

    while cursor < len(segments):
        current_tokens = 0
        end_index = cursor - 1
        index = cursor

        while index < len(segments):
            next_cost = token_costs[index]
            if index > cursor and current_tokens + next_cost > max_tokens:
                break
            current_tokens += next_cost
            end_index = index
            index += 1

        if end_index < cursor:
            end_index = cursor

        target_start = max(cursor, next_target_start)
        target_end = end_index
        chunk_segments = [dict(segment) for segment in segments[cursor : end_index + 1]]
        target_segment_ids = [segment["id"] for segment in segments[target_start : target_end + 1]]

        chunks.append(
            TranscriptChunk(
                chunk_id=chunk_id,
                start_index=cursor,
                end_index=end_index,
                target_start_index=target_start,
                target_end_index=target_end,
                segments=chunk_segments,
                target_segment_ids=target_segment_ids,
            )
        )

        if end_index >= len(segments) - 1:
            break

        overlap_index = end_index
        overlap_budget = 0

        while overlap_index > cursor and overlap_budget < overlap_tokens:
            overlap_budget += token_costs[overlap_index]
            overlap_index -= 1

        next_target_start = end_index + 1
        cursor = overlap_index + 1
        chunk_id += 1

    return chunks
