import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import httpx2
import numpy as np
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from vision_hub.core.config import AppConfig, Environment, SecurityConfig, Settings, VisionConfig
from vision_hub.core.errors import PROBLEM_JSON
from vision_hub.core.security import TokenService, TokenType
from vision_hub.domain.auth import Principal, Role
from vision_hub.vision.probe import ProbeResult

DEVICES = "/api/v1/devices"
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


@pytest.fixture
def settings(tmp_path: Path, admin_password_hash: SecretStr) -> Settings:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET)
    (tmp_path / "media").mkdir()
    return Settings(
        app=AppConfig(env=Environment.TEST),
        security=SecurityConfig(admin_password_hash=admin_password_hash),
        vision=VisionConfig(
            devices_file=devices, lock_file=tmp_path / "hub.lock", media_dir=tmp_path / "media"
        ),
    )


@pytest.fixture
async def admin(client: httpx2.AsyncClient, admin_credentials: dict[str, str]) -> dict[str, str]:
    response = await client.post("/api/v1/auth/token", data=admin_credentials)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def viewer(settings: Settings) -> dict[str, str]:
    token = TokenService(settings.security).issue(Principal("guest", Role.VIEWER), TokenType.ACCESS)
    return {"Authorization": f"Bearer {token.token}"}


async def eventually(check: Callable[[], Any], within: float = 5) -> None:
    async with asyncio.timeout(within):
        while not await check():
            await asyncio.sleep(0.05)


def synthetic(**extra: Any) -> dict[str, Any]:
    return {"kind": "synthetic", "fps": 20, **extra}


