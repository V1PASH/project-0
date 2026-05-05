import unittest

from fastapi import FastAPI

from rtc_room_framework import (
    AgentSession,
    AgentSessionError,
    EchoTranscriptResponder,
    RTCRoomFramework,
    create_app,
    get_manager,
    get_room_control,
    get_stt_service,
)


class FrameworkAPITests(unittest.TestCase):
    def test_create_app_returns_fastapi_instance(self) -> None:
        app = create_app()
        self.assertIsInstance(app, FastAPI)

    def test_framework_wrapper_exposes_runtime_components(self) -> None:
        framework = RTCRoomFramework()
        self.assertIs(framework.app, create_app())
        self.assertIs(framework.manager, get_manager())
        self.assertIs(framework.stt_service, get_stt_service())
        self.assertIs(framework.room_control, get_room_control())

    def test_agent_api_symbols_are_exported(self) -> None:
        self.assertTrue(callable(AgentSession))
        self.assertTrue(callable(EchoTranscriptResponder))
        self.assertTrue(issubclass(AgentSessionError, RuntimeError))


if __name__ == "__main__":
    unittest.main()
