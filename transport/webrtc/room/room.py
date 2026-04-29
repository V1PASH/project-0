"""
core/room.py
Room model — holds participants and enforces capacity rules.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time
import uuid

from transport.webrtc.participants.participant import Participant


class RoomError(Exception):
    """Base class for room-related errors."""


class RoomFullError(RoomError):
    pass


class ParticipantNotInRoomError(RoomError):
    pass


class DuplicateParticipantError(RoomError):
    pass


@dataclass
class Room:
    """
    A named audio-call room.

    Attributes
    ----------
    room_id          : str  Unique room identifier.
    max_participants : int  Hard cap on simultaneous participants (default 10).
    created_at       : float  Unix timestamp of room creation.
    created_by       : str | None  participant_id of the creator.
    """
    room_id: str = field(default_factory=lambda: f"r-{uuid.uuid4().hex[:12]}")
    max_participants: int = 10
    created_at: float = field(default_factory=time.time)
    created_by: Optional[str] = None

    # participant_id → Participant (insertion-ordered, Python 3.7+)
    _participants: dict[str, Participant] = field(default_factory=dict, repr=False)

    # ── Participant management ─────────────────────────────────────────────

    def add_participant(self, participant: Participant) -> None:
        if len(self._participants) >= self.max_participants:
            raise RoomFullError(
                f"Room {self.room_id!r} is full ({self.max_participants} participants)"
            )
        if participant.participant_id in self._participants:
            raise DuplicateParticipantError(
                f"Participant {participant.participant_id!r} is already in room {self.room_id!r}"
            )
        self._participants[participant.participant_id] = participant
        participant.room_id = self.room_id

    def remove_participant(self, participant_id: str) -> Optional[Participant]:
        """Remove and return the participant, or None if not found."""
        participant = self._participants.pop(participant_id, None)
        if participant:
            participant.room_id = None
        return participant

    def get_participant(self, participant_id: str) -> Optional[Participant]:
        return self._participants.get(participant_id)

    def get_other_participants(self, exclude_id: str) -> list[Participant]:
        """All participants except the one with exclude_id."""
        return [p for pid, p in self._participants.items() if pid != exclude_id]

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def participants(self) -> list[Participant]:
        return list(self._participants.values())

    @property
    def participant_count(self) -> int:
        return len(self._participants)

    @property
    def is_empty(self) -> bool:
        return len(self._participants) == 0

    @property
    def is_full(self) -> bool:
        return len(self._participants) >= self.max_participants

    # ── Serialisation ─────────────────────────────────────────────────────

    def to_dict(self, include_participants: bool = True) -> dict:
        data = {
            "room_id":           self.room_id,
            "max_participants":  self.max_participants,
            "participant_count": self.participant_count,
            "created_at":        self.created_at,
            "created_by":        self.created_by,
        }
        if include_participants:
            data["participants"] = [p.to_dict() for p in self.participants]
        return data

    def __repr__(self) -> str:
        return (
            f"Room(id={self.room_id!r}, "
            f"participants={self.participant_count}/{self.max_participants})"
        )