class TestAccess:
    async def test_requires_authentication(self, client: httpx2.AsyncClient) -> None:
        assert (await client.get(DEVICES)).status_code == 401

    async def test_viewers_can_read(
        self, client: httpx2.AsyncClient, viewer: dict[str, str]
    ) -> None:
        assert (await client.get(DEVICES, headers=viewer)).status_code == 200
        assert (await client.get(f"{DEVICES}/porch", headers=viewer)).status_code == 200

    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("POST", DEVICES, {"id": "x", "name": "x", "source": {"kind": "synthetic"}}),
            ("PATCH", f"{DEVICES}/porch", {"name": "x"}),
            ("DELETE", f"{DEVICES}/porch", None),
            ("POST", f"{DEVICES}/porch/stop", None),
            ("POST", f"{DEVICES}/test", {"source": {"kind": "synthetic"}}),
            ("POST", f"{DEVICES}/gate/test", None),
        ],
    )
    async def test_viewers_cannot_change_anything(
        self,
        client: httpx2.AsyncClient,
        viewer: dict[str, str],
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> None:
        response = await client.request(method, path, json=body, headers=viewer)

        assert response.status_code == 403


class TestReading:
    async def test_lists_devices_with_live_state(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        async def porch_online() -> bool:
            items = (await client.get(DEVICES, headers=admin)).json()["items"]
            return any(d["id"] == "porch" and d["status"] == "online" for d in items)

        await eventually(porch_online)
        body = (await client.get(DEVICES, headers=admin)).json()

        assert [d["id"] for d in body["items"]] == ["gate", "porch"]
        gate, porch = body["items"]
        assert (gate["running"], gate["status"]) == (False, "stopped")
        assert porch["running"] is True
        assert porch["source"]["kind"] == "synthetic"
        assert porch["detection"]["min_motion_area"] == 0.01
        assert body["next_cursor"] is None

    async def test_pagination(self, client: httpx2.AsyncClient, admin: dict[str, str]) -> None:
        first = (await client.get(DEVICES, params={"limit": 1}, headers=admin)).json()
        second = (
            await client.get(
                DEVICES, params={"limit": 1, "cursor": first["next_cursor"]}, headers=admin
            )
        ).json()

        assert [d["id"] for d in first["items"]] == ["gate"]
        assert [d["id"] for d in second["items"]] == ["porch"]
        assert second["next_cursor"] is None

    async def test_unknown_device_is_404(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.get(f"{DEVICES}/ghost", headers=admin)

        assert response.status_code == 404
        assert response.headers["content-type"] == PROBLEM_JSON
        assert response.json()["device_id"] == "ghost"

    async def test_snapshot_is_a_jpeg(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        async def snapshot_ready() -> bool:
            return (await client.get(f"{DEVICES}/porch/snapshot", headers=admin)).status_code == 200

        await eventually(snapshot_ready)
        response = await client.get(f"{DEVICES}/porch/snapshot", headers=admin)

        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["Cache-Control"] == "no-store"
        assert "X-Captured-At" in response.headers
        image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
        assert image is not None

    async def test_snapshot_of_a_stopped_camera_is_503(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.get(f"{DEVICES}/gate/snapshot", headers=admin)

        assert response.status_code == 503
        assert response.json()["type"] == "urn:vision-hub:problem:service-unavailable"


class TestWriting:
    async def test_create_starts_the_camera(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.post(
            DEVICES, json={"id": "garage", "name": "Garage", "source": synthetic()}, headers=admin
        )

        assert response.status_code == 201
        assert response.headers["Location"] == f"{DEVICES}/garage"
        assert response.json()["running"] is True
        assert response.json()["detection"]["min_motion_area"] == 0.01  # hub defaults

    async def test_duplicate_id_is_409(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.post(
            DEVICES, json={"id": "porch", "name": "Again", "source": synthetic()}, headers=admin
        )

        assert response.status_code == 409

    async def test_invalid_bodies_are_422(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        bad_id = {"id": "Bad Id", "name": "x", "source": synthetic()}
        typo = {"id": "x", "name": "x", "source": synthetic(), "enabeld": False}

        assert (await client.post(DEVICES, json=bad_id, headers=admin)).status_code == 422
        assert (await client.post(DEVICES, json=typo, headers=admin)).status_code == 422

    async def test_camera_passwords_are_write_only(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        source = {
            "kind": "rtsp",
            "url": "rtsp://10.0.0.9/s",
            "username": "u",
            "password": "hunter2",
        }

        created = await client.post(
            DEVICES,
            json={"id": "cam", "name": "Cam", "enabled": False, "source": source},
            headers=admin,
        )
        listed = await client.get(DEVICES, headers=admin)

        assert created.json()["source"]["has_password"] is True
        assert "password" not in created.json()["source"]
        assert "hunter2" not in created.text + listed.text

    async def test_patch_changes_only_given_fields(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.patch(f"{DEVICES}/gate", json={"name": "Side gate"}, headers=admin)

        assert response.status_code == 200
        assert response.json()["name"] == "Side gate"
        assert response.json()["enabled"] is False

    async def test_detection_config_is_replaced(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.put(
            f"{DEVICES}/porch/detection-config",
            json={"min_motion_area": 0.003, "motion_end_grace_seconds": 4},
            headers=admin,
        )

        assert response.status_code == 200
        assert response.json()["detection"]["min_motion_area"] == 0.003
        assert response.json()["running"] is True

    async def test_stop_and_start(self, client: httpx2.AsyncClient, admin: dict[str, str]) -> None:
        stopped = await client.post(f"{DEVICES}/porch/stop", headers=admin)
        started = await client.post(f"{DEVICES}/gate/start", headers=admin)

        assert (stopped.status_code, stopped.json()["running"]) == (200, False)
        assert (started.status_code, started.json()["running"]) == (202, True)

    async def test_delete(self, client: httpx2.AsyncClient, admin: dict[str, str]) -> None:
        assert (await client.delete(f"{DEVICES}/porch", headers=admin)).status_code == 204
        assert (await client.get(f"{DEVICES}/porch", headers=admin)).status_code == 404


class TestSourceTest:
    async def test_working_source(self, client: httpx2.AsyncClient, admin: dict[str, str]) -> None:
        response = await client.post(
            f"{DEVICES}/test", json={"source": synthetic(width=320, height=240)}, headers=admin
        )

        assert response.status_code == 200
        assert (response.json()["ok"], response.json()["width"]) == (True, 320)

    async def test_failing_source_is_reported_in_the_body(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.post(
            f"{DEVICES}/test",
            json={"source": {"kind": "video_file", "path": "missing.mp4"}},
            headers=admin,
        )

        assert response.status_code == 200
        assert response.json()["ok"] is False
        assert "does not exist" in response.json()["error"]

    async def test_files_outside_the_media_directory_are_refused(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.post(
            f"{DEVICES}/test",
            json={"source": {"kind": "video_file", "path": "/etc/passwd"}},
            headers=admin,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "Video files must be inside the media directory."


class TestSavedSourceTest:
    async def test_probes_the_stored_source(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.post(f"{DEVICES}/gate/test", headers=admin)

        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["width"] > 0

    async def test_uses_stored_credentials(
        self, client: httpx2.AsyncClient, admin: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probed: list[Any] = []

        def fake_probe(source: Any) -> Any:
            probed.append(source)
            return ProbeResult(ok=False, elapsed_ms=1.0, error="connection refused")

        monkeypatch.setattr("vision_hub.services.devices.probe_source", fake_probe)
        await client.post(
            DEVICES,
            json={
                "id": "door",
                "name": "Door",
                "enabled": False,
                "source": {
                    "kind": "rtsp",
                    "url": "rtsp://192.0.2.10/stream",
                    "username": "viewer",
                    "password": "s3cret",
                },
            },
            headers=admin,
        )

        response = await client.post(f"{DEVICES}/door/test", headers=admin)

        assert response.json() == {
            "ok": False,
            "elapsed_ms": 1.0,
            "width": None,
            "height": None,
            "fps": None,
            "error": "connection refused",
        }
        assert probed[0].password.get_secret_value() == "s3cret"

    async def test_unknown_device_is_404(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        assert (await client.post(f"{DEVICES}/nope/test", headers=admin)).status_code == 404


def test_openapi_responses_never_expose_passwords(app: FastAPI) -> None:
    schemas: dict[str, Any] = app.openapi()["components"]["schemas"]
    outputs = {
        name: schema for name, schema in schemas.items() if "Rtsp" in name and "Input" not in name
    }

    assert outputs, "an output schema for RTSP sources should exist"
    for schema in outputs.values():
        assert "password" not in schema["properties"]
        assert "has_password" in schema["properties"]
