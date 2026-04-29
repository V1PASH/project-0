"""
Participant models used by room and signaling management.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
import time
import uuid

from .base import ParticipantRole, ParticipantState


def _new_participant_id() -> str:
    """Generate a compact unique identifier for a participant."""
    return f"p-{uuid.uuid4().hex[:12]}"


@dataclass
class Participant:
    """Represents one local or remote room participant."""

    websocket: Any | None = None
    name: str = "Anonymous"
    role: ParticipantRole = ParticipantRole.LOCAL
    participant_id: str = field(default_factory=_new_participant_id)
    room_id: Optional[str] = None
    state: ParticipantState = ParticipantState.CONNECTING
    is_speaking: bool = False
    created_at: float = field(default_factory=time.time)

    def transition(self, new_state: ParticipantState) -> None:
        self.state = new_state

    @property
    def is_local(self) -> bool:
        return self.role is ParticipantRole.LOCAL

    @property
    def is_remote(self) -> bool:
        return self.role is ParticipantRole.REMOTE

    def to_dict(self) -> dict:
        return {
            "participant_id": self.participant_id,
            "name": self.name,
            "role": self.role.value,
            "room_id": self.room_id,
            "state": self.state.value,
            "is_speaking": self.is_speaking,
            "created_at": self.created_at,
        }


class LocalParticipant(Participant):
    """A participant connected from the local signaling endpoint."""

    def __init__(
        self,
        websocket: Any,
        name: str = "Anonymous",
        participant_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            websocket=websocket,
            name=name,
            role=ParticipantRole.LOCAL,
            participant_id=participant_id or _new_participant_id(),
        )


class RemoteParticipant(Participant):
    """A participant represented as a remote peer."""

    def __init__(self, name: str = "Remote", participant_id: Optional[str] = None) -> None:
        super().__init__(
            websocket=None,
            name=name,
            role=ParticipantRole.REMOTE,
            participant_id=participant_id or _new_participant_id(),
        )
