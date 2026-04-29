"""Provider integrations for server-side speech-to-text."""
from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Optional


class STTProviderError(RuntimeError):
    """Raised when provider transcription fails."""


@dataclass(frozen=True)
class ProviderTranscript:
    """Normalized transcript returned by a provider."""

    text: str
    is_final: bool
    source: str


class BaseSTTProvider:
    """Abstract provider contract for STT providers."""

    name: str = "base"

    async def transcribe_chunk(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        language_code: str,
    ) -> ProviderTranscript | None:
        raise NotImplementedError


class SarvamSTTProvider(BaseSTTProvider):
    """Sarvam STT provider using REST API for short audio chunks."""

    name = "sarvam"

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://api.sarvam.ai/speech-to-text",
        model: str = "saaras:v3",
        mode: str = "transcribe",
        timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key:
            raise ValueError("Sarvam API key is required")

        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._mode = mode
        self._timeout_seconds = timeout_seconds

    async def transcribe_chunk(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        language_code: str,
    ) -> ProviderTranscript | None:
        if not audio_bytes:
            return None

        response_json = await asyncio.to_thread(
            self._transcribe_chunk_sync,
            audio_bytes,
            mime_type,
            language_code,
        )

        transcript = response_json.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            return None

        return ProviderTranscript(
            text=transcript.strip(),
            is_final=True,
            source=self.name,
        )

    def _transcribe_chunk_sync(
        self,
        audio_bytes: bytes,
        mime_type: str,
        language_code: str,
    ) -> dict:
        fields = {
            "model": self._model,
            "mode": self._mode,
            "language_code": language_code,
        }
        file_ext = _mime_to_extension(mime_type)
        filename = f"chunk.{file_ext}"

        body, content_type = _encode_multipart_form_data(
            fields=fields,
            file_field_name="file",
            filename=filename,
            file_bytes=audio_bytes,
            file_content_type=mime_type or "application/octet-stream",
        )

        request = urllib.request.Request(
            self._api_url,
            data=body,
            headers={
                "api-subscription-key": self._api_key,
                "Content-Type": content_type,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise STTProviderError(f"Sarvam error ({exc.code}): {error_body}") from exc
        except urllib.error.URLError as exc:
            raise STTProviderError(f"Sarvam connection error: {exc.reason}") from exc

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise STTProviderError(f"Sarvam returned invalid JSON: {payload[:200]}") from exc


class DeepgramSTTProvider(BaseSTTProvider):
    """Deepgram pre-recorded STT provider for short audio chunks."""

    name = "deepgram"

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://api.deepgram.com/v1/listen",
        model: str = "nova-3",
        smart_format: bool = True,
        timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key:
            raise ValueError("Deepgram API key is required")

        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._smart_format = bool(smart_format)
        self._timeout_seconds = timeout_seconds

    async def transcribe_chunk(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        language_code: str,
    ) -> ProviderTranscript | None:
        if not audio_bytes:
            return None

        response_json = await asyncio.to_thread(
            self._transcribe_chunk_sync,
            audio_bytes,
            mime_type,
            language_code,
        )
        transcript = _extract_deepgram_transcript(response_json)
        if not transcript:
            return None

        return ProviderTranscript(
            text=transcript,
            is_final=True,
            source=self.name,
        )

    def _transcribe_chunk_sync(
        self,
        audio_bytes: bytes,
        mime_type: str,
        language_code: str,
    ) -> dict:
        query_params: dict[str, str] = {}
        if self._model:
            query_params["model"] = self._model
        if self._smart_format:
            query_params["smart_format"] = "true"

        normalized_language = (language_code or "").strip()
        if normalized_language and normalized_language.lower() != "unknown":
            query_params["language"] = normalized_language
        else:
            query_params["detect_language"] = "true"

        query_string = urllib.parse.urlencode(query_params)
        target_url = self._api_url if not query_string else f"{self._api_url}?{query_string}"
        normalized_mime_type = _normalize_mime_type(mime_type)

        request = urllib.request.Request(
            target_url,
            data=audio_bytes,
            headers={
                "Authorization": f"Token {self._api_key}",
                "Content-Type": normalized_mime_type or "application/octet-stream",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise STTProviderError(f"Deepgram error ({exc.code}): {error_body}") from exc
        except urllib.error.URLError as exc:
            raise STTProviderError(f"Deepgram connection error: {exc.reason}") from exc

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise STTProviderError(f"Deepgram returned invalid JSON: {payload[:200]}") from exc


class OpenAISTTProvider(BaseSTTProvider):
    """OpenAI Audio Transcriptions provider for short audio chunks."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = "https://api.openai.com/v1/audio/transcriptions",
        model: str = "gpt-4o-mini-transcribe",
        timeout_seconds: float = 20.0,
    ) -> None:
        if not api_key:
            raise ValueError("OpenAI API key is required")

        self._api_key = api_key
        self._api_url = api_url
        self._model = model
        self._timeout_seconds = timeout_seconds

    async def transcribe_chunk(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        language_code: str,
    ) -> ProviderTranscript | None:
        if not audio_bytes:
            return None

        response_json = await asyncio.to_thread(
            self._transcribe_chunk_sync,
            audio_bytes,
            mime_type,
            language_code,
        )
        transcript = response_json.get("text")
        if not isinstance(transcript, str) or not transcript.strip():
            return None

        return ProviderTranscript(
            text=transcript.strip(),
            is_final=True,
            source=self.name,
        )

    def _transcribe_chunk_sync(
        self,
        audio_bytes: bytes,
        mime_type: str,
        language_code: str,
    ) -> dict:
        normalized_mime_type = _normalize_mime_type(mime_type)
        file_ext = _mime_to_extension(normalized_mime_type)
        filename = f"chunk.{file_ext}"

        fields: dict[str, str] = {"model": self._model}
        openai_language = _normalize_openai_language(language_code)
        if openai_language:
            fields["language"] = openai_language

        body, content_type = _encode_multipart_form_data(
            fields=fields,
            file_field_name="file",
            filename=filename,
            file_bytes=audio_bytes,
            file_content_type=normalized_mime_type or "application/octet-stream",
        )

        request = urllib.request.Request(
            self._api_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": content_type,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise STTProviderError(f"OpenAI error ({exc.code}): {error_body}") from exc
        except urllib.error.URLError as exc:
            raise STTProviderError(f"OpenAI connection error: {exc.reason}") from exc

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise STTProviderError(f"OpenAI returned invalid JSON: {payload[:200]}") from exc


def build_stt_provider_from_env() -> Optional[BaseSTTProvider]:
    provider_name = os.getenv("STT_PROVIDER", "browser").strip().lower()

    if provider_name in {"", "browser", "none", "off"}:
        return None

    if provider_name == "sarvam":
        api_key = os.getenv("SARVAM_API_KEY", "").strip() or os.getenv(
            "SARVAM_API_SUBSCRIPTION_KEY", ""
        ).strip()
        if not api_key:
            raise ValueError(
                "STT_PROVIDER=sarvam requires SARVAM_API_KEY or SARVAM_API_SUBSCRIPTION_KEY"
            )

        return SarvamSTTProvider(
            api_key=api_key,
            model=os.getenv("SARVAM_STT_MODEL", "saaras:v3").strip() or "saaras:v3",
            mode=os.getenv("SARVAM_STT_MODE", "transcribe").strip() or "transcribe",
            timeout_seconds=float(os.getenv("SARVAM_STT_TIMEOUT_SECONDS", "20")),
        )

    if provider_name == "deepgram":
        api_key = os.getenv("DEEPGRAM_API_KEY", "").strip()
        if not api_key:
            raise ValueError("STT_PROVIDER=deepgram requires DEEPGRAM_API_KEY")

        smart_format_value = os.getenv("DEEPGRAM_STT_SMART_FORMAT", "true").strip().lower()
        smart_format = smart_format_value not in {"0", "false", "no", "off"}
        return DeepgramSTTProvider(
            api_key=api_key,
            model=os.getenv("DEEPGRAM_STT_MODEL", "nova-3").strip() or "nova-3",
            smart_format=smart_format,
            timeout_seconds=float(os.getenv("DEEPGRAM_STT_TIMEOUT_SECONDS", "20")),
        )

    if provider_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError("STT_PROVIDER=openai requires OPENAI_API_KEY")

        return OpenAISTTProvider(
            api_key=api_key,
            model=(
                os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe").strip()
                or "gpt-4o-mini-transcribe"
            ),
            timeout_seconds=float(os.getenv("OPENAI_STT_TIMEOUT_SECONDS", "20")),
        )

    raise ValueError(f"Unsupported STT provider: {provider_name}")


def _mime_to_extension(mime_type: str) -> str:
    normalized = _normalize_mime_type(mime_type)
    if not normalized:
        return "bin"
    if normalized in {"audio/webm", "video/webm"}:
        return "webm"
    if normalized in {"audio/wav", "audio/x-wav", "audio/wave"}:
        return "wav"
    if normalized in {"audio/ogg", "audio/opus"}:
        return "ogg"
    if normalized == "audio/mpeg":
        return "mp3"
    if normalized == "audio/mp4":
        return "m4a"
    if normalized in {"audio/mpga", "audio/mpeg3"}:
        return "mpga"
    return "bin"


def _normalize_mime_type(mime_type: str) -> str:
    return (mime_type or "").split(";", 1)[0].strip().lower()


def _extract_deepgram_transcript(response_json: dict) -> str:
    results = response_json.get("results")
    if not isinstance(results, dict):
        return ""

    channels = results.get("channels")
    if not isinstance(channels, list) or not channels:
        return ""

    channel = channels[0]
    if not isinstance(channel, dict):
        return ""

    alternatives = channel.get("alternatives")
    if not isinstance(alternatives, list) or not alternatives:
        return ""

    alternative = alternatives[0]
    if not isinstance(alternative, dict):
        return ""

    transcript = alternative.get("transcript")
    if not isinstance(transcript, str):
        return ""
    return transcript.strip()


def _normalize_openai_language(language_code: str) -> str | None:
    normalized = (language_code or "").strip().lower()
    if not normalized or normalized == "unknown":
        return None

    primary = normalized.split("-", 1)[0].split("_", 1)[0]
    if len(primary) != 2 or not primary.isalpha():
        return None
    return primary


def _encode_multipart_form_data(
    *,
    fields: dict[str, str],
    file_field_name: str,
    filename: str,
    file_bytes: bytes,
    file_content_type: str,
) -> tuple[bytes, str]:
    boundary = f"----codex-{uuid.uuid4().hex}"
    boundary_bytes = boundary.encode("utf-8")
    newline = b"\r\n"

    parts: list[bytes] = []

    for key, value in fields.items():
        parts.append(b"--" + boundary_bytes + newline)
        parts.append(
            f'Content-Disposition: form-data; name="{key}"'.encode("utf-8") + newline + newline
        )
        parts.append(str(value).encode("utf-8") + newline)

    parts.append(b"--" + boundary_bytes + newline)
    parts.append(
        (
            f'Content-Disposition: form-data; name="{file_field_name}"; filename="{filename}"'
        ).encode("utf-8")
        + newline
    )
    parts.append(f"Content-Type: {file_content_type}".encode("utf-8") + newline + newline)
    parts.append(file_bytes + newline)

    parts.append(b"--" + boundary_bytes + b"--" + newline)

    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type
