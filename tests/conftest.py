import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tgagent.storage.db import SessionFactory, create_engine, create_sessionmaker

# Postgres when TEST_DATABASE_URL is set, otherwise a throwaway SQLite file.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parents[1]
TABLES = (
    "usage_events",
    "reminders",
    "media_cache",
    "node_messages",
    "nodes",
    "conversations",
    "chat_messages",
    "chats",
    "users",
)


async def _reset_postgres(url: str) -> None:
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    if TEST_DATABASE_URL:
        asyncio.run(_reset_postgres(TEST_DATABASE_URL))
        yield TEST_DATABASE_URL
        return
    with tempfile.TemporaryDirectory() as folder:
        yield f"sqlite+aiosqlite:///{Path(folder) / 'test.db'}"


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session")
def migrated_url(alembic_config: Config, database_url: str) -> str:
    command.upgrade(alembic_config, "head")
    return database_url


@pytest.fixture
async def sessions(migrated_url: str) -> AsyncIterator[SessionFactory]:
    engine = create_engine(migrated_url)
    yield create_sessionmaker(engine)
    async with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            await conn.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        else:
            for table in TABLES:
                await conn.execute(text(f"DELETE FROM {table}"))
    await engine.dispose()
