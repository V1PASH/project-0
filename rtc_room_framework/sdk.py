"""Async Python SDK for room join and audio publish flows."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections import deque
from typing import Any


class SDKError(RuntimeError):
    """Raised when the signaling SDK call fails."""


class RTCRoomClient:
    """WebSocket SDK client for room create/join and audio publish."""

    def __init__(
        self,
        websocket_url: str,
        *,
        name: str = "Python SDK User",
        create_room_api_key: str | None = None,
        create_room_api_secret: str | None = None,
    ) -> None:
        self.websocket_url = websocket_url
        self.name = name
        self.create_room_api_key = create_room_api_key
        self.create_room_api_secret = create_room_api_secret
        self.participant_id: str | None = None
        self._websocket: Any | None = None
        self._pending_messages: deque[dict[str, Any]] = deque()

    async def connect(self) -> dict[str, Any]:
        if self._websocket is not None:
            return {"type": "connected", "participant_id": self.participant_id}

        try:
            import websockets  # Imported lazily so server-only users don't require this dependency.
        except Exception as exc:  # pragma: no cover - import guard
            raise SDKError("websockets package is required for RTCRoomClient") from exc

        self._websocket = await websockets.connect(self.websocket_url)
        connected = await self._recv_until({"connected"})
        self.participant_id = connected.get("participant_id")
        return connected

    async def close(self) -> None:
        if self._websocket is None:
            return
        await self._websocket.close()
        self._websocket = None
        self.participant_id = None
        self._pending_messages.clear()

    @staticmethod
    def build_create_room_token(
        *,
        api_key: str,
        api_secret: str,
        room_id: str,
        api_ts: int,
    ) -> str:
        message = f"{api_key}:{room_id}:{api_ts}".encode("utf-8")
        return hmac.new(api_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    async def create_room(
        self,
        room_id: str,
        *,
        max_participants: int = 10,
        api_key: str | None = None,
        api_secret: str | None = None,
        api_token: str | None = None,
        api_ts: int | None = None,
    ) -> dict[str, Any]:
        auth_key = api_key if api_key is not None else self.create_room_api_key
        auth_secret = api_secret if api_secret is not None else self.create_room_api_secret
        return await self._send_and_expect(
            self._build_create_room_payload(
                room_id=room_id,
                max_participants=max_participants,
                auth_key=auth_key,
                auth_secret=auth_secret,
                api_token=api_token,
                api_ts=api_ts,
            ),
            expected_types={"joined_room", "error"},
        )

    async def join_room(self, room_id: str, *, max_participants: int = 10) -> dict[str, Any]:
        return await self._send_and_expect(
            {
                "type": "join_room",
                "room_id": room_id,
                "name": self.name,
                "max_participants": max_participants,
            },
            expected_types={"joined_room", "error"},
        )

    async def leave_room(self) -> dict[str, Any]:
        return await self._send_and_expect(
            {"type": "leave_room"},
            expected_types={"left_room", "error"},
        )

    async def publish_audio(
        self,
        audio_bytes: bytes,
        *,
        mime_type: str = "audio/pcm",
        sequence: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            raise SDKError("audio_bytes must be non-empty bytes")

        payload: dict[str, Any] = {
            "type": "publish_audio",
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "mime_type": mime_type,
        }
        if sequence is not None:
            payload["sequence"] = sequence

        return await self._send_and_expect(
            payload,
            expected_types={"audio_published", "error"},
        )

    async def recv(self) -> dict[str, Any]:
        if self._pending_messages:
            return self._pending_messages.popleft()
        return await self._recv_message()

    async def _send_and_expect(
        self,
        payload: dict[str, Any],
        *,
        expected_types: set[str],
    ) -> dict[str, Any]:
        await self.send(payload)
        message = await self._recv_until(expected_types)
        if message.get("type") == "error":
            raise SDKError(f"{message.get('code')}: {message.get('message')}")
        return message

    async def send(self, payload: dict[str, Any]) -> None:
        if self._websocket is None:
            raise SDKError("Call connect() before sending messages")
        await self._websocket.send(json.dumps(payload))

    async def _recv_until(self, expected_types: set[str]) -> dict[str, Any]:
        pending_match = self._take_pending(expected_types)
        if pending_match is not None:
            return pending_match

        while True:
            message = await self._recv_message()
            message_type = message.get("type")
            if isinstance(message_type, str) and message_type in expected_types:
                return message
            self._pending_messages.append(message)

    async def _recv_message(self) -> dict[str, Any]:
        if self._websocket is None:
            raise SDKError("Call connect() before receiving messages")

        raw = await self._websocket.recv()
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SDKError("Server returned non-JSON message") from exc
        if not isinstance(message, dict):
            raise SDKError("Server returned invalid payload")
        return message

    def _take_pending(self, expected_types: set[str]) -> dict[str, Any] | None:
        for index, message in enumerate(self._pending_messages):
            message_type = message.get("type")
            if isinstance(message_type, str) and message_type in expected_types:
                del self._pending_messages[index]
                return message
        return None

    def _build_create_room_payload(
        self,
        *,
        room_id: str,
        max_participants: int,
        auth_key: str | None,
        auth_secret: str | None,
        api_token: str | None,
        api_ts: int | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "create_room",
            "room_id": room_id,
            "name": self.name,
            "max_participants": max_participants,
        }

        if api_token is not None:
            if not auth_key:
                raise SDKError("api_key is required when api_token is provided")
            if api_ts is None:
                api_ts = int(time.time())
            if not isinstance(api_ts, int) or isinstance(api_ts, bool):
                raise SDKError("api_ts must be an integer unix timestamp")
            payload["api_key"] = auth_key
            payload["api_token"] = api_token
            payload["api_ts"] = api_ts
            return payload

        if auth_key is None and auth_secret is None and api_ts is None:
            return payload

        if not auth_key or not auth_secret:
            raise SDKError("Both api_key and api_secret are required for create_room authentication")

        if api_ts is None:
            api_ts = int(time.time())
        if not isinstance(api_ts, int) or isinstance(api_ts, bool):
            raise SDKError("api_ts must be an integer unix timestamp")

        payload["api_key"] = auth_key
        payload["api_ts"] = api_ts
        payload["api_token"] = self.build_create_room_token(
            api_key=auth_key,
            api_secret=auth_secret,
            room_id=room_id,
            api_ts=api_ts,
        )
        return payload
