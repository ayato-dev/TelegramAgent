"""Secretary mode: replies in the owner's private chats through a Telegram Business connection.

Telegram sends the bot every message of the chats the owner chose, the owner's own included. Each chat
gets at most one pending reply at a time: a new message from the person restarts the wait, a message
from the owner cancels it. The rules for when to reply come from prompts/secretary.toml.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import BusinessConnection, Message

from tgagent.agent.pricing import turn_cost
from tgagent.agent.registry import ProviderRegistry
from tgagent.context.formatting import format_time, media_label
from tgagent.context.media import MediaService
from tgagent.context.normalize import normalize
from tgagent.i18n import lang_of, t
from tgagent.services.secretary_config import SecretaryConfig
from tgagent.storage.repos import ChatRepo, LoggedMessage, SecretaryLogRepo, Sender, UsageRecord, UsageRepo

log = logging.getLogger(__name__)

MAX_REPLY_TOKENS = 1_024
TELEGRAM_LIMIT = 4_096
TYPING_EVERY = 4.0
ROLES = {"person": "person", "owner": "owner", "bot": "you"}

type ChatKey = tuple[str, int]


def unanswered(history: Sequence[LoggedMessage]) -> list[LoggedMessage]:
    """The person's messages newer than anything the owner wrote or the bot answered."""
    answered = 0
    for message in history:
        if message.sender == "owner":
            answered = max(answered, message.message_id)
        elif message.sender == "bot":
            answered = max(answered, message.reply_to_message_id or message.message_id)
    return [m for m in history if m.sender == "person" and m.message_id > answered]


