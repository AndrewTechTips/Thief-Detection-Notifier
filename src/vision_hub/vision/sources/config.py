"""Source settings, one model per kind, selected by the ``kind`` field (discriminated union)."""

from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)


class _SourceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class WebcamSourceConfig(_SourceConfig):
    kind: Literal["webcam"] = "webcam"
    index: int = Field(default=0, ge=0, le=63, description="OS capture device index")
    width: int | None = Field(default=None, ge=16, le=7680, description="Requested width")
    height: int | None = Field(default=None, ge=16, le=4320, description="Requested height")
    fps: float | None = Field(default=None, gt=0, le=120, description="Requested frame rate")


class RtspSourceConfig(_SourceConfig):
    """Credentials live in their own fields so they stay secret; ``url`` must not embed them."""

    kind: Literal["rtsp"] = "rtsp"
    url: str = Field(examples=["rtsp://192.168.1.20:554/stream1"])
    username: str | None = None
    # exclude=True: accepted on input, but no serialization (API response, log, dump) emits it.
    password: SecretStr | None = Field(default=None, exclude=True)
    open_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    read_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    @field_validator("url")
    @classmethod
    def _valid_stream_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"rtsp", "rtsps"} or not parts.hostname:
            msg = "must be an rtsp:// or rtsps:// URL with a host"
            raise ValueError(msg)
        if parts.username is not None or parts.password is not None:
            msg = "must not contain credentials; use the username and password fields"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _password_needs_username(self) -> RtspSourceConfig:
        if self.password is not None and self.username is None:
            msg = "password requires a username"
            raise ValueError(msg)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_password(self) -> bool:
        return self.password is not None

    def connection_url(self) -> SecretStr:
        """The URL with credentials inserted; only ever handed to the capture backend."""
        if self.username is None:
            return SecretStr(self.url)
        parts = urlsplit(self.url)
        userinfo = quote(self.username, safe="")
        if self.password is not None:
            userinfo += ":" + quote(self.password.get_secret_value(), safe="")
        netloc = f"{userinfo}@{parts.netloc}"
        return SecretStr(urlunsplit(parts._replace(netloc=netloc)))


class VideoFileSourceConfig(_SourceConfig):
    kind: Literal["video_file"] = "video_file"
    path: Path
    loop: bool = True


class SyntheticSourceConfig(_SourceConfig):
    """Generated scene with a figure that walks through periodically; no hardware needed."""

    kind: Literal["synthetic"] = "synthetic"
    width: int = Field(default=640, ge=64, le=3840)
    height: int = Field(default=480, ge=48, le=2160)
    fps: float = Field(default=10.0, gt=0, le=60)
    visit_every_seconds: float = Field(default=20.0, gt=0, description="Time between visits")
    visit_seconds: float = Field(default=5.0, gt=0, description="How long each visit lasts")
    seed: int | None = Field(default=None, description="Fixes the sensor noise, for tests")

    @model_validator(mode="after")
    def _visit_fits_in_period(self) -> SyntheticSourceConfig:
        if self.visit_seconds >= self.visit_every_seconds:
            msg = "visit_seconds must be shorter than visit_every_seconds"
            raise ValueError(msg)
        return self


type SourceConfig = Annotated[
    WebcamSourceConfig | RtspSourceConfig | VideoFileSourceConfig | SyntheticSourceConfig,
    Field(discriminator="kind"),
]
