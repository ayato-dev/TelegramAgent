"""Response sinks turn agent events into Telegram output: drafts, edited messages or a guest reply."""

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineQueryResultArticle,
    InputRichMessage,
    InputRichMessageContent,
    InputTextMessageContent,
)

from tgagent.agent.events import AgentEvent, FileProduced, TextDelta, ThinkingDelta, ToolStarted
from tgagent.telegram.render import (
    RICH_LIMIT,
    TEXT_LIMIT,
    compose_draft,
    edit_markdown,
    entity_chunks,
    reply_parameters,
    send_files,
    send_markdown,
    split_markdown,
)

log = logging.getLogger(__name__)

TOOL_LABELS = {
    "web_search": "🔎 Ищу",
    "web_fetch": "🌐 Читаю",
    "code_execution": "🐍 Считаю",
    "bash_code_execution": "🐍 Выполняю код",
    "text_editor_code_execution": "📝 Работаю с файлом",
    "compaction": "🗜 Сжимаю историю",
    "set_reminder": "⏰ Ставлю напоминание",
    "list_reminders": "⏰ Смотрю напоминания",
    "cancel_reminder": "⏰ Отменяю напоминание",
    "read_chat_history": "📜 Читаю чат",
    "create_poll": "📊 Создаю опрос",
    "reply_to_checklist_task": "✅ Отчитываюсь по задаче",
}


def tool_status(event: ToolStarted) -> str:
    label = TOOL_LABELS.get(event.name, f"🛠 {event.name}")
    return f"{label}: {event.summary}" if event.summary else f"{label}…"


class ResponseSink(Protocol):
    async def start(self) -> None: ...

    async def on_event(self, event: AgentEvent) -> None: ...

    async def finish(self, markdown: str, files: Sequence[FileProduced]) -> list[int]:
        """Persist the final answer; returns ids of messages that carry it."""
        ...

    async def fail(self, text: str) -> None: ...


@dataclass(slots=True)
class StreamState:
    answer: str = ""
    thinking: str = ""
    status: str | None = None

    def apply(self, event: AgentEvent) -> bool:
        match event:
            case TextDelta(text=text):
                self.answer += text
                self.status = None
            case ThinkingDelta(text=text):
                self.thinking += text
            case ToolStarted():
                self.status = tool_status(event)
            case _:
                return False
        return True


class Ticker:
    """Runs ``flush`` at most once per ``interval`` while there are changes; optional idle keepalive."""

    def __init__(
        self, flush: Callable[[], Awaitable[None]], interval: float, keepalive: float | None
    ) -> None:
        self._flush = flush
        self._interval = interval
        self._keepalive = keepalive
        self._dirty = False
        self._last = 0.0
        self._task: asyncio.Task[None] | None = None

    def touch(self) -> None:
        self._dirty = True
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            idle = self._keepalive is not None and time.monotonic() - self._last >= self._keepalive
            if self._dirty or idle:
                self._dirty = False
                self._last = time.monotonic()
                try:
                    await self._flush()
                except Exception:
                    log.debug("streaming update failed", exc_info=True)
            await asyncio.sleep(self._interval)


