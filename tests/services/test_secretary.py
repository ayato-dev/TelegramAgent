import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.types import BusinessBotRights, BusinessConnection, Chat, Message, User, Voice

from tgagent.agent.models import CATALOG
from tgagent.agent.pricing import TurnUsage
from tgagent.agent.registry import ProviderRegistry
from tgagent.context.media import MediaService
from tgagent.domain import MediaRef
from tgagent.services.secretary import SecretaryService
from tgagent.services.secretary_config import SecretaryConfig
from tgagent.storage.db import SessionFactory
from tgagent.storage.repos import ChatRepo, SecretaryLogRepo, UsageRecord, UsageRepo

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
MOSCOW = ZoneInfo("Europe/Moscow")
OWNER = User(id=1, is_bot=False, first_name="Виталий", language_code="ru")
PERSON = User(id=7, is_bot=False, first_name="Иван")
ME = User(id=99, is_bot=True, first_name="Secretary")
HAIKU = CATALOG["anthropic:claude-haiku-5-5"]


def connection(
    owner: User = OWNER, *, can_reply: bool = True, enabled: bool = True, connection_id: str = "conn"
) -> BusinessConnection:
    return BusinessConnection(
        id=connection_id,
        user=owner,
        user_chat_id=owner.id,
        date=NOW,
        is_enabled=enabled,
        rights=BusinessBotRights(can_reply=can_reply),
    )


