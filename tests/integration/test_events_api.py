"""End to end: a live synthetic camera produces events that are stored and served."""

import asyncio
from pathlib import Path
from typing import Any

import cv2
import httpx2
import numpy as np
import pytest
from asgi_lifespan import LifespanManager
from pydantic import SecretStr

from vision_hub.core.config import (
    AppConfig,
    ClipConfig,
    Environment,
    SecurityConfig,
    Settings,
    VisionConfig,
)
from vision_hub.core.security import TokenService, TokenType
from vision_hub.domain.auth import Principal, Role
from vision_hub.main import create_app
from vision_hub.schemas.pagination import encode_cursor

EVENTS = "/api/v1/events"
FLEET = """
[[devices]]
id = "porch"
name = "Porch"
[devices.source]
kind = "synthetic"
fps = 20
visit_every_seconds = 2
visit_seconds = 0.6
[devices.detection]
warmup_frames = 3
motion_end_grace_seconds = 0.3
"""


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
async def bearer(client: httpx2.AsyncClient, admin_credentials: dict[str, str]) -> dict[str, str]:
    response = await client.post("/api/v1/auth/token", data=admin_credentials)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def wait_for_events(
    client: httpx2.AsyncClient, headers: dict[str, str], count: int = 1
) -> list[dict[str, Any]]:
    async with asyncio.timeout(15):
        while True:
            items: list[dict[str, Any]] = (await client.get(EVENTS, headers=headers)).json()[
                "items"
            ]
            complete = [item for item in items if item["complete"]]
            if len(complete) >= count:
                return complete
            await asyncio.sleep(0.2)


def decode(content: bytes) -> Any:
    return cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)


async def test_recorded_events_are_listed_with_signed_snapshots(
    client: httpx2.AsyncClient, bearer: dict[str, str]
) -> None:
    [event, *_] = await wait_for_events(client, bearer)

    assert event["device_id"] == "porch"
    assert event["interrupted"] is False
    assert event["duration_seconds"] > 0
    assert event["boxes"]
    links = {link["kind"]: link["url"] for link in event["snapshots"]}
    assert set(links) == {"annotated", "clean", "thumbnail"}

    thumbnail = await client.get(links["thumbnail"])  # no Authorization header at all
    clean = await client.get(links["clean"])

    assert thumbnail.status_code == 200
    assert thumbnail.headers["content-type"] == "image/jpeg"
    assert "immutable" in thumbnail.headers["cache-control"]
    assert decode(thumbnail.content).shape[1] == 320
    assert decode(clean.content).shape == (480, 640, 3)


async def test_signed_links_cannot_be_tampered_with(
    client: httpx2.AsyncClient, bearer: dict[str, str]
) -> None:
    [event, *_] = await wait_for_events(client, bearer)
    url = next(link["url"] for link in event["snapshots"] if link["kind"] == "thumbnail")

    other_kind = await client.get(url.replace("kind=thumbnail", "kind=clean"))
    bad_signature = await client.get(url[:-2] + "xx")
    unsigned = await client.get(f"{EVENTS}/{event['id']}/snapshot")

    assert other_kind.status_code == 401
    assert bad_signature.status_code == 401
    assert unsigned.status_code == 401


async def test_bearer_token_also_works_for_snapshots(
    client: httpx2.AsyncClient, bearer: dict[str, str]
) -> None:
    [event, *_] = await wait_for_events(client, bearer)

    response = await client.get(f"{EVENTS}/{event['id']}/snapshot?kind=clean", headers=bearer)

    assert response.status_code == 200


async def test_event_detail_filters_and_pagination(
    client: httpx2.AsyncClient, bearer: dict[str, str], settings: Settings
) -> None:
    events = await wait_for_events(client, bearer, count=2)
    viewer = TokenService(settings.security).issue(
        Principal("guest", Role.VIEWER), TokenType.ACCESS
    )
    viewer_headers = {"Authorization": f"Bearer {viewer.token}"}

    detail = await client.get(f"{EVENTS}/{events[0]['id']}", headers=viewer_headers)
    first = (await client.get(EVENTS, params={"limit": 1}, headers=bearer)).json()
    second = (
        await client.get(
            EVENTS, params={"limit": 1, "cursor": first["next_cursor"]}, headers=bearer
        )
    ).json()
    other_device = (await client.get(EVENTS, params={"device_id": "gate"}, headers=bearer)).json()

    assert detail.status_code == 200
    assert detail.json()["id"] == events[0]["id"]
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["items"][0]["started_at"] >= second["items"][0]["started_at"]
    assert other_device["items"] == []


