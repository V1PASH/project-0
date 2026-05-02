import asyncio
import base64
import json
import unittest
from collections import deque

from rtc_room_framework.sdk import RTCRoomClient, SDKError


class FakeSDKWebSocket:
    def __init__(self, incoming_messages: list[dict]) -> None:
        self._incoming = deque(json.dumps(message) for message in incoming_messages)
        self.sent_messages: list[dict] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent_messages.append(json.loads(payload))

    async def recv(self) -> str:
        if not self._incoming:
            await asyncio.sleep(0)
            return json.dumps({"type": "noop"})
        return self._incoming.popleft()

    async def close(self) -> None:
        self.closed = True


class RTCRoomClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_room_includes_signed_auth_when_credentials_are_configured(self) -> None:
        websocket = FakeSDKWebSocket(
            incoming_messages=[{"type": "joined_room", "room": {"room_id": "secure-room"}}]
        )
        client = RTCRoomClient(
            "ws://localhost:8000/ws",
            name="Alice",
            create_room_api_key="room-key",
            create_room_api_secret="room-secret",
        )
        client._websocket = websocket

        await client.create_room("secure-room")

        sent = websocket.sent_messages[0]
        self.assertEqual(sent["type"], "create_room")
        self.assertEqual(sent["api_key"], "room-key")
        self.assertIsInstance(sent["api_ts"], int)
        self.assertEqual(
            sent["api_token"],
            RTCRoomClient.build_create_room_token(
                api_key="room-key",
                api_secret="room-secret",
                room_id="secure-room",
                api_ts=sent["api_ts"],
            ),
        )

    async def test_create_room_rejects_partial_auth_credentials(self) -> None:
        client = RTCRoomClient("ws://localhost:8000/ws", create_room_api_key="room-key")
        client._websocket = FakeSDKWebSocket(
            incoming_messages=[{"type": "joined_room", "room": {"room_id": "secure-room"}}]
        )
        with self.assertRaises(SDKError):
            await client.create_room("secure-room")

    async def test_join_room_waits_for_joined_room_and_keeps_other_events(self) -> None:
        websocket = FakeSDKWebSocket(
            incoming_messages=[
                {"type": "room_event", "event": {"event_type": "participant_joined"}},
                {"type": "joined_room", "room": {"room_id": "sdk-room"}},
            ]
        )
        client = RTCRoomClient("ws://localhost:8000/ws", name="Alice")
        client._websocket = websocket

        joined = await client.join_room("sdk-room")

        self.assertEqual(joined["type"], "joined_room")
        self.assertEqual(websocket.sent_messages[0]["type"], "join_room")
        self.assertEqual(websocket.sent_messages[0]["room_id"], "sdk-room")
        self.assertEqual(websocket.sent_messages[0]["name"], "Alice")

        queued_event = await client.recv()
        self.assertEqual(queued_event["type"], "room_event")

    async def test_publish_audio_encodes_bytes_and_waits_for_ack(self) -> None:
        websocket = FakeSDKWebSocket(
            incoming_messages=[{"type": "audio_published", "room_id": "sdk-room", "audio_bytes": 4}]
        )
        client = RTCRoomClient("ws://localhost:8000/ws")
        client._websocket = websocket

        result = await client.publish_audio(b"\x01\x02\x03\x04", mime_type="audio/pcm", sequence=7)

        self.assertEqual(result["type"], "audio_published")
        sent = websocket.sent_messages[0]
        self.assertEqual(sent["type"], "publish_audio")
        self.assertEqual(sent["mime_type"], "audio/pcm")
        self.assertEqual(sent["sequence"], 7)
        self.assertEqual(sent["audio_base64"], base64.b64encode(b"\x01\x02\x03\x04").decode("ascii"))

    async def test_publish_audio_rejects_empty_bytes(self) -> None:
        client = RTCRoomClient("ws://localhost:8000/ws")
        with self.assertRaises(SDKError):
            await client.publish_audio(b"")


if __name__ == "__main__":
    unittest.main()