class DraftSink:
    """Private chats: native streaming drafts with a Stop button, then a persisted message."""

    def __init__(
        self, bot: Bot, chat_id: int, thread_id: int | None, *, interval: float = 0.6, keepalive: float = 10.0
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = thread_id
        self.draft_id = secrets.randbelow(2**31 - 2) + 1
        self._state = StreamState()
        self._rich = True
        self._ticker = Ticker(self._flush, interval, keepalive)

    async def start(self) -> None:
        self._ticker.touch()

    async def on_event(self, event: AgentEvent) -> None:
        if self._state.apply(event):
            self._ticker.touch()

    async def _flush(self) -> None:
        state = self._state
        if self._rich:
            try:
                await self._bot.send_rich_message_draft(
                    chat_id=self._chat_id,
                    draft_id=self.draft_id,
                    rich_message=InputRichMessage(
                        markdown=compose_draft(state.answer, state.thinking, state.status)
                    ),
                    message_thread_id=self._thread_id,
                    can_stop=True,
                )
                return
            except TelegramBadRequest as exc:
                log.info("rich drafts rejected (%s), using plain drafts", exc.message)
                self._rich = False
        text = state.answer[-TEXT_LIMIT:] if state.answer else (state.status or "")
        await self._bot.send_message_draft(
            chat_id=self._chat_id,
            draft_id=self.draft_id,
            text=text,
            message_thread_id=self._thread_id,
            can_stop=True,
        )

    async def finish(self, markdown: str, files: Sequence[FileProduced]) -> list[int]:
        await self._ticker.stop()
        sent = await send_markdown(self._bot, self._chat_id, markdown, thread_id=self._thread_id)
        await send_files(self._bot, self._chat_id, files, thread_id=self._thread_id)
        return [message.message_id for message in sent]

    async def fail(self, text: str) -> None:
        await self._ticker.stop()
        await self._bot.send_message(chat_id=self._chat_id, text=text, message_thread_id=self._thread_id)


class EditSink:
    """Groups: Telegram has no drafts here, so the native "typing…" status shows while the bot works
    and the answer message appears with the first real text, then grows by edits."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        thread_id: int | None,
        reply_to: int | None,
        *,
        interval: float = 3.0,
        typing_interval: float = 4.5,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._reply_to = reply_to
        self._state = StreamState()
        self._message_id: int | None = None
        self._ticker = Ticker(self._flush, interval, None)
        self._typing = Ticker(self._send_typing, typing_interval, typing_interval)

    async def _send_typing(self) -> None:
        await self._bot.send_chat_action(
            chat_id=self._chat_id, action="typing", message_thread_id=self._thread_id
        )

    async def start(self) -> None:
        self._typing.touch()

    async def on_event(self, event: AgentEvent) -> None:
        if self._state.apply(event) and self._state.answer.strip():
            self._ticker.touch()

    async def _flush(self) -> None:
        answer = self._state.answer
        markdown = answer if len(answer) <= RICH_LIMIT else "…\n\n" + answer[-RICH_LIMIT:]
        if self._message_id is None:
            sent = await send_markdown(
                self._bot, self._chat_id, markdown, thread_id=self._thread_id, reply_to=self._reply_to
            )
            self._message_id = sent[0].message_id if sent else None
        else:
            await edit_markdown(self._bot, self._chat_id, self._message_id, markdown)

    async def _stop(self) -> None:
        await self._ticker.stop()
        await self._typing.stop()

    async def finish(self, markdown: str, files: Sequence[FileProduced]) -> list[int]:
        await self._stop()
        chunks = split_markdown(markdown, RICH_LIMIT) or ["Готово."]
        ids: list[int] = []
        if self._message_id is not None and await edit_markdown(
            self._bot, self._chat_id, self._message_id, chunks[0]
        ):
            ids.append(self._message_id)
        else:
            sent = await send_markdown(
                self._bot, self._chat_id, chunks[0], thread_id=self._thread_id, reply_to=self._reply_to
            )
            ids.extend(message.message_id for message in sent)
        for chunk in chunks[1:]:
            sent = await send_markdown(
                self._bot, self._chat_id, chunk, thread_id=self._thread_id, reply_to=ids[-1] if ids else None
            )
            ids.extend(message.message_id for message in sent)
        await send_files(
            self._bot, self._chat_id, files, thread_id=self._thread_id, reply_to=ids[0] if ids else None
        )
        return ids

    async def fail(self, text: str) -> None:
        await self._stop()
        if self._message_id is None or not await edit_markdown(
            self._bot, self._chat_id, self._message_id, text
        ):
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                message_thread_id=self._thread_id,
                reply_parameters=reply_parameters(self._reply_to),
            )


class GuestSink:
    """Guest mode: exactly one reply via answerGuestQuery, so nothing streams."""

    def __init__(self, bot: Bot, guest_query_id: str) -> None:
        self._bot = bot
        self._query_id = guest_query_id

    async def start(self) -> None:
        return None

    async def on_event(self, event: AgentEvent) -> None:
        return None

    async def _answer_text(self, markdown: str) -> None:
        text, entities = (entity_chunks(markdown) or [(markdown[:TEXT_LIMIT] or "…", [])])[0]
        content = InputTextMessageContent(message_text=text, entities=entities, parse_mode=None)
        await self._bot.answer_guest_query(
            guest_query_id=self._query_id,
            result=InlineQueryResultArticle(id="answer", title="Ответ", input_message_content=content),
        )

    async def finish(self, markdown: str, files: Sequence[FileProduced]) -> list[int]:
        if files:
            markdown += "\n\n_Файлы из этого ответа можно получить в личном чате с ботом._"
        chunk = (split_markdown(markdown, RICH_LIMIT) or ["…"])[0]
        content = InputRichMessageContent(rich_message=InputRichMessage(markdown=chunk))
        try:
            await self._bot.answer_guest_query(
                guest_query_id=self._query_id,
                result=InlineQueryResultArticle(id="answer", title="Ответ", input_message_content=content),
            )
        except TelegramBadRequest as exc:
            log.info("rich guest answer rejected (%s), answering with text", exc.message)
            await self._answer_text(chunk)
        return []

    async def fail(self, text: str) -> None:
        await self._answer_text(text)


class PlainSink:
    """No streaming: used for scheduled tasks that run without a person waiting."""

    def __init__(self, bot: Bot, chat_id: int, thread_id: int | None, reply_to: int | None = None) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._reply_to = reply_to

    async def start(self) -> None:
        return None

    async def on_event(self, event: AgentEvent) -> None:
        return None

    async def finish(self, markdown: str, files: Sequence[FileProduced]) -> list[int]:
        sent = await send_markdown(
            self._bot, self._chat_id, markdown, thread_id=self._thread_id, reply_to=self._reply_to
        )
        await send_files(self._bot, self._chat_id, files, thread_id=self._thread_id)
        return [message.message_id for message in sent]

    async def fail(self, text: str) -> None:
        await self._bot.send_message(chat_id=self._chat_id, text=text, message_thread_id=self._thread_id)
