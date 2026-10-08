"""Markdown → Telegram. Rich Messages first (GFM tables, LaTeX, details); entity text as the fallback."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputRichMessage, Message, MessageEntity, ReplyParameters
from telegramify_markdown import convert, split_entities

from tgagent.agent.events import FileProduced

log = logging.getLogger(__name__)

RICH_LIMIT = 30_000
TEXT_LIMIT = 4096
PHOTO_LIMIT = 10 * 1024 * 1024
PHOTO_TYPES = {"image/png", "image/jpeg", "image/webp"}
THINKING_TAIL = 500


def _blocks(markdown: str) -> list[str]:
    """Paragraph blocks separated by blank lines; a fenced code block is never broken apart."""
    blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for line in markdown.split("\n"):
        stripped = line.strip()
        if fence is None and stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
        elif fence is not None and stripped.startswith(fence):
            fence = None
        if fence is None and not stripped and current:
            blocks.append("\n".join(current))
            current = []
        elif stripped or current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _hard_split(text: str, limit: int) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)] or [""]


def _split_block(block: str, limit: int) -> list[str]:
    lines = block.split("\n")
    opener = lines[0] if lines[0].lstrip().startswith(("```", "~~~")) else None
    if opener is None:
        prefix, suffix, body = "", "", lines
    else:
        closed = len(lines) > 1 and lines[-1].strip().startswith(opener.strip()[:3])
        prefix, suffix = opener + "\n", "\n" + opener.strip()[:3]
        body = lines[1:-1] if closed else lines[1:]
    budget = limit - len(prefix) - len(suffix)
    pieces: list[str] = []
    buffer: list[str] = []
    size = 0
    for line in body:
        for part in _hard_split(line, budget):
            extra = len(part) + (1 if buffer else 0)
            if buffer and size + extra > budget:
                pieces.append(prefix + "\n".join(buffer) + suffix)
                buffer, size = [], 0
                extra = len(part)
            buffer.append(part)
            size += extra
    if buffer:
        pieces.append(prefix + "\n".join(buffer) + suffix)
    return pieces


def split_markdown(markdown: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in _blocks(markdown):
        for piece in [block] if len(block) <= limit else _split_block(block, limit):
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _thinking_block(text: str) -> str:
    return f"<tg-thinking>{escape(text, quote=False)}</tg-thinking>"


def _tail(text: str, size: int) -> str:
    text = text.strip()
    return text if len(text) <= size else "…" + text[-size:]


def compose_draft(answer: str, thinking: str | None, status: str | None) -> str:
    """Draft shown while generating: answer so far plus a ``<tg-thinking>`` progress block."""
    if not answer:
        return _thinking_block(status or (_tail(thinking, THINKING_TAIL) if thinking else "Думаю…"))
    if len(answer) > RICH_LIMIT:
        answer = "…\n\n" + answer[-RICH_LIMIT:]
    return f"{answer}\n\n{_thinking_block(status)}" if status else answer


def compose_final(answer: str, thinking: str) -> str:
    if not thinking.strip():
        return answer
    details = escape(thinking.strip(), quote=False)
    return f"<details><summary>💭 Размышления</summary>\n\n{details}\n\n</details>\n\n{answer}"


def entity_chunks(markdown: str) -> list[tuple[str, list[MessageEntity]]]:
    text, entities = convert(markdown)
    return [
        (chunk, [MessageEntity(**entity.to_dict()) for entity in chunk_entities])
        for chunk, chunk_entities in split_entities(text, entities, max_utf16_len=TEXT_LIMIT)
        if chunk.strip()
    ]


async def retrying[T](call: Callable[[], Awaitable[T]], attempts: int = 3) -> T:
    for attempt in range(attempts):
        try:
            return await call()
        except TelegramRetryAfter as exc:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(exc.retry_after)
    raise AssertionError("unreachable")


def reply_parameters(message_id: int | None) -> ReplyParameters | None:
    if message_id is None:
        return None
    return ReplyParameters(message_id=message_id, allow_sending_without_reply=True)


async def _send_entities(
    bot: Bot, chat_id: int, markdown: str, thread_id: int | None, reply: ReplyParameters | None
) -> list[Message]:
    sent: list[Message] = []
    for index, (text, entities) in enumerate(entity_chunks(markdown)):
        sent.append(
            await retrying(
                partial(
                    bot.send_message,
                    chat_id=chat_id,
                    text=text,
                    entities=entities,
                    parse_mode=None,
                    message_thread_id=thread_id,
                    reply_parameters=reply if index == 0 else None,
                )
            )
        )
    return sent


async def send_markdown(
    bot: Bot,
    chat_id: int,
    markdown: str,
    *,
    thread_id: int | None = None,
    reply_to: int | None = None,
) -> list[Message]:
    sent: list[Message] = []
    for index, chunk in enumerate(split_markdown(markdown, RICH_LIMIT)):
        reply = reply_parameters(reply_to) if index == 0 else None
        try:
            sent.append(
                await retrying(
                    partial(
                        bot.send_rich_message,
                        chat_id=chat_id,
                        rich_message=InputRichMessage(markdown=chunk),
                        message_thread_id=thread_id,
                        reply_parameters=reply,
                    )
                )
            )
        except TelegramBadRequest as exc:
            log.info("rich message rejected (%s), sending entity text", exc.message)
            sent.extend(await _send_entities(bot, chat_id, chunk, thread_id, reply))
    return sent


def _not_modified(exc: TelegramBadRequest) -> bool:
    return "message is not modified" in exc.message


async def edit_markdown(bot: Bot, chat_id: int, message_id: int, markdown: str) -> bool:
    try:
        await retrying(
            lambda: bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, rich_message=InputRichMessage(markdown=markdown)
            )
        )
        return True
    except TelegramBadRequest as exc:
        if _not_modified(exc):
            return True
        log.info("rich edit rejected (%s), editing with entity text", exc.message)
    chunks = entity_chunks(markdown) or [("…", [])]
    text, entities = chunks[0]
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, entities=entities, parse_mode=None
        )
    except TelegramBadRequest as exc:
        return _not_modified(exc)
    return True


async def send_files(
    bot: Bot,
    chat_id: int,
    files: Sequence[FileProduced],
    *,
    thread_id: int | None = None,
    reply_to: int | None = None,
) -> list[Message]:
    sent: list[Message] = []
    for file in files:
        upload = BufferedInputFile(file.data, filename=file.filename)
        reply = reply_parameters(reply_to)
        try:
            if file.mime_type in PHOTO_TYPES and len(file.data) <= PHOTO_LIMIT:
                sent.append(
                    await bot.send_photo(
                        chat_id=chat_id, photo=upload, message_thread_id=thread_id, reply_parameters=reply
                    )
                )
            else:
                sent.append(
                    await bot.send_document(
                        chat_id=chat_id, document=upload, message_thread_id=thread_id, reply_parameters=reply
                    )
                )
        except TelegramBadRequest:
            log.warning("could not send file %s", file.filename, exc_info=True)
    return sent
