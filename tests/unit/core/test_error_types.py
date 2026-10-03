import json

import pytest
from pydantic import ValidationError

from vision_hub.core.errors import (
    PROBLEM_JSON,
    AppError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ServiceUnavailableError,
    problem_response,
    status_phrase,
)
from vision_hub.schemas.problem import ProblemDetail


class TestAppErrors:
    @pytest.mark.parametrize(
        ("error_type", "status", "code"),
        [
            (AppError, 500, "internal-error"),
            (NotFoundError, 404, "not-found"),
            (ConflictError, 409, "conflict"),
            (AuthenticationError, 401, "unauthenticated"),
            (PermissionDeniedError, 403, "permission-denied"),
            (ServiceUnavailableError, 503, "service-unavailable"),
        ],
    )
    def test_each_error_maps_to_a_status_and_type(
        self, error_type: type[AppError], status: int, code: str
    ) -> None:
        error = error_type()

        assert error.status_code == status
        assert error.type_uri == f"urn:vision-hub:problem:{code}"

    def test_detail_and_extensions_are_kept(self) -> None:
        error = NotFoundError("Camera cam-1 not found", device_id="cam-1")

        assert str(error) == "Camera cam-1 not found"
        assert error.detail == "Camera cam-1 not found"
        assert error.extensions == {"device_id": "cam-1"}

    def test_message_falls_back_to_title(self) -> None:
        assert str(ConflictError()) == "Conflict"

    def test_authentication_error_advertises_bearer_scheme(self) -> None:
        assert AuthenticationError.headers == {"WWW-Authenticate": "Bearer"}


class TestProblemResponse:
    def test_renders_rfc9457_body_without_empty_members(self) -> None:
        response = problem_response(404, detail="gone", instance="/x", request_id="r-1")

        assert response.status_code == 404
        assert response.media_type == PROBLEM_JSON
        assert json.loads(bytes(response.body)) == {
            "type": "about:blank",
            "title": "Not Found",
            "status": 404,
            "detail": "gone",
            "instance": "/x",
            "request_id": "r-1",
        }

    def test_includes_extension_members_and_headers(self) -> None:
        response = problem_response(
            409, extensions={"device_id": "cam-1"}, headers={"Retry-After": "5"}
        )

        assert json.loads(bytes(response.body))["device_id"] == "cam-1"
        assert response.headers["Retry-After"] == "5"

    def test_unknown_status_gets_generic_title(self) -> None:
        assert status_phrase(499) == "Error"
        assert status_phrase(418) == "I'm a Teapot"

    def test_problem_status_must_be_an_error(self) -> None:
        with pytest.raises(ValidationError):
            ProblemDetail(title="OK", status=200)
