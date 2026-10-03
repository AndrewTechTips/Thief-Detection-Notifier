import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from vision_hub.api.middleware import REQUEST_ID_HEADER, resolve_request_id
from vision_hub.core.errors import PROBLEM_JSON
from vision_hub.core.logging import get_logger

type LogRecords = Callable[[], list[dict[str, Any]]]


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    log = get_logger("vision_hub.test")

    @app.get("/echo")
    async def echo(request: Request) -> dict[str, str]:
        log.info("handler_ran")
        return {"request_id": request.state.request_id}

    @app.get("/explode")
    async def explode() -> None:
        msg = "database password is hunter2"
        raise RuntimeError(msg)

    @app.get("/explode-mid-stream")
    async def explode_mid_stream() -> StreamingResponse:
        async def frames() -> AsyncIterator[bytes]:
            yield b"frame-1"
            msg = "camera disconnected"
            raise RuntimeError(msg)

        return StreamingResponse(frames())

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text(websocket.state.request_id)
        await websocket.close()

    return app


def scope_with_header(value: str) -> dict[str, Any]:
    return {"type": "http", "headers": [(b"x-request-id", value.encode("latin-1"))]}


class TestRequestIdResolution:
    def test_generates_uuid7_when_absent(self) -> None:
        request_id = resolve_request_id({"type": "http", "headers": []})

        assert uuid.UUID(request_id).version == 7

    @pytest.mark.parametrize("value", ["abc-123", "trace.id:42_x", "a" * 128])
    def test_accepts_safe_caller_ids(self, value: str) -> None:
        assert resolve_request_id(scope_with_header(value)) == value

    @pytest.mark.parametrize(
        "value",
        ["", "a" * 129, "has space", 'quote"d', "new\nline", "<script>", "ünïcode"],
    )
    def test_replaces_unsafe_caller_ids(self, value: str) -> None:
        request_id = resolve_request_id(scope_with_header(value))

        assert request_id != value
        assert uuid.UUID(request_id).version == 7


class TestHttpRequests:
    async def test_request_id_is_echoed_and_available_to_handlers(
        self, client: httpx2.AsyncClient
    ) -> None:
        response = await client.get("/echo", headers={REQUEST_ID_HEADER: "trace-1"})

        assert response.headers[REQUEST_ID_HEADER] == "trace-1"
        assert response.json() == {"request_id": "trace-1"}

    async def test_each_request_gets_a_distinct_id(self, client: httpx2.AsyncClient) -> None:
        first = await client.get("/echo")
        second = await client.get("/echo")

        assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]

    async def test_handler_logs_and_access_log_share_the_request_id(
        self, client: httpx2.AsyncClient, log_records: LogRecords
    ) -> None:
        await client.get("/echo?token=secret-ticket", headers={REQUEST_ID_HEADER: "trace-2"})

        records = log_records()
        handler = next(r for r in records if r["event"] == "handler_ran")
        access = next(r for r in records if r["event"] == "request")
        assert handler["request_id"] == access["request_id"] == "trace-2"
        assert access["method"] == "GET"
        assert access["path"] == "/echo"
        assert access["status"] == 200
        assert access["duration_ms"] >= 0
        assert "secret-ticket" not in str(records)  # query strings are never logged

    async def test_context_does_not_leak_between_requests(
        self, client: httpx2.AsyncClient, log_records: LogRecords
    ) -> None:
        await client.get("/echo", headers={REQUEST_ID_HEADER: "first"})
        await client.get("/echo", headers={REQUEST_ID_HEADER: "second"})

        ids = [r["request_id"] for r in log_records() if r["event"] == "handler_ran"]
        assert ids == ["first", "second"]


class TestUnhandledExceptions:
    async def test_become_generic_problem_responses(
        self, client: httpx2.AsyncClient, log_records: LogRecords
    ) -> None:
        response = await client.get("/explode", headers={REQUEST_ID_HEADER: "trace-500"})

        assert response.status_code == 500
        assert response.headers["content-type"] == PROBLEM_JSON
        assert response.headers[REQUEST_ID_HEADER] == "trace-500"
        assert response.json() == {
            "type": "about:blank",
            "title": "Internal Server Error",
            "status": 500,
            "detail": "An unexpected error occurred.",
            "instance": "/explode",
            "request_id": "trace-500",
        }
        assert "hunter2" not in response.text

        records = log_records()
        crash = next(r for r in records if r["event"] == "unhandled_exception")
        assert crash["level"] == "error"
        assert crash["request_id"] == "trace-500"
        assert crash["exception"][0]["exc_type"] == "RuntimeError"
        access = next(r for r in records if r["event"] == "request")
        assert (access["status"], access["level"]) == (500, "error")

    async def test_after_response_started_are_reraised(self, client: httpx2.AsyncClient) -> None:
        with pytest.raises(RuntimeError, match="camera disconnected"):
            await client.get("/explode-mid-stream")


class TestWebSockets:
    def test_request_id_is_bound_for_websockets(self, app: FastAPI) -> None:
        with (
            TestClient(app) as test_client,
            test_client.websocket_connect("/ws", headers={REQUEST_ID_HEADER: "ws-1"}) as ws,
        ):
            assert ws.receive_text() == "ws-1"