class FakeTime:
    def __init__(self) -> None:
        self.now = NOW
        self.slept: list[float] = []

    def clock(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


class FakeBot:
    def __init__(self, time: FakeTime, *connections: BusinessConnection) -> None:
        self.time = time
        self.connections = {item.id: item for item in connections}
        self.sent: list[dict[str, Any]] = []
        self.actions: list[dict[str, Any]] = []
        self.gate: asyncio.Event | None = None
        self.sending = asyncio.Event()
        self._next_id = 1000

    def as_bot(self) -> Bot:
        return cast(Bot, self)

    async def get_business_connection(self, business_connection_id: str) -> BusinessConnection:
        return self.connections[business_connection_id]

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> Message:
        self.sending.set()
        if self.gate is not None:
            await self.gate.wait()
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        self._next_id += 1
        return Message(message_id=self._next_id, date=self.time.now, chat=Chat(id=chat_id, type="private"))

    async def send_chat_action(self, chat_id: int, action: str, **kw: Any) -> bool:
        self.actions.append({"chat_id": chat_id, "action": action, **kw})
        return True


class FakeRunner:
    spec = HAIKU

    def __init__(self, reply: str = "Виталий сейчас занят, передам ему.") -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def complete(
        self, prompt: str, *, max_tokens: int, system: str | None = None
    ) -> tuple[str, TurnUsage]:
        self.calls.append({"prompt": prompt, "max_tokens": max_tokens, "system": system})
        await asyncio.sleep(0)  # a real request yields to the event loop
        if self.error is not None:
            raise self.error
        usage = TurnUsage()
        usage.add_iteration(200, 20, 0, 0)
        return self.reply, usage


class FakeRegistry:
    default_model = "anthropic:claude-haiku-5-5"

    def __init__(self, runner: FakeRunner) -> None:
        self.runner_ = runner
        self.asked: list[str] = []

    def supports(self, key: str | None) -> bool:
        return key in ("anthropic:claude-haiku-5-5", "groq:openai/gpt-oss-120b")

    def runner(self, key: str) -> FakeRunner:
        self.asked.append(key)
        return self.runner_


class FakeChats:
    def __init__(self, saved: dict[int, dict[str, Any]] | None = None) -> None:
        self.saved = saved or {}

    async def get_settings(self, chat_id: int) -> dict[str, Any]:
        return self.saved.get(chat_id, {})


class FakeUsage:
    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    async def add(self, record: UsageRecord) -> None:
        self.records.append(record)


class FakeMedia:
    def __init__(self) -> None:
        self.transcribed: list[MediaRef] = []

    async def transcript(self, media: MediaRef, *, user_id: int | None, chat_id: int) -> str:
        self.transcribed.append(media)
        return "перезвони мне"


class Setup:
    def __init__(self, sessions: SessionFactory, config: SecretaryConfig, **chats: Any) -> None:
        self.time = FakeTime()
        self.bot = FakeBot(self.time, connection(), connection(PERSON, connection_id="stranger"))
        self.runner = FakeRunner()
        self.registry = FakeRegistry(self.runner)
        self.usage = FakeUsage()
        self.media = FakeMedia()
        self.log = SecretaryLogRepo(sessions)
        self.service = SecretaryService(
            self.bot.as_bot(),
            self.log,
            cast(ProviderRegistry, self.registry),
            cast(UsageRepo, self.usage),
            cast(MediaService, self.media),
            cast(ChatRepo, FakeChats(chats.get("saved"))),
            config,
            prompt="SECRETARY RULES",
            allowed_users=frozenset({OWNER.id}),
            bot_id=ME.id,
            bot_name=ME.first_name,
            tz=MOSCOW,
            clock=self.time.clock,
            sleep=self.time.sleep,
        )

    def message(
        self,
        message_id: int,
        text: str | None = "Привет, ты тут?",
        *,
        sender: User = PERSON,
        chat_id: int = 7,
        connection_id: str = "conn",
        minutes: float = 0,
        **fields: Any,
    ) -> Message:
        return Message(
            message_id=message_id,
            date=self.time.now + timedelta(minutes=minutes),
            chat=Chat(id=chat_id, type="private", first_name=PERSON.first_name),
            from_user=sender,
            text=text,
            business_connection_id=connection_id,
            **fields,
        )

    async def receive(self, *messages: Message) -> None:
        for message in messages:
            await self.service.on_message(message)
        await self.service.join()


def config(**changes: Any) -> SecretaryConfig:
    return SecretaryConfig(**{"delay_seconds": 0, **changes})


async def test_answers_a_person_on_behalf_of_the_owner(sessions: SessionFactory) -> None:
    s = Setup(sessions, config())

    await s.receive(s.message(10))

    assert s.bot.sent == [
        {"chat_id": 7, "text": "Виталий сейчас занят, передам ему.", "business_connection_id": "conn"}
    ]
    assert s.runner.calls[0]["system"] == "SECRETARY RULES"
    assert s.bot.actions[0] == {"chat_id": 7, "action": "typing", "business_connection_id": "conn"}
    reply = (await s.log.recent("conn", 7, limit=10))[-1]
    assert (reply.sender, reply.reply_to_message_id) == ("bot", 10)
    assert [(r.kind, r.user_id, r.chat_id, r.model) for r in s.usage.records] == [
        ("secretary", OWNER.id, 7, HAIKU.key)
    ]


async def test_the_prompt_shows_the_chat_with_roles(sessions: SessionFactory) -> None:
    s = Setup(sessions, config(reply_when_online=True))
    await s.receive(s.message(10, "Ты завтра свободен?"))
    await s.receive(s.message(11, "Да, после обеда", sender=OWNER, minutes=1))
    await s.receive(s.message(12, "Тогда в три?", minutes=2), s.message(13, "Набери меня", minutes=2))

    prompt = s.runner.calls[-1]["prompt"]
    context, _, new = prompt.partition("</context>")
    assert '<environment owner="Виталий" time="2026-10-09T15:00+03:00"/>' in prompt
    assert 'role="person" name="Иван"' in context and "Ты завтра свободен?" in context
    assert 'role="you"' in context and "передам ему" in context
    assert 'role="owner" name="Виталий"' in context and "после обеда" in context
    assert "Тогда в три?" in new and "Набери меня" in new


async def test_messages_in_a_row_get_one_reply(sessions: SessionFactory) -> None:
    s = Setup(sessions, config(delay_seconds=5))

    await s.receive(s.message(10, "Привет"), s.message(11, "Есть вопрос"))

    assert len(s.bot.sent) == 1
    assert "Привет" in s.runner.calls[0]["prompt"] and "Есть вопрос" in s.runner.calls[0]["prompt"]


async def test_waits_until_the_owner_has_been_away_long_enough(sessions: SessionFactory) -> None:
    s = Setup(sessions, config(delay_seconds=5, online_minutes=10))
    await s.receive(s.message(5, "Скоро буду", sender=OWNER, chat_id=8, minutes=-3))

    await s.receive(s.message(10))

    assert s.time.slept[:2] == [5, 415]
    assert len(s.bot.sent) == 1


async def test_messages_to_the_bot_count_as_being_online(sessions: SessionFactory) -> None:
    s = Setup(sessions, config(online_minutes=10))
    s.service.saw_owner(OWNER.id, NOW)

    await s.receive(s.message(10))

    assert s.time.slept == [0, 600]


async def test_replies_while_the_owner_is_online_when_allowed(sessions: SessionFactory) -> None:
    s = Setup(sessions, config(reply_when_online=True))
    s.service.saw_owner(OWNER.id, NOW)

    await s.receive(s.message(10))

    assert s.time.slept == [0]
    assert len(s.bot.sent) == 1


async def test_stays_silent_when_the_owner_answers_first(sessions: SessionFactory) -> None:
    s = Setup(sessions, config())

    await s.receive(s.message(10), s.message(11, "Привет, сейчас отвечу", sender=OWNER))

    assert s.bot.sent == [] and s.runner.calls == []


async def test_stops_after_the_reply_limit_until_the_owner_writes(sessions: SessionFactory) -> None:
    s = Setup(sessions, config(max_replies=1, reply_when_online=True))
    await s.receive(s.message(10))
    await s.receive(s.message(11, "Алло?", minutes=1))
    assert len(s.bot.sent) == 1

    await s.receive(s.message(12, "Я тут", sender=OWNER, minutes=2))
    await s.receive(s.message(13, "Ещё вопрос", minutes=3))

    assert len(s.bot.sent) == 2


async def test_stays_silent_outside_its_hours(sessions: SessionFactory) -> None:
    s = Setup(sessions, config(hours="09:00-14:00"))  # 12:00 UTC is 15:00 in Moscow

    await s.receive(s.message(10))

    assert s.bot.sent == []


async def test_ignores_accounts_outside_the_whitelist(sessions: SessionFactory) -> None:
    s = Setup(sessions, config())

    await s.receive(s.message(10, sender=OWNER, connection_id="stranger"))

    assert s.bot.sent == [] and await s.log.recent("stranger", 7, limit=10) == []


async def test_needs_the_right_to_reply(sessions: SessionFactory) -> None:
    s = Setup(sessions, config())
    s.bot.connections["conn"] = connection(can_reply=False)

    await s.receive(s.message(10))

    assert s.bot.sent == []


async def test_does_nothing_when_switched_off(sessions: SessionFactory) -> None:
    s = Setup(sessions, config(enabled=False))

    await s.receive(s.message(10))

    assert s.bot.sent == [] and await s.log.recent("conn", 7, limit=10) == []


async def test_a_failed_request_sends_nothing(sessions: SessionFactory) -> None:
    s = Setup(sessions, config())
    s.runner.error = RuntimeError("overloaded")

    await s.receive(s.message(10))

    assert s.bot.sent == []
    assert [m.sender for m in await s.log.recent("conn", 7, limit=10)] == ["person"]


async def test_a_message_that_arrives_while_sending_gets_its_own_reply(sessions: SessionFactory) -> None:
    s = Setup(sessions, config())
    s.bot.gate = asyncio.Event()
    await s.service.on_message(s.message(10, "Первый вопрос"))
    await s.bot.sending.wait()

    await s.service.on_message(s.message(11, "Второй вопрос"))
    s.bot.gate.set()
    await s.service.join()

    assert len(s.bot.sent) == 2
    second = s.runner.calls[1]["prompt"].partition("</context>")
    assert "Первый вопрос" in second[0] and "Второй вопрос" in second[2]


async def test_its_own_replies_coming_back_are_ignored(sessions: SessionFactory) -> None:
    s = Setup(sessions, config())

    await s.receive(s.message(10, "Передам", sender=OWNER, sender_business_bot=ME))

    assert await s.log.recent("conn", 7, limit=10) == []


async def test_voice_notes_are_transcribed_when_allowed(sessions: SessionFactory) -> None:
    voice = Voice(file_id="v", file_unique_id="vu", duration=4)
    s = Setup(sessions, config())
    await s.receive(s.message(10, None, voice=voice))
    assert "[голосовое 0:04]\nперезвони мне" in s.runner.calls[0]["prompt"]

    quiet = Setup(sessions, config(voice=False))
    await quiet.receive(quiet.message(20, None, voice=voice, chat_id=9))
    assert quiet.media.transcribed == [] and "[голосовое 0:04]" in quiet.runner.calls[0]["prompt"]


async def test_the_model_comes_from_the_config_or_the_owners_settings(sessions: SessionFactory) -> None:
    saved = {OWNER.id: {"model": "groq:openai/gpt-oss-120b"}}
    s = Setup(sessions, config(), saved=saved)
    await s.receive(s.message(10))
    assert s.registry.asked == ["groq:openai/gpt-oss-120b"]

    pinned = Setup(sessions, config(model="anthropic:claude-haiku-5-5"), saved=saved)
    await pinned.receive(pinned.message(20, chat_id=9))
    assert pinned.registry.asked == ["anthropic:claude-haiku-5-5"]


async def test_an_unknown_model_in_the_config_stops_the_bot(sessions: SessionFactory) -> None:
    with pytest.raises(ValueError, match=r"secretary\.toml"):
        Setup(sessions, config(model="openai:gpt-6-luna"))


async def test_connecting_tells_the_owner(sessions: SessionFactory) -> None:
    s = Setup(sessions, config())

    await s.service.on_connection(connection())
    await s.service.on_connection(connection(can_reply=False))
    await s.service.on_connection(connection(enabled=False))
    await s.service.on_connection(connection(PERSON, connection_id="stranger"))

    assert [m["chat_id"] for m in s.bot.sent] == [OWNER.id] * 3
    assert "Секретарь подключён" in s.bot.sent[0]["text"]
    assert "без права отвечать" in s.bot.sent[1]["text"]
    assert "отключён" in s.bot.sent[2]["text"]
