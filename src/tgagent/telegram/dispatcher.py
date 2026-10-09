from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from tgagent.i18n import Lang, t
from tgagent.telegram.access import AccessMiddleware
from tgagent.telegram.deps import Deps
from tgagent.telegram.handlers import commands, groups, guest, membership, private


def private_commands(lang: Lang) -> list[BotCommand]:
    return [
        BotCommand(command="new", description=t(lang, "command.new")),
        BotCommand(command="settings", description=t(lang, "command.settings")),
        BotCommand(command="usage", description=t(lang, "command.usage")),
        BotCommand(command="help", description=t(lang, "command.help")),
    ]


def group_commands(lang: Lang) -> list[BotCommand]:
    return [
        BotCommand(command="settings", description=t(lang, "command.settings_group"), is_ephemeral=True),
        BotCommand(command="usage", description=t(lang, "command.usage_group"), is_ephemeral=True),
        BotCommand(command="help", description=t(lang, "command.help"), is_ephemeral=True),
    ]


def build_dispatcher(deps: Deps, bot: Bot) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(
        AccessMiddleware(
            deps.policy,
            lambda chat_id: bot.leave_chat(chat_id=chat_id),
            denied_text=deps.settings.access_denied_text,
        )
    )
    dispatcher.include_routers(
        membership.router,
        commands.router,
        private.router,
        groups.router,
        guest.router,
    )
    dispatcher["deps"] = deps
    return dispatcher


async def setup_commands(bot: Bot) -> None:
    """English for everyone, Russian for Russian-language Telegram apps."""
    languages: tuple[tuple[Lang, str | None], ...] = (("en", None), ("ru", "ru"))
    for lang, language_code in languages:
        await bot.set_my_commands(
            private_commands(lang), scope=BotCommandScopeAllPrivateChats(), language_code=language_code
        )
        await bot.set_my_commands(
            group_commands(lang), scope=BotCommandScopeAllGroupChats(), language_code=language_code
        )
