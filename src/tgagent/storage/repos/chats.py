from typing import Any

from sqlalchemy import select, update

from tgagent.storage.db import SessionFactory, insert
from tgagent.storage.models import Chat, User


class UserRepo:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def upsert(
        self, user_id: int, first_name: str, username: str | None, language_code: str | None
    ) -> None:
        values = {"first_name": first_name, "username": username, "language_code": language_code}
        async with self._sessions.begin() as session:
            stmt = insert(session, User).values(id=user_id, **values)
            await session.execute(stmt.on_conflict_do_update(index_elements=[User.id], set_=values))

    async def names(self, user_ids: list[int]) -> dict[int, str]:
        if not user_ids:
            return {}
        async with self._sessions() as session:
            rows = await session.execute(
                select(User.id, User.first_name, User.username).where(User.id.in_(user_ids))
            )
        return {uid: f"{name} (@{username})" if username else name for uid, name, username in rows}


class ChatRepo:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def upsert(self, chat_id: int, chat_type: str, title: str | None) -> None:
        async with self._sessions.begin() as session:
            stmt = insert(session, Chat).values(id=chat_id, type=chat_type, title=title)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Chat.id], set_={"type": chat_type, "title": title}
            )
            await session.execute(stmt)

    async def set_allowed(self, chat_id: int, allowed: bool, added_by: int | None) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(Chat).where(Chat.id == chat_id).values(allowed=allowed, added_by=added_by)
            )

    async def allowed_ids(self) -> set[int]:
        async with self._sessions() as session:
            rows = await session.scalars(select(Chat.id).where(Chat.allowed))
        return set(rows)

    async def get_settings(self, chat_id: int) -> dict[str, Any]:
        async with self._sessions() as session:
            settings = await session.scalar(select(Chat.settings).where(Chat.id == chat_id))
        return dict(settings or {})

    async def update_settings(self, chat_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        async with self._sessions.begin() as session:
            chat = await session.get(Chat, chat_id, with_for_update=True)
            if chat is None:
                return {}
            chat.settings = {**chat.settings, **patch}
            return dict(chat.settings)

    async def migrate(self, old_id: int, new_id: int) -> None:
        async with self._sessions.begin() as session:
            old = await session.get(Chat, old_id)
            if old is None:
                return
            stmt = insert(session, Chat).values(
                id=new_id,
                type="supergroup",
                title=old.title,
                allowed=old.allowed,
                added_by=old.added_by,
                settings=old.settings,
            )
            await session.execute(stmt.on_conflict_do_nothing(index_elements=[Chat.id]))
            old.allowed = False
