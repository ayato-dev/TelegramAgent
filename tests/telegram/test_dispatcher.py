from types import SimpleNamespace
from typing import cast

from aiogram import Bot

from tgagent.telegram.access import AccessPolicy
from tgagent.telegram.deps import Deps
from tgagent.telegram.dispatcher import build_dispatcher, group_commands, setup_commands


def test_dispatcher_subscribes_to_agent_update_types() -> None:
    deps = cast(
        Deps,
        SimpleNamespace(
            policy=AccessPolicy(frozenset(), set()), settings=SimpleNamespace(access_denied_text="")
        ),
    )

    dispatcher = build_dispatcher(deps, cast(Bot, SimpleNamespace()))

    used = set(dispatcher.resolve_used_update_types())
    assert {
        "message",
        "edited_message",
        "callback_query",
        "my_chat_member",
        "guest_message",
        "stopped_message_generation",
        "business_connection",
        "business_message",
    } <= used


def test_group_commands_are_ephemeral() -> None:
    assert all(command.is_ephemeral for lang in ("ru", "en") for command in group_commands(lang))


async def test_commands_are_registered_in_english_and_russian() -> None:
    from tests.telegram.fakes import FakeBot

    bot = FakeBot()

    await setup_commands(bot.as_bot())

    calls = [params for name, params in bot.calls if name == "set_my_commands"]
    assert {params.get("language_code") for params in calls} == {None, "ru"}
    descriptions = {params.get("language_code"): params["commands"][0].description for params in calls}
    assert descriptions[None] != descriptions["ru"]
