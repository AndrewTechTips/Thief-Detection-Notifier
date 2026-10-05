"""Web push through the API: subscribing browsers, and motion reaching them as notifications."""

import asyncio
import functools
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from pydantic import SecretStr
from tests.fakes.webpush import Browser, FakePushService

from vision_hub.core import container as container_module
from vision_hub.core.config import (
    AppConfig,
    Environment,
    PushConfig,
    SecurityConfig,
    Settings,
    VisionConfig,
)
from vision_hub.core.security import TokenService, TokenType
from vision_hub.domain.auth import Principal, Role
from vision_hub.domain.push import subscription_id
from vision_hub.infra.notifiers.webpush import WebPushNotifier, b64url_decode, b64url_encode
from vision_hub.main import create_app

PUSH = "/api/v1/push"
SUBSCRIPTIONS = f"{PUSH}/subscriptions"

FLEET = """
[[devices]]
id = "front"
name = "Front door"
[devices.source]
kind = "synthetic"
fps = 20
visit_every_seconds = 1.5
visit_seconds = 0.6
[devices.detection]
warmup_frames = 3
motion_end_grace_seconds = 0.3
"""


@pytest.fixture
def push_service(monkeypatch: pytest.MonkeyPatch) -> FakePushService:
    """Stands in for the browsers' push services: the hub never reaches the internet."""
    service = FakePushService()
    monkeypatch.setattr(
        container_module, "WebPushNotifier", functools.partial(WebPushNotifier, transport=service)
    )
    return service


@pytest.fixture
def settings(tmp_path: Path, admin_password_hash: SecretStr) -> Settings:
    return Settings(
        app=AppConfig(env=Environment.TEST),
        security=SecurityConfig(admin_password_hash=admin_password_hash),
        vision=VisionConfig(lock_file=tmp_path / "hub.lock"),
        push=PushConfig(subject="mailto:owner@example.com"),
    )


@pytest.fixture
async def admin(client: httpx2.AsyncClient, admin_credentials: dict[str, str]) -> dict[str, str]:
    response = await client.post("/api/v1/auth/token", data=admin_credentials)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@asynccontextmanager
async def running(settings: Settings) -> AsyncIterator[httpx2.AsyncClient]:
    async with LifespanManager(create_app(settings)) as manager:
        transport = httpx2.ASGITransport(app=manager.app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://localhost") as http:
            yield http


def bearer(settings: Settings, username: str, role: Role = Role.VIEWER) -> dict[str, str]:
    token = TokenService(settings.security).issue(Principal(username, role), TokenType.ACCESS)
    return {"Authorization": f"Bearer {token.token}"}


def subscription_body(browser: Browser) -> dict[str, object]:
    """What ``PushSubscription.toJSON()`` gives in a browser."""
    return {
        "endpoint": browser.endpoint,
        "expirationTime": None,
        "keys": {"p256dh": b64url_encode(browser.public), "auth": b64url_encode(browser.auth)},
    }


class TestSubscribing:
    async def test_the_key_to_subscribe_with(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.get(PUSH, headers=admin)

        assert response.status_code == 200
        key = b64url_decode(response.json()["public_key"])
        assert len(key) == 65
        assert key[0] == 4

    async def test_the_key_stays_the_same_across_restarts(self, settings: Settings) -> None:
        keys = []
        for _ in range(2):
            async with running(settings) as http:
                response = await http.get(PUSH, headers=bearer(settings, "admin", Role.ADMIN))
                keys.append(response.json()["public_key"])

        assert keys[0] == keys[1]

    async def test_a_browser_subscribes_and_unsubscribes(
        self, client: httpx2.AsyncClient, admin: dict[str, str]
    ) -> None:
        browser = Browser()

        created = await client.post(SUBSCRIPTIONS, json=subscription_body(browser), headers=admin)
        again = await client.post(SUBSCRIPTIONS, json=subscription_body(browser), headers=admin)
        removed = await client.delete(f"{SUBSCRIPTIONS}/{browser.id}", headers=admin)
        missing = await client.delete(f"{SUBSCRIPTIONS}/{browser.id}", headers=admin)

        assert created.status_code == again.status_code == 201
        assert created.json()["id"] == again.json()["id"] == subscription_id(browser.endpoint)
        assert removed.status_code == 204
        assert missing.status_code == 404

    async def test_only_your_own_browsers_can_be_unsubscribed(
        self, client: httpx2.AsyncClient, admin: dict[str, str], settings: Settings
    ) -> None:
        browser = Browser()
        await client.post(SUBSCRIPTIONS, json=subscription_body(browser), headers=admin)

        response = await client.delete(
            f"{SUBSCRIPTIONS}/{browser.id}", headers=bearer(settings, "mallory", Role.ADMIN)
        )

        assert response.status_code == 404

    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://169.254.169.254/latest/meta-data/",
            "http://fcm.googleapis.com/fcm/send/x",
            "https://hub.internal/api/v1/devices",
        ],
    )
    async def test_endpoints_off_known_push_services_are_refused(
        self, client: httpx2.AsyncClient, admin: dict[str, str], endpoint: str
    ) -> None:
        body = subscription_body(Browser()) | {"endpoint": endpoint}

        response = await client.post(SUBSCRIPTIONS, json=body, headers=admin)

        assert response.status_code == 400
        assert response.json()["type"] == "urn:vision-hub:problem:push-service-not-allowed"

    @pytest.mark.parametrize(
        "keys",
        [
            {"p256dh": b64url_encode(b"\x04" * 64), "auth": b64url_encode(b"a" * 16)},
            {"p256dh": b64url_encode(b"\x03" * 65), "auth": b64url_encode(b"a" * 16)},
            {"p256dh": b64url_encode(b"\x04" * 65), "auth": b64url_encode(b"a" * 8)},
            {"p256dh": "not base64!", "auth": b64url_encode(b"a" * 16)},
        ],
    )
    async def test_malformed_keys_are_refused(
        self, client: httpx2.AsyncClient, admin: dict[str, str], keys: dict[str, str]
    ) -> None:
        body = subscription_body(Browser()) | {"keys": keys}

        response = await client.post(SUBSCRIPTIONS, json=body, headers=admin)

        assert response.status_code == 422

    async def test_needs_a_signed_in_user(self, client: httpx2.AsyncClient) -> None:
        response = await client.post(SUBSCRIPTIONS, json=subscription_body(Browser()))

        assert response.status_code == 401

    async def test_turned_off_on_the_hub(
        self, tmp_path: Path, admin_password_hash: SecretStr
    ) -> None:
        settings = Settings(
            app=AppConfig(env=Environment.TEST),
            security=SecurityConfig(admin_password_hash=admin_password_hash),
            vision=VisionConfig(lock_file=tmp_path / "hub.lock"),
            push=PushConfig(enabled=False),
        )
        async with running(settings) as http:
            response = await http.get(PUSH, headers=bearer(settings, "admin", Role.ADMIN))

        assert response.status_code == 404
        assert not (tmp_path / "data" / "vapid.key").exists()


