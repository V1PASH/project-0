"""Run signaling server with an in-process echo agent participant."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rtc_room_framework import AgentSession, EchoTranscriptResponder, create_app, get_manager, get_room_control


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run signaling server with an echo agent participant.")
    parser.add_argument("--room-id", required=True, help="Room id where the agent should join.")
    parser.add_argument("--agent-name", default="Room Agent", help="Display name for the agent.")
    parser.add_argument("--agent-prefix", default="Agent: ", help="Prefix prepended to echo responses.")
    parser.add_argument("--respond-to-interim", action="store_true", help="Respond to interim transcripts.")
    parser.add_argument("--create-room", action="store_true", help="Create room if it does not exist.")
    parser.add_argument(
        "--max-participants",
        type=int,
        default=10,
        help="Room max participants used only when creating a missing room.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind.")
    parser.add_argument("--ssl-certfile", default=None, help="Path to SSL certificate file.")
    parser.add_argument("--ssl-keyfile", default=None, help="Path to SSL key file.")
    args = parser.parse_args(argv)

    app = create_app()
    manager = get_manager()
    room_control = get_room_control()
    agent_session = AgentSession(
        manager,
        room_control=room_control,
        name=args.agent_name,
        responder=EchoTranscriptResponder(prefix=args.agent_prefix),
        respond_to_interim=args.respond_to_interim,
    )

    @app.on_event("startup")
    async def _start_agent() -> None:
        await agent_session.start(
            args.room_id,
            max_participants=args.max_participants,
            create_if_missing=args.create_room,
        )

    @app.on_event("shutdown")
    async def _stop_agent() -> None:
        await agent_session.stop()

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ssl_certfile=args.ssl_certfile,
        ssl_keyfile=args.ssl_keyfile,
    )


if __name__ == "__main__":
    main()
