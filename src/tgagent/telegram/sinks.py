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
from tgagent.i18n import DEFAULT_LANG, TEXTS, Lang, t
from tgagent.telegram.render import (
    RICH_LIMIT,
    TEXT_LIMIT,
    compose_draft,
    entity_chunks,
    reply_parameters,
    send_files,
    send_markdown,
    split_markdown,
)

log = logging.getLogger(__name__)


def tool_status(event: ToolStarted, lang: Lang = DEFAULT_LANG) -> str:
    key = f"tool.{event.name}"
    label = t(lang, key) if key in TEXTS else f"🛠 {event.name}"
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
    lang: Lang = DEFAULT_LANG
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
                self.status = tool_status(event, self.lang)
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
        self,
        bot: Bot,
        chat_id: int,
        thread_id: int | None,
        *,
        lang: Lang = DEFAULT_LANG,
        interval: float = 0.6,
        keepalive: float = 10.0,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = thread_id
        self.draft_id = secrets.randbelow(2**31 - 2) + 1
        self._state = StreamState(lang)
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
                        markdown=compose_draft(state.answer, state.thinking, state.status, state.lang)
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


class TypingSink:
    """Groups: bots get no streaming drafts here (Bot API allows them in private chats only), so the
    chat shows the native "typing…" status while the bot works and then one complete reply.
    Growing a message by edits left stray half-answers behind and read as a plain bot."""

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        thread_id: int | None,
        reply_to: int | None,
        *,
        lang: Lang = DEFAULT_LANG,
        typing_interval: float = 4.5,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._reply_to = reply_to
        self._lang = lang
        self._typing = Ticker(self._send_typing, typing_interval, typing_interval)

    async def _send_typing(self) -> None:
        await self._bot.send_chat_action(
            chat_id=self._chat_id, action="typing", message_thread_id=self._thread_id
        )

    async def start(self) -> None:
        self._typing.touch()

    async def on_event(self, event: AgentEvent) -> None:
        return None

    async def finish(self, markdown: str, files: Sequence[FileProduced]) -> list[int]:
        await self._typing.stop()
        ids: list[int] = []
        for chunk in split_markdown(markdown, RICH_LIMIT) or [t(self._lang, "done")]:
            reply_to = ids[-1] if ids else self._reply_to
            sent = await send_markdown(
                self._bot, self._chat_id, chunk, thread_id=self._thread_id, reply_to=reply_to
            )
            ids.extend(message.message_id for message in sent)
        await send_files(
            self._bot, self._chat_id, files, thread_id=self._thread_id, reply_to=ids[0] if ids else None
        )
        return ids

    async def fail(self, text: str) -> None:
        await self._typing.stop()
        await self._bot.send_message(
            chat_id=self._chat_id,
            text=text,
            message_thread_id=self._thread_id,
            reply_parameters=reply_parameters(self._reply_to),
        )


class GuestSink:
    """Guest mode: exactly one reply via answerGuestQuery, so nothing streams."""

    def __init__(self, bot: Bot, guest_query_id: str, *, lang: Lang = DEFAULT_LANG) -> None:
        self._bot = bot
        self._query_id = guest_query_id
        self._lang = lang

    async def start(self) -> None:
        return None

    async def on_event(self, event: AgentEvent) -> None:
        return None

    async def _answer_text(self, markdown: str) -> None:
        text, entities = (entity_chunks(markdown) or [(markdown[:TEXT_LIMIT] or "…", [])])[0]
        content = InputTextMessageContent(message_text=text, entities=entities, parse_mode=None)
        await self._bot.answer_guest_query(
            guest_query_id=self._query_id,
            result=InlineQueryResultArticle(
                id="answer", title=t(self._lang, "guest.title"), input_message_content=content
            ),
        )

    async def finish(self, markdown: str, files: Sequence[FileProduced]) -> list[int]:
        if files:
            markdown += "\n\n" + t(self._lang, "guest.files")
        chunk = (split_markdown(markdown, RICH_LIMIT) or ["…"])[0]
        content = InputRichMessageContent(rich_message=InputRichMessage(markdown=chunk))
        try:
            await self._bot.answer_guest_query(
                guest_query_id=self._query_id,
                result=InlineQueryResultArticle(
                    id="answer", title=t(self._lang, "guest.title"), input_message_content=content
                ),
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
