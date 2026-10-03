import base64
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI

from vision_hub.core.errors import PROBLEM_JSON, InvalidCursorError
from vision_hub.schemas.base import ApiSchema
from vision_hub.schemas.pagination import (
    DEFAULT_PAGE_SIZE,
    Page,
    PageParamsDep,
    decode_cursor,
    encode_cursor,
)

EVENTS = [{"id": n} for n in range(1, 8)]


class Event(ApiSchema):
    id: int


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    @app.get("/events")
    async def list_events(page: PageParamsDep) -> Page[Event]:
        after = page.cursor["after_id"] if page.cursor else 0
        items = [e for e in EVENTS if e["id"] > after][: page.limit]
        has_more = items and items[-1]["id"] < EVENTS[-1]["id"]
        return Page(
            items=[Event(**e) for e in items],
            next_cursor=encode_cursor({"after_id": items[-1]["id"]}) if has_more else None,
        )

    return app


class TestCursorEncoding:
    @pytest.mark.parametrize(
        "position", [{"after_id": 42}, {"ts": "2026-01-01T00:00:00Z", "id": "a?b/c"}]
    )
    def test_round_trip(self, position: dict[str, Any]) -> None:
        cursor = encode_cursor(position)

        assert decode_cursor(cursor) == position
        assert "=" not in cursor  # URL-safe without padding

    @pytest.mark.parametrize(
        "cursor",
        [
            "!!!not-base64",
            base64.urlsafe_b64encode(b"not json").decode(),
            base64.urlsafe_b64encode(b"[1, 2]").decode(),  # JSON, but not an object
            base64.urlsafe_b64encode(b"\xff\xfe").decode(),
        ],
    )
    def test_garbage_is_rejected(self, cursor: str) -> None:
        with pytest.raises(InvalidCursorError):
            decode_cursor(cursor)


class TestPaginatedEndpoint:
    async def test_walks_all_pages(self, client: httpx2.AsyncClient) -> None:
        seen: list[int] = []
        cursor: str | None = None
        while True:
            params: dict[str, str | int] = {"limit": 3}
            if cursor:
                params["cursor"] = cursor
            body = (await client.get("/events", params=params)).json()
            seen += [item["id"] for item in body["items"]]
            cursor = body["next_cursor"]
            if cursor is None:
                break

        assert seen == [e["id"] for e in EVENTS]

    async def test_default_limit(self, client: httpx2.AsyncClient) -> None:
        body = (await client.get("/events")).json()

        assert len(body["items"]) == min(DEFAULT_PAGE_SIZE, len(EVENTS))
        assert body["next_cursor"] is None

    async def test_invalid_cursor_is_a_400_problem(self, client: httpx2.AsyncClient) -> None:
        response = await client.get("/events", params={"cursor": "garbage!"})

        assert response.status_code == 400
        assert response.headers["content-type"] == PROBLEM_JSON
        assert response.json()["type"] == "urn:vision-hub:problem:invalid-cursor"

    @pytest.mark.parametrize("limit", [0, 201])
    async def test_limit_is_bounded(self, client: httpx2.AsyncClient, limit: int) -> None:
        response = await client.get("/events", params={"limit": limit})

        assert response.status_code == 422
        assert response.json()["errors"][0]["loc"] == ["query", "limit"]
