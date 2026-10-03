"""Reliability: notification outbox, audit log, interrupted events.

Revision ID: 0003
Revises: 0002
Created: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)
JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("motion_events") as batch:
        batch.add_column(
            sa.Column("interrupted", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("device_id", sa.String(63), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", TIMESTAMP, nullable=False),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("finished_at", TIMESTAMP, nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name=op.f("ck_notifications_status")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["motion_events.id"],
            name=op.f("fk_notifications_event_id_motion_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("event_id", "channel", name=op.f("uq_notifications_event_id_channel")),
    )
    op.create_index(
        "ix_notifications_status_next_attempt_at", "notifications", ["status", "next_attempt_at"]
    )
    op.create_index(
        "ix_notifications_device_id_created_at", "notifications", ["device_id", "created_at"]
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("at", TIMESTAMP, nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target_id", sa.String(100), nullable=False),
        sa.Column("details", JSON_DOCUMENT, nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])
    op.create_index(
        "ix_audit_log_target_type_target_id_at", "audit_log", ["target_type", "target_id", "at"]
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_target_type_target_id_at", table_name="audit_log")
    op.drop_index("ix_audit_log_at", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_notifications_device_id_created_at", table_name="notifications")
    op.drop_index("ix_notifications_status_next_attempt_at", table_name="notifications")
    op.drop_table("notifications")
    with op.batch_alter_table("motion_events") as batch:
        batch.drop_column("interrupted")
