import asyncio
import base64
import json
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import signaling.server as signaling_server
from room.control import RoomControlModule
from room.manager import RoomManager
from room.model import RoomFullError
from services.stt_providers import ProviderTranscript
from services.stt_service import STTService


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)


class FakeAudioProvider:
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
            text=f"chunk-{len(audio_bytes)}-{mime_type.split(';', 1)[0]}-{language_code}",
            is_final=True,
            source=self.name,
        )


class FakeSocketWebSocket(FakeWebSocket):
    """Websocket double that replays a scripted list of ASGI events."""

    def __init__(self, events: list[dict] | None = None) -> None:
        super().__init__()
        self.accepted = False
        self._events = list(events or [])

    async def accept(self) -> None:
        self.accepted = True

    async def receive(self) -> dict:
        if self._events:
            return self._events.pop(0)
        return {"type": "websocket.disconnect", "code": 1000}


class SignalingServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        signaling_server.manager = RoomManager()
        signaling_server.room_control = RoomControlModule(signaling_server.manager)
        signaling_server.stt_service = STTService()
        signaling_server.ROOM_CREATE_API_KEY = ""
        signaling_server.ROOM_CREATE_API_SECRET = ""
        signaling_server.ROOM_CREATE_TOKEN_TTL_SECONDS = 300
        signaling_server.REQUIRE_CREATE_ROOM_AUTH = False
        signaling_server.REQUIRE_EXPLICIT_ROOM_CREATE = False

    async def test_index_returns_404_when_no_index_is_available(self) -> None:
        original_web_root = signaling_server.WEB_ROOT
        signaling_server.WEB_ROOT = Path("/tmp/rtc-room-framework-missing-web-root")
        try:
            with patch("signaling.server._load_packaged_index_html", return_value=None):
                response = await signaling_server.index()
        finally:
            signaling_server.WEB_ROOT = original_web_root

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            json.loads(response.body),
            {
                "error": "index_not_found",
                "message": "No index.html found in web root or packaged assets",
            },
        )

    async def test_join_room_and_leave_room_flow(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")

        await signaling_server._handle_message(
            participant,
            {"type": "join_room", "room_id": "room-a", "name": "Alice"},
        )
        await signaling_server._handle_message(participant, {"type": "leave_room"})

        self.assertEqual(ws.messages[0]["type"], "joined_room")
        self.assertEqual(ws.messages[0]["room"]["room_id"], "room-a")
        self.assertEqual(ws.messages[1]["type"], "left_room")
        self.assertEqual(ws.messages[1]["room_id"], "room-a")
        self.assertTrue(ws.messages[1]["room_deleted"])

    async def test_create_room_marks_room_as_created(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")

        await signaling_server._handle_message(
            participant,
            {"type": "create_room", "room_id": "sdk-room", "name": "Alice"},
        )

        self.assertEqual(ws.messages[0]["type"], "joined_room")
        self.assertEqual(ws.messages[0]["room"]["room_id"], "sdk-room")
        self.assertTrue(ws.messages[0]["room_created"])
        self.assertEqual(ws.messages[0]["join_source"], "create_room")

    async def test_create_room_rejects_existing_room_name(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(
            alice,
            {"type": "create_room", "room_id": "same-room", "name": "Alice"},
        )
        await signaling_server._handle_message(
            bob,
            {"type": "create_room", "room_id": "same-room", "name": "Bob"},
        )

        self.assertEqual(ws_bob.messages[-1]["type"], "error")
        self.assertEqual(ws_bob.messages[-1]["code"], "room_already_exists")

    async def test_create_room_requires_auth_when_enabled(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        signaling_server.ROOM_CREATE_API_KEY = "room-key"
        signaling_server.ROOM_CREATE_API_SECRET = "room-secret"
        signaling_server.REQUIRE_CREATE_ROOM_AUTH = True

        await signaling_server._handle_message(
            participant,
            {"type": "create_room", "room_id": "secure-room", "name": "Alice"},
        )
        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "invalid_create_room_auth")

    async def test_create_room_rejects_invalid_auth_token(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        signaling_server.ROOM_CREATE_API_KEY = "room-key"
        signaling_server.ROOM_CREATE_API_SECRET = "room-secret"
        signaling_server.REQUIRE_CREATE_ROOM_AUTH = True
        api_ts = 1_700_000_000
        valid_token = signaling_server._build_create_room_token(
            api_key="room-key",
            api_secret="room-secret",
            room_id="secure-room",
            api_ts=api_ts,
        )

        await signaling_server._handle_message(
            participant,
            {
                "type": "create_room",
                "room_id": "secure-room",
                "api_key": "wrong-key",
                "api_token": valid_token,
                "api_ts": api_ts,
            },
        )
        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "unauthorized_create_room")

    async def test_create_room_rejects_expired_auth_token(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        signaling_server.ROOM_CREATE_API_KEY = "room-key"
        signaling_server.ROOM_CREATE_API_SECRET = "room-secret"
        signaling_server.REQUIRE_CREATE_ROOM_AUTH = True
        signaling_server.ROOM_CREATE_TOKEN_TTL_SECONDS = 60
        api_ts = int(time.time()) - 10_000
        token = signaling_server._build_create_room_token(
            api_key="room-key",
            api_secret="room-secret",
            room_id="secure-room",
            api_ts=api_ts,
        )

        await signaling_server._handle_message(
            participant,
            {
                "type": "create_room",
                "room_id": "secure-room",
                "api_key": "room-key",
                "api_token": token,
                "api_ts": api_ts,
            },
        )
        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "expired_create_room_token")

    async def test_create_room_accepts_valid_auth_token(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        signaling_server.ROOM_CREATE_API_KEY = "room-key"
        signaling_server.ROOM_CREATE_API_SECRET = "room-secret"
        signaling_server.REQUIRE_CREATE_ROOM_AUTH = True
        api_ts = int(time.time())
        token = signaling_server._build_create_room_token(
            api_key="room-key",
            api_secret="room-secret",
            room_id="secure-room",
            api_ts=api_ts,
        )

        await signaling_server._handle_message(
            participant,
            {
                "type": "create_room",
                "room_id": "secure-room",
                "api_key": "room-key",
                "api_token": token,
                "api_ts": api_ts,
            },
        )

        self.assertEqual(ws.messages[-1]["type"], "joined_room")
        self.assertTrue(ws.messages[-1]["room_created"])

    async def test_join_room_returns_room_full_error(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(
            alice,
            {"type": "create_room", "room_id": "tiny-room", "max_participants": 1},
        )
        await signaling_server._handle_message(
            bob,
            {"type": "join_room", "room_id": "tiny-room"},
        )

        self.assertEqual(ws_bob.messages[-1]["type"], "error")
        self.assertEqual(ws_bob.messages[-1]["code"], "room_full")

    async def test_join_room_requires_existing_when_explicit_create_enabled(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        signaling_server.REQUIRE_EXPLICIT_ROOM_CREATE = True

        await signaling_server._handle_message(
            participant,
            {"type": "join_room", "room_id": "must-create-first"},
        )

        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "room_not_found")

    async def test_relays_webrtc_offer_between_participants(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-b"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-b"})

        await signaling_server._handle_message(
            alice,
            {
                "type": "webrtc_offer",
                "target_participant_id": bob.participant_id,
                "sdp": {"type": "offer", "sdp": "fake-offer"},
            },
        )
        await asyncio.sleep(0)

        relayed = ws_bob.messages[-1]
        self.assertEqual(relayed["type"], "webrtc_offer")
        self.assertEqual(relayed["from_participant_id"], alice.participant_id)
        self.assertEqual(relayed["sdp"]["sdp"], "fake-offer")

    async def test_rejects_relay_when_target_not_in_same_room(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-c"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-d"})

        await signaling_server._handle_message(
            alice,
            {
                "type": "ice_candidate",
                "target_participant_id": bob.participant_id,
                "candidate": {"candidate": "fake-candidate"},
            },
        )

        error_message = ws_alice.messages[-1]
        self.assertEqual(error_message["type"], "error")
        self.assertEqual(error_message["code"], "target_not_in_room")

    async def test_participant_activity_broadcasts_speaking_events(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-speaking"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-speaking"})
        ws_alice.messages.clear()
        ws_bob.messages.clear()

        await signaling_server._handle_message(
            alice,
            {"type": "participant_activity", "activity": "speaking_started"},
        )
        await asyncio.sleep(0)

        started_event = ws_bob.messages[-1]
        self.assertEqual(started_event["type"], "room_event")
        self.assertEqual(
            started_event["event"]["event_type"],
            "participant_speaking_started",
        )
        self.assertEqual(started_event["event"]["participant_id"], alice.participant_id)

        await signaling_server._handle_message(
            alice,
            {"type": "participant_activity", "activity": "speaking_stopped"},
        )
        await asyncio.sleep(0)

        stopped_event = ws_bob.messages[-1]
        self.assertEqual(stopped_event["type"], "room_event")
        self.assertEqual(
            stopped_event["event"]["event_type"],
            "participant_speaking_stopped",
        )
        self.assertEqual(stopped_event["event"]["participant_id"], alice.participant_id)

    async def test_rejects_invalid_participant_activity(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        await signaling_server._handle_message(participant, {"type": "join_room", "room_id": "room-a"})
        ws.messages.clear()

        await signaling_server._handle_message(
            participant,
            {"type": "participant_activity", "activity": "invalid"},
        )

        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "invalid_activity")

    async def test_stt_transcript_broadcasts_transcript_event(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-stt"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-stt"})
        ws_alice.messages.clear()
        ws_bob.messages.clear()

        await signaling_server._handle_message(
            alice,
            {
                "type": "stt_transcript",
                "text": "hello from alice",
                "is_final": False,
                "source": "browser_web_speech",
            },
        )
        await asyncio.sleep(0)

        self.assertTrue(ws_bob.messages)
        event = ws_bob.messages[-1]
        self.assertEqual(event["type"], "room_event")
        self.assertEqual(event["event"]["event_type"], "participant_transcript")
        self.assertEqual(event["event"]["participant_id"], alice.participant_id)
        self.assertEqual(event["event"]["transcript_text"], "hello from alice")
        self.assertFalse(event["event"]["transcript_is_final"])

    async def test_stt_transcript_deduplicates_repeated_interim_updates(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-stt-dedupe"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-stt-dedupe"})

        ws_bob.messages.clear()

        payload = {
            "type": "stt_transcript",
            "text": "same interim",
            "is_final": False,
            "source": "browser_web_speech",
        }
        await signaling_server._handle_message(alice, payload)
        await signaling_server._handle_message(alice, payload)
        await asyncio.sleep(0)

        transcript_events = [
            msg
            for msg in ws_bob.messages
            if msg.get("type") == "room_event"
            and msg.get("event", {}).get("event_type") == "participant_transcript"
        ]
        self.assertEqual(len(transcript_events), 1)

    async def test_rejects_invalid_stt_payload(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        await signaling_server._handle_message(participant, {"type": "join_room", "room_id": "room-stt-error"})
        ws.messages.clear()

        await signaling_server._handle_message(
            participant,
            {"type": "stt_transcript", "text": 123, "is_final": False},
        )

        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "invalid_stt_payload")

    async def test_stt_audio_chunk_broadcasts_transcript_event(self) -> None:
        signaling_server.stt_service = STTService(provider=FakeAudioProvider())
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")

        await signaling_server._handle_message(alice, {"type": "join_room", "room_id": "room-stt-audio"})
        await signaling_server._handle_message(bob, {"type": "join_room", "room_id": "room-stt-audio"})
        ws_alice.messages.clear()
        ws_bob.messages.clear()

        payload = {
            "type": "stt_audio_chunk",
            "audio_base64": base64.b64encode(b"abc123").decode("ascii"),
            "mime_type": "audio/webm;codecs=opus",
            "language_code": "en-IN",
        }
        await signaling_server._handle_message(alice, payload)
        await asyncio.sleep(0)

        self.assertTrue(ws_bob.messages)
        event = ws_bob.messages[-1]
        self.assertEqual(event["type"], "room_event")
        self.assertEqual(event["event"]["event_type"], "participant_transcript")
        self.assertEqual(event["event"]["participant_id"], alice.participant_id)
        self.assertEqual(event["event"]["transcript_source"], "fake-provider")
        self.assertEqual(event["event"]["transcript_text"], "chunk-6-audio/webm-en-IN")

    async def test_stt_audio_chunk_rejects_invalid_base64(self) -> None:
        signaling_server.stt_service = STTService(provider=FakeAudioProvider())
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")
        await signaling_server._handle_message(
            participant,
            {"type": "join_room", "room_id": "room-stt-audio-error"},
        )
        ws.messages.clear()

        await signaling_server._handle_message(
            participant,
            {
                "type": "stt_audio_chunk",
                "audio_base64": "###invalid###",
                "mime_type": "audio/webm",
            },
        )

        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "invalid_stt_audio_chunk")

    async def test_publish_audio_broadcasts_to_other_participants(self) -> None:
        ws_alice = FakeWebSocket()
        ws_bob = FakeWebSocket()
        alice = signaling_server.manager.register_participant(ws_alice, name="Alice")
        bob = signaling_server.manager.register_participant(ws_bob, name="Bob")
        await signaling_server._handle_message(
            alice, {"type": "join_room", "room_id": "audio-room", "name": "Alice"}
        )
        await signaling_server._handle_message(
            bob, {"type": "join_room", "room_id": "audio-room", "name": "Bob"}
        )
        await asyncio.sleep(0)
        ws_alice.messages.clear()
        ws_bob.messages.clear()

        audio_bytes = b"\x01\x02\x03\x04"
        await signaling_server._handle_message(
            alice,
            {
                "type": "publish_audio",
                "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
                "mime_type": "audio/pcm",
                "sequence": 3,
            },
        )
        await asyncio.sleep(0)

        self.assertEqual(ws_alice.messages[-1]["type"], "audio_published")
        self.assertEqual(ws_alice.messages[-1]["audio_bytes"], len(audio_bytes))

        self.assertEqual(ws_bob.messages[-1]["type"], "room_audio")
        self.assertEqual(ws_bob.messages[-1]["from_participant_id"], alice.participant_id)
        self.assertEqual(ws_bob.messages[-1]["room_id"], "audio-room")
        self.assertEqual(ws_bob.messages[-1]["sequence"], 3)
        self.assertEqual(ws_bob.messages[-1]["audio_bytes"], len(audio_bytes))
        self.assertEqual(ws_bob.messages[-1]["audio_base64"], base64.b64encode(audio_bytes).decode("ascii"))

    async def test_publish_audio_requires_room_membership(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")

        await signaling_server._handle_message(
            participant,
            {
                "type": "publish_audio",
                "audio_base64": base64.b64encode(b"abc").decode("ascii"),
            },
        )

        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "not_in_room")

    async def test_rejects_max_participants_above_limit(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")

        await signaling_server._handle_message(
            participant,
            {
                "type": "join_room",
                "room_id": "huge-room",
                "max_participants": signaling_server.MAX_ROOM_PARTICIPANTS + 1,
            },
        )

        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "invalid_max_participants")

    async def test_create_room_does_not_leave_empty_room_behind_on_join_failure(self) -> None:
        ws = FakeWebSocket()
        participant = signaling_server.manager.register_participant(ws, name="Alice")

        with patch.object(
            signaling_server.room_control,
            "join_participant",
            side_effect=RoomFullError("room is full"),
        ):
            await signaling_server._handle_message(
                participant,
                {"type": "create_room", "room_id": "doomed-room"},
            )

        self.assertEqual(ws.messages[-1]["type"], "error")
        self.assertEqual(ws.messages[-1]["code"], "room_full")
        self.assertIsNone(signaling_server.manager.get_room("doomed-room"))


class WebSocketEndpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        signaling_server.manager = RoomManager()
        signaling_server.room_control = RoomControlModule(signaling_server.manager)
        signaling_server.stt_service = STTService()

    async def test_malformed_json_returns_error_and_keeps_connection_open(self) -> None:
        ws = FakeSocketWebSocket(
            [
                {"type": "websocket.receive", "text": "this is not json"},
                {
                    "type": "websocket.receive",
                    "text": json.dumps({"type": "join_room", "room_id": "ws-room"}),
                },
                {"type": "websocket.disconnect", "code": 1000},
            ]
        )

        await signaling_server.websocket_endpoint(ws)

        self.assertTrue(ws.accepted)
        self.assertEqual(ws.messages[0]["type"], "connected")
        self.assertEqual(ws.messages[1]["type"], "error")
        self.assertEqual(ws.messages[1]["code"], "invalid_json")
        self.assertEqual(ws.messages[2]["type"], "joined_room")

    async def test_non_text_frame_returns_error_and_keeps_connection_open(self) -> None:
        ws = FakeSocketWebSocket(
            [
                {"type": "websocket.receive", "bytes": b"\x00\x01"},
                {"type": "websocket.disconnect", "code": 1000},
            ]
        )

        await signaling_server.websocket_endpoint(ws)

        self.assertEqual(ws.messages[1]["type"], "error")
        self.assertEqual(ws.messages[1]["code"], "invalid_payload")

    async def test_non_object_payload_returns_error(self) -> None:
        ws = FakeSocketWebSocket(
            [
                {"type": "websocket.receive", "text": json.dumps(["not", "an", "object"])},
                {"type": "websocket.disconnect", "code": 1000},
            ]
        )

        await signaling_server.websocket_endpoint(ws)

        self.assertEqual(ws.messages[1]["type"], "error")
        self.assertEqual(ws.messages[1]["code"], "invalid_payload")

    async def test_participant_is_unregistered_on_disconnect(self) -> None:
        ws = FakeSocketWebSocket(
            [
                {
                    "type": "websocket.receive",
                    "text": json.dumps({"type": "join_room", "room_id": "ws-room"}),
                },
                {"type": "websocket.disconnect", "code": 1000},
            ]
        )

        await signaling_server.websocket_endpoint(ws)

        self.assertEqual(signaling_server.manager.active_participant_count, 0)
        self.assertIsNone(signaling_server.manager.get_room("ws-room"))


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            key: os.environ.get(key)
            for key in (
                "REQUIRE_EXPLICIT_ROOM_CREATE",
                "MAX_ROOM_PARTICIPANTS",
                "ROOM_CREATE_TOKEN_TTL_SECONDS",
            )
        }

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        signaling_server.reload_config()

    def test_reload_config_reads_environment(self) -> None:
        os.environ["REQUIRE_EXPLICIT_ROOM_CREATE"] = "true"
        os.environ["MAX_ROOM_PARTICIPANTS"] = "7"
        os.environ["ROOM_CREATE_TOKEN_TTL_SECONDS"] = "45"

        signaling_server.reload_config()

        self.assertTrue(signaling_server.REQUIRE_EXPLICIT_ROOM_CREATE)
        self.assertEqual(signaling_server.MAX_ROOM_PARTICIPANTS, 7)
        self.assertEqual(signaling_server.ROOM_CREATE_TOKEN_TTL_SECONDS, 45)

    def test_reload_config_ignores_invalid_positive_int(self) -> None:
        os.environ["MAX_ROOM_PARTICIPANTS"] = "not-a-number"

        signaling_server.reload_config()

        self.assertEqual(
            signaling_server.MAX_ROOM_PARTICIPANTS,
            RoomControlModule.DEFAULT_MAX_PARTICIPANTS_LIMIT,
        )

    def test_room_control_uses_configured_participant_limit(self) -> None:
        os.environ["MAX_ROOM_PARTICIPANTS"] = "5"

        signaling_server.reload_config()

        self.assertEqual(signaling_server.room_control.max_participants_limit, 5)


class WebClientAssetTests(unittest.TestCase):
    def _packaged_asset_path(self) -> Path:
        return (
            Path(__file__).resolve().parents[1]
            / "rtc_room_framework"
            / "web"
            / "index.html"
        )

    def test_served_index_matches_packaged_asset(self) -> None:
        packaged = self._packaged_asset_path()
        self.assertTrue(packaged.exists())

        response = asyncio.run(signaling_server.index())

        served_path = getattr(response, "path", None)
        if served_path is not None:
            self.assertEqual(Path(served_path).resolve(), packaged.resolve())
            body = Path(served_path).read_text(encoding="utf-8")
        else:
            body = response.body.decode("utf-8")

        self.assertEqual(body, packaged.read_text(encoding="utf-8"))

    def test_web_root_override_is_not_a_second_copy(self) -> None:
        web_root_index = Path(__file__).resolve().parents[1] / "web" / "index.html"
        if not web_root_index.exists():
            self.skipTest("No local web root override present")

        self.assertEqual(
            web_root_index.read_text(encoding="utf-8"),
            self._packaged_asset_path().read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
