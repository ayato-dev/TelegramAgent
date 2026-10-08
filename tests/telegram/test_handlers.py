from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from aiogram.types import Chat, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, User

from tests.telegram.fakes import FakeBot
from tgagent.services.turns import TurnRequest, TurnService
from tgagent.storage.repos import ChatRepo, ReminderRecord, UserRepo
from tgagent.telegram.access import AccessPolicy
from tgagent.telegram.deps import Deps
from tgagent.telegram.handlers.membership import handle_membership
from tgagent.telegram.reminder_delivery import ReminderDelivery
from tgagent.telegram.sinks import PlainSink, ResponseSink

NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)
OWNER = User(id=1, is_bot=False, first_name="Owner")
STRANGER = User(id=2, is_bot=False, first_name="Stranger")
ME = User(id=99, is_bot=True, first_name="Agent", username="agent_bot", can_read_all_group_messages=True)
GROUP = Chat(id=-50, type="supergroup", title="Team")


class FakeChats:
    def __init__(self) -> None:
        self.allowed: dict[int, tuple[bool, int | None]] = {}
        self.settings: dict[int, dict[str, Any]] = {}

    async def upsert(self, chat_id: int, chat_type: str, title: str | None) -> None:
        return None

    async def set_allowed(self, chat_id: int, allowed: bool, added_by: int | None) -> None:
        self.allowed[chat_id] = (allowed, added_by)

    async def get_settings(self, chat_id: int) -> dict[str, Any]:
        return self.settings.get(chat_id, {})


def deps(chats: FakeChats, policy: AccessPolicy) -> Deps:
    return cast(Deps, SimpleNamespace(chats=chats, policy=policy, me=ME, username=ME.username))


def membership(by: User, old: Any, new: Any) -> ChatMemberUpdated:
    return ChatMemberUpdated(chat=GROUP, from_user=by, date=NOW, old_chat_member=old, new_chat_member=new)


async def test_group_added_by_allowed_user_becomes_allowed() -> None:
    bot, chats, policy = FakeBot(), FakeChats(), AccessPolicy(frozenset({1}), set())

    await handle_membership(
        membership(OWNER, ChatMemberLeft(user=ME), ChatMemberMember(user=ME)),
        bot.as_bot(),
        deps(chats, policy),
    )

    assert chats.allowed[-50] == (True, 1)
    assert policy.chat_allowed(-50)
    assert "@agent_bot" in bot.last("send_message")["text"]
    assert "leave_chat" not in bot.names()


async def test_group_added_by_stranger_is_left() -> None:
    bot, chats, policy = FakeBot(), FakeChats(), AccessPolicy(frozenset({1}), set())

    await handle_membership(
        membership(STRANGER, ChatMemberLeft(user=ME), ChatMemberMember(user=ME)),
        bot.as_bot(),
        deps(chats, policy),
    )

    assert chats.allowed[-50] == (False, 2)
    assert not policy.chat_allowed(-50)
    assert bot.names() == ["leave_chat"]


async def test_removal_revokes_access() -> None:
    bot, chats, policy = FakeBot(), FakeChats(), AccessPolicy(frozenset({1}), {-50})

    await handle_membership(
        membership(OWNER, ChatMemberMember(user=ME), ChatMemberLeft(user=ME)),
        bot.as_bot(),
        deps(chats, policy),
    )

    assert not policy.chat_allowed(-50)
    assert chats.allowed[-50] == (False, None)


class FakeTurns:
    def __init__(self) -> None:
        self.requests: list[tuple[TurnRequest, ResponseSink]] = []

    async def run(self, request: TurnRequest, sink: ResponseSink) -> None:
        self.requests.append((request, sink))


class FakeUsers:
    async def names(self, user_ids: list[int]) -> dict[int, str]:
        return {1: "Аня"}


def delivery(bot: FakeBot, turns: FakeTurns) -> ReminderDelivery:
    return ReminderDelivery(
        bot.as_bot(),
        cast(TurnService, turns),
        cast(ChatRepo, FakeChats()),
        cast(UserRepo, FakeUsers()),
        default_effort="medium",
        clock=lambda: NOW,
    )


async def test_notify_reminder_in_group_mentions_user() -> None:
    bot, turns = FakeBot(), FakeTurns()

    await delivery(bot, turns)(ReminderRecord(1, -50, 3, 1, NOW, "созвон", "notify", "running"))

    sent = bot.last("send_rich_message")
    assert "созвон" in sent["markdown"]
    assert "[Аня](tg://user?id=1)" in sent["markdown"]
    assert sent["message_thread_id"] == 3


async def test_task_reminder_runs_agent_turn() -> None:
    bot, turns = FakeBot(), FakeTurns()

    await delivery(bot, turns)(ReminderRecord(2, 7, None, 7, NOW, "проверь курс евро", "task", "running"))

    request, sink = turns.requests[0]
    assert request.kind == "reminder"
    assert request.chat_id == 7
    assert "проверь курс евро" in (request.trigger.text or "")
    assert isinstance(sink, PlainSink)
