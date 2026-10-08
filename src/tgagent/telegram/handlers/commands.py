import logging
from datetime import UTC, datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

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

HELP = """\
Я ИИ-агент. Что умею:
• отвечать, рассуждать и считать (Python-песочница, графики и файлы);
• искать в интернете и читать ссылки;
• понимать фото, PDF и документы, расшифровывать голосовые и кружочки;
• ставить напоминания и отложенные задания («через час проверь курс и напиши»);
• выполнять задачи из чек-листов Telegram.

В личке каждый топик — отдельный разговор, /new начинает новый. Ответ на моё старое сообщение \
продолжает разговор с того места. Во время ответа можно нажать «Стоп».
В группе упомяните меня или ответьте на моё сообщение.

/settings — модель, глубина размышлений и инструменты, /usage — расходы."""


NOT_ALLOWED = "Эта команда доступна только тем, кто управляет ботом."


def can_manage(message: Message, deps: Deps) -> bool:
    """Anyone in an allowed group may talk to the bot, but only whitelisted users manage it."""
    if message.chat.type not in GROUP_TYPES:
        return True
    return deps.policy.user_allowed(message.from_user.id if message.from_user else None)


@router.message(CommandStart())
@router.message(Command("help"))
async def on_help(message: Message, deps: Deps) -> None:
    await remember_user(message, deps)
    await reply_privately(message, HELP)


@router.message(Command("new"), F.chat.type == "private")
async def on_new(message: Message, bot: Bot, deps: Deps) -> None:
    chat_id = message.chat.id
    if deps.me.has_topics_enabled:
        topic = await bot.create_forum_topic(chat_id=chat_id, name="Новый чат")
        await deps.conversations.create(chat_id, topic.message_thread_id, "private", title_pending=True)
        await bot.send_message(
            chat_id=chat_id,
            text="Новый чат готов — пишите сюда 👇",
            message_thread_id=topic.message_thread_id,
        )
        return
    thread_id = message.message_thread_id if message.is_topic_message else None
    await deps.conversations.deactivate(chat_id, thread_id)
    await message.answer("🆕 Начат новый разговор, прежний контекст больше не учитывается.")


@router.message(Command("settings"))
async def on_settings(message: Message, deps: Deps) -> None:
    if not can_manage(message, deps):
        await reply_privately(message, NOT_ALLOWED)
        return
    chat = message.chat
    await deps.chats.upsert(chat.id, chat.type, chat.title)
    options = deps.options(await deps.chats.get_settings(chat.id))
    model = deps.model(options)
    await reply_privately(
        message,
        settings_text(options, model, group=chat.type in GROUP_TYPES),
        markup=settings_keyboard(options, model, pickable=len(deps.models) > 1),
    )


@router.message(Command("usage"))
async def on_usage(message: Message, deps: Deps) -> None:
    if not can_manage(message, deps):
        await reply_privately(message, NOT_ALLOWED)
        return
    chat_id = message.chat.id if message.chat.type in GROUP_TYPES else None
    report = await deps.usage_report.render(chat_id=chat_id, now=datetime.now(UTC))
    await reply_privately(message, report, markdown=True)


@router.callback_query(F.data.startswith(SETTINGS_PREFIX))
async def on_settings_button(query: CallbackQuery, bot: Bot, deps: Deps) -> None:
    message = query.message
    if not isinstance(message, Message):
        await query.answer("Сообщение с настройками устарело, вызовите /settings ещё раз.")
        return
    chat_id = message.chat.id
    action = (query.data or "").removeprefix(SETTINGS_PREFIX)
    options = deps.options(await deps.chats.get_settings(chat_id))
    patch = toggle(options, action, deps.model_keys)
    if patch:
        options = deps.options(await deps.chats.update_settings(chat_id, patch))
    model = deps.model(options)
    if action == "models":
        text, markup = models_text(model), models_keyboard(deps.models, model.key)
    else:
        text = settings_text(options, model, group=message.chat.type in GROUP_TYPES)
        markup = settings_keyboard(options, model, pickable=len(deps.models) > 1)
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
        await query.answer(f"Модель: {model.label}. Следующее сообщение начнёт новый разговор.")
    else:
        await query.answer("Сохранено" if patch else None)
