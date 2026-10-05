"""Web push subscriptions.

Revision ID: 0004
Revises: 0003
Created: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("endpoint", sa.String(1024), nullable=False),
        sa.Column("p256dh", sa.String(100), nullable=False),
        sa.Column("auth", sa.String(50), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("last_success_at", TIMESTAMP, nullable=True),
        sa.ForeignKeyConstraint(
            ["username"],
            ["users.username"],
            name=op.f("fk_push_subscriptions_username_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_subscriptions")),
    )
    op.create_index("ix_push_subscriptions_username", "push_subscriptions", ["username"])


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_username", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
