"""Public framework API for creating and accessing the signaling app."""
from __future__ import annotations

import importlib
from typing import Any, cast

from fastapi import FastAPI

from room.control import RoomControlModule
from room.manager import RoomManager
from services.stt_service import STTService


def _load_signaling_server_module() -> Any:
    return importlib.import_module("signaling.server")


def create_app() -> FastAPI:
    """Return the configured FastAPI application."""
    module = _load_signaling_server_module()
    app = getattr(module, "app", None)
    if app is None:
        raise RuntimeError("signaling.server.app is not available")
    return cast(FastAPI, app)


def get_manager() -> RoomManager:
    """Return the active RoomManager instance used by the signaling app."""
    module = _load_signaling_server_module()
    manager = getattr(module, "manager", None)
    if manager is None:
        raise RuntimeError("signaling.server.manager is not available")
    return cast(RoomManager, manager)


def get_stt_service() -> STTService:
    """Return the active STTService instance used by the signaling app."""
    module = _load_signaling_server_module()
    stt_service = getattr(module, "stt_service", None)
    if stt_service is None:
        raise RuntimeError("signaling.server.stt_service is not available")
    return cast(STTService, stt_service)


def get_room_control() -> RoomControlModule:
    """Return the active RoomControlModule used by the signaling app."""
    module = _load_signaling_server_module()
    get_room_control_fn = getattr(module, "_get_room_control", None)
    if callable(get_room_control_fn):
        return cast(RoomControlModule, get_room_control_fn())
    room_control = getattr(module, "room_control", None)
    if room_control is None:
        raise RuntimeError("signaling.server.room_control is not available")
    return cast(RoomControlModule, room_control)


class RTCRoomFramework:
    """Convenience framework wrapper with app + runtime components."""

    def __init__(self) -> None:
        self.app = create_app()

    @property
    def manager(self) -> RoomManager:
        return get_manager()

    @property
    def stt_service(self) -> STTService:
        return get_stt_service()

    @property
    def room_control(self) -> RoomControlModule:
        return get_room_control()
