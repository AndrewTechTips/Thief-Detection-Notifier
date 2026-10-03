import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx2
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from vision_hub.api.deps import get_health_checks
from vision_hub.core.config import Settings
from vision_hub.main import create_app

type LogRecords = Callable[[], list[dict[str, Any]]]


@dataclass
class FakeCheck:
    name: str
    error: Exception | None = None
    delay: float = 0.0

    async def check(self) -> None:
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error


def use_checks(app: FastAPI, *checks: FakeCheck) -> None:
    app.dependency_overrides[get_health_checks] = lambda: checks


async def test_liveness(client: httpx2.AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["Cache-Control"] == "no-store"


async def test_readiness_checks_the_database(client: httpx2.AsyncClient) -> None:
    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [(c["name"], c["healthy"]) for c in body["checks"]] == [
        ("database", True),
        ("accepting_traffic", True),
    ]
    assert response.headers["Cache-Control"] == "no-store"


async def test_readiness_fails_once_shutdown_begins(
    app: FastAPI, client: httpx2.AsyncClient
) -> None:
    app.state.lifecycle.begin_shutdown()  # what the server does on SIGTERM

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    checks = {c["name"]: c["healthy"] for c in response.json()["checks"]}
    assert checks == {"database": True, "accepting_traffic": False}


async def test_readiness_reports_each_check(app: FastAPI, client: httpx2.AsyncClient) -> None:
    use_checks(app, FakeCheck("database"), FakeCheck("cameras"))

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert [(c["name"], c["healthy"]) for c in body["checks"]] == [
        ("database", True),
        ("cameras", True),
    ]


async def test_failing_dependency_returns_503_without_leaking_details(
    app: FastAPI, client: httpx2.AsyncClient, log_records: LogRecords
) -> None:
    use_checks(app, FakeCheck("database", error=ConnectionError("pg://admin:s3cret@db")))

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    [check] = response.json()["checks"]
    assert response.json()["status"] == "unavailable"
    assert set(check) == {"name", "healthy", "duration_ms"}  # no error text for the public
    assert (check["name"], check["healthy"]) == ("database", False)
    assert "s3cret" not in response.text
    failure = next(r for r in log_records() if r["event"] == "health_check_failed")
    assert failure["check"] == "database"
    assert "s3cret" in str(failure["exception"])  # operators still see the cause


async def test_readiness_uses_configured_timeout(
    settings_factory: Callable[..., Settings],
) -> None:
    app = create_app(settings_factory(health_check_timeout_seconds=0.05))
    use_checks(app, FakeCheck("camera", delay=5))

    async with (
        LifespanManager(app) as manager,
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=manager.app), base_url="http://localhost"
        ) as http,
    ):
        response = await http.get("/api/v1/health/ready")

    assert response.status_code == 503


async def test_successful_probes_are_logged_at_debug(
    client: httpx2.AsyncClient, log_records: LogRecords
) -> None:
    await client.get("/api/v1/health/live")
    await client.get("/api/v1/health/ready")

    assert [r for r in log_records() if r["event"] == "request"] == []


async def test_failing_probe_is_still_logged(
    app: FastAPI, client: httpx2.AsyncClient, log_records: LogRecords
) -> None:
    use_checks(app, FakeCheck("database", error=ConnectionError()))

    await client.get("/api/v1/health/ready")

    [access] = [r for r in log_records() if r["event"] == "request"]
    assert (access["status"], access["level"]) == (503, "error")
