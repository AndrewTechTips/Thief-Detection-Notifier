"""Person detection: whether each event had a person in it, the score, and whether it alerted.

Revision ID: 0006
Revises: 0005
Created: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("motion_events") as batch:
        batch.add_column(sa.Column("person", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("person_confidence", sa.Float(), nullable=True))
        # Every event recorded before this alerted.
        batch.add_column(sa.Column("alert", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("motion_events") as batch:
        batch.drop_column("alert")
        batch.drop_column("person_confidence")
        batch.drop_column("person")
