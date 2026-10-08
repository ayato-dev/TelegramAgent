from types import SimpleNamespace
from typing import cast

from aiogram import Bot

from tgagent.telegram.access import AccessPolicy
from tgagent.telegram.deps import Deps
from tgagent.telegram.dispatcher import GROUP_COMMANDS, build_dispatcher


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
    } <= used


def test_group_commands_are_ephemeral() -> None:
    assert all(command.is_ephemeral for command in GROUP_COMMANDS)
