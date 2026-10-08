from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from tgagent.domain import Checklist, MediaRef, NormalizedMessage
from tgagent.storage.db import SessionFactory
from tgagent.storage.models import ChatMessage


def _to_row(message: NormalizedMessage) -> dict[str, object]:
    return {
        "chat_id": message.chat_id,
        "message_id": message.message_id,
        "thread_id": message.thread_id,
        "sender_id": message.sender_id,
        "sender_name": message.sender_name,
        "date": message.date,
        "text": message.text,
        "media": message.media.to_json() if message.media else None,
        "checklist": message.checklist.to_json() if message.checklist else None,
        "reply_to_message_id": message.reply_to_message_id,
        "quote": message.quote,
        "forwarded_from": message.forwarded_from,
        "from_bot": message.from_bot,
    }


def _from_row(row: ChatMessage) -> NormalizedMessage:
    return NormalizedMessage(
        chat_id=row.chat_id,
        message_id=row.message_id,
        thread_id=row.thread_id,
        sender_id=row.sender_id,
        sender_name=row.sender_name,
        date=row.date,
        text=row.text,
        media=MediaRef.from_json(row.media) if row.media else None,
        reply_to_message_id=row.reply_to_message_id,
        checklist=Checklist.from_json(row.checklist) if row.checklist else None,
        quote=row.quote,
        from_bot=row.from_bot,
        forwarded_from=row.forwarded_from,
    )


class ChatLogRepo:
    """Rolling log of group messages: the bot's only view of chat history."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, message: NormalizedMessage) -> None:
        row = _to_row(message)
        stmt = insert(ChatMessage).values(**row)
        keys = {k: v for k, v in row.items() if k not in ("chat_id", "message_id")}
        stmt = stmt.on_conflict_do_update(
            index_elements=[ChatMessage.chat_id, ChatMessage.message_id], set_=keys
        )
        async with self._sessions.begin() as session:
            await session.execute(stmt)

    async def get(self, chat_id: int, message_id: int) -> NormalizedMessage | None:
        async with self._sessions() as session:
            row = await session.get(ChatMessage, (chat_id, message_id))
        return _from_row(row) if row else None

    async def chain(self, chat_id: int, message_id: int, depth: int) -> list[NormalizedMessage]:
        """The message and up to ``depth - 1`` messages it replies to, oldest first."""
        result: list[NormalizedMessage] = []
        current: int | None = message_id
        async with self._sessions() as session:
            while current is not None and len(result) < depth:
                row = await session.get(ChatMessage, (chat_id, current))
                if row is None:
                    break
                result.append(_from_row(row))
                current = row.reply_to_message_id
        return result[::-1]

    async def recent(self, chat_id: int, thread_id: int | None, limit: int) -> list[NormalizedMessage]:
        stmt = select(ChatMessage).where(ChatMessage.chat_id == chat_id)
        if thread_id is not None:
            stmt = stmt.where(ChatMessage.thread_id == thread_id)
        stmt = stmt.order_by(ChatMessage.date.desc(), ChatMessage.message_id.desc()).limit(limit)
        async with self._sessions() as session:
            rows = list(await session.scalars(stmt))
        return [_from_row(row) for row in reversed(rows)]

    async def purge_older_than(self, cutoff: datetime) -> int:
        async with self._sessions.begin() as session:
            result = await session.execute(delete(ChatMessage).where(ChatMessage.date < cutoff))
        return int(getattr(result, "rowcount", 0) or 0)
