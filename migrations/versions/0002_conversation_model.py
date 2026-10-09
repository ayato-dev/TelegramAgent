"""pin conversations to a model, track prompt size, cache OpenAI file ids

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08 21:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("model", sa.String(length=96), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("last_prompt_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    # Every conversation so far was written by Claude Haiku 5.5 in Anthropic's format.
    op.execute("UPDATE conversations SET model = 'anthropic:claude-haiku-5-5'")
    op.add_column("media_cache", sa.Column("openai_file_id", sa.String(length=128), nullable=True))


def downgrade() -> None:
    # Batch mode: SQLite rebuilds the table to drop columns.
    with op.batch_alter_table("media_cache") as batch:
        batch.drop_column("openai_file_id")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("last_prompt_tokens")
        batch.drop_column("model")
