"""The real server process: SIGTERM must not wait for endless MJPEG streams."""

import asyncio
import os
import signal
import socket
import sys
import time
from pathlib import Path

import httpx2
from pydantic import SecretStr

FLEET = """
[[devices]]
id = "front"
name = "Front door"
[devices.source]
kind = "synthetic"
fps = 20
"""


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


async def wait_until_ready(client: httpx2.AsyncClient, process: asyncio.subprocess.Process) -> None:
    async with asyncio.timeout(30):
        while True:
            assert process.returncode is None, "server exited during startup"
            try:
                if (await client.get("/api/v1/health/ready")).status_code == 200:
                    return
            except httpx2.TransportError:
                pass
            await asyncio.sleep(0.2)


async def test_sigterm_ends_open_streams_and_shuts_down_cleanly(
    tmp_path: Path, admin_password_hash: SecretStr, admin_credentials: dict[str, str]
) -> None:
    (tmp_path / "devices.toml").write_text(FLEET)
    port = unused_port()
    env = os.environ | {  # the test database URL is already in the environment
        "VISION_HUB_APP__PORT": str(port),
        "VISION_HUB_VISION__DEVICES_FILE": str(tmp_path / "devices.toml"),
        "VISION_HUB_SECURITY__ADMIN_PASSWORD_HASH": admin_password_hash.get_secret_value(),
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "vision_hub",
        "serve",
        cwd=tmp_path,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        async with httpx2.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            await wait_until_ready(client, process)
            token = (await client.post("/api/v1/auth/token", data=admin_credentials)).json()
            headers = {"Authorization": f"Bearer {token['access_token']}"}

            async with client.stream(
                "GET", "/api/v1/devices/front/stream", headers=headers
            ) as stream:
                chunks = stream.aiter_bytes()
                assert b"image/jpeg" in await anext(chunks)
                process.send_signal(signal.SIGTERM)
                stopping = time.monotonic()
                async for _ in chunks:  # ends when the server closes the stream
                    pass

            await asyncio.wait_for(process.wait(), 15)
            elapsed = time.monotonic() - stopping
    finally:
        if process.returncode is None:  # pragma: no cover - only when the test fails
            process.kill()
            await process.wait()

    assert process.stdout is not None
    output = (await process.stdout.read()).decode()
    # Uvicorn re-raises the signal once shutdown is complete, as process managers expect.
    assert process.returncode == -signal.SIGTERM
    assert elapsed < 8, output  # well below the 10 s graceful-shutdown backstop
    assert "shutdown_started" in output
    assert "container_stopped" in output
    assert "timeout graceful shutdown exceeded" not in output
