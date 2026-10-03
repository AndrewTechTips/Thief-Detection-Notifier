"""Command-line entrypoint: ``vision-hub`` or ``python -m vision_hub``."""

import argparse
from collections.abc import Sequence

import uvicorn

from vision_hub.core.config import get_settings


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="vision-hub", description="Run the IoT Vision Hub server."
    )
    parser.add_argument("--reload", action="store_true", help="restart on code changes (dev only)")
    args = parser.parse_args(argv)

    settings = get_settings()
    uvicorn.run(
        "vision_hub.main:create_app",
        factory=True,
        host=settings.app.host,
        port=settings.app.port,
        reload=args.reload,
        # Cameras are owned by the process: never run more than one worker (AD-8).
        workers=1,
        # Logging is configured by create_app(); uvicorn must not install its own config.
        log_config=None,
        access_log=False,
        server_header=False,
    )


if __name__ == "__main__":
    main()
