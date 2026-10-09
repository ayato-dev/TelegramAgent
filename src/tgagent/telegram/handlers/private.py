from aiogram import Bot, F, Router
from aiogram.types import Message, MessageGenerationStopped

from tgagent.context.normalize import normalize
from tgagent.services.turns import TurnRequest
from tgagent.telegram.deps import Deps
from tgagent.telegram.handlers.common import SUPPORTED_CONTENT, remember_user, run_with_stop
from tgagent.telegram.sinks import DraftSink

router = Router(name="private")
router.message.filter(F.chat.type == "private")


@router.message(F.forum_topic_created)
async def on_topic_created(message: Message, deps: Deps) -> None:
    """A user-created topic is a fresh conversation; an implicit name gets replaced after the first answer."""
    created = message.forum_topic_created
    if created is None or message.message_thread_id is None:
        return
    await deps.conversations.deactivate(message.chat.id, message.message_thread_id)
    await deps.conversations.create(
        message.chat.id, message.message_thread_id, "private", title_pending=bool(created.is_name_implicit)
    )


@router.message(F.content_type.in_(SUPPORTED_CONTENT))
async def on_private_message(message: Message, bot: Bot, deps: Deps) -> None:
    batch = await deps.bursts.collect(message)
    if batch is None:
        return
    first = batch[0]
    if first.from_user is None:
        return
    await remember_user(first, deps)
    await deps.chats.upsert(first.chat.id, "private", None)

    trigger = normalize(first)
    parent = first.reply_to_message
    reply_context = (normalize(parent),) if parent and parent.forum_topic_created is None else ()
    request = TurnRequest(
        kind="private",
        chat_id=first.chat.id,
        thread_id=trigger.thread_id,
        chat_title=None,
        user_id=first.from_user.id,
        trigger=trigger,
        reply_context=reply_context,
        album=tuple(normalize(m) for m in batch[1:]),
        options=deps.options(await deps.chats.get_settings(first.chat.id)),
    )
    sink = DraftSink(bot, first.chat.id, trigger.thread_id)
    async with deps.locks.hold((first.chat.id, trigger.thread_id)):
        await run_with_stop(deps, request, sink)


@router.stopped_message_generation()
async def on_stop(event: MessageGenerationStopped, deps: Deps) -> None:
    deps.generations.cancel(event.chat.id, event.draft_id)
