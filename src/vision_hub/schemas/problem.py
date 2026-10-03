"""RFC 9457 "Problem Details for HTTP APIs" response body."""

from pydantic import BaseModel, ConfigDict, Field


class FieldError(BaseModel):
    """One request validation failure. The offending input is deliberately omitted."""

    loc: list[str | int] = Field(description="Path to the invalid field, e.g. ['body', 'port']")
    msg: str
    type: str


class ProblemDetail(BaseModel):
    model_config = ConfigDict(extra="allow")  # RFC 9457 extension members

    type: str = Field(
        default="about:blank",
        description="URI identifying the problem type",
        examples=["about:blank"],
    )
    title: str = Field(description="Short, human-readable summary of the problem type")
    status: int = Field(description="HTTP status code", ge=400, le=599)
    detail: str | None = Field(default=None, description="Explanation specific to this occurrence")
    instance: str | None = Field(default=None, description="Request path that produced the problem")
    request_id: str | None = Field(default=None, description="Correlates with server logs")
    errors: list[FieldError] | None = Field(default=None, description="Validation failures")
