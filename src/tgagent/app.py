import asyncio
import logging
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiogram import Bot
from anthropic import AsyncAnthropic
from groq import AsyncGroq

from tgagent.agent.models import ModelSpec, available_models, parse_key
from tgagent.agent.providers.claude import ClaudeRunner
from tgagent.agent.registry import ProviderRegistry
from tgagent.config import Settings
from tgagent.context.builder import ContentBuilder
from tgagent.context.media import MediaService
from tgagent.services.agent_tools import AgentTools
from tgagent.services.generation import GenerationRegistry, KeyedLocks
from tgagent.services.reminders import ReminderScheduler
from tgagent.services.titles import TopicTitler
from tgagent.services.turns import TurnService
from tgagent.services.usage_report import UsageReport
from tgagent.storage.db import create_engine, create_sessionmaker
from tgagent.storage.repos import (
    ChatLogRepo,
    ChatRepo,
    ConversationRepo,
    MediaRepo,
    ReminderRecord,
    ReminderRepo,
    UsageRepo,
    UserRepo,
)
from tgagent.stt.groq import GroqTranscriber
from tgagent.telegram.access import AccessPolicy
from tgagent.telegram.albums import AlbumCollector
from tgagent.telegram.deps import Deps
from tgagent.telegram.dispatcher import build_dispatcher, setup_commands
from tgagent.telegram.reminder_delivery import ReminderDelivery

log = logging.getLogger("tgagent")


class TelegramFiles:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def download(self, file_id: str) -> bytes:
        buffer = await self._bot.download(file_id)
        if buffer is None:
            raise RuntimeError(f"empty download for {file_id}")
        return buffer.read()


class AnthropicFiles:
    def __init__(self, client: AsyncAnthropic) -> None:
        self._client = client

    async def upload(self, filename: str, data: bytes, mime_type: str) -> str:
        return (await self._client.files.upload(file=(filename, data, mime_type))).id


async def heartbeat(path: Path) -> None:
    """Docker healthcheck reads this file's age."""
    while True:
        await asyncio.to_thread(path.write_text, str(time.time()))
        await asyncio.sleep(30)


async def purge_chat_log(chat_log: ChatLogRepo, days: int) -> None:
    while True:
        try:
            removed = await chat_log.purge_older_than(datetime.now(UTC) - timedelta(days=days))
            if removed:
                log.info("purged %d old chat log messages", removed)
        except Exception:
            log.exception("chat log purge failed")
        await asyncio.sleep(6 * 3600)


async def _stop(tasks: list[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def serve(settings: Settings, stack: AsyncExitStack) -> None:
    engine = create_engine(settings.database_url)
    stack.push_async_callback(engine.dispose)
    sessions = create_sessionmaker(engine)
    users, chats, chat_log = UserRepo(sessions), ChatRepo(sessions), ChatLogRepo(sessions)
    conversations, usage, reminders = ConversationRepo(sessions), UsageRepo(sessions), ReminderRepo(sessions)

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    stack.push_async_callback(bot.session.close)
    provider, _ = parse_key(settings.default_model)
    anthropic_key, groq_key = settings.api_key("anthropic"), settings.api_key("groq")
    if provider != "anthropic" or anthropic_key is None or groq_key is None:
        raise RuntimeError(
            "this build runs Anthropic models only and needs ANTHROPIC_API_KEY and GROQ_API_KEY"
        )
    anthropic = AsyncAnthropic(api_key=anthropic_key.get_secret_value(), max_retries=3)
    stack.push_async_callback(anthropic.close)
    groq = AsyncGroq(api_key=groq_key.get_secret_value(), max_retries=2)
    stack.push_async_callback(groq.close)

    me = await bot.get_me()
    log.info(
        "starting @%s (topics=%s, reads all group messages=%s)",
        me.username,
        me.has_topics_enabled,
        me.can_read_all_group_messages,
    )

    media = MediaService(
        TelegramFiles(bot),
        AnthropicFiles(anthropic),
        GroqTranscriber(groq, settings.whisper_model),
        MediaRepo(sessions),
        usage,
        whisper_model=settings.whisper_model,
        whisper_paid=settings.groq_paid_tier,
    )

    async def fire(item: ReminderRecord) -> None:
        await delivery(item)

    scheduler = ReminderScheduler(reminders, fire)
    tool_registry = AgentTools(bot, reminders, scheduler, chat_log, media, tz=settings.tz).registry()
    builder = ContentBuilder(media, settings.tz)

    def claude(spec: ModelSpec) -> ClaudeRunner:
        return ClaudeRunner(
            anthropic,
            spec,
            builder=builder,
            registry=tool_registry,
            web_max_uses=settings.web_search_max_uses,
        )

    runners = ProviderRegistry(available_models(settings), settings.default_model, {"anthropic": claude})
    turns = TurnService(
        runners,
        conversations,
        usage,
        chat_log,
        tz=settings.tz,
        bot_name=me.first_name,
        on_title=TopicTitler(runners, bot, conversations, usage),
    )
    delivery = ReminderDelivery(
        bot,
        turns,
        chats,
        users,
        default_effort=settings.default_effort,
        models=[spec.key for spec in runners.models],
    )

    deps = Deps(
        settings=settings,
        me=me,
        policy=AccessPolicy(settings.allowed_user_ids, await chats.allowed_ids()),
        turns=turns,
        chats=chats,
        users=users,
        chat_log=chat_log,
        conversations=conversations,
        usage_report=UsageReport(usage, users, settings.tz),
        locks=KeyedLocks(),
        generations=GenerationRegistry(),
        albums=AlbumCollector(),
        models=tuple(runners.models),
    )
    dispatcher = build_dispatcher(deps, bot)
    await setup_commands(bot)

    background = [
        asyncio.create_task(scheduler.run()),
        asyncio.create_task(heartbeat(settings.heartbeat_path)),
        asyncio.create_task(purge_chat_log(chat_log, settings.chat_log_retention_days)),
    ]
    stack.push_async_callback(_stop, background)
    await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())


async def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    async with AsyncExitStack() as stack:
        await serve(settings, stack)


def run() -> None:
    asyncio.run(main())
