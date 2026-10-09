import logging
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from tgagent.i18n import Lang, lang_of, t
from tgagent.services.settings import toggle
from tgagent.telegram.access import GROUP_TYPES
from tgagent.telegram.deps import Deps
from tgagent.telegram.handlers.common import remember_user, reply_privately
from tgagent.telegram.keyboards import (
    SETTINGS_PREFIX,
    models_keyboard,
    models_text,
    settings_keyboard,
    settings_text,
)

log = logging.getLogger(__name__)
router = Router(name="commands")


def language(message: Message) -> Lang:
    return lang_of(message.from_user.language_code if message.from_user else None)


def can_manage(message: Message, deps: Deps) -> bool:
    """Anyone in an allowed group may talk to the bot, but only whitelisted users manage it."""
    if message.chat.type not in GROUP_TYPES:
        return True
    return deps.policy.user_allowed(message.from_user.id if message.from_user else None)


@router.message(CommandStart())
@router.message(Command("help"))
async def on_help(message: Message, deps: Deps) -> None:
    await remember_user(message, deps)
    await reply_privately(message, t(language(message), "help"))


@router.message(Command("new"), F.chat.type == "private")
async def on_new(message: Message, bot: Bot, deps: Deps) -> None:
    chat_id, lang = message.chat.id, language(message)
    if deps.me.has_topics_enabled:
        topic = await bot.create_forum_topic(chat_id=chat_id, name=t(lang, "new.topic_name"))
        await deps.conversations.create(chat_id, topic.message_thread_id, "private", title_pending=True)
        await bot.send_message(
            chat_id=chat_id,
            text=t(lang, "new.topic_ready"),
            message_thread_id=topic.message_thread_id,
        )
        return
    thread_id = message.message_thread_id if message.is_topic_message else None
    await deps.conversations.deactivate(chat_id, thread_id)
    await message.answer(t(lang, "new.started"))


@router.message(Command("settings"))
async def on_settings(message: Message, deps: Deps) -> None:
    lang = language(message)
    if not can_manage(message, deps):
        await reply_privately(message, t(lang, "not_allowed"))
        return
    chat, user = message.chat, message.from_user
    await deps.chats.upsert(chat.id, chat.type, chat.title)
    if user is None:
        return
    # Settings belong to the person and follow them into groups and guest mode; the row of their
    # private chat with the bot (chat id = user id) keeps them.
    await deps.chats.upsert(user.id, "private", None)
    options = deps.options(await deps.chats.get_settings(user.id))
    model = deps.model(options)
    await reply_privately(
        message,
        settings_text(options, model, lang),
        markup=settings_keyboard(options, model, pickable=len(deps.models) > 1, lang=lang),
    )


@router.message(Command("usage"))
async def on_usage(message: Message, deps: Deps) -> None:
    lang = language(message)
    if not can_manage(message, deps):
        await reply_privately(message, t(lang, "not_allowed"))
        return
    chat_id = message.chat.id if message.chat.type in GROUP_TYPES else None
    report = await deps.usage_report.render(chat_id=chat_id, now=datetime.now(UTC), lang=lang)
    await reply_privately(message, report, markdown=True)


@router.callback_query(F.data.startswith(SETTINGS_PREFIX))
async def on_settings_button(query: CallbackQuery, bot: Bot, deps: Deps) -> None:
    message = query.message
    lang = lang_of(query.from_user.language_code)
    if not isinstance(message, Message):
        await query.answer(t(lang, "settings.stale"))
        return
    chat_id, owner = message.chat.id, query.from_user.id
    action = (query.data or "").removeprefix(SETTINGS_PREFIX)
    options = deps.options(await deps.chats.get_settings(owner))
    patch = toggle(options, action, deps.model_keys)
    if patch:
        options = deps.options(await deps.chats.update_settings(owner, patch))
    model = deps.model(options)
    if action == "models":
        text, markup = models_text(model, lang), models_keyboard(deps.models, model.key, lang)
    else:
        text = settings_text(options, model, lang)
        markup = settings_keyboard(options, model, pickable=len(deps.models) > 1, lang=lang)
    try:
        if message.ephemeral_message_id:
            await bot.edit_ephemeral_message_text(
                chat_id=chat_id,
                receiver_user_id=query.from_user.id,
                ephemeral_message_id=message.ephemeral_message_id,
                text=text,
                reply_markup=markup,
            )
        else:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message.message_id, text=text, reply_markup=markup
            )
    except TelegramBadRequest as exc:
        if "message is not modified" not in exc.message:
            log.warning("could not update settings message: %s", exc.message)
    if "model" in patch:
        await query.answer(t(lang, "settings.model_chosen", label=model.label))
    else:
        await query.answer(t(lang, "settings.saved") if patch else None)
