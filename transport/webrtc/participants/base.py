"""
Base participant enums shared by local and remote participants.
"""
from __future__ import annotations

from enum import Enum


class ParticipantState(str, Enum):
    """Connection and room lifecycle state for a participant."""

    CONNECTING = "connecting"
    IN_ROOM = "in_room"
    DISCONNECTED = "disconnected"


class ParticipantRole(str, Enum):
    """Role of a participant from the perspective of a client/server."""

    LOCAL = "local"
    REMOTE = "remote"
