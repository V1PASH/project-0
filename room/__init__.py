"""Room domain models and orchestration."""

from room.control import (
    ParticipantNotFoundError,
    ParticipantNotInRoomControlError,
    ParticipantRemovalResult,
    RoomAlreadyExistsError,
    RoomCapacityError,
    RoomCloseResult,
    RoomControlError,
    RoomControlModule,
)
from room.manager import EventListener, RoomEvent, RoomEventType, RoomManager
from room.model import (
    DuplicateParticipantError,
    ParticipantNotInRoomError,
    Room,
    RoomError,
    RoomFullError,
    RoomNotFoundError,
)

__all__ = [
    "EventListener",
    "RoomEvent",
    "RoomEventType",
    "RoomManager",
    "RoomControlError",
    "RoomAlreadyExistsError",
    "RoomCapacityError",
    "ParticipantNotFoundError",
    "ParticipantNotInRoomControlError",
    "RoomCloseResult",
    "ParticipantRemovalResult",
    "RoomControlModule",
    "Room",
    "RoomError",
    "RoomFullError",
    "RoomNotFoundError",
    "ParticipantNotInRoomError",
    "DuplicateParticipantError",
]
