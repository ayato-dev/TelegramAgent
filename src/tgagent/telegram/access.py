import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from aiogram import BaseMiddleware, Bot
from aiogram.types import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
    TelegramObject,
    Update,
)

log = logging.getLogger(__name__)

GROUP_TYPES = {"group", "supergroup"}


class AccessPolicy:
    """Whitelisted users everywhere; groups only when a whitelisted user added the bot."""

    def __init__(self, users: frozenset[int], chats: set[int]) -> None:
        self._users = users
        self._chats = set(chats)

    def user_allowed(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self._users

    def chat_allowed(self, chat_id: int) -> bool:
        return chat_id in self._chats

    def allow_chat(self, chat_id: int) -> None:
        self._chats.add(chat_id)

    def revoke_chat(self, chat_id: int) -> None:
        self._chats.discard(chat_id)

    def message_allowed(self, message: Message) -> bool:
        if message.chat.type == "private":
            # The private chat id is its owner; service messages there (e.g. "topic renamed"
            # after the bot names a topic) are authored by the bot itself.
            return self.user_allowed(message.chat.id)
        if message.chat.type not in GROUP_TYPES:
            return False
        if self.chat_allowed(message.chat.id):
            return True
        migrated = message.migrate_to_chat_id or message.migrate_from_chat_id
        return migrated is not None and self.chat_allowed(migrated)


class AccessMiddleware(BaseMiddleware):
    """Outer update middleware: drops everything the policy does not allow.

    Strangers get ``denied_text`` (at most once per ``cooldown`` seconds each, so spamming
    the bot cannot get it flood-limited); an empty text keeps the bot silent.
    """

    def __init__(
        self,
        policy: AccessPolicy,
        leave_chat: Callable[[int], Awaitable[object]],
        *,
        denied_text: str = "",
        cooldown: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._leave_chat = leave_chat
        self._denied_text = denied_text
        self._cooldown = cooldown
        self._clock = clock
        self._left: set[int] = set()
        self._last_denied: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update = cast(Update, event)
        if self._allowed(update):
            return await handler(event, data)
        await self._leave_unknown_group(update)
        bot = data.get("bot")
        if self._denied_text and bot is not None:
            try:
                await self._deny(update, cast(Bot, bot))
            except Exception:
                log.debug("could not send access denial", exc_info=True)
        return None

    def _cooled_down(self, user_id: int) -> bool:
        now = self._clock()
        if now - self._last_denied.get(user_id, float("-inf")) < self._cooldown:
            return False
        self._last_denied[user_id] = now
        return True

    async def _deny(self, update: Update, bot: Bot) -> None:
        text = self._denied_text
        if query := update.callback_query:
            await bot.answer_callback_query(callback_query_id=query.id, text=text[:200])
            return
        if (guest := update.guest_message) and guest.guest_query_id and guest.from_user:
            if not guest.from_user.is_bot and self._cooled_down(guest.from_user.id):
                content = InputTextMessageContent(message_text=text)
                await bot.answer_guest_query(
                    guest_query_id=guest.guest_query_id,
                    result=InlineQueryResultArticle(
                        id="denied", title=text[:64], input_message_content=content
                    ),
                )
            return
        message = update.message
        if (
            message
            and message.chat.type == "private"
            and message.from_user
            and not message.from_user.is_bot
            and self._cooled_down(message.from_user.id)
        ):
            await bot.send_message(chat_id=message.chat.id, text=text)

    def _allowed(self, update: Update) -> bool:
        policy = self._policy
        if message := update.message or update.edited_message:
            return policy.message_allowed(message)
        if update.guest_message:
            user = update.guest_message.from_user
            return policy.user_allowed(user.id if user else None)
        if update.callback_query:
            return policy.user_allowed(update.callback_query.from_user.id)
        if update.stopped_message_generation:
            return policy.user_allowed(update.stopped_message_generation.chat.id)
        return update.my_chat_member is not None

    async def _leave_unknown_group(self, update: Update) -> None:
        message = update.message
        if message is None or message.chat.type not in GROUP_TYPES or message.chat.id in self._left:
            return
        self._left.add(message.chat.id)
        log.info("leaving chat %s: it was not added by an allowed user", message.chat.id)
        try:
            await self._leave_chat(message.chat.id)
        except Exception:
            log.warning("could not leave chat %s", message.chat.id, exc_info=True)
