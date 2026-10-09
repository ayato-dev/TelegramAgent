import asyncio
import logging
import time
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiogram import Bot
from anthropic import AsyncAnthropic
from google import genai
from groq import AsyncGroq
from openai import AsyncOpenAI

from tgagent.agent.base import LLMRunner
from tgagent.agent.models import ModelSpec, Provider, available_models
from tgagent.agent.prompt import prompts_dir, secretary_prompt
from tgagent.agent.providers.chat import ChatRunner
from tgagent.agent.providers.claude import ClaudeRunner
from tgagent.agent.providers.gemini import GeminiRunner
from tgagent.agent.providers.responses import ResponsesRunner
from tgagent.agent.registry import ProviderRegistry, RunnerFactory
from tgagent.agent.tools import ToolRegistry
from tgagent.config import Settings
from tgagent.context.builder import ContentBuilder
from tgagent.context.encoders import ChatEncoder, GeminiEncoder, ResponsesEncoder
from tgagent.context.media import MediaService
from tgagent.services.agent_tools import AgentTools
from tgagent.services.generation import GenerationRegistry, KeyedLocks
from tgagent.services.reminders import ReminderScheduler
from tgagent.services.secretary import SecretaryService
from tgagent.services.secretary_config import load_secretary_config
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
    SecretaryLogRepo,
    UsageRepo,
    UserRepo,
)
from tgagent.stt.groq import GroqTranscriber
from tgagent.telegram.access import AccessPolicy
from tgagent.telegram.bursts import BurstCollector
from tgagent.telegram.deps import Deps
from tgagent.telegram.dispatcher import build_dispatcher, setup_commands
from tgagent.telegram.reminder_delivery import ReminderDelivery
from tgagent.telegram.webhook import run_webhook, webhook_secret

log = logging.getLogger("tgagent")

CHAT_BASE_URLS: dict[Provider, str] = {
    "deepseek": "https://api.deepseek.com",
    "groq": "https://api.groq.com/openai/v1",
}


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


class OpenAIFiles:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def upload(self, filename: str, data: bytes, mime_type: str) -> str:
        return (await self._client.files.create(file=(filename, data, mime_type), purpose="user_data")).id


def build_runners(
    settings: Settings,
    stack: AsyncExitStack,
    anthropic: AsyncAnthropic | None,
    media: MediaService,
    cache: MediaRepo,
    tools: ToolRegistry,
) -> ProviderRegistry:
    """One runner factory per provider with a key; each model gets the media encoding it can read."""
    tz = settings.tz
    factories: dict[Provider, RunnerFactory] = {}

    def text_limit(spec: ModelSpec) -> int:
        # Characters of a document inlined as text: about half of the model's context budget.
        return spec.context_trigger * 2

    if anthropic is not None:
        claude_builder = ContentBuilder(media, tz)

        def claude(spec: ModelSpec) -> LLMRunner:
            web_max_uses = settings.web_search_max_uses
            return ClaudeRunner(
                anthropic, spec, builder=claude_builder, registry=tools, web_max_uses=web_max_uses
            )

        factories["anthropic"] = claude

    if key := settings.api_key("openai"):
        openai_client = AsyncOpenAI(api_key=key.get_secret_value())
        stack.push_async_callback(openai_client.close)
        files = OpenAIFiles(openai_client)

        def openai(spec: ModelSpec) -> LLMRunner:
            encoder = ResponsesEncoder(media, files, cache, text_limit=text_limit(spec))
            return ResponsesRunner(openai_client, spec, builder=ContentBuilder(encoder, tz), registry=tools)

        factories["openai"] = openai

    if key := settings.api_key("gemini"):
        gemini_client = genai.Client(api_key=key.get_secret_value())
        stack.push_async_callback(gemini_client.aio.aclose)

        def gemini(spec: ModelSpec) -> LLMRunner:
            encoder = GeminiEncoder(media, text_limit=text_limit(spec))
            return GeminiRunner(gemini_client, spec, builder=ContentBuilder(encoder, tz), registry=tools)

        factories["gemini"] = gemini

    def chat(client: AsyncOpenAI) -> RunnerFactory:
        def factory(spec: ModelSpec) -> LLMRunner:
            encoder = ChatEncoder(media, vision=spec.vision, text_limit=text_limit(spec))
            return ChatRunner(client, spec, builder=ContentBuilder(encoder, tz), registry=tools)

        return factory

    for provider, base_url in CHAT_BASE_URLS.items():
        if key := settings.api_key(provider):
            client = AsyncOpenAI(api_key=key.get_secret_value(), base_url=base_url)
            stack.push_async_callback(client.close)
            factories[provider] = chat(client)

    return ProviderRegistry(available_models(settings), settings.default_model, factories)


async def heartbeat(path: Path) -> None:
    """Docker healthcheck reads this file's age."""
    while True:
        await asyncio.to_thread(path.write_text, str(time.time()))
        await asyncio.sleep(30)


