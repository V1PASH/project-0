import unittest

from services.stt_service import STTService, STTServiceError
from services.stt_providers import ProviderTranscript, STTProviderError


class FakeProvider:
    name = "fake-provider"

    async def transcribe_chunk(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        language_code: str,
    ) -> ProviderTranscript | None:
        if not audio_bytes:
            return None
        return ProviderTranscript(
            text=f"provider-{language_code}",
            is_final=True,
            source=self.name,
        )


class FailingProvider:
    name = "failing-provider"

    async def transcribe_chunk(
        self,
        *,
        audio_bytes: bytes,
        mime_type: str,
        language_code: str,
    ) -> ProviderTranscript | None:
        raise STTProviderError("provider failed")


class STTServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = STTService(max_transcript_chars=30)

    def test_normalizes_payload_and_limits_length(self) -> None:
        update = self.service.normalize_update(
            participant_id="p-1",
            text="   hello    there from   this microphone stream   ",
            is_final=False,
            source=" browser_web_speech ",
        )

        self.assertIsNotNone(update)
        if update is None:
            self.fail("Expected transcript update")
        self.assertEqual(update.text, "hello there from this microphone")
        self.assertFalse(update.is_final)
        self.assertEqual(update.source, "browser_web_speech")

    def test_deduplicates_identical_updates(self) -> None:
        first = self.service.normalize_update(
            participant_id="p-1",
            text="same words",
            is_final=False,
            source="browser_web_speech",
        )
        second = self.service.normalize_update(
            participant_id="p-1",
            text="same words",
            is_final=False,
            source="browser_web_speech",
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_allows_final_after_interim(self) -> None:
        self.service.normalize_update(
            participant_id="p-1",
            text="same words",
            is_final=False,
            source="browser_web_speech",
        )
        final_update = self.service.normalize_update(
            participant_id="p-1",
            text="same words",
            is_final=True,
            source="browser_web_speech",
        )

        self.assertIsNotNone(final_update)
        if final_update is None:
            self.fail("Expected final transcript update")
        self.assertTrue(final_update.is_final)

    def test_rejects_non_string_text(self) -> None:
        with self.assertRaises(STTServiceError):
            self.service.normalize_update(
                participant_id="p-1",
                text=123,
                is_final=False,
                source="browser_web_speech",
            )

    def test_transcribe_audio_chunk_with_provider(self) -> None:
        async def _run() -> None:
            service = STTService(provider=FakeProvider())
            update = await service.transcribe_audio_chunk(
                participant_id="p-1",
                audio_bytes=b"1234",
                mime_type="audio/webm",
                language_code="en-IN",
            )
            self.assertIsNotNone(update)
            if update is None:
                self.fail("Expected provider transcript update")
            self.assertEqual(update.text, "provider-en-IN")
            self.assertTrue(update.is_final)
            self.assertEqual(update.source, "fake-provider")

        import asyncio

        asyncio.run(_run())

    def test_transcribe_audio_chunk_provider_error(self) -> None:
        async def _run() -> None:
            service = STTService(provider=FailingProvider())
            with self.assertRaises(STTServiceError):
                await service.transcribe_audio_chunk(
                    participant_id="p-1",
                    audio_bytes=b"1234",
                    mime_type="audio/webm",
                    language_code="en-IN",
                )

        import asyncio

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
