"""Command-line entrypoint: ``vision-hub [COMMAND]``, serving the hub by default.

Commands: serve, hash-password, create-user, openapi, vapid-key, download-model, demo-footage,
export-demo.
"""

import argparse
import asyncio
import getpass
import socket
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import uvicorn

from vision_hub.api.openapi import openapi_document
from vision_hub.core.config import env_name, get_settings
from vision_hub.core.downloads import ChecksumError
from vision_hub.core.security import PasswordHasher
from vision_hub.domain.audit import AuditAction, AuditTarget
from vision_hub.domain.auth import Role
from vision_hub.infra.db.engine import create_engine, create_sessions
from vision_hub.infra.db.migrate import upgrade_to_head
from vision_hub.infra.db.repositories.audit import SqlAuditLog
from vision_hub.infra.db.repositories.users import SqlUserRepository
from vision_hub.infra.notifiers.webpush import VapidKey
from vision_hub.main import create_app
from vision_hub.services.audit import Auditor
from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.demo import prepare_footage
from vision_hub.vision.demo_export import export_demo
from vision_hub.vision.fleet import FleetError, load_fleet
from vision_hub.vision.persons import MODEL_BYTES, ModelError, download_model, load_person_detector

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

    openapi = commands.add_parser(
        "openapi", help="print the OpenAPI schema (the dashboard generates its types from it)"
    )
    openapi.add_argument("-o", "--output", type=Path, help="write to this file instead")
    openapi.set_defaults(command="openapi")

    vapid_key = commands.add_parser(
        "vapid-key", help=f"print a new web push key for {env_name('push', 'vapid_private_key')}"
    )
    vapid_key.set_defaults(command="vapid-key")

    download = commands.add_parser(
        "download-model",
        help=f"download the person-detection model to {env_name('persons', 'model_path')}",
    )
    download.set_defaults(command="download-model")

    demo = commands.add_parser(
        "demo-footage",
        help="fetch the demo cameras' footage and make their loops (see devices.demo.toml)",
    )
    demo.add_argument(
        "-d", "--directory", type=Path, default=Path("data/demo"), help="default: data/demo"
    )
    demo.add_argument(
        "--no-fetch",
        action="store_true",
        help="use sources already in DIRECTORY/sources instead of downloading them",
    )
    demo.set_defaults(command="demo-footage")

    export = commands.add_parser(
        "export-demo",
        help="record what the hub sees on the demo cameras, for the dashboard's demo build",
    )
    export.add_argument(
        "--devices", type=Path, default=Path("devices.demo.toml"), help="default: devices.demo.toml"
    )
    export.add_argument(
        "-o", "--output", type=Path, default=Path("frontend/.demo"), help="default: frontend/.demo"
    )
    export.set_defaults(command="export-demo")

    args = parser.parse_args(argv)
    if args.command == "download-model":
        _download_model()
    elif args.command == "demo-footage":
        _demo_footage(args.directory, fetch=not args.no_fetch)
    elif args.command == "export-demo":
        _export_demo(args.devices, args.output)
    elif args.command == "vapid-key":
        sys.stdout.write(VapidKey.generate().to_text() + "\n")
    elif args.command == "openapi":
        _write_openapi(args.output)
    elif args.command == "hash-password":
        sys.stdout.write(PasswordHasher().hash(_prompt_new_password()) + "\n")
    elif args.command == "create-user":
        _create_user(args.username, Role(args.role))
    else:
        _serve(reload=args.reload)


def _download_model() -> None:
    path = get_settings().persons.model_path
    size = MODEL_BYTES // 1_000_000
    sys.stdout.write(f"Person-detection model (YOLOX-s, {size} MB): {path}\n")
    try:
        fetched = download_model(path)
    except (OSError, ModelError) as exc:
        sys.stderr.write(f"Download failed: {exc}\n")
        raise SystemExit(1) from exc
    sys.stdout.write("Downloaded and verified.\n" if fetched else "Already there and verified.\n")


def _demo_footage(directory: Path, *, fetch: bool) -> None:
    sys.stdout.write(f"Demo footage (Pexels, see docs/demo-footage.md): {directory}\n")
    try:
        made = prepare_footage(directory, fetch=fetch)
    except (OSError, ChecksumError, ValueError) as exc:
        sys.stderr.write(f"Demo footage failed: {exc}\n")
        raise SystemExit(1) from exc
    for clip, now in made:
        sys.stdout.write(f"  {clip.name}.webm: {'made' if now else 'already there'}\n")


def _export_demo(devices: Path, output: Path) -> None:
    persons = get_settings().persons
    detector = load_person_detector(persons.model_path)
    if detector is None:
        sys.stderr.write("The person model is missing: run `vision-hub download-model` first.\n")
        raise SystemExit(1)
    try:
        cameras = load_fleet(devices, defaults=DetectionConfig())
        summary = export_demo(cameras, detector, output, threshold=persons.threshold)
    except (FleetError, ValueError, OSError) as exc:
        sys.stderr.write(f"Export failed: {exc}\n")
        raise SystemExit(1) from exc
    sys.stdout.write(f"Demo data: {output}\n")
    for camera in summary:
        sys.stdout.write(
            f"  {camera.camera}: {camera.events} events per loop ({camera.people} with a person), "
            f"{camera.frames} analysed frames\n"
        )


class GracefulServer(uvicorn.Server):
    """Signals the application as soon as shutdown begins, *before* uvicorn waits for open
    connections, so endless responses (MJPEG streams) end instead of blocking shutdown."""

    def __init__(self, config: uvicorn.Config, *, on_shutdown: Callable[[], None]) -> None:
        super().__init__(config)
        self._on_shutdown = on_shutdown

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        self._on_shutdown()
        await super().shutdown(sockets)


def _serve(*, reload: bool) -> None:
    settings = get_settings()
    options: dict[str, Any] = {
        "host": settings.app.host,
        "port": settings.app.port,
        # Cameras are owned by the process: never run more than one worker (AD-8).
        "workers": 1,
        # Logging is configured by create_app(); uvicorn must not install its own config.
        "log_config": None,
        "access_log": False,
        "server_header": False,
        # Backstop: cancel requests still running this long after shutdown began.
        "timeout_graceful_shutdown": settings.app.shutdown_timeout_seconds,
    }
    if reload:  # development only: the reloader imports the app in a subprocess
        uvicorn.run("vision_hub.main:create_app", factory=True, reload=True, **options)
        return
    app = create_app(settings)
    GracefulServer(
        uvicorn.Config(app, **options), on_shutdown=app.state.lifecycle.begin_shutdown
    ).run()


def _write_openapi(output: Path | None) -> None:
    document = openapi_document(create_app())
    if output is None:
        sys.stdout.write(document)
    else:
        output.write_text(document, encoding="utf-8")


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
        sessions = create_sessions(engine)
        created = await SqlUserRepository(sessions).save(username, password_hash, role)
        await Auditor(SqlAuditLog(sessions)).record(
            _cli_actor(),
            AuditAction.USER_CREATED if created else AuditAction.USER_UPDATED,
            AuditTarget.USER,
            username,
            role=role,
        )
        return created
    finally:
        await engine.dispose()


def _cli_actor() -> str:
    """``cli:<os user>``: the operator who ran the command."""
    try:
        return f"cli:{getpass.getuser()}"
    except OSError, KeyError:  # e.g. a container uid without a passwd entry
        return "cli"


if __name__ == "__main__":
    main()
