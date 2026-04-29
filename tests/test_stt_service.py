import unittest

from services.stt_service import STTService, STTServiceError


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
        assert update is not None
        self.assertEqual(update.text, "hello there from this microphon")
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
        assert final_update is not None
        self.assertTrue(final_update.is_final)

    def test_rejects_non_string_text(self) -> None:
        with self.assertRaises(STTServiceError):
            self.service.normalize_update(
                participant_id="p-1",
                text=123,
                is_final=False,
                source="browser_web_speech",
            )


if __name__ == "__main__":
    unittest.main()
