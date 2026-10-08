import asyncio
import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    EphemeralMessageParameters,
    InlineKeyboardMarkup,
    InputRichMessage,
    Message,
    ReplyParameters,
)

from tgagent.services.turns import TurnRequest
from tgagent.telegram.deps import Deps
from tgagent.telegram.render import send_markdown
from tgagent.telegram.sinks import DraftSink

log = logging.getLogger(__name__)

SUPPORTED_CONTENT = {
    "text",
    "photo",
    "voice",
    "video_note",
    "audio",
    "video",
    "animation",
    "document",
    "sticker",
    "checklist",
}


async def remember_user(message: Message, deps: Deps) -> None:
    user = message.from_user
    if user is not None and not user.is_bot:
        await deps.users.upsert(user.id, user.first_name, user.username, user.language_code)


async def run_with_stop(deps: Deps, request: TurnRequest, sink: DraftSink) -> None:
    """Run a private turn in its own task so the draft's Stop button can cancel just that task."""
    task = asyncio.create_task(deps.turns.run(request, sink))
    with deps.generations.track(request.chat_id, sink.draft_id, task):
        try:
            await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise


async def reply_privately(
    message: Message,
    text: str,
    *,
    markup: InlineKeyboardMarkup | None = None,
    markdown: bool = False,
) -> None:
    """Answer a command: normally in private chats, as an ephemeral message in groups when possible."""
    bot = message.bot
    assert bot is not None
    chat_id, thread_id = message.chat.id, message.message_thread_id if message.is_topic_message else None
    if message.chat.type != "private" and message.ephemeral_message_id and message.from_user:
        ephemeral = EphemeralMessageParameters(receiver_user_id=message.from_user.id)
        reply = ReplyParameters(ephemeral_message_id=message.ephemeral_message_id)
        try:
            if markdown:
                await bot.send_rich_message(
                    chat_id=chat_id,
                    rich_message=InputRichMessage(markdown=text),
                    reply_markup=markup,
                    ephemeral_message_parameters=ephemeral,
                    reply_parameters=reply,
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=markup,
                    ephemeral_message_parameters=ephemeral,
                    reply_parameters=reply,
                )
            return
        except TelegramBadRequest as exc:
            log.info("ephemeral reply failed (%s), replying publicly", exc.message)
    if markdown and markup is None:
        await send_markdown(bot, chat_id, text, thread_id=thread_id)
    else:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, message_thread_id=thread_id)
