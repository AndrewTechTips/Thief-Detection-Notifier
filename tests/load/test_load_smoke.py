"""Load smoke test: 5 cameras, 20 WebSocket clients, 2 MJPEG viewers and steady API traffic
against a real uvicorn server, while measuring event-loop lag inside the server process.

Runs for a few seconds in CI; for a longer soak:  LOAD_SMOKE_SECONDS=60 uv run pytest tests/load -s
"""

import asyncio
import json
import os
import socket
import statistics
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx2
import pytest
import uvicorn
import websockets
from pydantic import SecretStr

from vision_hub.core.config import AppConfig, Environment, SecurityConfig, Settings, VisionConfig
from vision_hub.main import create_app

DURATION = float(os.environ.get("LOAD_SMOKE_SECONDS", "6"))
CAMERAS = 5
WS_CLIENTS = 20
MJPEG_VIEWERS = 2
MAX_P99_LAG_MS = 50
MAX_P95_API_MS = 100


@dataclass
class Stats:
    lag_ms: list[float] = field(default_factory=list)
    api_ms: list[float] = field(default_factory=list)
    ws_messages: dict[int, list[str]] = field(default_factory=dict)
    ws_errors: list[str] = field(default_factory=list)
    mjpeg_parts: list[int] = field(default_factory=list)


def fleet_toml() -> str:
    devices = []
    for n in range(CAMERAS):
        devices.append(
            f'[[devices]]\nid = "cam-{n}"\nname = "Camera {n}"\n'
            f'[devices.source]\nkind = "synthetic"\nfps = 10\nseed = {n}\n'
            "visit_every_seconds = 3\nvisit_seconds = 1\n"
            "[devices.detection]\nwarmup_frames = 3\nmotion_end_grace_seconds = 0.5\n"
        )
    return "\n".join(devices)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture
async def server(
    tmp_path: Path, admin_password_hash: SecretStr
) -> AsyncIterator[tuple[str, uvicorn.Server]]:
    devices = tmp_path / "devices.toml"
    devices.write_text(fleet_toml())
    settings = Settings(
        app=AppConfig(env=Environment.TEST, log_level="WARNING"),
        security=SecurityConfig(
            admin_password_hash=admin_password_hash, auth_rate_limit="100/minute"
        ),
        vision=VisionConfig(devices_file=devices, lock_file=tmp_path / "hub.lock"),
    )
    port = free_port()
    config = uvicorn.Config(
        create_app(settings), host="127.0.0.1", port=port, log_config=None, access_log=False
    )
    uvicorn_server = uvicorn.Server(config)
    task = asyncio.create_task(uvicorn_server.serve())
    async with asyncio.timeout(10):
        while not uvicorn_server.started:
            await asyncio.sleep(0.05)
    yield f"127.0.0.1:{port}", uvicorn_server
    uvicorn_server.should_exit = True
    await asyncio.wait_for(task, 15)


async def measure_lag(stats: Stats, stop: asyncio.Event, interval: float = 0.01) -> None:
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        started = loop.time()
        await asyncio.sleep(interval)
        stats.lag_ms.append((loop.time() - started - interval) * 1000)


async def ws_client(
    index: int, address: str, ticket: str, stats: Stats, stop: asyncio.Event
) -> None:
    received = stats.ws_messages.setdefault(index, [])
    try:
        async with websockets.connect(f"ws://{address}/api/v1/ws/events?ticket={ticket}") as ws:
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), 0.25)
                except TimeoutError:
                    continue
                message = json.loads(raw)
                received.append(message["type"])
                if message["type"] == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
    except websockets.ConnectionClosed as exc:
        stats.ws_errors.append(f"client {index}: closed {exc.code}")


async def mjpeg_viewer(
    client: httpx2.AsyncClient, url: str, stats: Stats, stop: asyncio.Event
) -> None:
    parts = 0
    async with client.stream("GET", url) as response:
        assert response.status_code == 200
        async for chunk in response.aiter_bytes():
            parts += chunk.count(b"--frame")
            if stop.is_set():
                break
    stats.mjpeg_parts.append(parts)


async def api_traffic(
    client: httpx2.AsyncClient, headers: dict[str, str], stats: Stats, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        started = time.perf_counter()
        response = await client.get("/api/v1/devices", headers=headers)
        stats.api_ms.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200
        await asyncio.sleep(0.1)


def percentile(values: list[float], q: int) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[q - 1] if values else 0.0


async def test_hub_stays_responsive_under_load(
    server: tuple[str, uvicorn.Server], admin_credentials: dict[str, str]
) -> None:
    address, _ = server
    stats, stop = Stats(), asyncio.Event()
    async with httpx2.AsyncClient(base_url=f"http://{address}", timeout=10) as client:
        token = (await client.post("/api/v1/auth/token", data=admin_credentials)).json()
        headers = {"Authorization": f"Bearer {token['access_token']}"}

        async def ticket() -> str:
            value: str = (await client.post("/api/v1/auth/tickets", headers=headers)).json()[
                "ticket"
            ]
            return value

        tasks = [asyncio.create_task(measure_lag(stats, stop))]
        tasks += [
            asyncio.create_task(ws_client(i, address, await ticket(), stats, stop))
            for i in range(WS_CLIENTS)
        ]
        tasks += [
            asyncio.create_task(
                mjpeg_viewer(
                    client, f"/api/v1/devices/cam-{i}/stream?ticket={await ticket()}", stats, stop
                )
            )
            for i in range(MJPEG_VIEWERS)
        ]
        tasks.append(asyncio.create_task(api_traffic(client, headers, stats, stop)))

        await asyncio.sleep(DURATION)
        stop.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 15)

    p99_lag, max_lag = percentile(stats.lag_ms, 99), max(stats.lag_ms)
    p95_api = percentile(stats.api_ms, 95)
    motion_per_client = [types.count("motion.started") for types in stats.ws_messages.values()]
    sys.stdout.write(  # shown with -s; useful for soak runs
        f"\n{DURATION:g}s, {CAMERAS} cameras, {WS_CLIENTS} WS clients, {MJPEG_VIEWERS} MJPEG "
        f"viewers | loop lag p99={p99_lag:.1f} ms max={max_lag:.1f} ms | "
        f"API p95={p95_api:.1f} ms ({len(stats.api_ms)} calls) | "
        f"motion events per client={min(motion_per_client)}..{max(motion_per_client)} | "
        f"MJPEG parts per viewer={stats.mjpeg_parts}\n"
    )

    assert stats.ws_errors == []
    assert p99_lag < MAX_P99_LAG_MS
    assert p95_api < MAX_P95_API_MS
    assert len(stats.ws_messages) == WS_CLIENTS
    assert min(motion_per_client) >= 1  # every client saw live motion events
    assert min(stats.mjpeg_parts) >= DURATION * 3  # ~10 fps per viewer, generous floor
