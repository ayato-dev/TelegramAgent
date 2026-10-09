from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from tgagent.telegram.access import AccessMiddleware
from tgagent.telegram.deps import Deps
from tgagent.telegram.handlers import commands, groups, guest, membership, private

PRIVATE_COMMANDS = [
    BotCommand(command="new", description="Новый разговор"),
    BotCommand(command="settings", description="Модель, глубина размышлений и инструменты"),
    BotCommand(command="usage", description="Расходы на API"),
    BotCommand(command="help", description="Что я умею"),
]
GROUP_COMMANDS = [
    BotCommand(command="settings", description="Твои настройки агента", is_ephemeral=True),
    BotCommand(command="usage", description="Расходы этого чата", is_ephemeral=True),
    BotCommand(command="help", description="Что я умею", is_ephemeral=True),
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
    await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(GROUP_COMMANDS, scope=BotCommandScopeAllGroupChats())
