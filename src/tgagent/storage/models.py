from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import text as sql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    first_name: Mapped[str] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(64))
    language_code: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(256))
    allowed: Mapped[bool] = mapped_column(Boolean, server_default=sql("false"))
    added_by: Mapped[int | None] = mapped_column(BigInteger)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql("'{}'::jsonb"))
    created_at: Mapped[datetime] = created_at()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index(None, "chat_id", "date"),)

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_id: Mapped[int | None] = mapped_column(BigInteger)
    sender_name: Mapped[str] = mapped_column(String(256))
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str | None] = mapped_column(Text)
    media: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    checklist: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    quote: Mapped[str | None] = mapped_column(Text)
    forwarded_from: Mapped[str | None] = mapped_column(String(256))
    from_bot: Mapped[bool] = mapped_column(Boolean, server_default=sql("false"))


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index(
            "ix_conversations_active",
            "chat_id",
            "thread_id",
            postgresql_where=sql("is_active"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sql("true"))
    head_node_id: Mapped[int | None] = mapped_column(BigInteger)
    title_pending: Mapped[bool] = mapped_column(Boolean, server_default=sql("false"))
    container_id: Mapped[str | None] = mapped_column(String(128))
    container_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    has_compaction: Mapped[bool] = mapped_column(Boolean, server_default=sql("false"))
    created_at: Mapped[datetime] = created_at()


class NodeMessage(Base):
    __tablename__ = "node_messages"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)


class MediaCache(Base):
    __tablename__ = "media_cache"

    file_unique_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    anthropic_file_id: Mapped[str | None] = mapped_column(String(128))
    transcript: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index(None, "status", "due_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default=sql("'pending'"))
    created_at: Mapped[datetime] = created_at()


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, server_default=sql("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, server_default=sql("0"))
    cache_read_tokens: Mapped[int] = mapped_column(Integer, server_default=sql("0"))
    cache_write_tokens: Mapped[int] = mapped_column(Integer, server_default=sql("0"))
    web_search_requests: Mapped[int] = mapped_column(Integer, server_default=sql("0"))
    audio_seconds: Mapped[float] = mapped_column(Float, server_default=sql("0"))
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), server_default=sql("0"))
