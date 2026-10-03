"""ORM tables. Every change here needs a migration (a test compares models and migrations)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vision_hub.core.security import utc_now
from vision_hub.infra.db.base import Base, JsonDocument


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
    retention_days: Mapped[int | None]  # overrides STORAGE__RETENTION_DAYS for this device
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now, onupdate=utc_now)


class RevokedTokenRow(Base):
    """Used or revoked refresh tokens, kept until they would have expired anyway."""

    __tablename__ = "revoked_tokens"

    token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)


class MotionEventRow(Base):
    """Event history. ``device_id`` is deliberately not a foreign key: history outlives a
    deleted device."""

    __tablename__ = "motion_events"
    # Plain B-tree indexes: both databases scan them backwards for ORDER BY started_at DESC.
    __table_args__ = (
        Index("ix_motion_events_device_id_started_at", "device_id", "started_at"),
        Index("ix_motion_events_started_at", "started_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(63))
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    peak_area_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    motion_frames: Mapped[int] = mapped_column(default=0)
    boxes: Mapped[list[dict[str, int]]] = mapped_column(JsonDocument, default=list)
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    snapshots: Mapped[list[SnapshotRow]] = relationship(
        back_populates="event", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin"
    )


class SnapshotRow(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        CheckConstraint("kind IN ('clean', 'annotated', 'thumbnail')", name="kind"),
        UniqueConstraint("event_id", "kind"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_new_id)
    event_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("motion_events.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))
    path: Mapped[str] = mapped_column(String(255))  # relative to the snapshot store root
    size_bytes: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    event: Mapped[MotionEventRow] = relationship(back_populates="snapshots")
