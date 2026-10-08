from aiogram import Bot, F, Router
from aiogram.types import Message

from tgagent.context.normalize import normalize
from tgagent.domain import NormalizedMessage
from tgagent.services.turns import TurnRequest
from tgagent.telegram.access import GROUP_TYPES
from tgagent.telegram.deps import Deps
from tgagent.telegram.handlers.common import SUPPORTED_CONTENT, remember_user
from tgagent.telegram.sinks import EditSink
from tgagent.telegram.triggers import is_addressed

router = Router(name="groups")
router.message.filter(F.chat.type.in_(GROUP_TYPES))
router.edited_message.filter(F.chat.type.in_(GROUP_TYPES))

REPLY_CHAIN_DEPTH = 6


async def reply_context(message: Message, deps: Deps) -> tuple[NormalizedMessage, ...]:
    """The replied-to message plus what it replies to, from the chat log (falling back to the update)."""
    parent = message.reply_to_message
    if parent is None or parent.forum_topic_created is not None:
        return ()
    chain = await deps.chat_log.chain(message.chat.id, parent.message_id, depth=REPLY_CHAIN_DEPTH)
    return tuple(chain) if chain else (normalize(parent),)


@router.edited_message(F.content_type.in_(SUPPORTED_CONTENT))
async def on_group_edit(message: Message, deps: Deps) -> None:
    await deps.chat_log.add(normalize(message))


@router.message(F.content_type.in_(SUPPORTED_CONTENT))
async def on_group_message(message: Message, bot: Bot, deps: Deps) -> None:
    normalized = normalize(message)
    await deps.chat_log.add(normalized)
    user = message.from_user
    if user is None or user.is_bot or not is_addressed(message, deps.me.id, deps.username):
        return
    await remember_user(message, deps)
    request = TurnRequest(
        kind="group",
        chat_id=message.chat.id,
        thread_id=normalized.thread_id,
        chat_title=message.chat.title,
        user_id=user.id,
        trigger=normalized,
        reply_context=await reply_context(message, deps),
        options=deps.options(await deps.chats.get_settings(message.chat.id)),
    )
    sink = EditSink(bot, message.chat.id, normalized.thread_id, reply_to=message.message_id)
    async with deps.locks.hold(message.chat.id):
        await deps.turns.run(request, sink)
