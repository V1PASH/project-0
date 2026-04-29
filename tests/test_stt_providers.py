import asyncio
import json
import os
import unittest
from unittest.mock import patch

from services.stt_providers import (
    DeepgramSTTProvider,
    OpenAISTTProvider,
    SarvamSTTProvider,
    build_stt_provider_from_env,
)


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class STTProviderFactoryTests(unittest.TestCase):
    def test_builds_none_provider_for_browser_mode(self) -> None:
        previous = os.environ.get("STT_PROVIDER")
        os.environ["STT_PROVIDER"] = "browser"
        try:
            provider = build_stt_provider_from_env()
            self.assertIsNone(provider)
        finally:
            if previous is None:
                os.environ.pop("STT_PROVIDER", None)
            else:
                os.environ["STT_PROVIDER"] = previous

    def test_builds_sarvam_provider(self) -> None:
        previous_provider = os.environ.get("STT_PROVIDER")
        previous_key = os.environ.get("SARVAM_API_KEY")
        os.environ["STT_PROVIDER"] = "sarvam"
        os.environ["SARVAM_API_KEY"] = "test-key"
        try:
            provider = build_stt_provider_from_env()
            self.assertIsInstance(provider, SarvamSTTProvider)
        finally:
            if previous_provider is None:
                os.environ.pop("STT_PROVIDER", None)
            else:
                os.environ["STT_PROVIDER"] = previous_provider
            if previous_key is None:
                os.environ.pop("SARVAM_API_KEY", None)
            else:
                os.environ["SARVAM_API_KEY"] = previous_key

    def test_builds_deepgram_provider(self) -> None:
        previous_provider = os.environ.get("STT_PROVIDER")
        previous_key = os.environ.get("DEEPGRAM_API_KEY")
        os.environ["STT_PROVIDER"] = "deepgram"
        os.environ["DEEPGRAM_API_KEY"] = "dg-key"
        try:
            provider = build_stt_provider_from_env()
            self.assertIsInstance(provider, DeepgramSTTProvider)
        finally:
            if previous_provider is None:
                os.environ.pop("STT_PROVIDER", None)
            else:
                os.environ["STT_PROVIDER"] = previous_provider
            if previous_key is None:
                os.environ.pop("DEEPGRAM_API_KEY", None)
            else:
                os.environ["DEEPGRAM_API_KEY"] = previous_key

    def test_builds_openai_provider(self) -> None:
        previous_provider = os.environ.get("STT_PROVIDER")
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["STT_PROVIDER"] = "openai"
        os.environ["OPENAI_API_KEY"] = "oa-key"
        try:
            provider = build_stt_provider_from_env()
            self.assertIsInstance(provider, OpenAISTTProvider)
        finally:
            if previous_provider is None:
                os.environ.pop("STT_PROVIDER", None)
            else:
                os.environ["STT_PROVIDER"] = previous_provider
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key


class STTProviderImplementationTests(unittest.TestCase):
    def test_deepgram_transcribe_chunk_parses_transcript(self) -> None:
        captured: dict = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeHTTPResponse(
                {
                    "results": {
                        "channels": [
                            {
                                "alternatives": [
                                    {"transcript": "hello from deepgram"},
                                ]
                            }
                        ]
                    }
                }
            )

        provider = DeepgramSTTProvider(api_key="dg-key")
        with patch("services.stt_providers.urllib.request.urlopen", side_effect=fake_urlopen):
            result = asyncio.run(
                provider.transcribe_chunk(
                    audio_bytes=b"abc",
                    mime_type="audio/webm;codecs=opus",
                    language_code="en-US",
                )
            )

        self.assertIsNotNone(result)
        if result is None:
            self.fail("Expected Deepgram transcript")
        self.assertEqual(result.text, "hello from deepgram")
        self.assertEqual(result.source, "deepgram")

        request = captured["request"]
        self.assertIn("api.deepgram.com/v1/listen", request.full_url)
        self.assertIn("model=nova-3", request.full_url)
        self.assertIn("smart_format=true", request.full_url)
        self.assertIn("language=en-US", request.full_url)
        self.assertEqual(request.data, b"abc")
        self.assertEqual(captured["timeout"], 20.0)

    def test_deepgram_uses_detect_language_for_unknown(self) -> None:
        captured: dict = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            return FakeHTTPResponse(
                {
                    "results": {
                        "channels": [{"alternatives": [{"transcript": "auto detected"}]}]
                    }
                }
            )

        provider = DeepgramSTTProvider(api_key="dg-key")
        with patch("services.stt_providers.urllib.request.urlopen", side_effect=fake_urlopen):
            asyncio.run(
                provider.transcribe_chunk(
                    audio_bytes=b"abc",
                    mime_type="audio/webm",
                    language_code="unknown",
                )
            )

        request = captured["request"]
        self.assertIn("detect_language=true", request.full_url)
        self.assertNotIn("&language=", request.full_url)
        self.assertNotIn("?language=", request.full_url)

    def test_openai_transcribe_chunk_parses_transcript(self) -> None:
        captured: dict = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeHTTPResponse({"text": "hello from openai"})

        provider = OpenAISTTProvider(api_key="oa-key")
        with patch("services.stt_providers.urllib.request.urlopen", side_effect=fake_urlopen):
            result = asyncio.run(
                provider.transcribe_chunk(
                    audio_bytes=b"xyz",
                    mime_type="audio/webm;codecs=opus",
                    language_code="en-IN",
                )
            )

        self.assertIsNotNone(result)
        if result is None:
            self.fail("Expected OpenAI transcript")
        self.assertEqual(result.text, "hello from openai")
        self.assertEqual(result.source, "openai")

        request = captured["request"]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/audio/transcriptions")
        self.assertEqual(captured["timeout"], 20.0)
        self.assertIn(b'name="model"', request.data)
        self.assertIn(b"gpt-4o-mini-transcribe", request.data)
        self.assertIn(b'name="language"', request.data)
        self.assertIn(b"\r\nen\r\n", request.data)
        self.assertIn(b'name="file"; filename="chunk.webm"', request.data)
        self.assertIn(b"Content-Type: audio/webm", request.data)

    def test_openai_omits_language_for_unknown(self) -> None:
        captured: dict = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            return FakeHTTPResponse({"text": "ok"})

        provider = OpenAISTTProvider(api_key="oa-key")
        with patch("services.stt_providers.urllib.request.urlopen", side_effect=fake_urlopen):
            asyncio.run(
                provider.transcribe_chunk(
                    audio_bytes=b"xyz",
                    mime_type="audio/webm",
                    language_code="unknown",
                )
            )

        request = captured["request"]
        self.assertNotIn(b'name="language"', request.data)


if __name__ == "__main__":
    unittest.main()