class TestDelivery:
    @pytest.fixture
    def app(self, settings: Settings, push_service: FakePushService) -> FastAPI:
        return create_app(settings)

    async def test_a_test_notification_reaches_your_browsers(
        self,
        client: httpx2.AsyncClient,
        admin: dict[str, str],
        settings: Settings,
        push_service: FakePushService,
    ) -> None:
        mine, theirs = Browser(), Browser()
        await client.post(SUBSCRIPTIONS, json=subscription_body(mine), headers=admin)
        await client.post(
            SUBSCRIPTIONS, json=subscription_body(theirs), headers=bearer(settings, "admin")
        )
        # Same browser, other account: the subscription moves with whoever subscribed last.
        await client.post(SUBSCRIPTIONS, json=subscription_body(theirs), headers=admin)

        response = await client.post(f"{PUSH}/test", headers=admin)

        assert response.json() == {"delivered": 2, "failed": 0}
        _, headers, body = push_service.request_for(mine)
        assert json.loads(mine.decrypt(body)) == {"kind": "test"}
        assert "mailto:owner@example.com" not in headers["Authorization"]  # inside the JWT


@pytest.fixture
async def hub(
    tmp_path: Path, admin_password_hash: SecretStr, push_service: FakePushService
) -> AsyncIterator[httpx2.AsyncClient]:
    devices = tmp_path / "devices.toml"
    devices.write_text(FLEET)
    settings = Settings(
        app=AppConfig(env=Environment.TEST),
        security=SecurityConfig(admin_password_hash=admin_password_hash),
        vision=VisionConfig(
            devices_file=devices, lock_file=tmp_path / "hub.lock", alert_cooldown_seconds=0
        ),
    )
    async with running(settings) as http:
        yield http


async def test_motion_reaches_subscribed_browsers_with_a_working_snapshot_link(
    hub: httpx2.AsyncClient, admin_credentials: dict[str, str], push_service: FakePushService
) -> None:
    token = (await hub.post("/api/v1/auth/token", data=admin_credentials)).json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    phone = Browser()
    await hub.post(SUBSCRIPTIONS, json=subscription_body(phone), headers=headers)

    async with asyncio.timeout(15):
        while not push_service.requests:
            await asyncio.sleep(0.05)

    url, request_headers, body = push_service.requests[0]
    alert = json.loads(phone.decrypt(body))
    assert url == phone.endpoint
    assert request_headers["Urgency"] == "high"
    assert alert["kind"] == "motion"
    assert (alert["device_id"], alert["device_name"]) == ("front", "Front door")
    assert alert["duration_seconds"] > 0
    # The service worker loads the picture without a token: the link is signed.
    picture = await hub.get(alert["image"])
    assert picture.status_code == 200
    assert picture.headers["content-type"] == "image/jpeg"
