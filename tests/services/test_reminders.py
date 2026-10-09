import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast

from tgagent.services.reminders import ReminderScheduler
from tgagent.storage.repos import ReminderRecord, ReminderRepo

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def reminder(reminder_id: int, due: datetime) -> ReminderRecord:
    return ReminderRecord(reminder_id, 1, None, 1, due, "текст", "notify", "pending")


class FakeReminders:
    def __init__(self, items: list[ReminderRecord]) -> None:
        self.items = items
        self.finished: dict[int, str] = {}

    async def claim_due(self, now: datetime, limit: int = 20) -> list[ReminderRecord]:
        due = [r for r in self.items if r.due_at <= now]
        self.items = [r for r in self.items if r.due_at > now]
        return due

    async def finish(self, reminder_id: int, status: str) -> None:
        self.finished[reminder_id] = status

    async def next_due(self) -> datetime | None:
        return min((r.due_at for r in self.items), default=None)


async def test_due_reminders_fire_and_finish() -> None:
    repo = FakeReminders([reminder(1, NOW - timedelta(minutes=1)), reminder(2, NOW + timedelta(days=1))])
    fired: list[int] = []

    async def fire(item: ReminderRecord) -> None:
        fired.append(item.id)

    async def broken(item: ReminderRecord) -> None:
        raise RuntimeError("boom")

    scheduler = ReminderScheduler(cast(ReminderRepo, repo), fire, clock=lambda: NOW)
    count = await scheduler.tick()

    assert (fired, count) == ([1], 1)
    assert repo.finished == {1: "done"}

    repo.items.append(reminder(3, NOW))
    failing = ReminderScheduler(cast(ReminderRepo, repo), broken, clock=lambda: NOW)
    await failing.tick()
    assert repo.finished[3] == "failed"


async def test_wake_interrupts_sleep() -> None:
    repo = FakeReminders([])
    fired: list[int] = []

    async def fire(item: ReminderRecord) -> None:
        fired.append(item.id)

    scheduler = ReminderScheduler(cast(ReminderRepo, repo), fire, poll_interval=60, clock=lambda: NOW)
    task = asyncio.create_task(scheduler.run())
    await asyncio.sleep(0.05)

    repo.items.append(reminder(7, NOW))
    scheduler.wake()
    await asyncio.sleep(0.05)
    task.cancel()

    assert fired == [7]
