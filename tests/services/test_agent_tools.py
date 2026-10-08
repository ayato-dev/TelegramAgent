from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from tests.telegram.fakes import FakeBot
from tgagent.agent.tools import ChatKind, ToolContext
from tgagent.context.media import MediaService
from tgagent.domain import MediaRef, NormalizedMessage
from tgagent.services.agent_tools import AgentTools
from tgagent.services.reminders import ReminderScheduler
from tgagent.storage.repos import ChatLogRepo, ReminderRecord, ReminderRepo

NOW = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
MSK = ZoneInfo("Europe/Moscow")


class FakeReminders:
    def __init__(self) -> None:
        self.items: list[ReminderRecord] = []

    async def create(
        self, chat_id: int, thread_id: int | None, user_id: int, due_at: datetime, text: str, mode: str
    ) -> ReminderRecord:
        record = ReminderRecord(
            len(self.items) + 1, chat_id, thread_id, user_id, due_at, text, mode, "pending"
        )  # type: ignore[arg-type]
        self.items.append(record)
        return record

    async def pending(self, chat_id: int) -> list[ReminderRecord]:
        return [r for r in self.items if r.chat_id == chat_id]

    async def cancel(self, reminder_id: int, *, chat_id: int) -> bool:
        before = len(self.items)
        self.items = [r for r in self.items if not (r.id == reminder_id and r.chat_id == chat_id)]
        return len(self.items) < before


class FakeScheduler:
    def __init__(self) -> None:
        self.wakes = 0

    def wake(self) -> None:
        self.wakes += 1


class FakeLog:
    def __init__(self, messages: list[NormalizedMessage]) -> None:
        self.messages = messages
        self.limits: list[int] = []

    async def recent(self, chat_id: int, thread_id: int | None, limit: int) -> list[NormalizedMessage]:
        self.limits.append(limit)
        return self.messages[-limit:]


class FakeMedia:
    async def transcript(self, media: MediaRef, *, user_id: int | None, chat_id: int) -> str:
        return "голосом сказал"


class Harness:
    def __init__(self, log_messages: list[NormalizedMessage] | None = None) -> None:
        self.bot = FakeBot()
        self.reminders = FakeReminders()
        self.scheduler = FakeScheduler()
        self.log = FakeLog(log_messages or [])
        self.tools = AgentTools(
            self.bot.as_bot(),
            cast(ReminderRepo, self.reminders),
            cast(ReminderScheduler, self.scheduler),
            cast(ChatLogRepo, self.log),
            cast(MediaService, FakeMedia()),
            tz=MSK,
            clock=lambda: NOW,
        )

    async def call(self, name: str, args: dict[str, Any], kind: ChatKind = "private") -> Any:
        ctx = ToolContext(
            chat_id=-5 if kind == "group" else 5, thread_id=None, user_id=1, chat_kind=kind, message_id=9
        )
        return await self.tools.registry().execute(name, args, ctx)


async def test_set_reminder_with_offset() -> None:
    h = Harness()

    outcome = await h.call(
        "set_reminder", {"when": "2026-10-08T18:30:00+03:00", "text": "позвонить", "mode": "notify"}
    )

    assert not outcome.is_error
    assert "#1" in outcome.content
    assert h.reminders.items[0].due_at == datetime(2026, 10, 8, 15, 30, tzinfo=UTC)
    assert h.scheduler.wakes == 1


async def test_naive_time_is_read_in_bot_timezone() -> None:
    h = Harness()

    await h.call("set_reminder", {"when": "2026-10-08T18:30", "text": "x", "mode": "task"})

    assert h.reminders.items[0].due_at == datetime(2026, 10, 8, 15, 30, tzinfo=UTC)
    assert h.reminders.items[0].mode == "task"


@pytest.mark.parametrize(
    ("when", "kind"),
    [
        ((NOW - timedelta(hours=1)).isoformat(), "private"),
        ("завтра", "private"),
        ((NOW + timedelta(hours=1)).isoformat(), "guest"),
    ],
)
async def test_invalid_reminders_are_errors(when: str, kind: ChatKind) -> None:
    h = Harness()

    outcome = await h.call("set_reminder", {"when": when, "text": "x", "mode": "notify"}, kind)

    assert outcome.is_error
    assert h.reminders.items == []


async def test_list_and_cancel_reminders() -> None:
    h = Harness()
    assert (await h.call("list_reminders", {})).content == "Активных напоминаний нет."
    await h.call("set_reminder", {"when": "2026-10-09T09:00:00+03:00", "text": "зарядка", "mode": "notify"})

    listing = await h.call("list_reminders", {})
    cancelled = await h.call("cancel_reminder", {"reminder_id": 1})
    missing = await h.call("cancel_reminder", {"reminder_id": 1})

    assert "#1" in listing.content
    assert "2026-10-09 09:00" in listing.content
    assert "зарядка" in listing.content
    assert not cancelled.is_error
    assert missing.is_error


async def test_chat_history_only_in_groups_with_transcripts() -> None:
    voice = MediaRef("voice", "f", "u", duration=4)
    messages = [
        NormalizedMessage(-5, 1, None, 2, "Петя", NOW, "привет"),
        NormalizedMessage(-5, 2, None, 3, "Оля", NOW, None, media=voice),
    ]
    h = Harness(messages)

    private = await h.call("read_chat_history", {"limit": 10})
    group = await h.call("read_chat_history", {"limit": 500}, "group")

    assert private.is_error
    assert "Петя: привет" in group.content
    assert "Оля: [голосовое 0:04] голосом сказал" in group.content
    assert h.log.limits == [200]


async def test_poll_validation_and_creation() -> None:
    h = Harness()

    bad = await h.call(
        "create_poll", {"question": "Куда?", "options": ["Кино"], "allows_multiple_answers": False}, "group"
    )
    good = await h.call(
        "create_poll",
        {"question": "Куда?", "options": ["Кино", "Бар"], "allows_multiple_answers": True},
        "group",
    )

    assert bad.is_error
    assert not good.is_error
    params = h.bot.last("send_poll")
    assert [option.text for option in params["options"]] == ["Кино", "Бар"]
    assert params["allows_multiple_answers"] is True
    assert params["is_anonymous"] is False


async def test_checklist_task_reply_targets_task() -> None:
    h = Harness()

    outcome = await h.call(
        "reply_to_checklist_task", {"checklist_message_id": 77, "task_id": 3, "text": "Готово"}
    )

    assert not outcome.is_error
    reply = h.bot.last("send_rich_message")["reply_parameters"]
    assert (reply.message_id, reply.checklist_task_id) == (77, 3)