async def purge_old_chat_log(chat_log: ChatLogRepo, secretary_log: SecretaryLogRepo, days: int) -> int:
    """Group messages and the secretary's chats are kept for the same number of days."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    removed = await chat_log.purge_older_than(cutoff) + await secretary_log.purge_older_than(cutoff)
    if removed:
        log.info("purged %d old chat log messages", removed)
    return removed


async def purge_chat_log(chat_log: ChatLogRepo, secretary_log: SecretaryLogRepo, days: int) -> None:
    while True:
        try:
            await purge_old_chat_log(chat_log, secretary_log, days)
        except Exception:
            log.exception("chat log purge failed")
        await asyncio.sleep(6 * 3600)


async def _stop(tasks: list[asyncio.Task[None]]) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def serve(settings: Settings, stack: AsyncExitStack) -> None:
    secretary_config = load_secretary_config(prompts_dir() / "secretary.toml")
    engine = create_engine(settings.database_url)
    stack.push_async_callback(engine.dispose)
    sessions = create_sessionmaker(engine)
    users, chats, chat_log = UserRepo(sessions), ChatRepo(sessions), ChatLogRepo(sessions)
    conversations, usage, reminders = ConversationRepo(sessions), UsageRepo(sessions), ReminderRepo(sessions)
    secretary_log = SecretaryLogRepo(sessions)

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    stack.push_async_callback(bot.session.close)
    anthropic: AsyncAnthropic | None = None
    if key := settings.api_key("anthropic"):
        anthropic = AsyncAnthropic(api_key=key.get_secret_value(), max_retries=3)
        stack.push_async_callback(anthropic.close)
    # Groq also transcribes voice for models that cannot hear it themselves.
    transcriber: GroqTranscriber | None = None
    if key := settings.api_key("groq"):
        groq = AsyncGroq(api_key=key.get_secret_value(), max_retries=2)
        stack.push_async_callback(groq.close)
        transcriber = GroqTranscriber(groq, settings.whisper_model)

    me = await bot.get_me()
    log.info(
        "starting @%s (topics=%s, reads all group messages=%s)",
        me.username,
        me.has_topics_enabled,
        me.can_read_all_group_messages,
    )

    media_cache = MediaRepo(sessions)
    media = MediaService(
        TelegramFiles(bot),
        AnthropicFiles(anthropic) if anthropic else None,
        transcriber,
        media_cache,
        usage,
        whisper_model=settings.whisper_model,
        whisper_paid=settings.groq_paid_tier,
    )

    async def fire(item: ReminderRecord) -> None:
        await delivery(item)

    scheduler = ReminderScheduler(reminders, fire, poll_interval=settings.reminder_poll_seconds)
    tool_registry = AgentTools(bot, reminders, scheduler, chat_log, media, tz=settings.tz).registry()
    runners = build_runners(settings, stack, anthropic, media, media_cache, tool_registry)
    log.info("models: %s (default %s)", ", ".join(spec.key for spec in runners.models), runners.default_model)
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

    secretary = SecretaryService(
        bot,
        secretary_log,
        runners,
        usage,
        media,
        chats,
        secretary_config,
        prompt=secretary_prompt(),
        allowed_users=settings.allowed_user_ids,
        bot_id=me.id,
        bot_name=me.first_name,
        tz=settings.tz,
    )
    log.info("secretary mode: %s", "on" if secretary_config.enabled else "off")

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
        bursts=BurstCollector(),
        models=tuple(runners.models),
        secretary=secretary,
    )
    dispatcher = build_dispatcher(deps, bot)
    await setup_commands(bot)

    background = [
        asyncio.create_task(scheduler.run()),
        asyncio.create_task(heartbeat(settings.heartbeat_path)),
        asyncio.create_task(purge_chat_log(chat_log, secretary_log, settings.chat_log_retention_days)),
    ]
    stack.push_async_callback(_stop, background)
    if settings.webhook_url:

        async def cron() -> dict[str, int]:
            fired = await scheduler.tick()
            purged = await purge_old_chat_log(chat_log, secretary_log, settings.chat_log_retention_days)
            return {"reminders": fired, "purged_messages": purged}

        token = settings.telegram_bot_token.get_secret_value()
        configured = settings.webhook_secret.get_secret_value() if settings.webhook_secret else None
        await run_webhook(
            dispatcher,
            bot,
            public_url=settings.webhook_url,
            secret=webhook_secret(token, configured),
            port=settings.port,
            inline=settings.webhook_inline,
            on_cron=cron,
        )
        return
    # A webhook left over from webhook mode would make polling fail.
    await bot.delete_webhook()
    await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())


async def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    async with AsyncExitStack() as stack:
        await serve(settings, stack)


def run() -> None:
    asyncio.run(main())
