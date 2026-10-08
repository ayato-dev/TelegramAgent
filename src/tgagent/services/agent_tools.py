"""Client tools the agent can call: reminders, chat history, polls, checklist replies."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputPollOption, InputRichMessage, ReplyParameters

from tgagent.agent.tools import ToolContext, ToolOutcome, ToolRegistry
from tgagent.context.formatting import media_label
from tgagent.context.media import MediaService
from tgagent.services.reminders import ReminderScheduler, utc_now
from tgagent.storage.repos import ChatLogRepo, ReminderRepo

HISTORY_LIMIT = 200
MAX_TRANSCRIPTS = 10
MAX_REMINDER_AHEAD = timedelta(days=366)


def _kind(mode: str) -> str:
    return "задача" if mode == "task" else "напоминание"


def error(text: str) -> ToolOutcome:
    return ToolOutcome(f"Ошибка: {text}", is_error=True)


class AgentTools:
    def __init__(
        self,
        bot: Bot,
        reminders: ReminderRepo,
        scheduler: ReminderScheduler,
        chat_log: ChatLogRepo,
        media: MediaService,
        *,
        tz: ZoneInfo,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._bot = bot
        self._reminders = reminders
        self._scheduler = scheduler
        self._chat_log = chat_log
        self._media = media
        self._tz = tz
        self._clock = clock

    def registry(self) -> ToolRegistry:
        return ToolRegistry(
            {
                "set_reminder": self.set_reminder,
                "list_reminders": self.list_reminders,
                "cancel_reminder": self.cancel_reminder,
                "read_chat_history": self.read_chat_history,
                "create_poll": self.create_poll,
                "reply_to_checklist_task": self.reply_to_checklist_task,
            }
        )

    def _local(self, moment: datetime) -> str:
        return moment.astimezone(self._tz).strftime("%Y-%m-%d %H:%M")

    async def set_reminder(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        if ctx.chat_kind == "guest":
            return error("в гостевом режиме напоминания недоступны — попросите в личном чате с ботом")
        try:
            due = datetime.fromisoformat(args["when"])
        except ValueError:
            return error("время должно быть в формате ISO 8601, например 2026-10-09T09:00:00+03:00")
        if due.tzinfo is None:
            due = due.replace(tzinfo=self._tz)
        now = self._clock()
        if due <= now:
            return error(f"это время уже прошло (сейчас {self._local(now)})")
        if due - now > MAX_REMINDER_AHEAD:
            return error("можно планировать не дальше чем на год вперёд")
        record = await self._reminders.create(
            ctx.chat_id, ctx.thread_id, ctx.user_id, due, args["text"], args["mode"]
        )
        self._scheduler.wake()
        return ToolOutcome(
            f"Поставлено: {_kind(record.mode)} #{record.id} на {self._local(due)} ({self._tz.key})."
        )

    async def list_reminders(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        items = await self._reminders.pending(ctx.chat_id)
        if not items:
            return ToolOutcome("Активных напоминаний нет.")
        lines = [
            f"#{item.id} — {self._local(item.due_at)} — {_kind(item.mode)}: {item.text}" for item in items
        ]
        return ToolOutcome("\n".join(lines))

    async def cancel_reminder(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        if await self._reminders.cancel(int(args["reminder_id"]), chat_id=ctx.chat_id):
            return ToolOutcome(f"Напоминание #{args['reminder_id']} отменено.")
        return error("активного напоминания с таким id в этом чате нет")

    async def read_chat_history(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        if ctx.chat_kind != "group":
            return error("история доступна только в группах")
        limit = max(1, min(int(args["limit"]), HISTORY_LIMIT))
        messages = await self._chat_log.recent(ctx.chat_id, ctx.thread_id, limit)
        if not messages:
            return ToolOutcome("В сохранённой истории чата пока нет сообщений.")
        transcribed = 0
        lines: list[str] = []
        for message in messages:
            parts: list[str] = []
            if message.media:
                parts.append(media_label(message.media))
                if message.media.kind in ("voice", "video_note") and transcribed < MAX_TRANSCRIPTS:
                    transcribed += 1
                    parts.append(
                        await self._media.transcript(message.media, user_id=ctx.user_id, chat_id=ctx.chat_id)
                    )
            if message.text:
                parts.append(message.text)
            author = message.sender_name + (" [бот]" if message.from_bot else "")
            lines.append(f"[{self._local(message.date)}] {author}: {' '.join(parts)}")
        return ToolOutcome("\n".join(lines))

    async def create_poll(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        if ctx.chat_kind == "guest":
            return error("в гостевом режиме опросы недоступны")
        options = [str(option).strip()[:100] for option in args["options"] if str(option).strip()]
        if not 2 <= len(options) <= 12:
            return error("в опросе должно быть от 2 до 12 вариантов")
        await self._bot.send_poll(
            chat_id=ctx.chat_id,
            question=str(args["question"])[:300],
            options=[InputPollOption(text=option) for option in options],
            is_anonymous=False,
            allows_multiple_answers=bool(args["allows_multiple_answers"]),
            message_thread_id=ctx.thread_id,
        )
        return ToolOutcome("Опрос отправлен в чат.")

    async def reply_to_checklist_task(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
        if ctx.chat_kind == "guest":
            return error("в гостевом режиме ответы на задачи недоступны")
        reply = ReplyParameters(
            message_id=int(args["checklist_message_id"]),
            checklist_task_id=int(args["task_id"]),
            allow_sending_without_reply=True,
        )
        try:
            await self._bot.send_rich_message(
                chat_id=ctx.chat_id,
                rich_message=InputRichMessage(markdown=args["text"]),
                message_thread_id=ctx.thread_id,
                reply_parameters=reply,
            )
        except TelegramBadRequest:
            await self._bot.send_message(
                chat_id=ctx.chat_id,
                text=args["text"],
                message_thread_id=ctx.thread_id,
                reply_parameters=reply,
            )
        return ToolOutcome(f"Ответ на задачу #{args['task_id']} отправлен.")
