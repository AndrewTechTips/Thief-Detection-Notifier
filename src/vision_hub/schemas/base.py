"""Base classes and shared field types for API schemas (see docs/api-conventions.md)."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


type UtcDateTime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
"""Timezone-aware timestamp normalised to UTC; naive datetimes are rejected."""


class ApiSchema(BaseModel):
    """Base for response bodies. ``from_attributes`` lets routes return domain objects directly.

    Fields with defaults are always present in a response, so the output schema marks them
    required; generated clients then don't treat them as optional."""

    model_config = ConfigDict(
        from_attributes=True, json_schema_serialization_defaults_required=True
    )


class RequestSchema(ApiSchema):
    """Base for request bodies: unknown fields are an error, so client typos never pass silently."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
