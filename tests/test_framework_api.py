import unittest

from fastapi import FastAPI

import signaling.server as signaling_server
from rtc_room_framework import (
    AgentSession,
    AgentSessionError,
    EchoTranscriptResponder,
    RTCRoomFramework,
    create_app,
    get_manager,
    get_room_control,
    get_stt_service,
    register_shutdown_hook,
    register_startup_hook,
    reload_config,
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

    def test_lifecycle_helpers_are_exported(self) -> None:
        self.assertTrue(callable(register_startup_hook))
        self.assertTrue(callable(register_shutdown_hook))
        self.assertTrue(callable(reload_config))

    def test_app_uses_a_lifespan_handler_instead_of_on_event(self) -> None:
        app = create_app()
        self.assertIsNotNone(app.router.lifespan_context)


class LifecycleHookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        signaling_server.clear_lifecycle_hooks()

    async def asyncTearDown(self) -> None:
        signaling_server.clear_lifecycle_hooks()

    async def test_lifespan_runs_registered_hooks_in_order(self) -> None:
        calls: list[str] = []

        async def _on_start() -> None:
            calls.append("start")

        async def _on_stop() -> None:
            calls.append("stop")

        register_startup_hook(_on_start)
        register_shutdown_hook(_on_stop)

        app = create_app()
        async with app.router.lifespan_context(app):
            self.assertEqual(calls, ["start"])

        self.assertEqual(calls, ["start", "stop"])

    async def test_registering_the_same_hook_twice_runs_it_once(self) -> None:
        calls: list[str] = []

        async def _on_start() -> None:
            calls.append("start")

        register_startup_hook(_on_start)
        register_startup_hook(_on_start)

        app = create_app()
        async with app.router.lifespan_context(app):
            pass

        self.assertEqual(calls, ["start"])


if __name__ == "__main__":
    unittest.main()
