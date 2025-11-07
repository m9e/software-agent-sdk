"""Entry point for running the sandbox controller service."""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn

from .app import SandboxSettings, create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenHands sandbox controller")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8085")))
    parser.add_argument(
        "--log-level",
        default=os.getenv("LOG_LEVEL", "info"),
        choices=["critical", "error", "warning", "info", "debug"],
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))
    settings = SandboxSettings()
    app = create_app(settings)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
