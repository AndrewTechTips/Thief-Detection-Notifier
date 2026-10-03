from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import SecretStr

from vision_hub.api.deps import AdminPrincipal, get_current_principal
from vision_hub.api.v1.router import PUBLIC_PATHS
from vision_hub.core.config import SecurityConfig, Settings
from vision_hub.core.errors import PROBLEM_JSON
from vision_hub.core.security import TokenService, TokenType
from vision_hub.domain.auth import Principal, Role

type LogRecords = Callable[[], list[dict[str, Any]]]

TOKEN_URL = "/api/v1/auth/token"
ME_URL = "/api/v1/auth/me"


@pytest.fixture
def settings(settings_factory: Callable[..., Settings], admin_password_hash: SecretStr) -> Settings:
    return settings_factory(
        security=SecurityConfig(admin_password_hash=admin_password_hash, auth_rate_limit="3/minute")
    )


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    @app.delete("/api/v1/test/admin-only")
    async def admin_only(principal: AdminPrincipal) -> dict[str, str]:
        return {"by": principal.username}

    return app


async def login(client: httpx2.AsyncClient, credentials: dict[str, str]) -> dict[str, Any]:
    response = await client.post(TOKEN_URL, data=credentials)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestLogin:
    async def test_returns_oauth2_token_response(
        self, client: httpx2.AsyncClient, admin_credentials: dict[str, str]
    ) -> None:
        response = await client.post(TOKEN_URL, data=admin_credentials)

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 15 * 60
        assert body["refresh_expires_in"] == 7 * 24 * 3600
        assert {"access_token", "refresh_token"} <= body.keys()
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Pragma"] == "no-cache"

    @pytest.mark.parametrize(
        "credentials",
        [{"username": "admin", "password": "wrong"}, {"username": "ghost", "password": "x"}],
    )
    async def test_bad_credentials_are_a_401_problem(
        self, client: httpx2.AsyncClient, credentials: dict[str, str]
    ) -> None:
        response = await client.post(TOKEN_URL, data=credentials)

        assert response.status_code == 401
        assert response.headers["content-type"] == PROBLEM_JSON
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["detail"] == "Incorrect username or password."

    async def test_json_body_is_rejected(
        self, client: httpx2.AsyncClient, admin_credentials: dict[str, str]
    ) -> None:
        response = await client.post(TOKEN_URL, json=admin_credentials)

        assert response.status_code == 422

    async def test_is_rate_limited_per_client(
        self, client: httpx2.AsyncClient, admin_credentials: dict[str, str]
    ) -> None:
        wrong = {**admin_credentials, "password": "wrong"}
        for _ in range(3):
            assert (await client.post(TOKEN_URL, data=wrong)).status_code == 401

        response = await client.post(TOKEN_URL, data=admin_credentials)

        assert response.status_code == 429
        assert response.json()["type"] == "urn:vision-hub:problem:rate-limited"
        assert int(response.headers["Retry-After"]) >= 1

    async def test_password_never_reaches_the_logs(
        self,
        client: httpx2.AsyncClient,
        admin_credentials: dict[str, str],
        log_records: LogRecords,
    ) -> None:
        await client.post(TOKEN_URL, data={**admin_credentials, "password": "leaky-guess"})
        await client.post(TOKEN_URL, data=admin_credentials)

        logs = str(log_records())
        assert "leaky-guess" not in logs
        assert admin_credentials["password"] not in logs
        assert "login_failed" in logs
        assert "login_succeeded" in logs


