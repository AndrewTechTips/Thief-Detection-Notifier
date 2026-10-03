from typing import Literal

from vision_hub.schemas.base import ApiSchema


class Liveness(ApiSchema):
    status: Literal["ok"] = "ok"


class CheckStatus(ApiSchema):
    name: str
    healthy: bool
    duration_ms: float


class Readiness(ApiSchema):
    status: Literal["ok", "unavailable"]
    checks: list[CheckStatus]
