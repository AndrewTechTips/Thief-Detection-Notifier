from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from starlette.types import Message, Receive, Scope, Send

from vision_hub.api.middleware import SecurityHeadersMiddleware, TrustedHostMiddleware
from vision_hub.core.config import SecurityConfig, Settings
from vision_hub.core.errors import PROBLEM_JSON
from vision_hub.main import create_app


async def ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal ASGI app; can pre-set a header to check it is not overridden."""
    headers = [(b"content-type", b"text/plain")]
    if scope["path"] == "/framed":
        headers.append((b"x-frame-options", b"SAMEORIGIN"))
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": b"ok"})


async def get(app: Any, path: str = "/", host: str = "localhost") -> httpx2.Response:
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url=f"http://{host}") as client:
        return await client.get(path)


class TestSecurityHeaders:
    async def test_api_responses_carry_defensive_headers(self, client: httpx2.AsyncClient) -> None:
        headers = (await client.get("/api/v1/health/live")).headers

        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
        assert headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert "Strict-Transport-Security" not in headers  # only in production

    async def test_error_responses_carry_them_too(self, client: httpx2.AsyncClient) -> None:
        headers = (await client.get("/nope")).headers

        assert headers["X-Content-Type-Options"] == "nosniff"

    async def test_docs_are_served_without_the_strict_csp(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/docs")

        assert response.status_code == 200
        assert "Content-Security-Policy" not in response.headers
        assert response.headers["X-Frame-Options"] == "DENY"

    async def test_hsts_when_enabled(self) -> None:
        response = await get(SecurityHeadersMiddleware(ok_app, hsts=True))

        assert response.headers["Strict-Transport-Security"].startswith("max-age=")

    async def test_existing_headers_are_not_overridden(self) -> None:
        response = await get(SecurityHeadersMiddleware(ok_app), "/framed")

        assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


class TestTrustedHost:
    @pytest.mark.parametrize(
        ("allowed", "host", "accepted"),
        [
            (["localhost"], "localhost", True),
            (["localhost"], "LOCALHOST:8000", True),
            (["localhost"], "evil.example", False),
            (["*.example.com"], "cam.example.com", True),
            (["*.example.com"], "example.com.evil.net", False),
            (["[::1]"], "[::1]:8000", True),
            (["*"], "anything.at.all", True),
        ],
    )
    async def test_host_matching(self, allowed: list[str], host: str, *, accepted: bool) -> None:
        response = await get(TrustedHostMiddleware(ok_app, allowed_hosts=allowed), host=host)

        assert (response.status_code == 200) is accepted

    async def test_rejection_is_a_problem_with_request_id(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/api/v1/health/live", headers={"Host": "evil.example"})

        assert response.status_code == 400
        assert response.headers["content-type"] == PROBLEM_JSON
        assert response.json()["detail"] == "Invalid host header."
        assert response.json()["request_id"] == response.headers["X-Request-ID"]

    async def test_websockets_with_bad_host_are_closed(self) -> None:
        sent: list[dict[str, Any]] = []

        async def send(message: Message) -> None:
            sent.append(dict(message))

        async def receive() -> Message:
            return {"type": "websocket.connect"}

        middleware = TrustedHostMiddleware(ok_app, allowed_hosts=["localhost"])
        scope = {"type": "websocket", "path": "/ws", "headers": [(b"host", b"evil.example")]}
        await middleware(scope, receive, send)

        assert sent == [{"type": "websocket.close", "code": 1008}]


class TestCors:
    @pytest.fixture
    def settings(self, settings_factory: Callable[..., Settings]) -> Settings:
        return settings_factory(security=SecurityConfig(cors_origins=["https://dashboard.example"]))

    async def preflight(self, client: httpx2.AsyncClient, origin: str) -> httpx2.Response:
        return await client.options(
            "/api/v1/auth/me",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )

    async def test_allowed_origin_preflight(self, client: httpx2.AsyncClient) -> None:
        response = await self.preflight(client, "https://dashboard.example")

        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == "https://dashboard.example"
        assert "Access-Control-Allow-Credentials" not in response.headers

    async def test_unknown_origin_is_not_allowed(self, client: httpx2.AsyncClient) -> None:
        response = await self.preflight(client, "https://evil.example")

        assert "Access-Control-Allow-Origin" not in response.headers

    async def test_request_id_is_exposed_to_browsers(self, client: httpx2.AsyncClient) -> None:
        response = await client.get(
            "/api/v1/health/live", headers={"Origin": "https://dashboard.example"}
        )

        assert "X-Request-ID" in response.headers["Access-Control-Expose-Headers"]


async def test_production_sends_hsts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_HUB_APP__ENV", "prod")
    monkeypatch.setenv("VISION_HUB_SECURITY__JWT_SECRET", "p" * 48)
    monkeypatch.setenv(
        "VISION_HUB_SECURITY__ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaA",
    )
    monkeypatch.setenv("VISION_HUB_DB__PASSWORD", "db-password")

    async with LifespanManager(create_app()) as manager:
        response = await get(manager.app, "/api/v1/health/live")

    assert response.status_code == 200
    assert response.headers["Strict-Transport-Security"] == "max-age=63072000; includeSubDomains"
