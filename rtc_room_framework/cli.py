"""CLI entry points for running the signaling server."""
from __future__ import annotations

import argparse
from typing import Sequence

import uvicorn

from rtc_room_framework.app import create_app

_UVICORN_APP_FACTORY_IMPORT = "rtc_room_framework.app:create_app"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the RTC room signaling framework server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind.")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload.")
    parser.add_argument("--ssl-certfile", default=None, help="Path to SSL certificate file.")
    parser.add_argument("--ssl-keyfile", default=None, help="Path to SSL key file.")
    args = parser.parse_args(argv)

    app_target = _UVICORN_APP_FACTORY_IMPORT if args.reload else create_app()
    run_kwargs = {
        "host": args.host,
        "port": args.port,
        "reload": args.reload,
        "ssl_certfile": args.ssl_certfile,
        "ssl_keyfile": args.ssl_keyfile,
    }
    if args.reload:
        run_kwargs["factory"] = True

    uvicorn.run(app_target, **run_kwargs)


if __name__ == "__main__":
    main()
