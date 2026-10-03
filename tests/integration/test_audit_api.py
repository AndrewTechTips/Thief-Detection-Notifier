"""Admin changes through the API land in the audit trail, readable by admins only."""

from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from vision_hub.core.config import AppConfig, Environment, SecurityConfig, Settings, VisionConfig
from vision_hub.core.security import TokenService, TokenType
from vision_hub.domain.auth import Principal, Role
from vision_hub.schemas.pagination import encode_cursor

AUDIT = "/api/v1/audit"
DEVICES = "/api/v1/devices"


@pytest.fixture
def settings(tmp_path: Path, admin_password_hash: SecretStr) -> Settings:
    return Settings(
        app=AppConfig(env=Environment.TEST),
        security=SecurityConfig(admin_password_hash=admin_password_hash),
        vision=VisionConfig(lock_file=tmp_path / "hub.lock"),
    )


@pytest.fixture
async def admin(client: httpx2.AsyncClient, admin_credentials: dict[str, str]) -> dict[str, str]:
    response = await client.post("/api/v1/auth/token", data=admin_credentials)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def bearer(settings: Settings, username: str, role: Role) -> dict[str, str]:
    token = TokenService(settings.security).issue(Principal(username, role), TokenType.ACCESS)
    return {"Authorization": f"Bearer {token.token}"}


async def add_device(client: httpx2.AsyncClient, headers: dict[str, str], device_id: str) -> str:
    response = await client.post(
        DEVICES,
        json={
            "id": device_id,
            "name": device_id,
            "enabled": False,
            "source": {"kind": "synthetic"},
        },
        headers=headers,
    )
    assert response.status_code == 201
    return response.headers["X-Request-ID"]


async def test_device_changes_are_audited(
    client: httpx2.AsyncClient, admin: dict[str, str]
) -> None:
    request_id = await add_device(client, admin, "porch")
    await client.patch(f"{DEVICES}/porch", json={"name": "Front porch"}, headers=admin)
    await client.delete(f"{DEVICES}/porch", headers=admin)

    response = await client.get(AUDIT, params={"target_type": "device"}, headers=admin)

    assert response.status_code == 200
    entries = response.json()["items"]
    assert [(e["action"], e["target_id"], e["actor"]) for e in entries] == [
        ("device.deleted", "porch", "admin"),
        ("device.updated", "porch", "admin"),
        ("device.created", "porch", "admin"),
    ]
    assert entries[1]["details"] == {"fields": ["name"]}
    assert entries[2]["request_id"] == request_id  # matches the request's log lines


async def test_filters_and_pagination(
    client: httpx2.AsyncClient, admin: dict[str, str], settings: Settings
) -> None:
    await add_device(client, admin, "porch")
    await add_device(client, bearer(settings, "bob", Role.ADMIN), "gate")

    by_bob = (await client.get(AUDIT, params={"actor": "bob"}, headers=admin)).json()
    gate = (await client.get(AUDIT, params={"target_id": "gate"}, headers=admin)).json()
    first = (await client.get(AUDIT, params={"limit": 1}, headers=admin)).json()
    rest = (
        await client.get(AUDIT, params={"limit": 50, "cursor": first["next_cursor"]}, headers=admin)
    ).json()

    assert [e["target_id"] for e in by_bob["items"]] == ["gate"]
    assert [e["actor"] for e in gate["items"]] == ["bob"]
    ids = [e["id"] for e in first["items"] + rest["items"]]
    assert len(ids) == len(set(ids)) == 3  # both devices and the bootstrapped admin
    assert rest["next_cursor"] is None


async def test_admins_only(client: httpx2.AsyncClient, settings: Settings) -> None:
    assert (await client.get(AUDIT)).status_code == 401
    viewer = await client.get(AUDIT, headers=bearer(settings, "guest", Role.VIEWER))
    assert viewer.status_code == 403


@pytest.mark.parametrize(
    "position",
    [{"id": "x"}, {"at": "yesterday", "id": "x"}, {"at": "2026-10-03T10:00:00", "id": "x"}],
)
async def test_cursors_without_a_valid_position_are_rejected(
    client: httpx2.AsyncClient, admin: dict[str, str], position: dict[str, str]
) -> None:
    response = await client.get(AUDIT, params={"cursor": encode_cursor(position)}, headers=admin)

    assert response.status_code == 400
    assert response.json()["type"].endswith("invalid-cursor")
