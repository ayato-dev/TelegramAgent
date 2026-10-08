from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import func, select, update

from tgagent.storage.db import SessionFactory
from tgagent.storage.models import Reminder

ReminderMode = Literal["notify", "task"]
ReminderStatus = Literal["pending", "running", "done", "failed", "cancelled"]


@dataclass(frozen=True, slots=True)
class ReminderRecord:
    id: int
    chat_id: int
    thread_id: int | None
    user_id: int
    due_at: datetime
    text: str
    mode: ReminderMode
    status: ReminderStatus


def _record(row: Reminder) -> ReminderRecord:
    return ReminderRecord(
        id=row.id,
        chat_id=row.chat_id,
        thread_id=row.thread_id,
        user_id=row.user_id,
        due_at=row.due_at,
        text=row.text,
        mode=row.mode,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
    )


class ReminderRepo:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def create(
        self,
        chat_id: int,
        thread_id: int | None,
        user_id: int,
        due_at: datetime,
        text: str,
        mode: ReminderMode,
    ) -> ReminderRecord:
        row = Reminder(
            chat_id=chat_id, thread_id=thread_id, user_id=user_id, due_at=due_at, text=text, mode=mode
        )
        async with self._sessions.begin() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return _record(row)

    async def pending(self, chat_id: int) -> list[ReminderRecord]:
        stmt = (
            select(Reminder)
            .where(Reminder.chat_id == chat_id, Reminder.status == "pending")
            .order_by(Reminder.due_at)
        )
        async with self._sessions() as session:
            return [_record(row) for row in await session.scalars(stmt)]

    async def cancel(self, reminder_id: int, *, chat_id: int) -> bool:
        stmt = (
            update(Reminder)
            .where(Reminder.id == reminder_id, Reminder.chat_id == chat_id, Reminder.status == "pending")
            .values(status="cancelled")
            .returning(Reminder.id)
        )
        async with self._sessions.begin() as session:
            return (await session.scalar(stmt)) is not None

    async def claim_due(self, now: datetime, limit: int = 20) -> list[ReminderRecord]:
        """Atomically move due reminders to ``running`` so each fires exactly once."""
        due = (
            select(Reminder.id)
            .where(Reminder.status == "pending", Reminder.due_at <= now)
            .order_by(Reminder.due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        stmt = update(Reminder).where(Reminder.id.in_(due)).values(status="running").returning(Reminder)
        async with self._sessions.begin() as session:
            rows = list(await session.scalars(stmt))
        return sorted((_record(row) for row in rows), key=lambda r: r.due_at)

    async def finish(self, reminder_id: int, status: ReminderStatus) -> None:
        async with self._sessions.begin() as session:
            await session.execute(update(Reminder).where(Reminder.id == reminder_id).values(status=status))

    async def next_due(self) -> datetime | None:
        async with self._sessions() as session:
            return await session.scalar(select(func.min(Reminder.due_at)).where(Reminder.status == "pending"))
