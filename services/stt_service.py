"""Utilities for real-time speech-to-text transcript updates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class STTServiceError(ValueError):
    """Raised when an STT transcript payload is invalid."""


@dataclass(frozen=True)
class TranscriptUpdate:
    """Normalized transcript update emitted by the STT service."""

    text: str
    is_final: bool
    source: str


class STTService:
    """Normalizes and de-duplicates transcript payloads from clients."""

    DEFAULT_SOURCE = "browser_web_speech"

    def __init__(self, max_transcript_chars: int = 400) -> None:
        self._max_transcript_chars = max(32, max_transcript_chars)
        self._last_transcript_state_by_participant: dict[str, tuple[str, bool]] = {}

    def normalize_update(
        self,
        *,
        participant_id: str,
        text: Any,
        is_final: Any,
        source: Any,
    ) -> TranscriptUpdate | None:
        if not isinstance(participant_id, str) or not participant_id.strip():
            raise STTServiceError("participant_id is required")

        if not isinstance(text, str):
            raise STTServiceError("text must be a string")

        normalized_text = " ".join(text.strip().split())
        if not normalized_text:
            return None

        if len(normalized_text) > self._max_transcript_chars:
            normalized_text = normalized_text[: self._max_transcript_chars].rstrip()

        normalized_is_final = bool(is_final)
        normalized_source = self._normalize_source(source)

        previous_state = self._last_transcript_state_by_participant.get(participant_id)
        current_state = (normalized_text, normalized_is_final)
        if previous_state == current_state:
            return None

        if normalized_is_final:
            self._last_transcript_state_by_participant.pop(participant_id, None)
        else:
            self._last_transcript_state_by_participant[participant_id] = current_state

        return TranscriptUpdate(
            text=normalized_text,
            is_final=normalized_is_final,
            source=normalized_source,
        )

    def clear_participant(self, participant_id: str) -> None:
        self._last_transcript_state_by_participant.pop(participant_id, None)

    def _normalize_source(self, source: Any) -> str:
        if isinstance(source, str):
            normalized_source = source.strip()
            if normalized_source:
                return normalized_source[:64]
        return self.DEFAULT_SOURCE
