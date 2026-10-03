import asyncio
import base64
import io
import json
import os
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from pydantic import SecretStr

from vision_hub.core.config import (
    ENV_PREFIX,
    AppConfig,
    Environment,
    SecurityConfig,
    Settings,
    get_settings,
)
from vision_hub.core.logging import configure_logging
from vision_hub.core.security import PasswordHasher
from vision_hub.main import create_app

type SettingsFactory = Callable[..., Settings]
type LogRecords = Callable[[], list[dict[str, Any]]]


@pytest.fixture(autouse=True)
def isolated_settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep the developer's shell variables and local `.env` out of every test."""
    for key in list(os.environ):
        if key.startswith(ENV_PREFIX):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "correct horse battery staple"


@pytest.fixture
def admin_credentials() -> dict[str, str]:
    """Form fields for ``POST /api/v1/auth/token``."""
    return {"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD}


@pytest.fixture(scope="session")
def admin_password_hash() -> SecretStr:
    """Hashed once per session: Argon2 is deliberately slow."""
    return SecretStr(PasswordHasher().hash(ADMIN_PASSWORD))


@pytest.fixture
def settings_factory() -> SettingsFactory:
    """Build test settings; keyword arguments override ``AppConfig`` fields."""

    def factory(*, security: SecurityConfig | None = None, **app_overrides: Any) -> Settings:
        return Settings(
            app=AppConfig(env=Environment.TEST, **app_overrides),
            security=security or SecurityConfig(),
        )

    return factory


@pytest.fixture
def settings(settings_factory: SettingsFactory) -> Settings:
    return settings_factory()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    """HTTP client with the app's lifespan running (startup/shutdown and lifespan state)."""
    async with LifespanManager(app) as manager:
        transport = httpx2.ASGITransport(app=manager.app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as http:
            yield http


@pytest.fixture
def log_records(app: FastAPI, settings_factory: SettingsFactory) -> LogRecords:
    """Redirect logging to an in-memory JSON stream; call the result to get parsed records.

    Depends on ``app`` so it runs after ``create_app`` has done its own logging setup.
    """
    stream = io.StringIO()
    configure_logging(settings_factory(log_format="json"), stream=stream)
    return lambda: [json.loads(line) for line in stream.getvalue().splitlines()]


# ── Fake SMTP server ─────────────────────────────────────────
# A small but real SMTP server on asyncio streams (aiosmtpd does not work on Python 3.14), so the
# email notifier is exercised over TCP exactly as against a production server.


@dataclass
class ReceivedMail:
    sender: str
    recipients: list[str]
    data: bytes
    authenticated_as: str | None

    @property
    def message(self) -> EmailMessage:
        parsed = message_from_bytes(self.data, policy=policy.default)
        assert isinstance(parsed, EmailMessage)
        return parsed


@dataclass
class FakeSmtpServer:
    username: str | None = None
    password: str | None = None
    reject_recipients: bool = False
    drop_connections: bool = False  # simulate a server that is up but unusable
    received: list[ReceivedMail] = field(default_factory=list)
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    port: int = 0
    _server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def wait_for_mail(self, count: int = 1, within: float = 10) -> list[ReceivedMail]:
        async with asyncio.timeout(within):
            while len(self.received) < count:
                self.arrived.clear()
                await self.arrived.wait()
        return self.received

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        async def reply(line: str) -> None:
            writer.write(f"{line}\r\n".encode())
            await writer.drain()

        if self.drop_connections:
            writer.close()
            return
        await reply("220 fake-smtp ready")
        sender, recipients, user = "", [], None
        try:
            while line := (await reader.readline()).decode().rstrip("\r\n"):
                command = line.split(" ", 1)[0].upper()
                if command == "EHLO":
                    await reply("250-fake-smtp\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME")
                elif command == "HELO":
                    await reply("250 fake-smtp")
                elif command == "AUTH":
                    user = await self._authenticate(line, reader, reply)
                elif command == "MAIL":
                    sender = line.split(":", 1)[1].strip().split()[0].strip("<>")
                    await reply("250 OK")
                elif command == "RCPT":
                    if self.reject_recipients:
                        await reply("550 No such user")
                        continue
                    recipients.append(line.split(":", 1)[1].strip().strip("<>"))
                    await reply("250 OK")
                elif command == "DATA":
                    await reply("354 End data with <CR><LF>.<CR><LF>")
                    data = await self._read_data(reader)
                    self.received.append(ReceivedMail(sender, recipients, data, user))
                    self.arrived.set()
                    sender, recipients = "", []
                    await reply("250 OK queued")
                elif command in {"RSET", "NOOP"}:
                    await reply("250 OK")
                elif command == "QUIT":
                    await reply("221 Bye")
                    break
                else:
                    await reply("502 Command not implemented")
        finally:
            writer.close()

    async def _authenticate(
        self, line: str, reader: asyncio.StreamReader, reply: Callable[[str], Any]
    ) -> str | None:
        parts = line.split()
        if parts[1].upper() == "PLAIN":
            encoded = parts[2] if len(parts) > 2 else None
            if encoded is None:
                await reply("334 ")
                encoded = (await reader.readline()).decode().strip()
            _, user, password = base64.b64decode(encoded).decode().split("\0")
        else:  # LOGIN
            await reply("334 VXNlcm5hbWU6")
            user = base64.b64decode((await reader.readline()).strip()).decode()
            await reply("334 UGFzc3dvcmQ6")
            password = base64.b64decode((await reader.readline()).strip()).decode()
        if (user, password) == (self.username, self.password):
            await reply("235 Authentication successful")
            return user
        await reply("535 Authentication credentials invalid")
        return None

    @staticmethod
    async def _read_data(reader: asyncio.StreamReader) -> bytes:
        lines = []
        while (line := await reader.readline()) not in {b".\r\n", b""}:
            lines.append(line[1:] if line.startswith(b"..") else line)  # dot-unstuffing
        return b"".join(lines)


type SmtpServerFactory = Callable[..., Any]


@pytest.fixture
async def smtp_server_factory() -> AsyncIterator[SmtpServerFactory]:
    """Start fake SMTP servers on demand; all are stopped at teardown."""
    servers: list[FakeSmtpServer] = []

    async def start(**options: Any) -> FakeSmtpServer:
        server = FakeSmtpServer(**options)
        await server.start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        await server.stop()


@pytest.fixture
async def smtp_server(smtp_server_factory: SmtpServerFactory) -> FakeSmtpServer:
    server: FakeSmtpServer = await smtp_server_factory(
        username="alerts@example.com", password="app-password"
    )
    return server
