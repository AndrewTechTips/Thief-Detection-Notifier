"""Camera fleet definition loaded from a TOML file (stdlib ``tomllib``: no extra dependency).

[[devices]]
id = "porch"
name = "Porch"

[devices.source]
kind = "rtsp"
url = "rtsp://192.168.1.20:554/stream1"

[devices.detection]          # optional per-device overrides
min_motion_area = 0.005
"""

import tomllib
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vision_hub.vision.config import DetectionConfig
from vision_hub.vision.sources import SourceConfig

type DeviceId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]


class FleetError(Exception):
    """The fleet file is missing, unreadable or invalid."""


class DeviceSpec(BaseModel):
    """A camera to run, with its detection settings fully resolved."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: DeviceId
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = True
    target_fps: float | None = Field(default=None, gt=0, le=60)
    retention_days: int | None = Field(default=None, ge=1, le=3650)  # None: hub-wide default
    source: SourceConfig
    detection: DetectionConfig = Field(default_factory=DetectionConfig)


class _FleetFile(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    devices: list[dict[str, Any]] = Field(default_factory=list)


def load_fleet(path: Path, *, defaults: DetectionConfig) -> list[DeviceSpec]:
    """Parse and validate the file; per-device ``detection`` keys override ``defaults``."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        msg = f"devices file {path} does not exist"
        raise FleetError(msg) from None
    except tomllib.TOMLDecodeError as exc:
        msg = f"devices file {path} is not valid TOML: {exc}"
        raise FleetError(msg) from None

    try:
        fleet = _FleetFile.model_validate(raw)
        specs = []
        for entry in fleet.devices:
            overrides = entry.get("detection", {})
            if not isinstance(overrides, dict):
                msg = f"devices file {path}: 'detection' must be a table"
                raise FleetError(msg)
            detection = {**defaults.model_dump(), **overrides}
            specs.append(DeviceSpec.model_validate({**entry, "detection": detection}))
    except ValidationError as exc:
        msg = f"devices file {path} is invalid:\n{exc}"
        raise FleetError(msg) from None

    ids = [spec.id for spec in specs]
    if duplicates := sorted({i for i in ids if ids.count(i) > 1}):
        msg = f"devices file {path} has duplicate ids: {', '.join(duplicates)}"
        raise FleetError(msg)
    return specs
