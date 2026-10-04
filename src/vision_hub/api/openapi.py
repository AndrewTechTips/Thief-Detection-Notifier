"""OpenAPI adjustments so the published schema matches what the API actually returns.

FastAPI documents errors as ``application/json`` with its own ``HTTPValidationError`` shape,
while this API returns RFC 9457 ``application/problem+json`` bodies. WebSocket messages have no
OpenAPI path at all. The dashboard generates its types from this schema, so both are fixed here.
"""

import json
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import TypeAdapter
from pydantic.json_schema import JsonSchemaMode

from vision_hub.core.errors import PROBLEM_JSON
from vision_hub.schemas.problem import ProblemDetail
from vision_hub.schemas.ws import ServerMessage, client_message_adapter

_REF_TEMPLATE = "#/components/schemas/{model}"
_PROBLEM_REF = {"$ref": _REF_TEMPLATE.format(model=ProblemDetail.__name__)}
_FASTAPI_ERROR_SCHEMAS = ("HTTPValidationError", "ValidationError")
# Component name -> (model, mode): what the server sends is serialised, what it receives validated.
_WS_MESSAGES: dict[str, tuple[TypeAdapter[Any], JsonSchemaMode]] = {
    "WsServerMessage": (TypeAdapter(ServerMessage), "serialization"),
    "WsClientMessage": (client_message_adapter, "validation"),
}


def operation_id(route: APIRoute) -> str:
    """Readable, stable operation IDs for generated clients: ``health_live`` rather than
    FastAPI's default ``live_api_v1_health_live_get``."""
    return f"{route.tags[0]}_{route.name}" if route.tags else route.name


def openapi_document(app: FastAPI) -> str:
    """The schema as committed for the dashboard (``frontend/openapi.json``): stable formatting,
    so regenerating it only changes what changed in the API."""
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def install_schema_fixes(app: FastAPI) -> None:
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        return apply_websocket_messages(apply_problem_details(generate()))

    app.openapi = openapi  # type: ignore[method-assign]


def _problem_response(description: str) -> dict[str, Any]:
    return {"description": description, "content": {PROBLEM_JSON: {"schema": _PROBLEM_REF}}}


def apply_problem_details(schema: dict[str, Any]) -> dict[str, Any]:
    """Idempotent: FastAPI caches the schema object, so this may run on it repeatedly."""
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            responses: dict[str, Any] = operation.setdefault("responses", {})
            for status, response in responses.items():
                content = response.get("content", {})
                if status[0] in "45" and content.get("application/json", {}).get("schema") == (
                    _PROBLEM_REF
                ):
                    content[PROBLEM_JSON] = content.pop("application/json")
            if "422" in responses:
                responses["422"] = _problem_response("Validation Failed")
            responses.setdefault("500", _problem_response("Internal Server Error"))

    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name in _FASTAPI_ERROR_SCHEMAS:
        components.pop(name, None)
    problem_schema = ProblemDetail.model_json_schema(ref_template=_REF_TEMPLATE)
    components.update(problem_schema.pop("$defs", {}))
    components[ProblemDetail.__name__] = problem_schema
    return schema


def apply_websocket_messages(schema: dict[str, Any]) -> dict[str, Any]:
    """Publishes the WebSocket protocol (``schemas.ws``) as components: ``WsServerMessage`` and
    ``WsClientMessage`` are discriminated unions on ``type``. Idempotent."""
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name, (adapter, mode) in _WS_MESSAGES.items():
        message_schema = adapter.json_schema(ref_template=_REF_TEMPLATE, mode=mode)
        for model, definition in message_schema.pop("$defs", {}).items():
            # Models shared with the REST API (e.g. DeviceStatus) must render identically.
            if components.setdefault(model, definition) != definition:
                msg = f"OpenAPI component {model!r} differs between REST and WebSocket schemas"
                raise RuntimeError(msg)
        components[name] = message_schema
    return schema
