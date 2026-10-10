from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select

from tgagent.storage.db import SessionFactory
from tgagent.storage.models import Memory


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    chat_id: int
    author_id: int | None
    text: str
    created_at: datetime


def _record(row: Memory) -> MemoryRecord:
    return MemoryRecord(row.id, row.chat_id, row.author_id, row.text, row.created_at)


class MemoryRepo:
    """Each chat's own notes; nothing here is shared between chats."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, chat_id: int, author_id: int | None, text: str) -> MemoryRecord:
        row = Memory(chat_id=chat_id, author_id=author_id, text=text)
        async with self._sessions.begin() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return _record(row)

    async def list(self, chat_id: int) -> list[MemoryRecord]:
        stmt = select(Memory).where(Memory.chat_id == chat_id).order_by(Memory.id)
        async with self._sessions() as session:
            return [_record(row) for row in await session.scalars(stmt)]

    async def delete(self, memory_id: int, *, chat_id: int) -> bool:
        async with self._sessions.begin() as session:
            result = await session.execute(
                delete(Memory).where(Memory.id == memory_id, Memory.chat_id == chat_id)
            )
        return bool(getattr(result, "rowcount", 0))

    async def clear(self, chat_id: int) -> int:
        async with self._sessions.begin() as session:
            result = await session.execute(delete(Memory).where(Memory.chat_id == chat_id))
        return int(getattr(result, "rowcount", 0) or 0)
