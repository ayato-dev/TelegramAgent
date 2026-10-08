import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tgagent.storage.db import SessionFactory, create_engine, create_sessionmaker

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "usage_events, reminders, media_cache, node_messages, nodes, conversations, chat_messages, chats, users"
)


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


@pytest.fixture(scope="session")
def migrated_url(alembic_config: Config) -> str:
    assert TEST_DATABASE_URL
    asyncio.run(_reset_schema(TEST_DATABASE_URL))
    command.upgrade(alembic_config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def sessions(migrated_url: str) -> AsyncIterator[SessionFactory]:
    engine = create_engine(migrated_url)
    yield create_sessionmaker(engine)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    await engine.dispose()