async def test_errors(client: httpx2.AsyncClient, bearer: dict[str, str]) -> None:
    unknown = "01a10000-0000-7000-8000-000000000000"

    assert (await client.get(EVENTS)).status_code == 401
    assert (await client.get(f"{EVENTS}/{unknown}", headers=bearer)).status_code == 404
    snapshot = await client.get(f"{EVENTS}/{unknown}/snapshot", headers=bearer)
    assert snapshot.status_code == 404
    assert (await client.get(f"{EVENTS}/{unknown}/clip", headers=bearer)).status_code == 404
    assert (
        await client.get(EVENTS, params={"since": "not-a-date"}, headers=bearer)
    ).status_code == 422
    wrong_cursor = encode_cursor({"at": "2026-10-03T10:00:00+00:00", "id": unknown})  # audit's
    assert (
        await client.get(EVENTS, params={"cursor": wrong_cursor}, headers=bearer)
    ).status_code == 400


async def test_events_come_with_a_playable_clip(
    client: httpx2.AsyncClient, bearer: dict[str, str], tmp_path: Path
) -> None:
    [event, *_] = await wait_for_events(client, bearer)
    clip = event["clip"]
    assert clip["content_type"] == "video/webm"

    response = await client.get(clip["url"])  # signed: what a <video> tag sends

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/webm"
    assert response.headers["accept-ranges"] == "bytes"
    assert "immutable" in response.headers["cache-control"]
    assert int(response.headers["content-length"]) == clip["size_bytes"]
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('inline; filename="motion-porch-')
    assert disposition.endswith('.webm"')
    video = tmp_path / "clip.webm"
    video.write_bytes(response.content)
    capture = cv2.VideoCapture(str(video))
    ok, first = capture.read()
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    assert ok
    assert first.shape == (480, 640, 3)
    assert frames >= 10


async def test_clips_can_be_fetched_in_ranges(
    client: httpx2.AsyncClient, bearer: dict[str, str]
) -> None:
    # Browsers seek with Range requests; Safari refuses to play video without them.
    [event, *_] = await wait_for_events(client, bearer)
    url = event["clip"]["url"]
    whole = (await client.get(url)).content

    part = await client.get(url, headers={"Range": "bytes=100-199"})
    beyond = await client.get(url, headers={"Range": f"bytes={len(whole) + 10}-"})

    assert part.status_code == 206
    assert part.content == whole[100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{len(whole)}"
    assert beyond.status_code == 416


async def test_clip_links_are_signed_for_that_event_only(
    client: httpx2.AsyncClient, bearer: dict[str, str]
) -> None:
    first, second, *_ = await wait_for_events(client, bearer, count=2)
    url = first["clip"]["url"]

    other_event = await client.get(url.replace(first["id"], second["id"]))
    bad_signature = await client.get(url[:-2] + "xx")
    extra_parameter = await client.get(url + "&kind=clean")
    with_token = await client.get(f"{EVENTS}/{first['id']}/clip", headers=bearer)

    assert other_event.status_code == 401
    assert bad_signature.status_code == 401
    assert extra_parameter.status_code == 401  # the signature covers every parameter
    assert with_token.status_code == 200


async def test_the_snapshot_endpoint_serves_images_only(
    client: httpx2.AsyncClient, bearer: dict[str, str]
) -> None:
    [event, *_] = await wait_for_events(client, bearer)

    response = await client.get(f"{EVENTS}/{event['id']}/snapshot?kind=clip", headers=bearer)

    assert response.status_code == 422


async def test_with_clips_turned_off_events_have_none(
    tmp_path: Path, admin_password_hash: SecretStr, admin_credentials: dict[str, str]
) -> None:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET)
    settings = Settings(
        app=AppConfig(env=Environment.TEST),
        security=SecurityConfig(admin_password_hash=admin_password_hash),
        vision=VisionConfig(devices_file=devices, lock_file=tmp_path / "hub.lock"),
        clips=ClipConfig(enabled=False),
    )
    async with LifespanManager(create_app(settings)) as manager:
        transport = httpx2.ASGITransport(app=manager.app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as http:
            token = (await http.post("/api/v1/auth/token", data=admin_credentials)).json()
            headers = {"Authorization": f"Bearer {token['access_token']}"}
            [event, *_] = await wait_for_events(http, headers)
            missing = await http.get(f"{EVENTS}/{event['id']}/clip", headers=headers)

    assert event["clip"] is None
    assert missing.status_code == 404
