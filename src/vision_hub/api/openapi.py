"""OpenAPI adjustments so the published schema matches what the API actually returns.

FastAPI documents errors as ``application/json`` with its own ``HTTPValidationError`` shape,
while this API returns RFC 9457 ``application/problem+json`` bodies. Generated clients (Phase 4)
depend on the schema being accurate, so it is corrected here.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute

from vision_hub.core.errors import PROBLEM_JSON
from vision_hub.schemas.problem import ProblemDetail

_REF_TEMPLATE = "#/components/schemas/{model}"
_PROBLEM_REF = {"$ref": _REF_TEMPLATE.format(model=ProblemDetail.__name__)}
_FASTAPI_ERROR_SCHEMAS = ("HTTPValidationError", "ValidationError")


def operation_id(route: APIRoute) -> str:
    """Readable, stable operation IDs for generated clients: ``health_live`` rather than
    FastAPI's default ``live_api_v1_health_live_get``."""
    return f"{route.tags[0]}_{route.name}" if route.tags else route.name


def install_problem_details_schema(app: FastAPI) -> None:
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        return apply_problem_details(generate())

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
