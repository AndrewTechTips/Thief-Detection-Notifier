"""ORM tables. Every change here needs a migration (a test compares models and migrations)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Float, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from vision_hub.core.security import utc_now
from vision_hub.infra.db.base import Base


def _new_id() -> str:
    return str(uuid.uuid7())


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'viewer')", name="role"),)

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_new_id)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class DeviceRow(Base):
    """``source`` holds the source settings without secrets; ``source_secret`` holds the
    encrypted camera password (Fernet), if any."""

    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(63), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool]
    target_fps: Mapped[float | None] = mapped_column(Float)
    source_kind: Mapped[str] = mapped_column(String(20))
    source: Mapped[dict[str, Any]]
    source_secret: Mapped[str | None] = mapped_column(Text)
    detection: Mapped[dict[str, Any]]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class RevokedTokenRow(Base):
    """Used or revoked refresh tokens, kept until they would have expired anyway."""

    __tablename__ = "revoked_tokens"

    token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
