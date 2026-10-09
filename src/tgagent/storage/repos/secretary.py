from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import delete, func, select

from tgagent.domain import MediaRef
from tgagent.storage.db import SessionFactory, insert
from tgagent.storage.models import SecretaryMessage

Sender = Literal["person", "owner", "bot"]


@dataclass(frozen=True, slots=True)
class LoggedMessage:
    connection_id: str
    chat_id: int
    message_id: int
    sender: Sender
    sender_id: int | None
    sender_name: str
    date: datetime
    text: str | None
    media: MediaRef | None = None
    reply_to_message_id: int | None = None


def _from_row(row: SecretaryMessage) -> LoggedMessage:
    return LoggedMessage(
        connection_id=row.connection_id,
        chat_id=row.chat_id,
        message_id=row.message_id,
        sender=row.sender,  # type: ignore[arg-type]
        sender_id=row.sender_id,
        sender_name=row.sender_name,
        date=row.date,
        text=row.text,
        media=MediaRef.from_json(row.media) if row.media else None,
        reply_to_message_id=row.reply_to_message_id,
    )


class SecretaryLogRepo:
    """The secretary's view of each business chat: the person's, the owner's and its own messages."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, message: LoggedMessage) -> None:
        row = {
            "connection_id": message.connection_id,
            "chat_id": message.chat_id,
            "message_id": message.message_id,
            "sender": message.sender,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "date": message.date,
            "text": message.text,
            "media": message.media.to_json() if message.media else None,
            "reply_to_message_id": message.reply_to_message_id,
        }
        keys = ("connection_id", "chat_id", "message_id")
        async with self._sessions.begin() as session:
            stmt = insert(session, SecretaryMessage).values(**row)
            stmt = stmt.on_conflict_do_update(
                index_elements=[getattr(SecretaryMessage, key) for key in keys],
                set_={key: value for key, value in row.items() if key not in keys},
            )
            await session.execute(stmt)

    async def recent(self, connection_id: str, chat_id: int, limit: int) -> list[LoggedMessage]:
        """The chat's latest messages, oldest first."""
        stmt = (
            select(SecretaryMessage)
            .where(SecretaryMessage.connection_id == connection_id, SecretaryMessage.chat_id == chat_id)
            .order_by(SecretaryMessage.date.desc(), SecretaryMessage.message_id.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows = list(await session.scalars(stmt))
        return [_from_row(row) for row in reversed(rows)]

    async def replies_since(self, connection_id: str, chat_id: int, since: datetime) -> int:
        """The bot's replies in the chat after ``since`` and after the owner last wrote there."""
        in_chat = (SecretaryMessage.connection_id == connection_id, SecretaryMessage.chat_id == chat_id)
        async with self._sessions() as session:
            owner_wrote = await session.scalar(
                select(func.max(SecretaryMessage.date)).where(*in_chat, SecretaryMessage.sender == "owner")
            )
            if owner_wrote is not None:
                since = max(since, owner_wrote)
            count = await session.scalar(
                select(func.count())
                .select_from(SecretaryMessage)
                .where(*in_chat, SecretaryMessage.sender == "bot", SecretaryMessage.date > since)
            )
        return int(count or 0)

    async def owner_last_seen(self, owner_id: int) -> datetime | None:
        stmt = select(func.max(SecretaryMessage.date)).where(
            SecretaryMessage.sender == "owner", SecretaryMessage.sender_id == owner_id
        )
        async with self._sessions() as session:
            return await session.scalar(stmt)

    async def purge_older_than(self, cutoff: datetime) -> int:
        async with self._sessions.begin() as session:
            result = await session.execute(delete(SecretaryMessage).where(SecretaryMessage.date < cutoff))
        return int(getattr(result, "rowcount", 0) or 0)
