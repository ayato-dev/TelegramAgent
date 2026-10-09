from aiogram import Bot, Router
from aiogram.types import Message

from tgagent.context.normalize import normalize
from tgagent.i18n import lang_of
from tgagent.services.turns import TurnRequest
from tgagent.telegram.deps import Deps
from tgagent.telegram.handlers.common import remember_user
from tgagent.telegram.sinks import GuestSink

router = Router(name="guest")


@router.guest_message()
async def on_guest_message(message: Message, bot: Bot, deps: Deps) -> None:
    """Summoned in a chat the bot is not a member of: one answer, using the caller's own settings."""
    if not message.guest_query_id or message.from_user is None:
        return
    await remember_user(message, deps)
    parent = message.reply_to_message
    request = TurnRequest(
        kind="guest",
        chat_id=message.chat.id,
        thread_id=None,
        chat_title=message.chat.title,
        user_id=message.from_user.id,
        trigger=normalize(message),
        reply_context=(normalize(parent),) if parent else (),
        options=deps.options(await deps.chats.get_settings(message.from_user.id)),
        lang=lang_of(message.from_user.language_code),
    )
    await deps.turns.run(request, GuestSink(bot, message.guest_query_id, lang=request.lang))
