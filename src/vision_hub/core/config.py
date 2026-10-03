"""Typed application settings loaded from environment variables and an optional `.env` file.

Every variable is prefixed with ``VISION_HUB_`` and nested groups use ``__`` as the delimiter,
e.g. ``VISION_HUB_SMTP__PASSWORD`` maps to ``Settings().smtp.password``.
"""

import secrets
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from limits import parse as parse_rate_limit
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ENV_PREFIX = "VISION_HUB_"
ENV_NESTED_DELIMITER = "__"

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


def _split_csv(value: Any) -> Any:
    """Allow list settings to be given as ``a,b,c`` instead of a JSON array."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


# NoDecode stops pydantic-settings from requiring JSON; the validator splits on commas instead.
CsvStrList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]
CsvEmailList = Annotated[list[EmailStr], NoDecode, BeforeValidator(_split_csv)]


class _Group(BaseModel):
    # Validation errors must never echo raw input: it may contain secrets.
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class AppConfig(_Group):
    name: str = "IoT Vision Hub"
    env: Environment = Environment.DEV
    debug: bool = False
    log_level: LogLevel = "INFO"
    log_format: Literal["auto", "console", "json"] = "auto"  # auto: json in prod, console otherwise
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    docs_enabled: bool | None = None  # None: enabled everywhere except prod
    health_check_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


class SecurityConfig(_Group):
    # Random per-process default keeps dev friction-free; prod must set it explicitly.
    jwt_secret: Annotated[SecretStr, Field(min_length=32)] = Field(
        default_factory=lambda: SecretStr(secrets.token_urlsafe(48))
    )
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "vision-hub"
    jwt_audience: str = "vision-hub-api"
    access_token_ttl_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_ttl_days: int = Field(default=7, ge=1, le=90)

    admin_username: str = Field(default="admin", min_length=3)
    admin_password_hash: SecretStr | None = None  # Argon2 hash, never a plain password

    cors_origins: CsvStrList = Field(default_factory=lambda: ["http://localhost:5173"])
    allowed_hosts: CsvStrList = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    # Per client IP, for login and token refresh. Syntax: "5/minute", "20/hour", "3 per 10 seconds"
    auth_rate_limit: str = "5/minute"

    @field_validator("auth_rate_limit")
    @classmethod
    def _valid_rate_limit(cls, value: str) -> str:
        try:
            parse_rate_limit(value)
        except ValueError:
            msg = "must be a rate limit like '5/minute' or '20 per hour'"
            raise ValueError(msg) from None
        return value

    @model_validator(mode="after")
    def _secret_matches_algorithm(self) -> Self:
        """HMAC keys shorter than the hash output weaken the signature (RFC 7518 §3.2)."""
        minimum = {"HS256": 32, "HS384": 48, "HS512": 64}[self.jwt_algorithm]
        if len(self.jwt_secret.get_secret_value()) < minimum:
            msg = f"jwt_secret must be at least {minimum} characters for {self.jwt_algorithm}"
            raise ValueError(msg)
        return self

    @field_validator("admin_password_hash")
    @classmethod
    def _must_be_argon2(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith("$argon2"):
            msg = "must be an Argon2 hash (starting with '$argon2'), not a plain password"
            raise ValueError(msg)
        return value


class SmtpConfig(_Group):
    enabled: bool = False
    host: str = "smtp.gmail.com"
    port: int = Field(default=587, ge=1, le=65535)
    # starttls: upgrade a plain connection (port 587); implicit: TLS from the start (port 465)
    tls: Literal["starttls", "implicit", "none"] = "starttls"
    timeout_seconds: float = Field(default=10.0, gt=0)
    local_hostname: str | None = None  # name sent in EHLO; defaults to the machine's hostname
    username: str | None = None
    password: SecretStr | None = None
    sender: EmailStr | None = None  # defaults to username
    recipients: CsvEmailList = Field(default_factory=list)  # defaults to sender

    @model_validator(mode="after")
    def _consistent_credentials(self) -> Self:
        """Credentials are optional (local relays often need none), but a password needs a
        username, and without a username there must be an explicit sender address."""
        if self.password is not None and self.username is None:
            msg = "SMTP password requires a username"
            raise ValueError(msg)
        if self.enabled and self.effective_sender is None:
            msg = "SMTP is enabled but neither username nor sender is set"
            raise ValueError(msg)
        return self

    @property
    def effective_sender(self) -> str | None:
        return self.sender or self.username

    @property
    def effective_recipients(self) -> list[str]:
        """Recipients default to the sender, matching the original notify-yourself behaviour."""
        if self.recipients:
            return list(self.recipients)
        return [self.effective_sender] if self.effective_sender else []


class DatabaseConfig(_Group):
    host: str = "localhost"
    port: int = Field(default=5432, ge=1, le=65535)
    user: str = "vision_hub"
    password: SecretStr = SecretStr("vision_hub")
    name: str = "vision_hub"
    pool_size: int = Field(default=5, ge=1, le=50)
    echo: bool = False


class StorageConfig(_Group):
    snapshots_dir: Path = Path("data/snapshots")
    jpeg_quality: int = Field(default=85, ge=1, le=100)
    retention_days: int = Field(default=30, ge=1)


class VisionConfig(_Group):
    """Camera fleet and motion detection defaults; each device can override detection."""

    devices_file: Path | None = None  # TOML fleet definition; None runs no cameras
    lock_file: Path = Path("data/vision-hub.lock")  # ensures one process owns the cameras
    media_dir: Path = Path("data/media")  # video_file sources added via the API must live here
    target_fps: float = Field(default=10.0, gt=0, le=60)
    # Fraction of the frame a moving region must cover; resolution-independent.
    min_motion_area: float = Field(default=0.01, gt=0, lt=1)
    blur_kernel_size: int = Field(default=21, ge=3)
    threshold: int = Field(default=60, ge=1, le=255)
    motion_end_grace_seconds: float = Field(default=2.0, ge=0)
    alert_cooldown_seconds: float = Field(default=60.0, ge=0)
    stream_jpeg_quality: int = Field(default=70, ge=1, le=100)
    stream_max_width: int = Field(default=960, ge=160, le=3840)

    @field_validator("blur_kernel_size")
    @classmethod
    def _must_be_odd(cls, value: int) -> int:
        if value % 2 == 0:
            msg = "Gaussian blur kernel size must be odd"
            raise ValueError(msg)
        return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter=ENV_NESTED_DELIMITER,
        env_nested_max_split=1,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    app: AppConfig = Field(default_factory=AppConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    smtp: SmtpConfig = Field(default_factory=SmtpConfig)
    db: DatabaseConfig = Field(default_factory=DatabaseConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)

    @property
    def is_prod(self) -> bool:
        return self.app.env is Environment.PROD

    @property
    def docs_enabled(self) -> bool:
        """Interactive docs and the OpenAPI schema are hidden in prod unless explicitly enabled."""
        if self.app.docs_enabled is None:
            return not self.is_prod
        return self.app.docs_enabled

    @property
    def log_as_json(self) -> bool:
        if self.app.log_format == "auto":
            return self.is_prod
        return self.app.log_format == "json"

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Self:
        """Fail fast with every problem listed at once, instead of one per restart."""
        if not self.is_prod:
            return self

        problems: list[str] = []
        if "jwt_secret" not in self.security.model_fields_set:
            problems.append(f"{env_name('security', 'jwt_secret')} must be set explicitly")
        if self.security.admin_password_hash is None:
            problems.append(f"{env_name('security', 'admin_password_hash')} must be set")
        if "password" not in self.db.model_fields_set:
            problems.append(f"{env_name('db', 'password')} must be set explicitly")
        if self.app.debug:
            problems.append(f"{env_name('app', 'debug')} must be false")
        if "*" in self.security.cors_origins:
            problems.append(f"{env_name('security', 'cors_origins')} must not contain '*'")
        if self.smtp.enabled and self.smtp.tls == "none":
            problems.append(
                f"{env_name('smtp', 'tls')} must not be 'none' (password in clear text)"
            )
        if "*" in self.security.allowed_hosts:
            problems.append(f"{env_name('security', 'allowed_hosts')} must not contain '*'")

        if problems:
            msg = "Unsafe production configuration:\n  - " + "\n  - ".join(problems)
            raise ValueError(msg)
        return self


def env_name(group: str, field: str) -> str:
    """Environment variable name for a settings field, e.g. ``VISION_HUB_SMTP__PASSWORD``."""
    return f"{ENV_PREFIX}{group}{ENV_NESTED_DELIMITER}{field}".upper()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, created once. Override via ``app.dependency_overrides`` in tests."""
    return Settings()