class TestProtectedRoutes:
    async def test_missing_token_is_401(self, client: httpx2.AsyncClient) -> None:
        response = await client.get(ME_URL)

        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["type"] == "urn:vision-hub:problem:unauthenticated"

    async def test_valid_token_identifies_the_caller(
        self, client: httpx2.AsyncClient, admin_credentials: dict[str, str]
    ) -> None:
        tokens = await login(client, admin_credentials)

        response = await client.get(ME_URL, headers=bearer(tokens["access_token"]))

        assert response.status_code == 200
        assert response.json() == {"username": "admin", "role": "admin"}

    async def test_refresh_token_is_not_an_access_token(
        self, client: httpx2.AsyncClient, admin_credentials: dict[str, str]
    ) -> None:
        tokens = await login(client, admin_credentials)

        response = await client.get(ME_URL, headers=bearer(tokens["refresh_token"]))

        assert response.status_code == 401

    async def test_malformed_token_is_401(self, client: httpx2.AsyncClient) -> None:
        assert (await client.get(ME_URL, headers=bearer("garbage"))).status_code == 401

    async def test_access_log_records_the_user(
        self,
        client: httpx2.AsyncClient,
        admin_credentials: dict[str, str],
        log_records: LogRecords,
    ) -> None:
        tokens = await login(client, admin_credentials)

        await client.get(ME_URL, headers=bearer(tokens["access_token"]))

        access = [r for r in log_records() if r["event"] == "request" and r["path"] == ME_URL]
        assert access[-1]["user"] == "admin"
        assert tokens["access_token"] not in str(log_records())


class TestRoles:
    async def test_admin_passes_role_check(
        self, client: httpx2.AsyncClient, admin_credentials: dict[str, str]
    ) -> None:
        tokens = await login(client, admin_credentials)

        response = await client.delete(
            "/api/v1/test/admin-only", headers=bearer(tokens["access_token"])
        )

        assert response.status_code == 200

    async def test_viewer_is_forbidden(
        self, client: httpx2.AsyncClient, settings: Settings
    ) -> None:
        viewer = TokenService(settings.security).issue(
            Principal("guest", Role.VIEWER), TokenType.ACCESS
        )

        response = await client.delete("/api/v1/test/admin-only", headers=bearer(viewer.token))

        assert response.status_code == 403
        assert response.json()["type"] == "urn:vision-hub:problem:permission-denied"


class TestRefreshAndLogout:
    async def test_refresh_rotates_tokens(
        self, client: httpx2.AsyncClient, admin_credentials: dict[str, str]
    ) -> None:
        first = await login(client, admin_credentials)

        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )
        reused = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
        )

        assert response.status_code == 200
        assert response.json()["refresh_token"] != first["refresh_token"]
        assert reused.status_code == 401

    async def test_logout_revokes_the_refresh_token(
        self, client: httpx2.AsyncClient, admin_credentials: dict[str, str]
    ) -> None:
        tokens = await login(client, admin_credentials)
        body = {"refresh_token": tokens["refresh_token"]}

        logout = await client.post("/api/v1/auth/logout", json=body)
        refresh = await client.post("/api/v1/auth/refresh", json=body)

        assert logout.status_code == 204
        assert refresh.status_code == 401

    async def test_refresh_body_rejects_unknown_fields(self, client: httpx2.AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": "x", "extra": 1}
        )

        assert response.status_code == 422


def test_every_non_public_route_requires_authentication(app: FastAPI) -> None:
    """Guards the secure-by-default rule as new routers are added."""

    def dependency_calls(route: APIRoute) -> set[object]:
        pending = [route.dependant]
        seen: set[object] = set()
        while pending:
            dependant = pending.pop()
            seen.add(dependant.call)
            pending.extend(dependant.dependencies)
        return seen

    unprotected = [
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith("/api/v1")
        and route.path not in PUBLIC_PATHS
        and get_current_principal not in dependency_calls(route)
    ]

    assert unprotected == []


def test_openapi_advertises_the_oauth2_password_flow(app: FastAPI) -> None:
    schema = app.openapi()

    scheme = schema["components"]["securitySchemes"]["OAuth2PasswordBearer"]
    assert scheme["flows"]["password"]["tokenUrl"] == TOKEN_URL
    assert schema["paths"][ME_URL]["get"]["security"] == [{"OAuth2PasswordBearer": []}]
    assert "security" not in schema["paths"]["/api/v1/health/live"]["get"]
