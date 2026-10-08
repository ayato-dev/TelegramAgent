import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from tgagent.storage.db import SessionFactory

pytestmark = pytest.mark.db


async def test_existing_conversations_stay_on_claude_haiku(
    alembic_config: Config, sessions: SessionFactory
) -> None:
    await asyncio.to_thread(command.downgrade, alembic_config, "0001")
    try:
        async with sessions.begin() as session:
            await session.execute(text("INSERT INTO conversations (chat_id, kind) VALUES (1, 'private')"))
    finally:
        await asyncio.to_thread(command.upgrade, alembic_config, "head")

    async with sessions() as session:
        row = (await session.execute(text("SELECT model, last_prompt_tokens FROM conversations"))).one()

    assert tuple(row) == ("anthropic:claude-haiku-5-5", 0)
