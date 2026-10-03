"""Command-line entrypoint: ``vision-hub [serve|hash-password|create-user]``."""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Sequence

import uvicorn

from vision_hub.core.config import env_name, get_settings
from vision_hub.core.security import PasswordHasher
from vision_hub.domain.auth import Role
from vision_hub.infra.db.engine import create_engine, create_sessions
from vision_hub.infra.db.migrate import upgrade_to_head
from vision_hub.infra.db.repositories.users import SqlUserRepository

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

    create_user = commands.add_parser(
        "create-user", help="create a user, or reset an existing user's password and role"
    )
    create_user.add_argument("username")
    create_user.add_argument("--role", choices=[role.value for role in Role], default="viewer")
    create_user.set_defaults(command="create-user")

    args = parser.parse_args(argv)
    if args.command == "hash-password":
        sys.stdout.write(PasswordHasher().hash(_prompt_new_password()) + "\n")
    elif args.command == "create-user":
        _create_user(args.username, Role(args.role))
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


def _prompt_new_password() -> str:
    """Prompts without echo, so the password never lands in shell history or `ps` output."""
    password = getpass.getpass("New password: ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if getpass.getpass("Repeat password: ") != password:
        sys.exit("Passwords do not match.")
    return password


def _create_user(username: str, role: Role) -> None:
    if not 3 <= len(username) <= 100:
        sys.exit("Username must be 3 to 100 characters.")
    password_hash = PasswordHasher().hash(_prompt_new_password())
    created = asyncio.run(_save_user(username, password_hash, role))
    sys.stdout.write(f"{'Created' if created else 'Updated'} user {username} ({role}).\n")


async def _save_user(username: str, password_hash: str, role: Role) -> bool:
    settings = get_settings()
    engine = create_engine(settings.db)
    try:
        if settings.db.migrate_on_startup:
            await upgrade_to_head(engine)
        return await SqlUserRepository(create_sessions(engine)).save(username, password_hash, role)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
