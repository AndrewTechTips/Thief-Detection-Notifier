"""Event clips: a snapshot kind for the video of an event.

Revision ID: 0005
Revises: 0004
Created: 2026-10-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("snapshots") as batch:
        batch.drop_constraint(op.f("ck_snapshots_kind"), type_="check")
        batch.create_check_constraint("kind", "kind IN ('clean', 'annotated', 'thumbnail', 'clip')")


def downgrade() -> None:
    op.execute("DELETE FROM snapshots WHERE kind = 'clip'")  # the files stay on disk
    with op.batch_alter_table("snapshots") as batch:
        batch.drop_constraint(op.f("ck_snapshots_kind"), type_="check")
        batch.create_check_constraint("kind", "kind IN ('clean', 'annotated', 'thumbnail')")
