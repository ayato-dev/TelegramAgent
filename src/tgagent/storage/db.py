from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

type SessionFactory = async_sessionmaker[AsyncSession]

# One file next to the bot, no database server needed. Postgres: postgresql+asyncpg://...
DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///data/tgagent.db"


def prepare_sqlite_path(url: str) -> None:
    """SQLite creates the file but not its folder."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database and parsed.database != ":memory:":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)


def _sqlite_pragmas(connection: Any, _record: Any) -> None:
    cursor = connection.cursor()
    # WAL lets readers work while the bot writes; foreign keys are off by default in SQLite.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine(url: str) -> AsyncEngine:
    if make_url(url).get_backend_name() == "sqlite":
        prepare_sqlite_path(url)
        engine = create_async_engine(url, connect_args={"timeout": 30})
        event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
        return engine
    return create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def create_sessionmaker(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False)


def insert(session: AsyncSession, table: Any) -> postgresql.Insert | sqlite.Insert:
    """INSERT … ON CONFLICT in the session's SQL dialect."""
    assert session.bind is not None
    if session.bind.dialect.name == "sqlite":
        return sqlite.insert(table)
    return postgresql.insert(table)
