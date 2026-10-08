import asyncio
import logging
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiogram import Bot
from anthropic import AsyncAnthropic
from groq import AsyncGroq

from tgagent.agent.runner import AgentRunner
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


async def web_search_supported(client: AsyncAnthropic, model: str) -> bool:
    try:
        info = await client.models.retrieve(model)
    except Exception:
        log.warning("Models API unavailable, assuming web search is supported", exc_info=True)
        return True
    tools = info.capabilities.server_tools if info.capabilities else None
    supported = tools is None or tools.web_search is None or tools.web_search.supported
    if not supported:
        log.warning("%s does not support web search; web tools disabled", model)
    return supported


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
    anthropic = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value(), max_retries=3)
    stack.push_async_callback(anthropic.close)
    groq = AsyncGroq(api_key=settings.groq_api_key.get_secret_value(), max_retries=2)
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
    )

    async def fire(item: ReminderRecord) -> None:
        await delivery(item)

    scheduler = ReminderScheduler(reminders, fire)
    tools = AgentTools(bot, reminders, scheduler, chat_log, media, tz=settings.tz)
    runner = AgentRunner(
        anthropic,
        model=settings.anthropic_model,
        max_tokens=settings.max_output_tokens,
        compaction_trigger=settings.compaction_trigger_tokens,
        registry=tools.registry(),
        web_supported=await web_search_supported(anthropic, settings.anthropic_model),
        web_max_uses=settings.web_search_max_uses,
    )
    turns = TurnService(
        runner,
        ContentBuilder(media, settings.tz),
        conversations,
        usage,
        chat_log,
        tz=settings.tz,
        bot_name=me.first_name,
        on_title=TopicTitler(anthropic, bot, conversations, usage, settings.anthropic_model),
    )
    delivery = ReminderDelivery(bot, turns, chats, users, default_effort=settings.default_effort)

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
