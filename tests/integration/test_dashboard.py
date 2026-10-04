import gzip
from collections.abc import AsyncIterator
from pathlib import Path

import httpx2
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from vision_hub.api.dashboard import PAGE_CSP
from vision_hub.core.config import AppConfig, Environment, Settings
from vision_hub.core.errors import PROBLEM_JSON
from vision_hub.main import create_app

INDEX = (
    "<!doctype html><title>Vision Hub</title><script type=module src=/assets/app-1a2b.js></script>"
)
SCRIPT = "console.log('dashboard');" * 100


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (root / "assets" / "app-1a2b.js").write_text(SCRIPT)
    (root / "assets" / "app-1a2b.js.gz").write_bytes(gzip.compress(SCRIPT.encode()))
    (tmp_path / "secret.txt").write_text("not for the web")
    return root


@pytest.fixture
def app(dist: Path) -> FastAPI:
    return create_app(Settings(app=AppConfig(env=Environment.TEST, dashboard_dir=dist)))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    async with (
        LifespanManager(app) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app), base_url="http://localhost"
        ) as http,
    ):
        yield http


class TestPages:
    @pytest.mark.parametrize("path", ["/", "/events", "/devices/front-door", "/login?next=/x"])
    async def test_every_page_gets_the_app_shell(
        self, client: httpx2.AsyncClient, path: str
    ) -> None:
        response = await client.get(path)

        assert response.status_code == 200
        assert response.text == INDEX
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "no-cache"

    async def test_pages_get_the_page_csp_not_the_api_one(self, client: httpx2.AsyncClient) -> None:
        page = await client.get("/")
        api = await client.get("/api/v1/health/live")

        assert page.headers["content-security-policy"] == PAGE_CSP
        assert "script-src 'self'" in PAGE_CSP
        assert "unsafe-inline" not in PAGE_CSP
        assert api.headers["content-security-policy"].startswith("default-src 'none'")
        assert page.headers["x-frame-options"] == "DENY"

    async def test_head_requests_work(self, client: httpx2.AsyncClient) -> None:
        response = await client.head("/events")

        assert response.status_code == 200
        assert response.content == b""


class TestFiles:
    async def test_hashed_assets_are_cached_for_a_year(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/assets/app-1a2b.js", headers={"Accept-Encoding": "identity"})

        assert response.status_code == 200
        assert response.text == SCRIPT
        assert response.headers["content-type"].startswith("text/javascript")
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable"

    async def test_unhashed_files_are_revalidated(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/favicon.svg")

        assert response.headers["content-type"] == "image/svg+xml"
        assert response.headers["cache-control"] == "no-cache"

    async def test_precompressed_copies_are_sent_when_accepted(
        self, client: httpx2.AsyncClient
    ) -> None:
        compressed = await client.get("/assets/app-1a2b.js", headers={"Accept-Encoding": "gzip"})
        refused = await client.get(
            "/assets/app-1a2b.js", headers={"Accept-Encoding": "gzip;q=0, identity"}
        )

        assert compressed.headers["content-encoding"] == "gzip"
        assert "Accept-Encoding" in compressed.headers["vary"]
        assert compressed.text == SCRIPT  # decoded by the client
        assert "content-encoding" not in refused.headers

    async def test_a_missing_file_is_404_not_the_app_shell(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/assets/gone-9z9z.js")

        assert response.status_code == 404
        assert response.headers["content-type"] == PROBLEM_JSON

    @pytest.mark.parametrize("path", ["/%2e%2e/secret.txt", "/assets/%2e%2e/%2e%2e/secret.txt"])
    async def test_nothing_outside_the_dashboard_is_served(
        self, client: httpx2.AsyncClient, path: str
    ) -> None:
        response = await client.get(path)

        assert "not for the web" not in response.text


class TestBoundaries:
    @pytest.mark.parametrize("path", ["/api/v1/nope", "/api", "/metrics/x"])
    async def test_api_paths_never_fall_back_to_the_page(
        self, client: httpx2.AsyncClient, path: str
    ) -> None:
        response = await client.get(path)

        assert response.status_code == 404
        assert response.headers["content-type"] == PROBLEM_JSON

    async def test_api_and_docs_still_work(self, client: httpx2.AsyncClient) -> None:
        assert (await client.get("/api/v1/health/live")).json() == {"status": "ok"}
        assert (await client.get("/docs")).status_code == 200

    async def test_writes_to_pages_are_not_allowed(self, client: httpx2.AsyncClient) -> None:
        assert (await client.post("/events")).status_code == 405


async def test_without_a_build_the_hub_serves_the_api_only(tmp_path: Path) -> None:
    app = create_app(
        Settings(app=AppConfig(env=Environment.TEST, dashboard_dir=tmp_path / "missing"))
    )
    async with (
        LifespanManager(app) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app), base_url="http://localhost"
        ) as http,
    ):
        assert (await http.get("/")).status_code == 404
        assert (await http.get("/api/v1/health/live")).status_code == 200