class SecretaryService:
    def __init__(
        self,
        bot: Bot,
        log_repo: SecretaryLogRepo,
        runners: ProviderRegistry,
        usage: UsageRepo,
        media: MediaService,
        chats: ChatRepo,
        config: SecretaryConfig,
        *,
        prompt: str,
        allowed_users: frozenset[int],
        bot_id: int,
        bot_name: str,
        tz: ZoneInfo,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if config.model and not runners.supports(config.model):
            raise ValueError(f"secretary.toml: model {config.model} is not available, check its API key")
        self._bot = bot
        self._log = log_repo
        self._runners = runners
        self._usage = usage
        self._media = media
        self._chats = chats
        self._config = config
        self._prompt = prompt
        self._allowed = allowed_users
        self._bot_id = bot_id
        self._bot_name = bot_name
        self._tz = tz
        self._clock = clock
        self._sleep = sleep
        self._connections: dict[str, BusinessConnection] = {}
        self._seen: dict[int, datetime | None] = {}
        self._workers: dict[ChatKey, asyncio.Task[None]] = {}
        self._sending: set[ChatKey] = set()
        self._pending: set[ChatKey] = set()

    async def on_connection(self, connection: BusinessConnection) -> None:
        """The owner connected, changed or disconnected the bot in Telegram Business settings."""
        self._connections[connection.id] = connection
        owner = connection.user
        if owner.id not in self._allowed:
            log.warning("ignoring business connection of user %s: not in ALLOWED_USER_IDS", owner.id)
            return
        if not connection.is_enabled:
            key = "secretary.disconnected"
        elif not self._config.enabled:
            key = "secretary.off"
        elif not self._can_reply(connection):
            key = "secretary.no_reply"
        else:
            key = "secretary.connected"
        try:
            await self._bot.send_message(
                chat_id=connection.user_chat_id, text=t(lang_of(owner.language_code), key)
            )
        except Exception:
            log.warning("could not tell user %s about the business connection", owner.id, exc_info=True)

    async def on_message(self, message: Message) -> None:
        if not self._config.enabled or message.business_connection_id is None or message.from_user is None:
            return
        if message.sender_business_bot is not None and message.sender_business_bot.id == self._bot_id:
            return  # the bot's own reply coming back
        connection = await self._connection(message.business_connection_id)
        if connection is None:
            return
        owner = connection.user
        sender: Sender = "owner" if message.from_user.id == owner.id else "person"
        if sender == "person" and message.from_user.is_bot:
            return
        normalized = normalize(message)
        await self._log.add(
            LoggedMessage(
                connection_id=connection.id,
                chat_id=message.chat.id,
                message_id=message.message_id,
                sender=sender,
                sender_id=message.from_user.id,
                sender_name=normalized.sender_name,
                date=normalized.date,
                text=normalized.text,
                media=normalized.media,
            )
        )
        key = (connection.id, message.chat.id)
        if sender == "owner":
            self.saw_owner(owner.id, normalized.date)
            self._cancel(key)
        elif key in self._sending:
            self._pending.add(key)
        else:
            self._cancel(key)
            self._start(key, connection)

    def saw_owner(self, user_id: int, when: datetime) -> None:
        """The owner wrote somewhere: they count as online for ``online_minutes`` from ``when``."""
        last = self._seen.get(user_id)
        if last is None or when > last:
            self._seen[user_id] = when

    async def join(self) -> None:
        """Wait until every scheduled reply is sent or dropped."""
        while self._workers:
            await asyncio.gather(*self._workers.values(), return_exceptions=True)

    async def _connection(self, connection_id: str) -> BusinessConnection | None:
        """The connection when the bot may reply through it."""
        connection = self._connections.get(connection_id)
        if connection is None:
            try:
                connection = await self._bot.get_business_connection(business_connection_id=connection_id)
            except Exception:
                log.warning("could not load business connection %s", connection_id, exc_info=True)
                return None
            self._connections[connection_id] = connection
        usable = connection.is_enabled and connection.user.id in self._allowed and self._can_reply(connection)
        return connection if usable else None

    @staticmethod
    def _can_reply(connection: BusinessConnection) -> bool:
        return connection.rights is not None and bool(connection.rights.can_reply)

    def _start(self, key: ChatKey, connection: BusinessConnection) -> None:
        task = asyncio.create_task(self._work(key, connection))
        self._workers[key] = task

        def forget(done: asyncio.Task[None]) -> None:
            if self._workers.get(key) is done:
                del self._workers[key]

        task.add_done_callback(forget)

    def _cancel(self, key: ChatKey) -> None:
        self._pending.discard(key)
        task = self._workers.get(key)
        if task is not None and key not in self._sending:
            task.cancel()

    async def _work(self, key: ChatKey, connection: BusinessConnection) -> None:
        try:
            await self._sleep(self._config.delay_seconds)
            await self._wait_until_away(connection.user.id)
            reply = await self._compose(key, connection)
            if reply is None:
                return
            self._sending.add(key)
            try:
                await self._deliver(key, *reply)
            finally:
                self._sending.discard(key)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("secretary failed in chat %s", key[1])
            return
        if key in self._pending:
            self._pending.discard(key)
            self._start(key, connection)

    async def _wait_until_away(self, owner_id: int) -> None:
        if self._config.reply_when_online:
            return
        if owner_id not in self._seen:
            self._seen[owner_id] = await self._log.owner_last_seen(owner_id)
        while (last := self._seen[owner_id]) is not None:
            wait = (last + timedelta(minutes=self._config.online_minutes) - self._clock()).total_seconds()
            if wait <= 0:
                return
            await self._sleep(wait)

    async def _compose(self, key: ChatKey, connection: BusinessConnection) -> tuple[str, int] | None:
        """The reply text and the newest message it answers; None when the bot should stay silent."""
        connection_id, chat_id = key
        config, now = self._config, self._clock()
        if not config.answers_at(now.astimezone(self._tz).time()):
            return None
        if config.max_replies:
            replies = await self._log.replies_since(
                connection_id, chat_id, now - timedelta(hours=config.limit_hours)
            )
            if replies >= config.max_replies:
                return None
        history = await self._log.recent(connection_id, chat_id, config.history)
        new = unanswered(history)
        if not new:
            return None
        owner = connection.user
        prompt = await self._render_chat(owner.id, owner.first_name, history, new)
        runner = self._runners.runner(await self._model(owner.id))
        typing = asyncio.create_task(self._typing(connection_id, chat_id))
        try:
            text, usage = await runner.complete(prompt, max_tokens=MAX_REPLY_TOKENS, system=self._prompt)
        finally:
            typing.cancel()
        record = UsageRecord(
            owner.id,
            chat_id,
            "secretary",
            runner.spec.key,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
        )
        await self._usage.add(record.with_cost(turn_cost(runner.spec, usage)))
        text = text.strip()
        return (text[:TELEGRAM_LIMIT], new[-1].message_id) if text else None

    async def _model(self, owner_id: int) -> str:
        if self._config.model:
            return self._config.model
        chosen = (await self._chats.get_settings(owner_id)).get("model")
        if isinstance(chosen, str) and self._runners.supports(chosen):
            return chosen
        return self._runners.default_model

    async def _render_chat(
        self, owner_id: int, owner_name: str, history: Sequence[LoggedMessage], new: Sequence[LoggedMessage]
    ) -> str:
        lines = [f'<environment owner="{escape(owner_name)}" time="{format_time(self._clock(), self._tz)}"/>']
        new_ids = {m.message_id for m in new}
        earlier = [m for m in history if m.message_id not in new_ids]
        if earlier:
            lines += ["<context>", *[await self._render(m, owner_id) for m in earlier], "</context>"]
        lines += [await self._render(m, owner_id) for m in new]
        return "\n".join(lines)

    async def _render(self, message: LoggedMessage, owner_id: int) -> str:
        attrs = [f'role="{ROLES[message.sender]}"']
        if message.sender != "bot":
            attrs.append(f'name="{escape(message.sender_name)}"')
        attrs.append(f'time="{format_time(message.date, self._tz)}"')
        body: list[str] = []
        if message.media:
            body.append(media_label(message.media))
            if self._config.voice and message.media.kind in ("voice", "video_note"):
                body.append(
                    await self._media.transcript(message.media, user_id=owner_id, chat_id=message.chat_id)
                )
        if message.text:
            body.append(message.text)
        return f"<message {' '.join(attrs)}>\n" + "\n".join(body) + "\n</message>"

    async def _typing(self, connection_id: str, chat_id: int) -> None:
        while True:
            try:
                await self._bot.send_chat_action(
                    chat_id=chat_id, action="typing", business_connection_id=connection_id
                )
            except Exception:
                log.debug("could not show typing in chat %s", chat_id, exc_info=True)
            await asyncio.sleep(TYPING_EVERY)

    async def _deliver(self, key: ChatKey, text: str, answered: int) -> None:
        connection_id, chat_id = key
        sent = await self._bot.send_message(chat_id=chat_id, text=text, business_connection_id=connection_id)
        await self._log.add(
            LoggedMessage(
                connection_id=connection_id,
                chat_id=chat_id,
                message_id=sent.message_id,
                sender="bot",
                sender_id=None,
                sender_name=self._bot_name,
                date=sent.date,
                text=text,
                reply_to_message_id=answered,
            )
        )
