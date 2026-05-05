"""Agentic room-participant helpers for server-side AI agents."""
from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from participants.participant import Participant
from room.control import RoomControlModule
from room.manager import RoomEventType, RoomManager

log = logging.getLogger(__name__)

AgentResponderResult = str | None | Awaitable[str | None]
AgentResponder = Callable[[dict[str, Any], "AgentSession"], AgentResponderResult]


class AgentSessionError(RuntimeError):
    """Raised when agent-session lifecycle or API usage is invalid."""


@dataclass(frozen=True)
class AgentResponse:
    """Normalized response emitted by an agent."""

    text: str
    source: str = "agent_llm"
    is_final: bool = True


class EchoTranscriptResponder:
    """Simple transcript responder useful for local testing."""

    def __init__(self, *, prefix: str = "Agent: ") -> None:
        self._prefix = prefix

    def __call__(self, event: dict[str, Any], _: AgentSession) -> str | None:
        text = event.get("transcript_text")
        if not isinstance(text, str):
            return None
        normalized = text.strip()
        if not normalized:
            return None
        return f"{self._prefix}{normalized}"


class AgentSession:
    """Registers an agent as a room participant and wires event-driven replies."""

    def __init__(
        self,
        manager: RoomManager,
        *,
        room_control: RoomControlModule | None = None,
        name: str = "Agent",
        participant_id: str | None = None,
        responder: AgentResponder | None = None,
        respond_to_interim: bool = False,
        transcript_source: str = "agent_llm",
    ) -> None:
        self._manager = manager
        self._room_control = room_control or RoomControlModule(manager)
        self._name = name
        self._participant_id = participant_id
        self._responder = responder
        self._respond_to_interim = respond_to_interim
        self._transcript_source = transcript_source.strip() or "agent_llm"
        self._participant: Participant | None = None
        self._listener_attached = False
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def participant(self) -> Participant | None:
        return self._participant

    @property
    def participant_id(self) -> str | None:
        return self._participant.participant_id if self._participant is not None else None

    @property
    def room_id(self) -> str | None:
        return self._participant.room_id if self._participant is not None else None

    async def start(
        self,
        room_id: str,
        *,
        max_participants: int = 10,
        create_if_missing: bool = False,
    ) -> Participant:
        if self._participant is not None:
            raise AgentSessionError("Agent session is already started")

        participant = self._manager.register_agent_participant(
            name=self._name,
            participant_id=self._participant_id,
        )
        self._participant = participant
        try:
            self._room_control.join_participant(
                participant,
                room_name=room_id,
                max_participants=max_participants,
                create_if_missing=create_if_missing,
            )
            self._manager.add_event_listener(self._on_room_event)
            self._listener_attached = True
            return participant
        except Exception:
            self._manager.unregister_participant_by_id(participant.participant_id)
            self._participant = None
            self._listener_attached = False
            raise

    async def stop(self) -> None:
        if self._listener_attached:
            self._manager.remove_event_listener(self._on_room_event)
            self._listener_attached = False

        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        if self._participant is not None:
            self._manager.unregister_participant_by_id(self._participant.participant_id)
            self._participant = None

    def emit_transcript(
        self,
        text: str,
        *,
        is_final: bool = True,
        source: str | None = None,
    ) -> AgentResponse | None:
        participant = self._require_active_participant()
        normalized = text.strip()
        if not normalized:
            return None

        transcript_source = (source or self._transcript_source).strip() or "agent_llm"
        self._manager.emit_participant_transcript(
            participant,
            transcript_text=normalized,
            transcript_is_final=is_final,
            transcript_source=transcript_source,
        )
        return AgentResponse(text=normalized, source=transcript_source, is_final=is_final)

    async def publish_audio(
        self,
        audio_bytes: bytes,
        *,
        mime_type: str = "audio/pcm",
        sequence: int | None = None,
    ) -> dict[str, Any]:
        participant = self._require_active_participant()
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            raise AgentSessionError("audio_bytes must be non-empty bytes")
        if sequence is not None and (not isinstance(sequence, int) or sequence < 0):
            raise AgentSessionError("sequence must be a non-negative integer")
        if not participant.room_id:
            raise AgentSessionError("Agent must be in a room to publish audio")

        payload: dict[str, Any] = {
            "type": "room_audio",
            "room_id": participant.room_id,
            "from_participant_id": participant.participant_id,
            "mime_type": mime_type,
            "audio_base64": base64.b64encode(audio_bytes).decode("ascii"),
            "audio_bytes": len(audio_bytes),
            "created_at": time.time(),
        }
        if sequence is not None:
            payload["sequence"] = sequence

        await self._manager.broadcast_message_to_room(
            room_id=participant.room_id,
            payload=payload,
            exclude_participant_id=participant.participant_id,
        )
        return payload

    def _require_active_participant(self) -> Participant:
        if self._participant is None:
            raise AgentSessionError("Agent session is not started")
        return self._participant

    def _on_room_event(self, event: dict[str, Any]) -> None:
        participant = self._participant
        if participant is None or self._responder is None:
            return
        if event.get("event_type") != RoomEventType.PARTICIPANT_TRANSCRIPT.value:
            return
        if event.get("room_id") != participant.room_id:
            return
        if event.get("participant_id") == participant.participant_id:
            return

        is_final = bool(event.get("transcript_is_final"))
        if not self._respond_to_interim and not is_final:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.warning("Skipping agent responder callback: no running event loop")
            return

        task = loop.create_task(self._respond_to_event(event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _respond_to_event(self, event: dict[str, Any]) -> None:
        try:
            result = self._responder(event, self) if self._responder is not None else None
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                return
            if not isinstance(result, str):
                log.warning("Ignoring non-string agent response type: %s", type(result).__name__)
                return
            self.emit_transcript(result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Agent responder failed")
