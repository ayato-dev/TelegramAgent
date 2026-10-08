import logging

from aiogram import Bot, F, Router
from aiogram.types import ChatMemberUpdated, Message

from tgagent.telegram.access import GROUP_TYPES
from tgagent.telegram.deps import Deps

log = logging.getLogger(__name__)
router = Router(name="membership")

PRESENT = {"member", "administrator", "restricted"}


def greeting(deps: Deps) -> str:
    text = (
        f"Привет! Я ИИ-агент. Упомяните меня (@{deps.username}) или ответьте на моё сообщение — "
        "отвечу, поищу в интернете, посчитаю, расшифрую голосовое или поставлю напоминание.\n"
        "Подсказка: ответьте на чужое сообщение с упоминанием, например «@"
        f"{deps.username} это правда?», — я учту, о чём речь."
    )
    if not deps.me.can_read_all_group_messages:
        text += (
            "\n\n⚠️ У меня включён privacy mode: я вижу только обращения ко мне, поэтому не смогу "
            "пересказать обсуждение. Владелец может выключить его в @BotFather и добавить меня заново."
        )
    return text


async def handle_membership(event: ChatMemberUpdated, bot: Bot, deps: Deps) -> None:
    chat = event.chat
    if chat.type not in GROUP_TYPES:
        return
    await deps.chats.upsert(chat.id, chat.type, chat.title)
    if event.new_chat_member.status not in PRESENT:
        await deps.chats.set_allowed(chat.id, False, None)
        deps.policy.revoke_chat(chat.id)
        return
    if deps.policy.chat_allowed(chat.id):
        return
    if deps.policy.user_allowed(event.from_user.id):
        await deps.chats.set_allowed(chat.id, True, event.from_user.id)
        deps.policy.allow_chat(chat.id)
        await bot.send_message(chat_id=chat.id, text=greeting(deps))
        return
    log.info("added to chat %s by non-allowed user %s, leaving", chat.id, event.from_user.id)
    await deps.chats.set_allowed(chat.id, False, event.from_user.id)
    await bot.leave_chat(chat_id=chat.id)


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot, deps: Deps) -> None:
    await handle_membership(event, bot, deps)


@router.message(F.migrate_to_chat_id | F.migrate_from_chat_id)
async def on_migrate(message: Message, deps: Deps) -> None:
    if message.migrate_to_chat_id:
        old, new = message.chat.id, message.migrate_to_chat_id
    else:
        old, new = message.migrate_from_chat_id or 0, message.chat.id
    if not deps.policy.chat_allowed(old) and not deps.policy.chat_allowed(new):
        return
    await deps.chats.migrate(old, new)
    deps.policy.allow_chat(new)
    deps.policy.revoke_chat(old)
