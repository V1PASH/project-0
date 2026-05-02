"""High-level room control module for room creation and admin operations."""
from __future__ import annotations

from dataclasses import dataclass

from participants.participant import Participant
from room.manager import RoomManager
from room.model import Room, RoomNotFoundError


class RoomControlError(ValueError):
    """Base class for room control validation and operation errors."""


class RoomAlreadyExistsError(RoomControlError):
    """Raised when creating a room that already exists."""


class RoomCapacityError(RoomControlError):
    """Raised when a room capacity update is invalid."""


class ParticipantNotFoundError(RoomControlError):
    """Raised when a participant identifier is unknown."""


class ParticipantNotInRoomControlError(RoomControlError):
    """Raised when an admin operation expects the participant to be in a room."""


@dataclass(frozen=True)
class RoomCloseResult:
    room_id: str
    removed_participant_ids: list[str]


@dataclass(frozen=True)
class ParticipantRemovalResult:
    participant_id: str
    room_id: str
    room_deleted: bool


class RoomControlModule:
    """Central control surface for room lifecycle and admin operations."""

    def __init__(
        self,
        manager: RoomManager,
        *,
        default_max_participants: int = 10,
        max_room_name_length: int = 64,
    ) -> None:
        self._manager = manager
        self._default_max_participants = default_max_participants
        self._max_room_name_length = max_room_name_length

    @property
    def manager(self) -> RoomManager:
        return self._manager

    def validate_room_name(self, room_name: str) -> str:
        if not isinstance(room_name, str):
            raise RoomControlError("room_name must be a string")
        normalized = room_name.strip()
        if not normalized:
            raise RoomControlError("room_name is required")
        if len(normalized) > self._max_room_name_length:
            raise RoomControlError(
                f"room_name must be <= {self._max_room_name_length} characters"
            )
        return normalized

    def validate_max_participants(self, max_participants: int | None) -> int:
        value = self._default_max_participants if max_participants is None else max_participants
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RoomControlError("max_participants must be a positive integer")
        return value

    def create_room(
        self,
        room_name: str,
        *,
        creator_id: str | None = None,
        max_participants: int | None = None,
    ) -> Room:
        room_name = self.validate_room_name(room_name)
        max_participants = self.validate_max_participants(max_participants)
        if self._manager.has_room(room_name):
            raise RoomAlreadyExistsError(f"Room already exists: {room_name}")
        return self._manager.get_or_create_room(room_name, max_participants, creator_id)

    def join_participant(
        self,
        participant: Participant,
        *,
        room_name: str,
        max_participants: int | None = None,
        create_if_missing: bool = True,
    ) -> tuple[Room, bool]:
        room_name = self.validate_room_name(room_name)
        max_participants = self.validate_max_participants(max_participants)

        room_created = False
        if not self._manager.has_room(room_name):
            if not create_if_missing:
                raise RoomNotFoundError(f"Room does not exist: {room_name}")
            self.create_room(
                room_name,
                creator_id=participant.participant_id,
                max_participants=max_participants,
            )
            room_created = True

        room = self._manager.join_room(
            participant,
            room_name,
            max_participants=max_participants,
            create_if_missing=False,
        )
        return room, room_created

    def update_room_capacity(self, room_name: str, max_participants: int) -> Room:
        room_name = self.validate_room_name(room_name)
        max_participants = self.validate_max_participants(max_participants)
        room = self._manager.get_room(room_name)
        if room is None:
            raise RoomNotFoundError(f"Room does not exist: {room_name}")
        if max_participants < room.participant_count:
            raise RoomCapacityError(
                "max_participants cannot be less than the current participant count"
            )
        room.max_participants = max_participants
        return room

    def close_room(self, room_name: str) -> RoomCloseResult:
        room_name = self.validate_room_name(room_name)
        room = self._manager.get_room(room_name)
        if room is None:
            raise RoomNotFoundError(f"Room does not exist: {room_name}")

        removed_participant_ids: list[str] = []
        for participant in list(room.participants):
            removed_participant_ids.append(participant.participant_id)
            self._manager.leave_room(participant)

        return RoomCloseResult(
            room_id=room_name,
            removed_participant_ids=removed_participant_ids,
        )

    def remove_participant(self, participant_id: str) -> ParticipantRemovalResult:
        participant = self._manager.get_participant(participant_id)
        if participant is None:
            raise ParticipantNotFoundError(f"Unknown participant: {participant_id}")
        if not participant.room_id:
            raise ParticipantNotInRoomControlError(
                f"Participant is not in a room: {participant_id}"
            )

        room_id = participant.room_id
        remaining_room = self._manager.leave_room(participant)
        return ParticipantRemovalResult(
            participant_id=participant.participant_id,
            room_id=room_id,
            room_deleted=remaining_room is None,
        )

    def list_rooms(self) -> list[dict]:
        return self._manager.list_rooms()

    def list_room_participants(self, room_name: str) -> list[dict]:
        room_name = self.validate_room_name(room_name)
        room = self._manager.get_room(room_name)
        if room is None:
            raise RoomNotFoundError(f"Room does not exist: {room_name}")
        return [participant.to_dict() for participant in room.participants]
