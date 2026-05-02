"""RoomManager as the single source of truth for rooms and participants."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from participants.base import ParticipantState
from participants.participant import LocalParticipant, Participant, RemoteParticipant
from room.model import Room, RoomNotFoundError

log = logging.getLogger(__name__)

EventListener = Callable[[dict], Any]


class RoomEventType(str, Enum):
    ROOM_CREATED = "room_created"
    ROOM_DESTROYED = "room_destroyed"
    PARTICIPANT_JOINED = "participant_joined"
    PARTICIPANT_LEFT = "participant_left"
    PARTICIPANT_SPEAKING_STARTED = "participant_speaking_started"
    PARTICIPANT_SPEAKING_STOPPED = "participant_speaking_stopped"
    PARTICIPANT_TRANSCRIPT = "participant_transcript"


def _new_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class RoomEvent:
    event_type: RoomEventType
    room_id: str
    participant_id: Optional[str] = None
    participant_name: Optional[str] = None
    participant_role: Optional[str] = None
    is_speaking: Optional[bool] = None
    transcript_text: Optional[str] = None
    transcript_is_final: Optional[bool] = None
    transcript_source: Optional[str] = None
    participant_count: int = 0
    created_at: float = field(default_factory=time.time)
    event_id: str = field(default_factory=_new_event_id)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "room_id": self.room_id,
            "participant_id": self.participant_id,
            "participant_name": self.participant_name,
            "participant_role": self.participant_role,
            "is_speaking": self.is_speaking,
            "transcript_text": self.transcript_text,
            "transcript_is_final": self.transcript_is_final,
            "transcript_source": self.transcript_source,
            "participant_count": self.participant_count,
            "created_at": self.created_at,
        }


class RoomManager:
    """Creates, tracks, and destroys rooms and websocket participants."""

    def __init__(self, event_history_size: int = 200) -> None:
        self._rooms: dict[str, Room] = {}
        self._participants: dict[str, Participant] = {}
        self._ws_index: dict[int, str] = {}
        self._event_listeners: set[EventListener] = set()
        self._event_history: list[RoomEvent] = []
        self._event_history_size = max(event_history_size, 0)

    def add_event_listener(self, listener: EventListener) -> None:
        self._event_listeners.add(listener)

    def remove_event_listener(self, listener: EventListener) -> None:
        self._event_listeners.discard(listener)

    def list_events(self) -> list[dict]:
        return [event.to_dict() for event in self._event_history]

    def _emit_event(self, event: RoomEvent) -> None:
        if self._event_history_size > 0:
            self._event_history.append(event)
            if len(self._event_history) > self._event_history_size:
                self._event_history.pop(0)

        payload = event.to_dict()
        for listener in tuple(self._event_listeners):
            try:
                result = listener(payload)
                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        log.warning(
                            "No running loop for async event listener while emitting %s",
                            event.event_id,
                        )
                        continue
                    loop.create_task(result)
            except Exception:
                log.exception("Event listener failed while handling %s", event.event_id)

        self._schedule_event_broadcast(event)

    def _schedule_event_broadcast(self, event: RoomEvent) -> None:
        if event.event_type not in {
            RoomEventType.PARTICIPANT_JOINED,
            RoomEventType.PARTICIPANT_LEFT,
            RoomEventType.PARTICIPANT_SPEAKING_STARTED,
            RoomEventType.PARTICIPANT_SPEAKING_STOPPED,
            RoomEventType.PARTICIPANT_TRANSCRIPT,
        }:
            return
        room = self._rooms.get(event.room_id)
        if room is None:
            return

        recipient_ids = [
            participant.participant_id
            for participant in room.participants
            if participant.participant_id != event.participant_id and participant.websocket is not None
        ]
        if not recipient_ids:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(
            self._broadcast_room_event(
                event=event.to_dict(),
                recipient_participant_ids=recipient_ids,
            )
        )

    async def _broadcast_room_event(
        self, event: dict, recipient_participant_ids: list[str]
    ) -> None:
        envelope = {"type": "room_event", "event": event}
        for participant_id in recipient_participant_ids:
            participant = self._participants.get(participant_id)
            if participant is None or participant.room_id != event["room_id"]:
                continue
            websocket = participant.websocket
            if websocket is None:
                continue
            try:
                await self._send_to_websocket(websocket, envelope)
            except Exception:
                log.exception(
                    "Failed to broadcast event %s to participant %s",
                    event.get("event_id"),
                    participant.participant_id,
                )

    async def broadcast_message_to_room(
        self,
        *,
        room_id: str,
        payload: dict[str, Any],
        exclude_participant_id: str | None = None,
    ) -> None:
        room = self._rooms.get(room_id)
        if room is None:
            return

        recipients = [
            participant
            for participant in room.participants
            if participant.websocket is not None and participant.participant_id != exclude_participant_id
        ]
        for participant in recipients:
            try:
                await self._send_to_websocket(participant.websocket, payload)
            except Exception:
                log.exception(
                    "Failed to broadcast message type %s to participant %s",
                    payload.get("type"),
                    participant.participant_id,
                )

    async def _send_to_websocket(self, websocket: object, payload: dict) -> None:
        send_json = getattr(websocket, "send_json", None)
        if callable(send_json):
            result = send_json(payload)
            if asyncio.iscoroutine(result):
                await result
            return

        send_text = getattr(websocket, "send_text", None)
        if callable(send_text):
            result = send_text(json.dumps(payload))
            if asyncio.iscoroutine(result):
                await result
            return

        send = getattr(websocket, "send", None)
        if callable(send):
            result = send(json.dumps(payload))
            if asyncio.iscoroutine(result):
                await result
            return

    def register_participant(self, websocket: object, name: str = "Anonymous") -> Participant:
        p = LocalParticipant(websocket=websocket, name=name)
        self._participants[p.participant_id] = p
        self._ws_index[id(websocket)] = p.participant_id
        log.info("Registered participant %s (%s)", p.participant_id, p.name)
        return p

    def register_remote_participant(
        self, name: str = "Remote", participant_id: Optional[str] = None
    ) -> Participant:
        p = RemoteParticipant(name=name, participant_id=participant_id)
        self._participants[p.participant_id] = p
        log.info("Registered remote participant %s (%s)", p.participant_id, p.name)
        return p

    def unregister_participant_by_id(self, participant_id: str) -> Optional[Participant]:
        participant = self._participants.pop(participant_id, None)
        if participant is None:
            return None
        if participant.websocket is not None:
            self._ws_index.pop(id(participant.websocket), None)
        self.leave_room(participant)
        participant.transition(ParticipantState.DISCONNECTED)
        log.info("Unregistered participant %s (%s)", participant_id, participant.name)
        return participant

    def unregister_participant(self, websocket: object) -> Optional[Participant]:
        pid = self._ws_index.pop(id(websocket), None)
        if pid is None:
            return None
        participant = self._participants.pop(pid, None)
        if participant and participant.room_id:
            room_id = participant.room_id
            room = self._rooms.get(room_id)
            if room:
                room.remove_participant(pid)
                self._emit_event(
                    RoomEvent(
                        event_type=RoomEventType.PARTICIPANT_LEFT,
                        room_id=room_id,
                        participant_id=participant.participant_id,
                        participant_name=participant.name,
                        participant_role=participant.role.value,
                        participant_count=room.participant_count,
                    )
                )
                participant.is_speaking = False
                log.info("Auto-removed %s from room %s on disconnect", pid, room_id)
                if room.is_empty:
                    self._destroy_room(room_id, triggered_by=participant)
        if participant:
            participant.transition(ParticipantState.DISCONNECTED)
            log.info("Unregistered participant %s (%s)", pid, participant.name)
        return participant

    def get_participant_by_ws(self, websocket: object) -> Optional[Participant]:
        pid = self._ws_index.get(id(websocket))
        return self._participants.get(pid) if pid else None

    def get_participant(self, participant_id: str) -> Optional[Participant]:
        return self._participants.get(participant_id)

    def get_or_create_room(
        self, room_id: Optional[str], max_participants: int = 10, creator_id: Optional[str] = None
    ) -> Room:
        room_id = room_id or f"r-{uuid.uuid4().hex[:12]}"
        if room_id not in self._rooms:
            room = Room(
                room_id=room_id,
                max_participants=max_participants,
                created_by=creator_id,
            )
            self._rooms[room_id] = room
            self._emit_event(
                RoomEvent(
                    event_type=RoomEventType.ROOM_CREATED,
                    room_id=room_id,
                    participant_id=creator_id,
                    participant_count=room.participant_count,
                )
            )
            log.info("Created room %s (max %d)", room_id, max_participants)
        return self._rooms[room_id]

    def join_room(
        self,
        participant: Participant,
        room_id: str,
        max_participants: int = 10,
        *,
        create_if_missing: bool = True,
    ) -> Room:
        if participant.room_id and participant.room_id != room_id:
            self.leave_room(participant)

        if create_if_missing:
            room = self.get_or_create_room(room_id, max_participants, participant.participant_id)
        else:
            room = self._rooms.get(room_id)
            if room is None:
                raise RoomNotFoundError(f"Room {room_id!r} does not exist")

        if participant.room_id == room.room_id and room.get_participant(participant.participant_id):
            return room
        room.add_participant(participant)
        participant.transition(ParticipantState.IN_ROOM)
        participant.is_speaking = False
        self._emit_event(
            RoomEvent(
                event_type=RoomEventType.PARTICIPANT_JOINED,
                room_id=room.room_id,
                participant_id=participant.participant_id,
                participant_name=participant.name,
                participant_role=participant.role.value,
                participant_count=room.participant_count,
            )
        )
        log.info(
            "Participant %s joined room %s (%d/%d)",
            participant.participant_id,
            room_id,
            room.participant_count,
            room.max_participants,
        )
        return room

    def update_participant_speaking(self, participant: Participant, speaking: bool) -> Optional[RoomEvent]:
        if not participant.room_id:
            return None

        room = self._rooms.get(participant.room_id)
        if room is None or room.get_participant(participant.participant_id) is None:
            return None

        speaking = bool(speaking)
        if participant.is_speaking == speaking:
            return None

        participant.is_speaking = speaking
        event_type = (
            RoomEventType.PARTICIPANT_SPEAKING_STARTED
            if speaking
            else RoomEventType.PARTICIPANT_SPEAKING_STOPPED
        )
        event = RoomEvent(
            event_type=event_type,
            room_id=room.room_id,
            participant_id=participant.participant_id,
            participant_name=participant.name,
            participant_role=participant.role.value,
            is_speaking=speaking,
            participant_count=room.participant_count,
        )
        self._emit_event(event)
        return event

    def emit_participant_transcript(
        self,
        participant: Participant,
        *,
        transcript_text: str,
        transcript_is_final: bool,
        transcript_source: str,
    ) -> Optional[RoomEvent]:
        if not participant.room_id:
            return None

        room = self._rooms.get(participant.room_id)
        if room is None or room.get_participant(participant.participant_id) is None:
            return None

        event = RoomEvent(
            event_type=RoomEventType.PARTICIPANT_TRANSCRIPT,
            room_id=room.room_id,
            participant_id=participant.participant_id,
            participant_name=participant.name,
            participant_role=participant.role.value,
            transcript_text=transcript_text,
            transcript_is_final=bool(transcript_is_final),
            transcript_source=transcript_source,
            participant_count=room.participant_count,
        )
        self._emit_event(event)
        return event

    def leave_room(self, participant: Participant) -> Optional[Room]:
        if not participant.room_id:
            return None
        room_id = participant.room_id
        room = self._rooms.get(room_id)
        if room:
            participant.is_speaking = False
            room.remove_participant(participant.participant_id)
            self._emit_event(
                RoomEvent(
                    event_type=RoomEventType.PARTICIPANT_LEFT,
                    room_id=room.room_id,
                    participant_id=participant.participant_id,
                    participant_name=participant.name,
                    participant_role=participant.role.value,
                    participant_count=room.participant_count,
                )
            )
            log.info("Participant %s left room %s", participant.participant_id, room.room_id)
            if room.is_empty:
                self._destroy_room(room.room_id, triggered_by=participant)
                return None
        if participant.state is not ParticipantState.DISCONNECTED:
            participant.transition(ParticipantState.CONNECTING)
        return room

    def _destroy_room(self, room_id: str, triggered_by: Optional[Participant] = None) -> None:
        room = self._rooms.pop(room_id, None)
        if room is None:
            return
        self._emit_event(
            RoomEvent(
                event_type=RoomEventType.ROOM_DESTROYED,
                room_id=room_id,
                participant_id=triggered_by.participant_id if triggered_by else None,
                participant_name=triggered_by.name if triggered_by else None,
                participant_role=triggered_by.role.value if triggered_by else None,
                participant_count=room.participant_count,
            )
        )
        log.info("Destroyed empty room %s", room_id)

    def get_room(self, room_id: str) -> Optional[Room]:
        return self._rooms.get(room_id)

    def has_room(self, room_id: str) -> bool:
        return room_id in self._rooms

    def list_rooms(self) -> list[dict]:
        return [r.to_dict(include_participants=False) for r in self._rooms.values()]

    @property
    def active_room_count(self) -> int:
        return len(self._rooms)

    @property
    def active_participant_count(self) -> int:
        return len(self._participants)

    def stats(self) -> dict:
        return {
            "active_rooms": self.active_room_count,
            "active_participants": self.active_participant_count,
            "event_listeners": len(self._event_listeners),
            "events_buffered": len(self._event_history),
        }
