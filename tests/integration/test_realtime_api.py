import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import cv2
import httpx2
import numpy as np
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.types import Message
from starlette.websockets import WebSocketDisconnect

from vision_hub.core.config import AppConfig, Environment, SecurityConfig, Settings, VisionConfig

FLEET = """
[[devices]]
id = "porch"
name = "Porch"
[devices.source]
kind = "synthetic"
fps = 20

[[devices]]
id = "gate"
name = "Gate"
enabled = false
[devices.source]
kind = "synthetic"
"""
STREAM = "/api/v1/devices/porch/stream"


@pytest.fixture
def settings(tmp_path: Path, admin_password_hash: SecretStr) -> Settings:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET)
    return Settings(
        app=AppConfig(env=Environment.TEST),
        security=SecurityConfig(admin_password_hash=admin_password_hash),
        vision=VisionConfig(devices_file=devices, lock_file=tmp_path / "hub.lock"),
    )


@pytest.fixture
async def running(app: FastAPI) -> AsyncIterator[Any]:
    """The app with its lifespan running, shared by the HTTP client and raw ASGI reads."""
    async with LifespanManager(app) as manager:
        yield manager.app


@pytest.fixture
async def client(running: Any) -> AsyncIterator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=running)
    async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as http:
        yield http


@pytest.fixture
async def bearer(client: httpx2.AsyncClient, admin_credentials: dict[str, str]) -> dict[str, str]:
    response = await client.post("/api/v1/auth/token", data=admin_credentials)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def new_ticket(client: httpx2.AsyncClient, bearer: dict[str, str]) -> str:
    response = await client.post("/api/v1/auth/tickets", headers=bearer)
    ticket: str = response.json()["ticket"]
    return ticket


async def read_stream(
    app: Any, path: str, *, headers: dict[str, str] | None = None, parts: int = 2
) -> tuple[int, dict[str, str], list[bytes]]:
    """Drive the ASGI app directly so an endless stream can be read and then abandoned, the way
    a browser closing the tab would (httpx buffers whole bodies)."""
    path_only, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path_only,
        "raw_path": path_only.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", b"localhost")]
        + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 5000),
        "server": ("localhost", 80),
    }
    gone = asyncio.Event()
    status, response_headers, body = 0, {}, b""

    async def receive() -> Message:
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal status, response_headers, body
        if message["type"] == "http.response.start":
            status = message["status"]
            response_headers = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")
            if body.count(b"--frame") >= parts:
                gone.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        async with asyncio.timeout(10):
            while not task.done() and not gone.is_set():
                await asyncio.sleep(0.01)
        gone.set()
        await asyncio.wait_for(task, 5)
    finally:
        task.cancel()
    return status, response_headers, [p for p in body.split(b"--frame") if p.strip()]


class TestTickets:
    async def test_require_authentication(self, client: httpx2.AsyncClient) -> None:
        assert (await client.post("/api/v1/auth/tickets")).status_code == 401

    async def test_issue(self, client: httpx2.AsyncClient, bearer: dict[str, str]) -> None:
        response = await client.post("/api/v1/auth/tickets", headers=bearer)

        assert response.status_code == 200
        assert response.json()["expires_in"] == 30
        assert response.headers["Cache-Control"] == "no-store"


class TestMjpegEndpoint:
    async def test_streams_jpeg_parts_with_a_bearer_token(
        self, running: Any, bearer: dict[str, str]
    ) -> None:
        status, headers, parts = await read_stream(running, STREAM, headers=bearer, parts=3)

        assert status == 200
        assert headers["content-type"] == "multipart/x-mixed-replace; boundary=frame"
        assert headers["cache-control"] == "no-store"
        jpeg = parts[0].split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n")
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        assert image is not None
        assert image.shape == (480, 640, 3)

    async def test_a_ticket_works_exactly_once(
        self, client: httpx2.AsyncClient, running: Any, bearer: dict[str, str]
    ) -> None:
        ticket = await new_ticket(client, bearer)

        first, _, _ = await read_stream(running, f"{STREAM}?ticket={ticket}", parts=1)
        second = await client.get(f"{STREAM}?ticket={ticket}")

        assert first == 200
        assert second.status_code == 401
        assert second.json()["detail"] == "Ticket is invalid, expired or already used."

    async def test_requires_credentials(self, client: httpx2.AsyncClient) -> None:
        response = await client.get(STREAM)

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing bearer token or ticket."

    async def test_stopped_and_unknown_cameras(
        self, client: httpx2.AsyncClient, bearer: dict[str, str]
    ) -> None:
        stopped = await client.get("/api/v1/devices/gate/stream", headers=bearer)
        unknown = await client.get("/api/v1/devices/ghost/stream", headers=bearer)

        assert stopped.status_code == 503
        assert unknown.status_code == 404


class TestWebSocket:
    def connect(self, test_client: TestClient, ticket: str | None) -> Any:
        query = f"?ticket={ticket}" if ticket else ""
        return test_client.websocket_connect(f"ws://localhost/api/v1/ws/events{query}")

    def login(self, test_client: TestClient, credentials: dict[str, str]) -> dict[str, str]:
        token = test_client.post("/api/v1/auth/token", data=credentials).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.parametrize("ticket", [None, "forged-ticket"])
    def test_rejects_missing_or_invalid_tickets(
        self, app: FastAPI, ticket: str | None, log_records: Callable[[], list[dict[str, Any]]]
    ) -> None:
        with (
            TestClient(app, base_url="http://localhost") as test_client,
            pytest.raises(WebSocketDisconnect) as exc_info,
            self.connect(test_client, ticket),
        ):
            pass

        assert exc_info.value.code == 4401
        rejected = next(r for r in log_records() if r["event"] == "ws_rejected")
        assert rejected["reason"] == ("missing ticket" if ticket is None else "invalid ticket")

    def test_live_events_and_device_filter(
        self, app: FastAPI, admin_credentials: dict[str, str]
    ) -> None:
        with TestClient(app, base_url="http://localhost") as test_client:
            headers = self.login(test_client, admin_credentials)
            ticket = test_client.post("/api/v1/auth/tickets", headers=headers).json()["ticket"]

            with self.connect(test_client, ticket) as ws:
                assert ws.receive_json()["type"] == "subscription"
                ws.send_json({"type": "subscribe", "devices": ["gate"]})
                assert ws.receive_json()["data"]["devices"] == ["gate"]

                test_client.post("/api/v1/devices/gate/start", headers=headers)
                messages = [ws.receive_json() for _ in range(2)]

            assert [m["type"] for m in messages] == ["device.status", "device.status"]
            assert {m["device_id"] for m in messages} == {"gate"}
            assert [m["data"]["status"] for m in messages] == ["starting", "online"]

            reused = pytest.raises(WebSocketDisconnect)
            with reused, self.connect(test_client, ticket):
                pass
            assert reused.excinfo.value.code == 4401  # tickets are single use
