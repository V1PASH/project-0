"""
FastAPI signaling server that coordinates room membership and WebRTC signaling.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from services.stt_providers import build_stt_provider_from_env
from services.stt_service import STTService, STTServiceError
from transport.webrtc.manager import RoomManager
from transport.webrtc.participants.participant import Participant

log = logging.getLogger(__name__)
app = FastAPI(title="WebRTC Room Signaling")
manager = RoomManager()


def _build_stt_service() -> STTService:
    try:
        provider = build_stt_provider_from_env()
    except Exception as exc:  # pragma: no cover - guarded fallback for invalid env.
        log.warning("Failed to initialize STT provider. Falling back to browser STT: %s", exc)
        provider = None
    return STTService(provider=provider)


stt_service = _build_stt_service()
DEFAULT_STT_LANGUAGE_CODE = os.getenv("STT_LANGUAGE_CODE", "unknown").strip() or "unknown"
MAX_STT_AUDIO_CHUNK_BYTES = 1024 * 1024 * 5
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(
        WEB_ROOT / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        },
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

    if message_type in {"webrtc_offer", "webrtc_answer", "ice_candidate"}:
        await _relay_webrtc_message(participant, payload, message_type)
        return

    await _send_error(participant.websocket, "unsupported_type", f"Unsupported message type: {message_type}")


async def _handle_join_room(participant: Participant, payload: dict[str, Any]) -> None:
    room_id = payload.get("room_id")
    if not room_id or not isinstance(room_id, str):
        await _send_error(participant.websocket, "invalid_room_id", "room_id is required")
        return

    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        participant.name = name.strip()

    max_participants = payload.get("max_participants", 10)
    if not isinstance(max_participants, int) or max_participants <= 0:
        await _send_error(
            participant.websocket,
            "invalid_max_participants",
            "max_participants must be a positive integer",
        )
        return

    room = manager.join_room(participant, room_id=room_id, max_participants=max_participants)
    others = [p.to_dict() for p in room.get_other_participants(participant.participant_id)]

    await participant.websocket.send_json(
        {
            "type": "joined_room",
            "room": room.to_dict(include_participants=False),
            "self": participant.to_dict(),
            "participants": others,
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
