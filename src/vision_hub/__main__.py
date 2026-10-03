"""Command-line entrypoint: ``vision-hub [serve|hash-password]`` or ``python -m vision_hub``."""

import argparse
import getpass
import sys
from collections.abc import Sequence

import uvicorn

from vision_hub.core.config import env_name, get_settings
from vision_hub.core.security import PasswordHasher

MIN_PASSWORD_LENGTH = 12


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vision-hub", description="IoT Vision Hub")
    parser.add_argument("--reload", action="store_true", help=argparse.SUPPRESS)
    parser.set_defaults(command="serve")
    commands = parser.add_subparsers(title="commands", metavar="COMMAND")

    serve = commands.add_parser("serve", help="run the API server (default)")
    serve.add_argument("--reload", action="store_true", help="restart on code changes (dev only)")
    serve.set_defaults(command="serve")

    hash_password = commands.add_parser(
        "hash-password",
        help=f"print an Argon2 hash for {env_name('security', 'admin_password_hash')}",
    )
    hash_password.set_defaults(command="hash-password")

    args = parser.parse_args(argv)
    if args.command == "hash-password":
        _hash_password()
    else:
        _serve(reload=args.reload)


def _serve(*, reload: bool) -> None:
    settings = get_settings()
    uvicorn.run(
        "vision_hub.main:create_app",
        factory=True,
        host=settings.app.host,
        port=settings.app.port,
        reload=reload,
        # Cameras are owned by the process: never run more than one worker (AD-8).
        workers=1,
        # Logging is configured by create_app(); uvicorn must not install its own config.
        log_config=None,
        access_log=False,
        server_header=False,
    )


def _hash_password() -> None:
    """Prompts without echo, so the password never lands in shell history or `ps` output."""
    password = getpass.getpass("New admin password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if getpass.getpass("Repeat password: ") != password:
        sys.exit("Passwords do not match.")
    sys.stdout.write(PasswordHasher().hash(password) + "\n")


if __name__ == "__main__":
    main()
