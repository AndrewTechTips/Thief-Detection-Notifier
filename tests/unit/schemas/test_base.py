from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from vision_hub.schemas.base import ApiSchema, RequestSchema, UtcDateTime


class CameraIn(RequestSchema):
    name: str


class EventOut(ApiSchema):
    id: int
    at: UtcDateTime


@dataclass
class EventRecord:
    id: int
    at: datetime


class TestRequestSchema:
    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra_forbidden"):
            CameraIn.model_validate({"name": "porch", "nmae": "typo"})

    def test_strings_are_stripped(self) -> None:
        assert CameraIn(name="  porch  ").name == "porch"


class TestApiSchema:
    def test_builds_from_domain_objects(self) -> None:
        record = EventRecord(id=1, at=datetime(2026, 1, 1, tzinfo=UTC))

        assert EventOut.model_validate(record).id == 1


class TestUtcDateTime:
    def test_aware_values_are_normalised_to_utc(self) -> None:
        bucharest = timezone(timedelta(hours=3))

        event = EventOut(id=1, at=datetime(2026, 6, 1, 15, 0, tzinfo=bucharest))

        assert event.at == datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        assert event.at.utcoffset() == timedelta(0)
        assert event.model_dump_json().endswith('"2026-06-01T12:00:00Z"}')

    def test_naive_values_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone"):
            EventOut(id=1, at=datetime(2026, 6, 1, 12, 0))
