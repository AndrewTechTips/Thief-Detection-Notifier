"""Event history: motion events, their snapshots, per-device retention.

Revision ID: 0002
Revises: 0001
Created: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)
JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("retention_days", sa.Integer(), nullable=True))

    op.create_table(
        "motion_events",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("device_id", sa.String(63), nullable=False),
        sa.Column("started_at", TIMESTAMP, nullable=False),
        sa.Column("ended_at", TIMESTAMP, nullable=True),
        sa.Column("peak_area_ratio", sa.Float(), nullable=False),
        sa.Column("motion_frames", sa.Integer(), nullable=False),
        sa.Column("boxes", JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_motion_events")),
    )
    op.create_index(
        "ix_motion_events_device_id_started_at", "motion_events", ["device_id", "started_at"]
    )
    op.create_index("ix_motion_events_started_at", "motion_events", ["started_at"])

    op.create_table(
        "snapshots",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("event_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.CheckConstraint(
            "kind IN ('clean', 'annotated', 'thumbnail')", name=op.f("ck_snapshots_kind")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["motion_events.id"],
            name=op.f("fk_snapshots_event_id_motion_events"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_snapshots")),
        sa.UniqueConstraint("event_id", "kind", name=op.f("uq_snapshots_event_id_kind")),
    )


def downgrade() -> None:
    op.drop_table("snapshots")
    op.drop_index("ix_motion_events_started_at", table_name="motion_events")
    op.drop_index("ix_motion_events_device_id_started_at", table_name="motion_events")
    op.drop_table("motion_events")
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("retention_days")
