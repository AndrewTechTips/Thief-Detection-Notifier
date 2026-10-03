"""Initial schema: users, devices, revoked refresh tokens.

Revision ID: 0001
Revises:
Created: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMP = sa.DateTime(timezone=True)
JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
        sa.CheckConstraint("role IN ('admin', 'viewer')", name=op.f("ck_users_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "devices",
        sa.Column("id", sa.String(63), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("target_fps", sa.Float(), nullable=True),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("source", JSON_DOCUMENT, nullable=False),
        sa.Column("source_secret", sa.Text(), nullable=True),
        sa.Column("detection", JSON_DOCUMENT, nullable=False),
        sa.Column("created_at", TIMESTAMP, nullable=False),
        sa.Column("updated_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
    )
    op.create_table(
        "revoked_tokens",
        sa.Column("token_id", sa.String(64), nullable=False),
        sa.Column("expires_at", TIMESTAMP, nullable=False),
        sa.PrimaryKeyConstraint("token_id", name=op.f("pk_revoked_tokens")),
    )
    op.create_index(
        op.f("ix_revoked_tokens_expires_at"), "revoked_tokens", ["expires_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_revoked_tokens_expires_at"), table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
    op.drop_table("devices")
    op.drop_table("users")
