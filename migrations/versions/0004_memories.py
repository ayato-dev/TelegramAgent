"""per-chat memory notes

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10 09:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

AUTO_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
NOW = sa.text("CURRENT_TIMESTAMP")

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", AUTO_ID, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    op.create_index(op.f("ix_memories_chat_id"), "memories", ["chat_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_memories_chat_id"), table_name="memories")
    op.drop_table("memories")
