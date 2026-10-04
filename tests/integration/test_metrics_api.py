"""GET /metrics: Prometheus text format, scrape token or admin, per-camera health."""

import asyncio
from pathlib import Path

import cv2
import httpx2
import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts
from prometheus_client.parser import text_string_to_metric_families
from pydantic import SecretStr

from vision_hub.core.config import (
    AppConfig,
    Environment,
    MetricsConfig,
    SecurityConfig,
    Settings,
    VisionConfig,
)
from vision_hub.core.security import TokenService, TokenType
from vision_hub.domain.auth import Principal, Role
from vision_hub.main import create_app

SCRAPE_TOKEN = "s" * 40
FLEET = """
[[devices]]
id = "porch"
name = "Porch"
[devices.source]
kind = "synthetic"
fps = 20
"""


@pytest.fixture
def settings(tmp_path: Path, admin_password_hash: SecretStr) -> Settings:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET)
    return Settings(
        app=AppConfig(env=Environment.TEST),
        security=SecurityConfig(admin_password_hash=admin_password_hash),
        vision=VisionConfig(devices_file=devices, lock_file=tmp_path / "hub.lock"),
        metrics=MetricsConfig(token=SecretStr(SCRAPE_TOKEN)),
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def samples(text: str) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
    }


async def scrape(
    client: httpx2.AsyncClient,
) -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
    response = await client.get("/metrics", headers=bearer(SCRAPE_TOKEN))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=")
    assert response.headers["cache-control"] == "no-store"
    return samples(response.text)


async def test_camera_health_and_http_metrics(client: httpx2.AsyncClient) -> None:
    device = (("device", "porch"),)
    async with asyncio.timeout(10):
        while (await scrape(client)).get(
            ("vision_hub_camera_frames_analysed_total", device), 0
        ) < 5:
            await asyncio.sleep(0.1)
    await client.get("/api/v1/devices/porch")  # 401, but counted under its route template
    await client.get("/nope")

    metrics = await scrape(client)

    assert metrics[("vision_hub_camera_status", (*device, ("status", "online")))] == 1
    assert metrics[("vision_hub_camera_status", (*device, ("status", "stopped")))] == 0
    assert metrics[("vision_hub_camera_live_viewers", device)] == 0
    assert 0 <= metrics[("vision_hub_camera_last_frame_age_seconds", device)] < 5
    assert metrics[("vision_hub_camera_processing_seconds_count", device)] >= 5
    assert metrics[("vision_hub_websocket_clients", ())] == 0
    assert metrics[("vision_hub_alerts_pending", ())] == 0
    route = (("method", "GET"), ("route", "/api/v1/devices/{device_id}"), ("status", "401"))
    assert metrics[("vision_hub_http_requests_total", route)] == 1
    unmatched = (("method", "GET"), ("route", "unmatched"), ("status", "404"))
    assert metrics[("vision_hub_http_requests_total", unmatched)] == 1
    assert ("vision_hub_event_loop_lag_seconds_count", ()) in metrics


async def test_access_needs_the_scrape_token_or_an_admin(
    client: httpx2.AsyncClient, settings: Settings
) -> None:
    def issued(role: Role) -> str:
        principal = Principal("someone", role)
        return TokenService(settings.security).issue(principal, TokenType.ACCESS).token

    assert (await client.get("/metrics")).status_code == 401
    assert (await client.get("/metrics", headers=bearer("x" * 40))).status_code == 401
    assert (await client.get("/metrics", headers=bearer(issued(Role.VIEWER)))).status_code == 403
    assert (await client.get("/metrics", headers=bearer(issued(Role.ADMIN)))).status_code == 200


async def test_scrapers_may_use_the_container_ip_as_host(client: httpx2.AsyncClient) -> None:
    response = await client.get(
        "/metrics", headers={**bearer(SCRAPE_TOKEN), "Host": "172.18.0.5:8000"}
    )

    assert response.status_code == 200


async def test_metrics_can_be_disabled(settings: Settings) -> None:
    app: FastAPI = create_app(settings.model_copy(update={"metrics": MetricsConfig(enabled=False)}))

    assert "/metrics" not in {route.path for route in iter_route_contexts(app.routes)}


async def test_opencv_runs_without_its_own_thread_pool(client: httpx2.AsyncClient) -> None:
    assert cv2.getNumThreads() == 1  # setNumThreads(0): sequential, on every backend
