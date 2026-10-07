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
    false,
    true,
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
    # Never ended because the hub stopped abruptly; set by the startup recovery.
    interrupted: Mapped[bool] = mapped_column(default=False, server_default=false())
    # Whether a person was seen, and the best score; NULL when nobody checked.
    person: Mapped[bool | None]
    person_confidence: Mapped[float | None] = mapped_column(Float)
    # False when the camera alerts on people only and none was seen.
    alert: Mapped[bool] = mapped_column(default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    snapshots: Mapped[list[SnapshotRow]] = relationship(
        back_populates="event", cascade="all, delete-orphan", passive_deletes=True, lazy="selectin"
    )


class SnapshotRow(Base):
    """A stored file of an event: one of its images, or its clip."""

    __tablename__ = "snapshots"
    __table_args__ = (
        CheckConstraint("kind IN ('clean', 'annotated', 'thumbnail', 'clip')", name="kind"),
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


class NotificationRow(Base):
    """Outbox: one row per alert and channel, kept until it is sent or given up on, so
    deliveries survive restarts. Deleted together with its event."""

    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'sent', 'failed')", name="status"),
        UniqueConstraint("event_id", "channel"),
        Index("ix_notifications_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_notifications_device_id_created_at", "device_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_new_id)
    event_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("motion_events.id", ondelete="CASCADE")
    )
    device_id: Mapped[str] = mapped_column(String(63))
    channel: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime]
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    finished_at: Mapped[datetime | None]


class AuditLogRow(Base):
    """Who changed which device or user, and when. Append-only."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_at", "at"),
        Index("ix_audit_log_target_type_target_id_at", "target_type", "target_id", "at"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=_new_id)
    at: Mapped[datetime] = mapped_column(default=utc_now)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    target_type: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column(JsonDocument, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64))


class PushSubscriptionRow(Base):
    """A browser receiving alerts as web push notifications. ``id`` is the SHA-256 of the
    endpoint (one row per browser); deleted with its user."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (Index("ix_push_subscriptions_username", "username"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(
        String(100), ForeignKey("users.username", ondelete="CASCADE")
    )
    endpoint: Mapped[str] = mapped_column(String(1024))
    p256dh: Mapped[str] = mapped_column(String(100))
    auth: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    last_success_at: Mapped[datetime | None]
