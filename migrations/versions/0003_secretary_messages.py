"""secretary mode: the log of business chats

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09 12:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "secretary_messages",
        sa.Column("connection_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("sender", sa.String(length=8), nullable=False),
        sa.Column("sender_id", sa.BigInteger(), nullable=True),
        sa.Column("sender_name", sa.String(length=256), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("media", JSON, nullable=True),
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("connection_id", "chat_id", "message_id", name=op.f("pk_secretary_messages")),
    )
    op.create_index(
        op.f("ix_secretary_messages_connection_id_chat_id_date"),
        "secretary_messages",
        ["connection_id", "chat_id", "date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_secretary_messages_connection_id_chat_id_date"), table_name="secretary_messages")
    op.drop_table("secretary_messages")
