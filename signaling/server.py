"""
FastAPI signaling server that coordinates room membership and WebRTC signaling.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import importlib.resources
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from participants.participant import Participant
from room.control import (
    RoomAlreadyExistsError,
    RoomControlError,
    RoomControlModule,
    RoomNotFoundError,
)
from room.manager import RoomManager
from room.model import RoomFullError
from services.stt_providers import build_stt_provider_from_env
from services.stt_service import STTService, STTServiceError

log = logging.getLogger(__name__)
app = FastAPI(title="WebRTC Room Signaling")
manager = RoomManager()
room_control = RoomControlModule(manager)


def _build_stt_service() -> STTService:
    try:
        provider = build_stt_provider_from_env()
    except Exception as exc:  # pragma: no cover - guarded fallback for invalid env.
        log.warning("Failed to initialize STT provider. Falling back to browser STT: %s", exc)
        provider = None
    return STTService(provider=provider)


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


stt_service = _build_stt_service()
DEFAULT_STT_LANGUAGE_CODE = os.getenv("STT_LANGUAGE_CODE", "unknown").strip() or "unknown"
MAX_STT_AUDIO_CHUNK_BYTES = 1024 * 1024 * 5
MAX_ROOM_AUDIO_CHUNK_BYTES = 1024 * 1024
ROOM_CREATE_API_KEY = os.getenv("ROOM_CREATE_API_KEY", "").strip()
ROOM_CREATE_API_SECRET = os.getenv("ROOM_CREATE_API_SECRET", "").strip()
ROOM_CREATE_TOKEN_TTL_SECONDS = _read_positive_int_env("ROOM_CREATE_TOKEN_TTL_SECONDS", 300)
_require_create_room_auth_raw = os.getenv("REQUIRE_CREATE_ROOM_AUTH", "").strip().lower()
if _require_create_room_auth_raw:
    REQUIRE_CREATE_ROOM_AUTH = _require_create_room_auth_raw in {"1", "true", "yes", "on"}
else:
    REQUIRE_CREATE_ROOM_AUTH = bool(ROOM_CREATE_API_KEY and ROOM_CREATE_API_SECRET)
REQUIRE_EXPLICIT_ROOM_CREATE = os.getenv("REQUIRE_EXPLICIT_ROOM_CREATE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


class _CreateRoomAuthError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _get_room_control() -> RoomControlModule:
    global room_control
    if room_control.manager is not manager:
        room_control = RoomControlModule(manager)
    return room_control


def _build_create_room_token(
    *,
    api_key: str,
    api_secret: str,
    room_id: str,
    api_ts: int,
) -> str:
    message = f"{api_key}:{room_id}:{api_ts}".encode("utf-8")
    return hmac.new(api_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _validate_create_room_auth(payload: dict[str, Any], *, room_id: str) -> None:
    if not REQUIRE_CREATE_ROOM_AUTH:
        return
    if not ROOM_CREATE_API_KEY or not ROOM_CREATE_API_SECRET:
        raise _CreateRoomAuthError(
            "create_room_auth_not_configured",
            "Create-room authentication is enabled but not configured",
        )

    api_key = payload.get("api_key")
    api_token = payload.get("api_token")
    api_ts = payload.get("api_ts")

    if (
        not isinstance(api_key, str)
        or not api_key.strip()
        or not isinstance(api_token, str)
        or not api_token.strip()
        or not isinstance(api_ts, int)
        or isinstance(api_ts, bool)
    ):
        raise _CreateRoomAuthError(
            "invalid_create_room_auth",
            "api_key, api_token, and integer api_ts are required for create_room",
        )

    if not hmac.compare_digest(api_key.strip(), ROOM_CREATE_API_KEY):
        raise _CreateRoomAuthError(
            "unauthorized_create_room",
            "Invalid create_room credentials",
        )

    now = int(time.time())
    if abs(now - api_ts) > max(ROOM_CREATE_TOKEN_TTL_SECONDS, 1):
        raise _CreateRoomAuthError(
            "expired_create_room_token",
            "create_room token has expired",
        )

    expected = _build_create_room_token(
        api_key=ROOM_CREATE_API_KEY,
        api_secret=ROOM_CREATE_API_SECRET,
        room_id=room_id,
        api_ts=api_ts,
    )
    if not hmac.compare_digest(api_token.strip(), expected):
        raise _CreateRoomAuthError(
            "unauthorized_create_room",
            "Invalid create_room credentials",
        )


@app.get("/")
async def index():
    index_file = WEB_ROOT / "index.html"
    no_cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    if index_file.exists():
        return FileResponse(index_file, headers=no_cache_headers)

    packaged_index_html = _load_packaged_index_html()
    if packaged_index_html is not None:
        return HTMLResponse(packaged_index_html, headers=no_cache_headers)

    log.error("No index.html found at %s and packaged fallback is unavailable", index_file)
    return JSONResponse(
        status_code=404,
        content={
            "error": "index_not_found",
            "message": "No index.html found in web root or packaged assets",
        },
        headers=no_cache_headers,
    )


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "stats": manager.stats()})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    participant = manager.register_participant(websocket=websocket)
    await websocket.send_json(
        {
            "type": "connected",
            "participant_id": participant.participant_id,
            "stt": {
                "provider": stt_service.provider_name,
                "transport": "audio_chunk" if stt_service.uses_audio_provider else "browser_text",
                "default_language_code": DEFAULT_STT_LANGUAGE_CODE,
            },
        }
    )

    try:
        while True:
            payload = await websocket.receive_json()
            if not isinstance(payload, dict):
                await _send_error(websocket, "invalid_payload", "Payload must be a JSON object")
                continue
            await _handle_message(participant, payload)
    except WebSocketDisconnect:
        stt_service.clear_participant(participant.participant_id)
        manager.unregister_participant(websocket)
    except Exception:
        stt_service.clear_participant(participant.participant_id)
        manager.unregister_participant(websocket)
        raise


async def _handle_message(participant: Participant, payload: dict[str, Any]) -> None:
    message_type = payload.get("type")
    if not message_type:
        await _send_error(participant.websocket, "missing_type", "Message type is required")
        return

    if message_type == "join_room":
        await _handle_join_room(participant, payload)
        return

    if message_type == "create_room":
        await _handle_create_room(participant, payload)
        return

    if message_type == "leave_room":
        await _handle_leave_room(participant)
        return

    if message_type == "participant_activity":
        await _handle_participant_activity(participant, payload)
        return

    if message_type == "stt_transcript":
        await _handle_stt_transcript(participant, payload)
        return

    if message_type == "stt_audio_chunk":
        await _handle_stt_audio_chunk(participant, payload)
        return

    if message_type == "publish_audio":
        await _handle_publish_audio(participant, payload)
        return

    if message_type in {"webrtc_offer", "webrtc_answer", "ice_candidate"}:
        await _relay_webrtc_message(participant, payload, message_type)
        return

    await _send_error(participant.websocket, "unsupported_type", f"Unsupported message type: {message_type}")


async def _handle_join_room(participant: Participant, payload: dict[str, Any]) -> None:
    await _handle_join_or_create_room(participant, payload, create_only=False)


async def _handle_create_room(participant: Participant, payload: dict[str, Any]) -> None:
    await _handle_join_or_create_room(participant, payload, create_only=True)


async def _handle_join_or_create_room(
    participant: Participant,
    payload: dict[str, Any],
    *,
    create_only: bool,
) -> None:
    control = _get_room_control()
    room_id_value = payload.get("room_id")
    if not isinstance(room_id_value, str):
        await _send_error(participant.websocket, "invalid_room_id", "room_id is required")
        return
    room_id = room_id_value.strip()
    if not room_id:
        await _send_error(participant.websocket, "invalid_room_id", "room_id is required")
        return

    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        participant.name = name.strip()

    try:
        max_participants = control.validate_max_participants(payload.get("max_participants"))
    except RoomControlError:
        await _send_error(
            participant.websocket,
            "invalid_max_participants",
            "max_participants must be a positive integer",
        )
        return

    room_was_created = False
    if create_only:
        try:
            _validate_create_room_auth(payload, room_id=room_id)
        except _CreateRoomAuthError as exc:
            await _send_error(participant.websocket, exc.code, exc.message)
            return

        try:
            control.create_room(
                room_id,
                creator_id=participant.participant_id,
                max_participants=max_participants,
            )
            room_was_created = True
        except RoomAlreadyExistsError:
            await _send_error(participant.websocket, "room_already_exists", f"Room already exists: {room_id}")
            return
        except RoomControlError as exc:
            await _send_error(participant.websocket, "invalid_room_id", str(exc))
            return

    try:
        room, created_during_join = control.join_participant(
            participant,
            room_name=room_id,
            max_participants=max_participants,
            create_if_missing=(not create_only) and (not REQUIRE_EXPLICIT_ROOM_CREATE),
        )
    except RoomNotFoundError:
        await _send_error(participant.websocket, "room_not_found", f"Room does not exist: {room_id}")
        return
    except RoomFullError:
        await _send_error(participant.websocket, "room_full", f"Room is full: {room_id}")
        return
    except RoomControlError as exc:
        await _send_error(participant.websocket, "invalid_room_id", str(exc))
        return

    room_was_created = room_was_created or created_during_join
    others = [p.to_dict() for p in room.get_other_participants(participant.participant_id)]

    await participant.websocket.send_json(
        {
            "type": "joined_room",
            "room": room.to_dict(include_participants=False),
            "self": participant.to_dict(),
            "participants": others,
            "room_created": room_was_created,
            "join_source": "create_room" if create_only else "join_room",
        }
    )


async def _handle_leave_room(participant: Participant) -> None:
    previous_room_id = participant.room_id
    room = manager.leave_room(participant)
    stt_service.clear_participant(participant.participant_id)
    room_deleted = previous_room_id is not None and room is None
    await participant.websocket.send_json(
        {
            "type": "left_room",
            "room_id": previous_room_id,
            "room_exists": room is not None,
            "room_deleted": room_deleted,
        }
    )


async def _handle_participant_activity(participant: Participant, payload: dict[str, Any]) -> None:
    if participant.room_id is None:
        await _send_error(participant.websocket, "not_in_room", "Join a room first")
        return

    activity = payload.get("activity")
    if activity == "speaking_started":
        manager.update_participant_speaking(participant, speaking=True)
        return
    if activity == "speaking_stopped":
        manager.update_participant_speaking(participant, speaking=False)
        return

    await _send_error(
        participant.websocket,
        "invalid_activity",
        "activity must be one of: speaking_started, speaking_stopped",
    )


async def _handle_stt_transcript(participant: Participant, payload: dict[str, Any]) -> None:
    if participant.room_id is None:
        await _send_error(participant.websocket, "not_in_room", "Join a room first")
        return

    try:
        update = stt_service.normalize_update(
            participant_id=participant.participant_id,
            text=payload.get("text"),
            is_final=payload.get("is_final", False),
            source=payload.get("source"),
        )
    except STTServiceError as exc:
        await _send_error(participant.websocket, "invalid_stt_payload", str(exc))
        return

    if update is None:
        return

    manager.emit_participant_transcript(
        participant,
        transcript_text=update.text,
        transcript_is_final=update.is_final,
        transcript_source=update.source,
    )


async def _handle_stt_audio_chunk(participant: Participant, payload: dict[str, Any]) -> None:
    if participant.room_id is None:
        await _send_error(participant.websocket, "not_in_room", "Join a room first")
        return

    if not stt_service.uses_audio_provider:
        await _send_error(
            participant.websocket,
            "stt_transport_not_available",
            "Server-side audio STT is not enabled",
        )
        return

    audio_b64 = payload.get("audio_base64")
    if not isinstance(audio_b64, str) or not audio_b64.strip():
        await _send_error(participant.websocket, "invalid_stt_audio_chunk", "audio_base64 is required")
        return

    mime_type = payload.get("mime_type")
    if not isinstance(mime_type, str) or not mime_type.strip():
        mime_type = "audio/webm"

    language_code = payload.get("language_code")
    if not isinstance(language_code, str) or not language_code.strip():
        language_code = DEFAULT_STT_LANGUAGE_CODE

    try:
        audio_bytes = base64.b64decode(audio_b64, validate=True)
    except (binascii.Error, ValueError):
        await _send_error(participant.websocket, "invalid_stt_audio_chunk", "audio_base64 is invalid")
        return

    if not audio_bytes:
        return

    if len(audio_bytes) > MAX_STT_AUDIO_CHUNK_BYTES:
        await _send_error(
            participant.websocket,
            "stt_audio_chunk_too_large",
            f"audio chunk too large ({len(audio_bytes)} bytes)",
        )
        return

    try:
        update = await stt_service.transcribe_audio_chunk(
            participant_id=participant.participant_id,
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            language_code=language_code,
        )
    except STTServiceError as exc:
        await _send_error(participant.websocket, "stt_provider_error", str(exc))
        return

    if update is None:
        return

    manager.emit_participant_transcript(
        participant,
        transcript_text=update.text,
        transcript_is_final=update.is_final,
        transcript_source=update.source,
    )


async def _handle_publish_audio(participant: Participant, payload: dict[str, Any]) -> None:
    if participant.room_id is None:
        await _send_error(participant.websocket, "not_in_room", "Join a room first")
        return

    audio_b64 = payload.get("audio_base64")
    if not isinstance(audio_b64, str) or not audio_b64.strip():
        await _send_error(participant.websocket, "invalid_audio_payload", "audio_base64 is required")
        return

    mime_type = payload.get("mime_type")
    if not isinstance(mime_type, str) or not mime_type.strip():
        mime_type = "audio/pcm"

    sequence = payload.get("sequence")
    if sequence is not None and (not isinstance(sequence, int) or sequence < 0):
        await _send_error(participant.websocket, "invalid_audio_payload", "sequence must be a non-negative integer")
        return

    try:
        audio_bytes = base64.b64decode(audio_b64, validate=True)
    except (binascii.Error, ValueError):
        await _send_error(participant.websocket, "invalid_audio_payload", "audio_base64 is invalid")
        return

    if not audio_bytes:
        await _send_error(participant.websocket, "invalid_audio_payload", "audio chunk is empty")
        return

    if len(audio_bytes) > MAX_ROOM_AUDIO_CHUNK_BYTES:
        await _send_error(
            participant.websocket,
            "audio_chunk_too_large",
            f"audio chunk too large ({len(audio_bytes)} bytes)",
        )
        return

    room_audio_payload: dict[str, Any] = {
        "type": "room_audio",
        "room_id": participant.room_id,
        "from_participant_id": participant.participant_id,
        "mime_type": mime_type,
        "audio_base64": audio_b64,
        "audio_bytes": len(audio_bytes),
        "created_at": time.time(),
    }
    if sequence is not None:
        room_audio_payload["sequence"] = sequence

    await manager.broadcast_message_to_room(
        room_id=participant.room_id,
        payload=room_audio_payload,
        exclude_participant_id=participant.participant_id,
    )

    await participant.websocket.send_json(
        {
            "type": "audio_published",
            "room_id": participant.room_id,
            "audio_bytes": len(audio_bytes),
            "sequence": sequence,
        }
    )


async def _relay_webrtc_message(
    sender: Participant, payload: dict[str, Any], message_type: str
) -> None:
    if sender.room_id is None:
        await _send_error(sender.websocket, "not_in_room", "Join a room first")
        return

    target_participant_id = payload.get("target_participant_id")
    if not target_participant_id or not isinstance(target_participant_id, str):
        await _send_error(
            sender.websocket,
            "invalid_target",
            "target_participant_id is required",
        )
        return

    target = manager.get_participant(target_participant_id)
    if target is None or target.websocket is None:
        await _send_error(sender.websocket, "target_not_found", "Target participant is not connected")
        return

    if target.room_id != sender.room_id:
        await _send_error(sender.websocket, "target_not_in_room", "Target participant is not in your room")
        return

    relay_payload = {
        "type": message_type,
        "from_participant_id": sender.participant_id,
        "room_id": sender.room_id,
    }
    if "sdp" in payload:
        relay_payload["sdp"] = payload["sdp"]
    if "candidate" in payload:
        relay_payload["candidate"] = payload["candidate"]

    await target.websocket.send_json(relay_payload)


async def _send_error(websocket: WebSocket, code: str, message: str) -> None:
    await websocket.send_json({"type": "error", "code": code, "message": message})


def _load_packaged_index_html() -> str | None:
    try:
        return importlib.resources.files("rtc_room_framework.web").joinpath("index.html").read_text(
            encoding="utf-8"
        )
    except Exception:
        return None
