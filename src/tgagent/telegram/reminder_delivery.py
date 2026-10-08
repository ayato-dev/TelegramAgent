from collections.abc import Callable, Collection
from datetime import datetime

from aiogram import Bot

from tgagent.config import Effort
from tgagent.domain import NormalizedMessage
from tgagent.services.reminders import utc_now
from tgagent.services.settings import options_from
from tgagent.services.turns import TurnRequest, TurnService
from tgagent.storage.repos import ChatRepo, ReminderRecord, UserRepo
from tgagent.telegram.render import send_markdown
from tgagent.telegram.sinks import PlainSink


class ReminderDelivery:
    """Fires a due reminder: a plain notification, or a full agent turn for scheduled tasks."""

    def __init__(
        self,
        bot: Bot,
        turns: TurnService,
        chats: ChatRepo,
        users: UserRepo,
        *,
        default_effort: Effort,
        models: Collection[str] = (),
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._bot = bot
        self._turns = turns
        self._chats = chats
        self._users = users
        self._default_effort = default_effort
        self._models = models
        self._clock = clock

    async def __call__(self, item: ReminderRecord) -> None:
        if item.mode == "notify":
            await self._notify(item)
        else:
            await self._run_task(item)

    async def _notify(self, item: ReminderRecord) -> None:
        text = f"⏰ **Напоминание**\n\n{item.text}"
        if item.chat_id != item.user_id:
            name = (await self._users.names([item.user_id])).get(item.user_id, "напоминание")
            text = f"[{name}](tg://user?id={item.user_id}), {text}"
        await send_markdown(self._bot, item.chat_id, text, thread_id=item.thread_id)

    async def _run_task(self, item: ReminderRecord) -> None:
        trigger = NormalizedMessage(
            chat_id=item.chat_id,
            message_id=0,
            thread_id=item.thread_id,
            sender_id=item.user_id,
            sender_name="планировщик",
            date=self._clock(),
            text=f"[Запланированное задание #{item.id} — выполни его сейчас и пришли результат]\n{item.text}",
        )
        options = options_from(
            await self._chats.get_settings(item.chat_id),
            default_effort=self._default_effort,
            models=self._models,
        )
        request = TurnRequest(
            kind="reminder",
            chat_id=item.chat_id,
            thread_id=item.thread_id,
            chat_title=None,
            user_id=item.user_id,
            trigger=trigger,
            options=options,
        )
        await self._turns.run(request, PlainSink(self._bot, item.chat_id, item.thread_id))
